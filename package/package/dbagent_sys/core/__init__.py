"""dbagent_sys.core.logging 兼容实现 —— 复用 context 里的 get_logger。"""

from __future__ import annotations

from .context import get_logger

__all__ = ["get_logger"]
