"""工具类型定义 —— dbagent 自有，不依赖 pi 项目。

原先 tools/ 下的工具从 pi 导入这些类型，现已改为本地定义：
  原 pi.agent.types.AgentTool              -> 本文件 AgentTool
  原 pi.agent.types.AgentToolResult        -> 本文件 AgentToolResult
  原 pi.agent.types.AgentToolUpdateCallback-> 本文件 AgentToolUpdateCallback
  原 pi.ai.types.TextContent               -> 本文件 TextContent
  原 pi.ai.types.ImageContent              -> 本文件 ImageContent
"""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TextContent:
    """文本内容块。"""

    text: str
    type: str = "text"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}


@dataclass
class ImageContent:
    """图片内容块（base64 编码）。"""

    data: str
    mime_type: str = "image/png"
    type: str = "image"

    @classmethod
    def from_file(cls, path: str | Path) -> "ImageContent":
        p = Path(path)
        raw = p.read_bytes()
        mime = mimetypes.guess_type(str(p))[0] or "image/png"
        return cls(data=base64.b64encode(raw).decode("ascii"), mime_type=mime)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data, "mimeType": self.mime_type}


@dataclass
class AgentToolResult:
    """工具执行结果。"""

    content: list[Any] = field(default_factory=list)
    details: Any = None

    def text(self) -> str:
        """把结果里的文本块拼成一个字符串。"""
        parts = [c.text for c in self.content if isinstance(c, TextContent)]
        return "\n".join(parts)


AgentToolUpdateCallback = Callable[[AgentToolResult], None]


@dataclass
class AgentTool:
    """带 async 执行能力的工具定义。"""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    label: str = ""
    execute: Callable[..., Awaitable[AgentToolResult]] | None = None


__all__ = [
    "TextContent",
    "ImageContent",
    "AgentTool",
    "AgentToolResult",
    "AgentToolUpdateCallback",
]
