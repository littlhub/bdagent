"""文件读写后端，对应 feature/workspace/domain/LocalFileAccess.kt。

所有路径入参是 AI 给的容器路径，由 WorkspacePathMapper 映射到宿主真实文件。
写入走原子替换：同目录临时文件 → 写入 → fsync → 长度回读校验 → 原子替换目标。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import List, Optional

from .paths import WorkspacePathMapper

TMP_FILE_PREFIX = ".aicode-tmp-"
TMP_FILE_SUFFIX = ".part"


class FileAccessError(Exception):
    """文件操作失败的基类。"""


class NoSuchFile(FileAccessError):
    pass


class FileAlreadyExists(FileAccessError):
    pass


@dataclass
class FileEntry:
    name: str
    is_directory: bool
    size: int
    last_modified: int
    permissions: str = "---"
    local_path: Optional[str] = None


def sha256_fingerprint(data: bytes) -> str:
    """内容指纹：`sha256:<十六进制小写>`。

    前缀写进指纹本身，将来若换算法，旧记录与新记录天然不可比，
    会退化成「未知版本」而不是错误地判定为相等。
    """
    return "sha256:" + hashlib.sha256(data).hexdigest()


class LocalFileAccess:
    def __init__(self, mapper: WorkspacePathMapper):
        self._mapper = mapper

    @property
    def mapper(self) -> WorkspacePathMapper:
        return self._mapper

    def _resolve(self, path: str) -> str:
        return self._mapper.to_host_path(path)

    # ── 读 ────────────────────────────────────────────────────────────────

    def read_file(self, path: str) -> str:
        target = self._resolve(path)
        if not os.path.exists(target):
            raise NoSuchFile(target)
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()

    def read_lines(self, path: str) -> List[str]:
        target = self._resolve(path)
        if not os.path.exists(target):
            raise NoSuchFile(target)
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            return handle.readlines()

    def read_bytes(self, path: str) -> bytes:
        target = self._resolve(path)
        if not os.path.exists(target):
            raise NoSuchFile(target)
        with open(target, "rb") as handle:
            return handle.read()

    # ── 写 ────────────────────────────────────────────────────────────────

    def write_file(self, path: str, content: str, overwrite: bool = True) -> None:
        self.write_bytes(path, content.encode("utf-8"), overwrite)

    def write_bytes(self, path: str, data: bytes, overwrite: bool = True) -> None:
        target = self._resolve(path)
        if os.path.exists(target) and not overwrite:
            raise FileAlreadyExists(target)
        self._write_atomically(target, data)

    def _write_atomically(self, target: str, data: bytes) -> None:
        """同目录临时文件 → 写入 → fsync → 长度校验 → 原子替换。

        原地截断写会让并发读者看到半截内容，且中途失败会留下半个文件。
        """
        parent = os.path.dirname(target) or "."
        os.makedirs(parent, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(prefix=TMP_FILE_PREFIX, suffix=TMP_FILE_SUFFIX, dir=parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            actual = os.path.getsize(tmp_path)
            if actual != len(data):
                raise FileAccessError(
                    f"write verification failed: {target} expected {len(data)} bytes, found {actual}"
                )
            os.replace(tmp_path, target)
            tmp_path = ""
        finally:
            # 替换成功后临时文件已不存在，这里兜底清理失败与异常路径
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    # ── 查询 ──────────────────────────────────────────────────────────────

    def exists(self, path: str) -> bool:
        return os.path.exists(self._resolve(path))

    def is_directory(self, path: str) -> bool:
        return os.path.isdir(self._resolve(path))

    def is_file(self, path: str) -> bool:
        return os.path.isfile(self._resolve(path))

    def file_size(self, path: str) -> int:
        return os.path.getsize(self._resolve(path))

    def last_modified(self, path: str) -> int:
        return int(os.path.getmtime(self._resolve(path)) * 1000)

    def fingerprint(self, path: str) -> Optional[str]:
        """内容指纹；非普通文件返回 None，让调用方放行。"""
        target = self._resolve(path)
        if not os.path.isfile(target):
            return None
        try:
            with open(target, "rb") as handle:
                return sha256_fingerprint(handle.read())
        except OSError:
            return None

    def permissions(self, path: str) -> str:
        target = self._resolve(path)
        read = "r" if os.access(target, os.R_OK) else "-"
        write = "w" if os.access(target, os.W_OK) else "-"
        execute = "x" if os.access(target, os.X_OK) or os.path.isdir(target) else "-"
        return read + write + execute

    def list_files(self, path: str) -> List[FileEntry]:
        directory = self._resolve(path)
        if not os.path.isdir(directory):
            return []
        entries: List[FileEntry] = []
        for name in sorted(os.listdir(directory)):
            child = os.path.join(directory, name)
            is_dir = os.path.isdir(child)
            entries.append(FileEntry(
                name=name,
                is_directory=is_dir,
                size=0 if is_dir else os.path.getsize(child),
                last_modified=int(os.path.getmtime(child) * 1000),
                permissions=self.permissions(self._mapper.to_container_path(child)),
                local_path=child,
            ))
        return entries

    def list_files_recursive(self, path: str, max_depth: int) -> List[str]:
        directory = self._resolve(path)
        if not os.path.isdir(directory):
            return []
        results: List[str] = []
        base_depth = directory.rstrip(os.sep).count(os.sep)
        for root, dirs, files in os.walk(directory):
            depth = root.rstrip(os.sep).count(os.sep) - base_depth
            if depth >= max_depth:
                dirs[:] = []
            for name in files:
                full = os.path.join(root, name)
                results.append(os.path.relpath(full, directory).replace(os.sep, "/"))
        return results

    # ── 变更 ──────────────────────────────────────────────────────────────

    def delete(self, path: str) -> None:
        target = self._resolve(path)
        if os.path.exists(target):
            os.remove(target)

    def delete_recursively(self, path: str) -> None:
        target = self._resolve(path)
        if not os.path.exists(target):
            return
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)

    def rename(self, path: str, new_path: str) -> None:
        source = self._resolve(path)
        target = self._resolve(new_path)
        if not os.path.exists(source):
            raise NoSuchFile(source)
        if os.path.exists(target):
            raise FileAlreadyExists(target)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        os.rename(source, target)

    def mkdirs(self, path: str) -> None:
        os.makedirs(self._resolve(path), exist_ok=True)

    def parent_path(self, path: str) -> Optional[str]:
        parent = os.path.dirname(self._resolve(path))
        if not parent:
            return None
        return self._mapper.to_container_path(parent)

    def to_display_path(self, path: str) -> str:
        """把宿主路径还原为 AI 视角的容器路径。"""
        return self._mapper.to_container_path(os.path.abspath(self._resolve(path)))
