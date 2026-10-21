"""dbagent_sys.core.logging 兼容实现。

原始是 Android 的 `Log` 静态类（d/i/w/e）+ `get_logger`，
这里用标准 logging 对齐：
    Log.d -> debug   Log.i -> info   Log.w -> warning   Log.e -> error
"""

from __future__ import annotations

import logging

from .context import get_logger

__all__ = ["get_logger", "Log"]


class Log:
    """Android 风格的静态日志接口。"""

    _loggers: dict[str, logging.Logger] = {}

    @classmethod
    def _of(cls, tag: str) -> logging.Logger:
        if tag not in cls._loggers:
            cls._loggers[tag] = get_logger(tag)
        return cls._loggers[tag]

    @classmethod
    def d(cls, tag: str, message: str, *args) -> None:
        cls._of(tag).debug(_render(message, args))

    @classmethod
    def i(cls, tag: str, message: str, *args) -> None:
        cls._of(tag).info(_render(message, args))

    @classmethod
    def w(cls, tag: str, message: str, *args) -> None:
        cls._of(tag).warning(_render(message, args))

    @classmethod
    def e(cls, tag: str, message: str, *args) -> None:
        cls._of(tag).error(_render(message, args))


def _render(message: str, args) -> str:
    """Android 的 Log 支持 %s 占位；末尾若是异常对象则附上。"""
    text = message
    values = list(args)
    if values and isinstance(values[-1], BaseException):
        exc = values.pop()
        text = f"{text} | {type(exc).__name__}: {exc}"
    if values:
        try:
            text = text % tuple(values)
        except (TypeError, ValueError):
            text = f"{text} {' '.join(str(v) for v in values)}"
    return text
