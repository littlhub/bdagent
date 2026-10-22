"""文件版本登记，对应 domain/tool/FileVersionRegistry.kt。

记录每个会话「最后看到的文件版本」。回滚时用它区分两种「被改过」：
- 本会话自己的改动——正是用户要撤销的，照常还原；
- 外部（其他会话/用户）叠加的改动——必须保留，跳过并告知用户。

不能用快照时记的指纹来判定：那个指纹只在拍快照那一刻算过一次，本会话其后的写入
不会刷新它，拿它比对会把本会话的正常改动误判成外部改动，让回滚永远拒绝执行。
"""
from __future__ import annotations

import threading
from typing import Dict, Optional, Tuple


class FileVersionRegistry:
    def __init__(self) -> None:
        # key: (session_id, display_path) -> fingerprint
        self._versions: Dict[Tuple[str, str], str] = {}
        self._lock = threading.RLock()

    def record(self, session_id: str, display_path: str, fingerprint: Optional[str]) -> None:
        """本会话读取或写入后刷新自己看到的版本。"""
        if fingerprint is None:
            return
        with self._lock:
            self._versions[(session_id, display_path)] = fingerprint

    def get(self, session_id: str, display_path: str) -> Optional[str]:
        with self._lock:
            return self._versions.get((session_id, display_path))

    def record_external(self, display_path: str, fingerprint: str) -> None:
        """外部写入（如回滚）：所有会话手上的版本都过期了，统一更新。"""
        with self._lock:
            for key in list(self._versions):
                if key[1] == display_path:
                    self._versions[key] = fingerprint

    def forget(self, session_id: str, display_path: str) -> None:
        with self._lock:
            self._versions.pop((session_id, display_path), None)
