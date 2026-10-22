"""文件快照与回滚，对应 domain/checkpoint/CheckpointManager.kt。

App 侧快照元数据存 Room、内容存 filesDir；此处改为「快照目录 + manifest.json」，
语义保持一致：同一 checkpoint 内同一文件只保留最原始的一份快照；回滚前用内容指纹
判断文件在快照之后是否被他人改过，改过则跳过并把清单交给调用方。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .versions import FileVersionRegistry

MANIFEST_NAME = "manifest.json"


@dataclass
class CheckpointRestoreResult:
    restored: int
    skipped: List[str] = field(default_factory=list)


@dataclass
class SnapshotEntry:
    path: str
    change_type: str  # MODIFY | CREATE
    snapshot_file: str
    fingerprint: Optional[str] = None
    created_at: float = 0.0


def fingerprint_file(path: str) -> Optional[str]:
    """内容指纹；文件不存在或读取失败返回 None（视为未知版本）。"""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


class CheckpointManager:
    def __init__(self, base_dir: str, versions: Optional[FileVersionRegistry] = None):
        self._base_dir = base_dir
        self._active: Dict[str, str] = {}
        self._versions = versions or FileVersionRegistry()

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def create_checkpoint(self, session_id: str, user_message_id: str, prompt: str) -> str:
        checkpoint_id = str(uuid.uuid4())
        snippet = prompt[:60] + "..." if len(prompt) > 60 else prompt
        directory = self._checkpoint_dir(session_id, checkpoint_id)
        os.makedirs(directory, exist_ok=True)
        self._write_manifest(directory, {
            "id": checkpoint_id,
            "sessionId": session_id,
            "userMessageId": user_message_id,
            "promptSnippet": snippet,
            "createdAt": time.time(),
            "files": [],
        })
        self._active[session_id] = checkpoint_id
        return checkpoint_id

    def set_active_checkpoint_id(self, session_id: str, checkpoint_id: Optional[str]) -> None:
        if checkpoint_id is None:
            self._active.pop(session_id, None)
        else:
            self._active[session_id] = checkpoint_id

    # ── 快照 ──────────────────────────────────────────────────────────────

    def before_file_modified(self, session_id: str, file_path: str) -> None:
        """工具修改文件前调用：同一 checkpoint 内同一文件只快照一次。"""
        checkpoint_id = self._active.get(session_id)
        if checkpoint_id is None:
            return
        directory = self._checkpoint_dir(session_id, checkpoint_id)
        manifest = self._read_manifest(directory)
        if any(entry["path"] == file_path for entry in manifest["files"]):
            return

        exists = os.path.exists(file_path)
        snapshot_name = f"{uuid.uuid4()}_{os.path.basename(file_path) or 'file'}"
        snapshot_path = os.path.join(directory, snapshot_name)

        if exists:
            # 指纹算不出来（文件过大、IO 异常）就留空，回滚时按「未知版本」放行
            fingerprint = fingerprint_file(file_path)
            try:
                shutil.copyfile(file_path, snapshot_path)
            except OSError:
                fingerprint = None
                open(snapshot_path, "wb").close()
        else:
            fingerprint = None
            open(snapshot_path, "wb").close()

        manifest["files"].append({
            "path": file_path,
            "changeType": "MODIFY" if exists else "CREATE",
            "snapshotFile": snapshot_name,
            "fingerprint": fingerprint,
            "createdAt": time.time(),
        })
        self._write_manifest(directory, manifest)

    # ── 回滚 ──────────────────────────────────────────────────────────────

    def restore(self, session_id: str, checkpoint_id: str) -> CheckpointRestoreResult:
        directory = self._checkpoint_dir(session_id, checkpoint_id)
        if not os.path.isdir(directory):
            return CheckpointRestoreResult(0, [])
        manifest = self._read_manifest(directory)

        restored = 0
        skipped: List[str] = []
        for entry in reversed(manifest["files"]):
            path = entry["path"]
            snapshot_path = os.path.join(directory, entry["snapshotFile"])

            if entry["changeType"] == "CREATE":
                # 创建快照意味着文件是本会话新建的，常规回滚就是删掉它
                if os.path.exists(path):
                    try:
                        os.remove(path)
                        self._versions.forget(session_id, path)
                        restored += 1
                    except OSError:
                        skipped.append(path)
                continue

            if not os.path.exists(snapshot_path):
                continue

            # 只在「磁盘当前版本 != 本会话记录版本」时跳过：那才是本会话没见过的外部改动。
            # 本会话自己的写入会刷新记录，因此不会被误判。
            current = fingerprint_file(path)
            known = self._versions.get(session_id, path)
            if current is not None and known is not None and current != known:
                skipped.append(path)
                continue

            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                shutil.copyfile(snapshot_path, path)
                # 回滚是一次「外部写入」：所有会话手上的版本都过期了
                self._versions.record_external(path, fingerprint_file(path) or "")
                restored += 1
            except OSError:
                skipped.append(path)
        return CheckpointRestoreResult(restored, skipped)

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _checkpoint_dir(self, session_id: str, checkpoint_id: str) -> str:
        return os.path.join(self._base_dir, session_id, checkpoint_id)

    @staticmethod
    def _read_manifest(directory: str) -> dict:
        path = os.path.join(directory, MANIFEST_NAME)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"files": []}

    @staticmethod
    def _write_manifest(directory: str, manifest: dict) -> None:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, MANIFEST_NAME)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
