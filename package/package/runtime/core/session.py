"""会话记录 - 以 JSONL 追加方式保存对话与工具调用历史。"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SessionEntry:
    """一条会话记录。"""

    role: str
    content: str
    kind: str = "message"
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionStore:
    """按会话保存历史，每次写入追加一行 JSON。

    写不了盘时自动降级为内存存储：会话记录只是辅助信息，
    不能因为它不可写就让整个对话失败。
    """

    def __init__(self, base_dir: str | None = None, session_id: str | None = None):
        self.session_id = session_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self._memory: list[SessionEntry] = []
        self._writable = True

        base = base_dir or os.path.join(
            os.path.expanduser("~"), ".dbagent", "sessions")
        try:
            os.makedirs(base, exist_ok=True)
            # 实测可写性，避免等到 append 才失败
            probe = os.path.join(base, f".probe-{uuid.uuid4().hex[:8]}")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("")
            os.remove(probe)
        except OSError:
            self._writable = False
            base = None

        self.base_dir = base
        self.path = os.path.join(base, f"{self.session_id}.jsonl") if base else ""

    def append(self, entry: SessionEntry) -> None:
        if not self._writable:
            self._memory.append(entry)
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            # 中途变成不可写：转内存，后续不再重试磁盘
            self._writable = False
            self._memory.append(entry)

    def add(self, role: str, content: str, kind: str = "message", **metadata: Any) -> SessionEntry:
        entry = SessionEntry(role=role, content=content, kind=kind, metadata=metadata)
        self.append(entry)
        return entry

    def entries(self) -> list[SessionEntry]:
        if not self._writable:
            return list(self._memory)
        if not self.path or not os.path.exists(self.path):
            return []
        result = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    result.append(SessionEntry(**data))
        except OSError:
            return list(self._memory)
        return result

    def messages(self) -> list[dict[str, str]]:
        """转成 chat/completions 需要的消息格式。"""
        return [{"role": e.role, "content": e.content}
                for e in self.entries() if e.kind == "message"]

    @classmethod
    def latest(cls, base_dir: str | None = None) -> "SessionStore | None":
        base = base_dir or os.path.join(os.path.expanduser("~"), ".dbagent", "sessions")
        try:
            if not os.path.isdir(base):
                return None
            files = [f for f in os.listdir(base) if f.endswith(".jsonl")]
            if not files:
                return None
            newest = max(files, key=lambda f: os.path.getmtime(os.path.join(base, f)))
            return cls(base_dir=base, session_id=newest[:-len(".jsonl")])
        except OSError:
            return None
