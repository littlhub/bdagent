"""Coding agent tools: bash, read, write, edit, find, grep, ls.

已从 pi 项目移植进 dbagent：类型定义在本包 tools/types.py，
截断工具在 runtime/core/truncate.py，不再依赖外部 pi 包。
"""

from __future__ import annotations

from .bash import create_bash_tool
from .edit import create_edit_tool
from .find import create_find_tool
from .grep import create_grep_tool
from .ls import create_ls_tool
from .read import create_read_tool
from .types import (
    AgentTool,
    AgentToolResult,
    AgentToolUpdateCallback,
    ImageContent,
    TextContent,
)
from .write import create_write_tool


def create_coding_tools(cwd: str) -> list[AgentTool]:
    """创建标准读写工具集（read, bash, edit, write）。"""
    return [
        create_read_tool(cwd),
        create_bash_tool(cwd),
        create_edit_tool(cwd),
        create_write_tool(cwd),
    ]


def create_read_only_tools(cwd: str) -> list[AgentTool]:
    """创建只读探索工具集（read, grep, find, ls）。"""
    return [
        create_read_tool(cwd),
        create_grep_tool(cwd),
        create_find_tool(cwd),
        create_ls_tool(cwd),
    ]


def create_all_tools(cwd: str) -> dict[str, AgentTool]:
    """创建全部工具，以名称为键。"""
    tools = [
        create_read_tool(cwd),
        create_bash_tool(cwd),
        create_edit_tool(cwd),
        create_write_tool(cwd),
        create_grep_tool(cwd),
        create_find_tool(cwd),
        create_ls_tool(cwd),
    ]
    return {t.name: t for t in tools}


__all__ = [
    "AgentTool",
    "AgentToolResult",
    "AgentToolUpdateCallback",
    "ImageContent",
    "TextContent",
    "create_all_tools",
    "create_bash_tool",
    "create_coding_tools",
    "create_edit_tool",
    "create_find_tool",
    "create_grep_tool",
    "create_ls_tool",
    "create_read_only_tools",
    "create_read_tool",
    "create_write_tool",
]
