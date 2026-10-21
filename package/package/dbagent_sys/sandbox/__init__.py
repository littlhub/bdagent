"""dbagent_sys.sandbox 兼容层 —— 转发到扁平化后的 tools.sandbox。

原始沙箱代码里写的是 `from dbagent_sys.sandbox.persistent_shell import ...`，
但这些模块实际已移到 tools/sandbox/ 下，这里做一层转发。
"""

from __future__ import annotations

import os
import sys

# 确保 package 根在 sys.path 上，才能导入 tools.sandbox.*
_package_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _package_dir not in sys.path:
    sys.path.insert(0, _package_dir)

from tools.sandbox.persistent_shell import PersistentShell  # noqa: E402,F401
from tools.sandbox.terminal_sanitizer import TerminalSanitizer  # noqa: E402,F401
from tools.sandbox.shell_timeout_policy import ShellTimeoutPolicy  # noqa: E402,F401
from tools.sandbox.seccomp_fallback_policy import SeccompFallbackPolicy  # noqa: E402,F401
from tools.sandbox.pty_bridge import PtyBridge, PtyBridgeError  # noqa: E402,F401

__all__ = [
    "PersistentShell",
    "TerminalSanitizer",
    "ShellTimeoutPolicy",
    "SeccompFallbackPolicy",
    "PtyBridge",
    "PtyBridgeError",
]
