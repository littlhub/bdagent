"""内核端到端验证：多轮工具调用、授权拒绝、PLAN 拦截、上下文压缩、检查点回滚。

运行：python3 -m codecore.demo
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional, Sequence

from .checkpoint import CheckpointManager, fingerprint_file
from .context import ContextCompactor, ModelMetadata
from .messages import AgentContext, AgentMode, ToolCall, UserMessage
from .permission import PermissionRule, PermissionDecision, ToolPermissionPolicyEngine
from .versions import FileVersionRegistry
from .workflow import (
    AIResponse,
    Final,
    StatefulAgentWorkflow,
    TextDelta,
    error,
    success,
)


# ── 假 provider：按脚本依次返回预设响应 ────────────────────────────────────

@dataclass
class ScriptedProvider:
    provider_id: str = "demo"
    model: str = "demo-model"
    max_output_tokens: Optional[int] = 4096
    script: List[AIResponse] = field(default_factory=list)
    calls: List[List[str]] = field(default_factory=list)

    async def complete_stream(self, system_prompt, messages, tools, reasoning_effort=None) -> AsyncIterator:
        self.calls.append([type(m).__name__ for m in messages])
        response = self.script.pop(0) if self.script else AIResponse(content="done")
        if response.content:
            yield TextDelta(response.content)
        yield Final(response)


# ── 假工具 ────────────────────────────────────────────────────────────────

@dataclass
class EchoTool:
    name: str = "echo"
    description: str = "回显参数"
    capabilities: frozenset = frozenset()

    def to_json_schema(self) -> dict:
        return {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def execute(self, args: dict) -> dict:
        return success(f"echo:{args.get('text')}")


@dataclass
class BashTool:
    name: str = "Bash"
    description: str = "执行 shell 命令"
    capabilities: frozenset = frozenset({"EXECUTE_COMMANDS"})
    executed: List[str] = field(default_factory=list)

    def to_json_schema(self) -> dict:
        return {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}

    def execute(self, args: dict) -> dict:
        command = args.get("command", "")
        if command == "fail":
            return error("boom", "BOOM")
        self.executed.append(command)
        return success(f"ran:{command}")


async def collect(workflow: StatefulAgentWorkflow, request: str, context: AgentContext) -> List[dict]:
    return [event async for event in workflow.run(request, context)]


def make_compactor() -> ContextCompactor:
    return ContextCompactor(
        summarize=lambda messages: "SUMMARY: 已完成前置步骤",
        threshold_percent=lambda: 80,
    )


# ── 场景 ──────────────────────────────────────────────────────────────────

async def scenario_multi_turn() -> None:
    """模型先调工具，拿到结果后给出最终答复。"""
    provider = ScriptedProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall("c1", "echo", {"text": "hi"})]),
        AIResponse(content="已完成", input_tokens=100),
    ])
    bash = BashTool()
    workflow = StatefulAgentWorkflow(
        provider=provider,
        tools=[EchoTool(), bash],
        policy_engine=ToolPermissionPolicyEngine(),
        compactor=make_compactor(),
        permission_resolver=lambda call, ev: asyncio.sleep(0, result=True),
    )
    context = AgentContext(project_root="/tmp", mode=AgentMode.AUTO)
    events = await collect(workflow, "测试", context)

    types = [e["type"] for e in events]
    assert "tool_call_started" in types, types
    assert "tool_call_finished" in types, types
    assert types[-1] == "completed", types
    finished = [e for e in events if e["type"] == "tool_call_finished"][0]
    assert "echo:hi" in finished["result"], finished
    assert len(provider.calls) == 2, provider.calls
    print("[ok] 多轮工具调用")


async def scenario_user_reject_cancels_batch() -> None:
    """用户拒绝批次中任意一个调用 → 整批取消，按原始顺序补齐所有 tool 响应。"""
    provider = ScriptedProvider(script=[
        AIResponse(content="", tool_calls=[
            ToolCall("a", "echo", {"text": "1"}),
            ToolCall("b", "echo", {"text": "2"}),
        ]),
    ])
    workflow = StatefulAgentWorkflow(
        provider=provider,
        tools=[EchoTool()],
        policy_engine=ToolPermissionPolicyEngine(),
        compactor=make_compactor(),
        permission_resolver=lambda call, ev: asyncio.sleep(0, result=False),
    )
    context = AgentContext(project_root="/tmp", mode=AgentMode.BUILD)
    events = await collect(workflow, "测试", context)
    types = [e["type"] for e in events]
    assert types[-1] == "completed", types
    # 第一个被拒即整批取消，第二个不会执行，但两条 tool 消息都要补齐
    finished = [e for e in events if e["type"] == "tool_call_finished"]
    assert len(finished) == 1, finished
    assert "USER_REJECTED" in finished[0]["result"], finished
    print("[ok] 用户拒绝导致整批取消")


async def scenario_plan_mode_deny() -> None:
    """PLAN 模式下写操作被策略拒绝，但仍补齐 tool 响应并继续。"""
    bash = BashTool()
    provider = ScriptedProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall("c1", "Bash", {"command": "rm -rf build"})]),
        AIResponse(content="计划完成", input_tokens=50),
    ])
    workflow = StatefulAgentWorkflow(
        provider=provider,
        tools=[bash],
        policy_engine=ToolPermissionPolicyEngine(),
        compactor=make_compactor(),
        permission_resolver=lambda call, ev: asyncio.sleep(0, result=True),
    )
    context = AgentContext(project_root="/tmp", mode=AgentMode.PLAN)
    events = await collect(workflow, "清理构建产物", context)
    finished = [e for e in events if e["type"] == "tool_call_finished"][0]
    assert "PLAN" in finished["result"], finished
    assert bash.executed == [], bash.executed
    assert events[-1]["type"] == "completed"
    print("[ok] PLAN 模式拦截写操作")


async def scenario_catastrophic_rm() -> None:
    """AUTO 模式下灾难性 rm 仍被硬拒绝。"""
    bash = BashTool()
    provider = ScriptedProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall("c1", "Bash", {"command": "rm -rf /"})]),
        AIResponse(content="放弃", input_tokens=20),
    ])
    workflow = StatefulAgentWorkflow(
        provider=provider,
        tools=[bash],
        policy_engine=ToolPermissionPolicyEngine(),
        compactor=make_compactor(),
    )
    context = AgentContext(project_root="/tmp", mode=AgentMode.AUTO)
    events = await collect(workflow, "清理", context)
    finished = [e for e in events if e["type"] == "tool_call_finished"][0]
    assert "根目录删除" in finished["result"], finished
    assert bash.executed == []
    print("[ok] AUTO 模式下灾难性 rm 拦截")


async def scenario_readonly_allow() -> None:
    """内置白名单内的只读命令直接放行，不弹窗。"""
    bash = BashTool()
    provider = ScriptedProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall("c1", "Bash", {"command": "ls -la && git status -s"})]),
        AIResponse(content="看完了", input_tokens=30),
    ])
    asked: List[str] = []

    async def resolver(call, ev):
        asked.append(call.id)
        return True

    workflow = StatefulAgentWorkflow(
        provider=provider,
        tools=[bash],
        policy_engine=ToolPermissionPolicyEngine(),
        compactor=make_compactor(),
        permission_resolver=resolver,
    )
    context = AgentContext(project_root="/tmp", mode=AgentMode.BUILD)
    events = await collect(workflow, "看下目录", context)
    assert asked == [], asked
    assert bash.executed == ["ls -la && git status -s"], bash.executed
    print("[ok] 内置白名单只读命令自动放行")


async def scenario_unanalyzable_asks() -> None:
    """含命令替换的命令不可静态判定，必须弹窗且不可记忆。"""
    bash = BashTool()
    provider = ScriptedProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall("c1", "Bash", {"command": "cat $(ls)"})]),
        AIResponse(content="ok", input_tokens=10),
    ])
    asked: List[str] = []

    async def resolver(call, ev):
        asked.append(call.id)
        return True

    workflow = StatefulAgentWorkflow(
        provider=provider,
        tools=[bash],
        policy_engine=ToolPermissionPolicyEngine(),
        compactor=make_compactor(),
        permission_resolver=resolver,
    )
    context = AgentContext(project_root="/tmp", mode=AgentMode.BUILD)
    await collect(workflow, "看", context)
    assert asked == ["c1"], asked
    print("[ok] 不可静态判定的命令触发弹窗")


async def scenario_memory_rule_allow() -> None:
    """已记忆的 ALLOW 规则命中后直接放行。"""
    bash = BashTool()
    provider = ScriptedProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall("c1", "Bash", {"command": "npm run build"})]),
        AIResponse(content="ok", input_tokens=10),
    ])
    asked: List[str] = []

    async def resolver(call, ev):
        asked.append(call.id)
        return True

    engine = ToolPermissionPolicyEngine(rules=[
        PermissionRule("Bash", "npm run", PermissionDecision.ALLOW),
    ])
    workflow = StatefulAgentWorkflow(
        provider=provider, tools=[bash], policy_engine=engine,
        compactor=make_compactor(), permission_resolver=resolver,
    )
    await collect(workflow, "构建", AgentContext(project_root="/tmp"))
    assert asked == [], asked
    assert bash.executed == ["npm run build"], bash.executed
    print("[ok] 已记忆规则自动放行")


async def scenario_truncated_continuation() -> None:
    """输出被截断时自动续写。"""
    provider = ScriptedProvider(script=[
        AIResponse(content="前半段", stop_reason="max_tokens"),
        AIResponse(content="后半段", input_tokens=10),
    ])
    workflow = StatefulAgentWorkflow(
        provider=provider, tools=[EchoTool()],
        policy_engine=ToolPermissionPolicyEngine(), compactor=make_compactor(),
    )
    events = await collect(workflow, "写长文", AgentContext(project_root="/tmp"))
    assert len(provider.calls) == 2, provider.calls
    assert events[-1]["type"] == "completed"
    print("[ok] 输出截断自动续写")


async def scenario_abort_reports_error() -> None:
    """拒答类中止把原因作为错误上报，而不是静默结束。"""
    provider = ScriptedProvider(script=[
        AIResponse(content="", stop_reason="refusal", stop_detail="无法协助该请求"),
    ])
    workflow = StatefulAgentWorkflow(
        provider=provider, tools=[EchoTool()],
        policy_engine=ToolPermissionPolicyEngine(), compactor=make_compactor(),
    )
    events = await collect(workflow, "x", AgentContext(project_root="/tmp"))
    failed = [e for e in events if e["type"] == "failed"]
    assert failed and "无法协助" in failed[0]["error"], events
    print("[ok] 拒答原因上报")


async def scenario_compaction() -> None:
    """历史超阈值时压缩，并把「标记 + 摘要」放在最近上下文之前。"""
    provider = ScriptedProvider(script=[AIResponse(content="好", input_tokens=10)])
    metadata = ModelMetadata(context_tokens=8_000, output_tokens=1_000)
    history = [UserMessage(content=f"第 {i} 轮：" + "内容" * 200) for i in range(20)]
    workflow = StatefulAgentWorkflow(
        provider=provider, tools=[EchoTool()],
        policy_engine=ToolPermissionPolicyEngine(),
        compactor=make_compactor(),
        metadata=metadata,
    )
    context = AgentContext(project_root="/tmp", history=history)
    events = await collect(workflow, "继续", context)
    types = [e["type"] for e in events]
    assert "context_usage" in types, types
    print("[ok] 上下文压缩触发")


async def scenario_checkpoint_restore() -> None:
    """修改前快照，回滚恢复原内容；被外部改过的文件跳过。"""
    with tempfile.TemporaryDirectory() as tmp:
        registry = FileVersionRegistry()
        manager = CheckpointManager(os.path.join(tmp, "checkpoints"), registry)
        target = os.path.join(tmp, "a.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("original")

        checkpoint_id = manager.create_checkpoint("s1", "m1", "改文件")
        manager.before_file_modified("s1", target)
        # 本会话自己写入，写入路径会刷新版本记录
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("modified by agent")
        registry.record("s1", target, fingerprint_file(target))

        result = manager.restore("s1", checkpoint_id)
        assert result.restored == 1, result
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "original"

        # 快照后被外部改动（本会话版本记录未刷新）→ 跳过而不是覆盖
        checkpoint_id2 = manager.create_checkpoint("s1", "m2", "再改")
        manager.before_file_modified("s1", target)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("changed by someone else")
        result2 = manager.restore("s1", checkpoint_id2)
        assert result2.skipped == [target], result2
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "changed by someone else"
    print("[ok] 检查点快照与回滚")


async def main() -> None:
    await scenario_multi_turn()
    await scenario_user_reject_cancels_batch()
    await scenario_plan_mode_deny()
    await scenario_catastrophic_rm()
    await scenario_readonly_allow()
    await scenario_unanalyzable_asks()
    await scenario_memory_rule_allow()
    await scenario_truncated_continuation()
    await scenario_abort_reports_error()
    await scenario_compaction()
    await scenario_checkpoint_restore()
    print("\n全部场景通过")


if __name__ == "__main__":
    asyncio.run(main())
