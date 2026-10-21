"""runtime.core - 自包含的运行时核心模块。

提供文本截断、系统提示构建、配置管理与会话记录，不依赖外部框架。
"""

from .truncate import TruncationResult, truncate_head, truncate_tail, truncate_line
from .prompt import ContextFile, build_system_prompt
from .settings import Settings, load_settings, save_settings
from .session import SessionStore, SessionEntry

__all__ = [
    "TruncationResult",
    "truncate_head",
    "truncate_tail",
    "truncate_line",
    "ContextFile",
    "build_system_prompt",
    "Settings",
    "load_settings",
    "save_settings",
    "SessionStore",
    "SessionEntry",
]
