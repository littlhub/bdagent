"""DBAgent 兼容层 —— 让移植过来的沙箱系统能在本项目里直接运行。

原始沙箱（tools/sandbox/sandbox/）来自 Android 端的 DBAgent 应用，
依赖 `dbagent_sys.core.context` 与 `dbagent_sys.core.logging`。这里用最小
实现补齐它实际用到的接口，避免为了几个符号去搬整套框架。

用到的接口只有：
    app_context().data_dir           沙箱状态文件目录（guard 事件、密码闸门）
    app_context().external_files_dir 外部文件根目录（session 路径解析）
    get_logger(name)                 标准 logging 包装
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

__all__ = ["app_context", "get_logger", "AppContext"]


class AppContext:
    """本项目下的"应用上下文"。

    目录布局：
        <data_dir>/sandbox/       沙箱运行期状态（guard 事件、白名单）
        <external_files_dir>/     外部文件根（session 工作目录映射到这里）
    """

    def __init__(self, data_dir: str | Path | None = None,
                 external_files_dir: str | Path | None = None):
        package_dir = Path(__file__).resolve().parent.parent.parent
        self._data_dir = Path(data_dir) if data_dir else (package_dir / "data")
        self._external = (Path(external_files_dir) if external_files_dir
                          else (package_dir.parent / "workspace" / "data" / "work"))

    @property
    def data_dir(self) -> Path:
        path = self._data_dir / "sandbox"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def external_files_dir(self) -> Path:
        self._external.mkdir(parents=True, exist_ok=True)
        return self._external


_context: AppContext | None = None


def app_context() -> AppContext:
    """返回全局上下文；可用 DSH_SANDBOX_DATA_DIR / DSH_SANDBOX_WORK_DIR 覆盖。"""
    global _context
    if _context is None:
        _context = AppContext(
            data_dir=os.environ.get("DSH_SANDBOX_DATA_DIR") or None,
            external_files_dir=os.environ.get("DSH_SANDBOX_WORK_DIR") or None,
        )
    return _context


def set_app_context(ctx: AppContext) -> None:
    """替换全局上下文（测试或自定义布局时用）。"""
    global _context
    _context = ctx


_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str) -> logging.Logger:
    """统一 logger，首次调用时按需配置一次。"""
    if name in _loggers:
        return _loggers[name]
    logger = logging.getLogger(name)
    if not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(os.environ.get("DSH_LOG_LEVEL", "WARNING").upper())
    _loggers[name] = logger
    return logger
