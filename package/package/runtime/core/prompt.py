"""系统提示构建 - 根据可用工具生成提示词，并附加项目上下文。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

# 工具说明，用于系统提示
TOOL_DESCRIPTIONS: dict[str, str] = {
    "read": "读取文件内容，可按行范围查看",
    "bash": "在沙箱内执行 shell 命令",
    "edit": "精确编辑文件（替换指定文本、按行插入或删除）",
    "write": "创建或覆盖文件",
    "grep": "按正则搜索文件内容",
    "find": "按名称或后缀查找文件",
    "ls": "列出目录内容",
}

DEFAULT_TOOLS = ["read", "bash", "edit", "write"]


@dataclass
class ContextFile:
    """要并入系统提示的项目上下文文件（如 AGENTS.md）。"""

    path: str
    content: str


def build_system_prompt(
    *,
    selected_tools: list[str] | None = None,
    custom_prompt: str | None = None,
    append_system_prompt: str | None = None,
    cwd: str | None = None,
    context_files: list[ContextFile] | None = None,
) -> str:
    """构建系统提示。

    Args:
        selected_tools: 要声明的工具，默认 read/bash/edit/write。
        custom_prompt: 自定义提示，替换默认内容。
        append_system_prompt: 追加到提示末尾的文本。
        cwd: 工作目录，默认当前目录。
        context_files: 预先读取的项目上下文文件。

    Returns:
        完整的系统提示字符串。
    """
    resolved_cwd = cwd or os.getcwd()
    date_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    tools = [t for t in (DEFAULT_TOOLS if selected_tools is None else selected_tools)
             if t in TOOL_DESCRIPTIONS]
    tools_list = "\n".join(f"- {t}: {TOOL_DESCRIPTIONS[t]}" for t in tools) or "（无）"
    guidelines = _build_guidelines(tools)

    if custom_prompt:
        prompt = custom_prompt.strip()
    else:
        prompt = (
            "你是一个在受限沙箱中工作的开发助手，可以读取文件、执行命令、编辑代码和创建文件。\n\n"
            f"可用工具：\n{tools_list}\n\n"
            f"工作准则：\n{guidelines}"
        )

    if append_system_prompt:
        prompt += f"\n\n{append_system_prompt}"

    if context_files:
        prompt += "\n\n# 项目上下文\n\n"
        for cf in context_files:
            prompt += f"## {cf.path}\n\n{cf.content}\n\n"

    prompt += f"\n当前时间：{date_time}"
    prompt += f"\n工作目录：{resolved_cwd}"
    return prompt


def _build_guidelines(tools: list[str]) -> str:
    """按可用工具生成行为准则。"""
    guidelines: list[str] = []
    has = set(tools)

    if "bash" in has and not has & {"grep", "find", "ls"}:
        guidelines.append("文件浏览用 bash（ls、find、grep）")
    elif "bash" in has and has & {"grep", "find", "ls"}:
        guidelines.append("优先用 grep/find/ls 浏览文件，比 bash 更快也更可控")

    if "read" in has and "edit" in has:
        guidelines.append("修改前先用 read 查看文件内容，不要用 cat 或 sed 代替")

    if "edit" in has:
        guidelines.append("用 edit 做精确修改，旧文本必须与文件内容逐字一致")

    if "write" in has:
        guidelines.append("write 只用于新建文件或整体重写")

    if has & {"edit", "write"}:
        guidelines.append("总结改动时直接输出文字，不要用 cat 或 bash 展示")

    guidelines.append("回答保持简洁")
    guidelines.append("涉及文件时明确写出路径")

    return "\n".join(f"- {g}" for g in guidelines)
