"""沙箱路径工具 —— 工作区根、只读根、机器路径清洗。

原始实现属于 DBAgent 桌面端，这里按本项目布局重写，
只保留 guard.py 实际调用的三个函数。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ...dbagent_sys.core.context import app_context

__all__ = ["workspace_root", "readonly_roots", "scrub_machine_paths"]

# 只读目录：沙箱内不允许写入的位置
_READONLY_NAMES = {"readonly", "read_only", "ro"}


def workspace_root() -> Path:
    """可写工作区根目录。"""
    return app_context().external_files_dir


def readonly_roots() -> list[Path]:
    """只读根目录列表；当前项目未设置只读挂载时返回空。"""
    base = app_context().external_files_dir
    roots: list[Path] = []
    if base.is_dir():
        for name in os.listdir(base):
            path = base / name
            if path.is_dir() and name.lower() in _READONLY_NAMES:
                roots.append(path)
    return roots


# 本机绝对路径（Windows 盘符 / Unix 家目录）在对外文本里要抹掉
_WIN_ABS = re.compile(r"\b[A-Za-z]:\\[^\s\"'<>|]*")
_POSIX_HOME = re.compile(r"/(?:home|Users)/[^/\s\"'<>|]+/[^\s\"'<>|]*")


def scrub_machine_paths(text: str) -> str:
    """把文本里的本机绝对路径替换成占位符，避免泄露主机目录结构。"""
    if not text:
        return text
    root = str(workspace_root())
    out = text.replace(root, "<workdir>")
    out = _WIN_ABS.sub("<path>", out)
    out = _POSIX_HOME.sub("<path>", out)
    return out
