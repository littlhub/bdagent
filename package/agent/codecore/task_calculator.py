"""用内核端到端完成一个真实任务：编写计算器 HTML。

跑法：python3 -m codecore.task_calculator
需要本地 free-model 网关（http://127.0.0.1:18790/v1）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from .context import ContextCompactor, ModelMetadata
from .file_access import LocalFileAccess
from .messages import AgentContext, AgentMode
from .paths import WorkspacePathMapper
from .permission import PermissionScope, ToolPermissionPolicyEngine, Verdict
from .prompt import PromptFragmentCatalog, SystemPromptProvider
from .providers import OpenAIProvider
from .tools import default_tools
from .workflow import StatefulAgentWorkflow

WORKSPACE = Path("/root/workspace")
AICODE_DIR = os.path.expanduser("~/.aicode")
BUILTIN_PROMPTS = str(Path(__file__).resolve().parent.parent / "prompts")

REQUEST = """在当前工作区创建计算器网页 calc.html。

要求：
1. 单文件，HTML + CSS + JS 内联，不依赖任何外部库或 CDN。
2. 支持四则运算、小数点、正负号、百分号、清除（AC）与退格。
3. 支持键盘输入：数字、+ - * /、Enter 计算、Backspace 退格、Escape 清除。
4. 界面上方显示当前表达式，下方显示结果，除零要给出提示而不是崩溃。
5. 视觉上要清爽：深色背景、圆角按钮、按压缩放反馈、等宽数字字体。
6. 写完后用 node 校验一次页面里的 JS 语法，确认没有语法错误。

写入路径请直接用 calc.html（相对工作区根目录），不要加子目录，也不要用绝对路径。
完成后简要说明你做了什么。"""


async def ask_permission(tool_call, eval_result):
    """非交互授权：只读放行，写操作与命令一律批准（演示用）。"""
    print(f"  [授权] {tool_call.name} -> {eval_result.verdict.value}")
    return True


async def main() -> int:
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    provider = OpenAIProvider(
        base_url="http://127.0.0.1:18790/v1",
        api_key="sk-free",
        model=os.environ.get("MODEL", "deepseek-v4.1-flash-free"),
    )

    catalog = PromptFragmentCatalog(AICODE_DIR, BUILTIN_PROMPTS)
    prompt_provider = SystemPromptProvider(
        catalog=catalog,
        aicode_dir=AICODE_DIR,
        environment="本地容器（PRoot）",
    )

    policy = ToolPermissionPolicyEngine()
    access = LocalFileAccess(WorkspacePathMapper(
        workspace_root=str(WORKSPACE),
        rootfs_root="/",
        aicode_dir=AICODE_DIR,
        home="/root",
    ))
    workflow = StatefulAgentWorkflow(
        provider=provider,
        tools=default_tools(access),
        policy_engine=policy,
        compactor=ContextCompactor(
            summarize=lambda messages: "（本任务无需压缩）",
            threshold_percent=lambda: 85,
        ),
        metadata=ModelMetadata(context_tokens=128_000, output_tokens=8_192),
        permission_resolver=ask_permission,
        system_prompt_provider=prompt_provider,
    )

    context = AgentContext(
        project_root=str(WORKSPACE),
        session_id="calculator-demo",
        mode=AgentMode.BUILD,
    )

    # 先看一眼系统提示词里是否真的带上了项目规则
    system_prompt = prompt_provider.build(context)
    has_rules = "CLAUDE.md" in system_prompt or "基本约定" in system_prompt
    print(f"系统提示词 {len(system_prompt)} 字符，含项目规则: {has_rules}")
    if os.environ.get("DUMP_PROMPT"):
        print(system_prompt)

    tool_calls = 0
    async for event in workflow.run(REQUEST, context):
        kind = event["type"]
        if kind == "tool_call_started":
            tool_calls += 1
            print(f"[工具] {event['tool_name']} {event['args_preview'][:120]}")
        elif kind == "tool_call_finished":
            status = "失败" if event["is_error"] else "成功"
            print(f"[工具] {event['tool_name']} {status}")
        elif kind == "assistant_text":
            text = (event.get("content") or "").strip()
            if text:
                print(f"\n[模型] {text[:2000]}\n")
        elif kind == "failed":
            print(f"[失败] {event.get('error')}")
        elif kind == "completed":
            print("[完成]")

    target = WORKSPACE / "calc.html"
    print(f"\n工具调用 {tool_calls} 次")
    if target.is_file():
        size = target.stat().st_size
        print(f"产物：{target}（{size} 字节）")
        return 0
    print("产物未生成")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
