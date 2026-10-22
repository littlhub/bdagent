"""MVI 状态机工作流，对应 domain/workflow/StatefulAgentWorkflow.kt。

状态（AgentSessionState）不可变，动作（AgentAction）驱动纯函数 reduce 产出
副作用（AgentSideEffect），由 run() 的主循环执行副作用并继续投递动作。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Protocol, Sequence

from .context import (
    ContextCompactor,
    ModelMetadata,
    calibrated,
    effective_input_budget,
    estimate_request,
    output_reserve_tokens,
)
from .messages import (
    AgentContext,
    AgentImage,
    AgentMessage,
    AgentMode,
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from .notification_format import build_message as build_notification_message
from .notification_format import inject as inject_notifications
from .notifications import AgentNotificationKind
from .permission import EvalResult, ToolPermissionPolicyEngine, Verdict

USER_REJECTED_CODE = "USER_REJECTED"
USER_REJECTED_REASON = "用户拒绝执行该工具"
USER_REJECTED_BATCH_REASON = "用户拒绝了本轮工具调用，该调用未执行。"
TRUNCATION_CONTINUATION = "Your response was truncated. Continue from where it stopped."


# ── 结果与事件 ──────────────────────────────────────────────────────────────

class ToolResultStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"


def success(data: Any) -> dict:
    return {"status": ToolResultStatus.SUCCESS.value, "data": data}


def error(message: str, code: str = "UNKNOWN") -> dict:
    return {"status": ToolResultStatus.ERROR.value, "message": message, "code": code}


def is_error_result(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == ToolResultStatus.ERROR.value


def result_to_transport_string(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


@dataclass
class AIResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: Optional[str] = None
    stop_detail: Optional[str] = None
    reasoning: Optional[str] = None
    signature: Optional[str] = None
    thinking_blocks_json: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    images: List[AgentImage] = field(default_factory=list)

    @property
    def is_truncated(self) -> bool:
        return self.stop_reason in TRUNCATION_STOP_REASONS

    @property
    def is_aborted(self) -> bool:
        return self.stop_reason in ABORT_STOP_REASONS


TRUNCATION_STOP_REASONS = {"max_tokens", "length", "MAX_TOKENS"}
ABORT_STOP_REASONS = {
    "refusal", "model_context_window_exceeded",
    "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII",
    "failed", "budget_exceeded",
}


@dataclass
class TextDelta:
    text: str


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class ToolCallDeclared:
    name: str


@dataclass
class Retrying:
    attempt: int
    max_retries: int
    error: str


@dataclass
class KeySwitched:
    new_index: int
    total: int


@dataclass
class Final:
    response: AIResponse


AIStreamChunk = TextDelta | ReasoningDelta | ToolCallDeclared | Retrying | KeySwitched | Final


# ── 工具接口 ────────────────────────────────────────────────────────────────

class AgentTool(Protocol):
    name: str
    description: str
    capabilities: frozenset

    def to_json_schema(self) -> dict: ...
    def execute(self, args: dict) -> dict: ...


class AIProvider(Protocol):
    provider_id: str
    model: str
    max_output_tokens: Optional[int]

    def complete_stream(
        self,
        system_prompt: str,
        messages: Sequence[AgentMessage],
        tools: Sequence[AgentTool],
        reasoning_effort: Optional[str] = None,
    ) -> AsyncIterator[AIStreamChunk]: ...


# ── 状态 / 动作 / 副作用 ────────────────────────────────────────────────────

@dataclass
class ToolBatchResult:
    id: str
    tool_name: str
    result: str
    is_error: bool
    attachments: List[Any] = field(default_factory=list)
    images: List[AgentImage] = field(default_factory=list)


@dataclass
class AgentSessionState:
    messages: List[AgentMessage] = field(default_factory=list)
    iterations: int = 0
    is_finished: bool = False
    error: Optional[str] = None
    error_code: Optional[str] = None
    batch_tool_calls: List[ToolCall] = field(default_factory=list)
    pending_permission_calls: List[ToolCall] = field(default_factory=list)
    approved_tool_calls: List[ToolCall] = field(default_factory=list)
    rejected_tool_results: Dict[str, ToolBatchResult] = field(default_factory=dict)


@dataclass
class InitRequest:
    initial_messages: List[AgentMessage]


@dataclass
class LlmResponse:
    response: AIResponse
    message_id: str


@dataclass
class LlmError:
    error: str
    reason_code: Optional[str] = None


@dataclass
class PermissionEvaluated:
    tool_call: ToolCall
    approved: bool
    args_preview: str
    deny_reason: str = USER_REJECTED_REASON
    error_code: str = USER_REJECTED_CODE


@dataclass
class ToolBatchFinished:
    results: List[ToolBatchResult]


AgentAction = InitRequest | LlmResponse | LlmError | PermissionEvaluated | ToolBatchFinished


@dataclass
class CallLlm:
    pass


@dataclass
class PersistUser:
    message: UserMessage


@dataclass
class RequestPermission:
    tool_call: ToolCall


@dataclass
class ExecuteToolBatch:
    tool_calls: List[ToolCall]


@dataclass
class CancelToolBatch:
    tool_calls: List[ToolCall]


AgentSideEffect = CallLlm | PersistUser | RequestPermission | ExecuteToolBatch | CancelToolBatch


@dataclass
class ToolRunResult:
    raw: str
    is_error: bool
    attachments: List[Any] = field(default_factory=list)
    images: List[AgentImage] = field(default_factory=list)


# ── reduce ─────────────────────────────────────────────────────────────────

def reduce_state(state: AgentSessionState, action: AgentAction) -> tuple[AgentSessionState, List[AgentSideEffect]]:
    """纯函数：(State, Action) -> (State', [SideEffect])。"""
    effects: List[AgentSideEffect] = []

    if isinstance(action, InitRequest):
        new_state = replace(state, messages=list(action.initial_messages))
        effects.append(CallLlm())
        return new_state, effects

    if isinstance(action, LlmResponse):
        response = action.response
        assistant = AssistantMessage(
            id=action.message_id,
            content=response.content,
            tool_calls=list(response.tool_calls),
            reasoning=response.reasoning or "",
            signature=response.signature or "",
            thinking_blocks_json=response.thinking_blocks_json or "",
            images=list(response.images),
            input_tokens=response.input_tokens,
        )
        new_state = replace(
            state,
            messages=state.messages + [assistant],
            iterations=state.iterations + 1,
        )

        if not response.tool_calls:
            if response.is_aborted:
                # 拒答/上下文超限：正文可能为空，静默结束会让用户看到空白气泡
                new_state = replace(
                    new_state,
                    is_finished=True,
                    error=response.stop_detail or "",
                    error_code=response.stop_reason,
                )
            elif response.is_truncated:
                continuation = UserMessage(content=TRUNCATION_CONTINUATION)
                new_state = replace(new_state, messages=new_state.messages + [continuation])
                effects.append(PersistUser(continuation))
                effects.append(CallLlm())
            else:
                new_state = replace(new_state, is_finished=True)
            return new_state, effects

        # 本批多个 tool_call：全部进待权限队列，逐个弹窗；全部批准后才并行执行
        calls = list(response.tool_calls)
        new_state = replace(
            new_state,
            batch_tool_calls=calls,
            pending_permission_calls=calls,
            approved_tool_calls=[],
            rejected_tool_results={},
        )
        effects.append(RequestPermission(calls[0]))
        return new_state, effects

    if isinstance(action, LlmError):
        return replace(state, is_finished=True, error=action.error, error_code=action.reason_code), effects

    if isinstance(action, PermissionEvaluated):
        call = action.tool_call
        if action.approved:
            remaining = [c for c in state.pending_permission_calls if c.id != call.id]
            approved = state.approved_tool_calls + [call]
            new_state = replace(state, pending_permission_calls=remaining, approved_tool_calls=approved)
            if remaining:
                effects.append(RequestPermission(remaining[0]))
            else:
                effects.append(ExecuteToolBatch(approved))
            return new_state, effects

        raw_result = result_to_transport_string(error(action.deny_reason, action.error_code))

        if action.error_code == USER_REJECTED_CODE:
            # 用户拒绝批次中任意一个 → 整批取消，按原始顺序补齐所有 tool 响应，
            # 否则 assistant(toolCalls=N) 后只有部分 tool 消息，OpenAI 会报 400。
            cancelled = [
                ToolResultMessage(
                    id=c.id,
                    tool_name=c.name,
                    result=result_to_transport_string(error(USER_REJECTED_BATCH_REASON, USER_REJECTED_CODE)),
                )
                for c in state.batch_tool_calls
            ]
            new_state = replace(
                state,
                messages=state.messages + cancelled,
                batch_tool_calls=[],
                pending_permission_calls=[],
                approved_tool_calls=[],
                is_finished=True,
            )
            if state.approved_tool_calls:
                effects.append(CancelToolBatch(state.approved_tool_calls))
            return new_state, effects

        # 策略/系统拒绝（如 PLAN 模式）：记录拒绝结果，继续收集后续权限
        remaining = [c for c in state.pending_permission_calls if c.id != call.id]
        rejected = dict(state.rejected_tool_results)
        rejected[call.id] = ToolBatchResult(call.id, call.name, raw_result, True)
        new_state = replace(
            state,
            pending_permission_calls=remaining,
            rejected_tool_results=rejected,
        )
        if remaining:
            effects.append(RequestPermission(remaining[0]))
        else:
            effects.append(ExecuteToolBatch(new_state.approved_tool_calls))
        return new_state, effects

    if isinstance(action, ToolBatchFinished):
        # 按 batch_tool_calls 原始顺序组装：优先策略拒绝结果，其次并行执行结果
        results_by_id = {r.id: r for r in action.results}
        appended: List[AgentMessage] = []
        for call in state.batch_tool_calls:
            batch_result = state.rejected_tool_results.get(call.id) or results_by_id.get(call.id)
            if batch_result is None:
                continue
            appended.append(
                ToolResultMessage(
                    id=batch_result.id,
                    tool_name=batch_result.tool_name,
                    result=batch_result.result,
                    images=batch_result.images,
                )
            )
        new_state = replace(
            state,
            messages=state.messages + appended,
            batch_tool_calls=[],
            pending_permission_calls=[],
            approved_tool_calls=[],
            rejected_tool_results={},
        )
        effects.append(CallLlm())
        return new_state, effects

    return state, effects


# ── 流式累计器 ──────────────────────────────────────────────────────────────

class StreamAccumulator:
    """按时间窗口节流推送增量；上游每个 chunk 携带完整累积文本，逐条推送会让下游频繁重建状态。"""

    DELTA_THROTTLE_MS = 60

    def __init__(self, emit: Callable[[dict], Awaitable[None]], now_ms: Callable[[], int]):
        self._emit = emit
        self._now_ms = now_ms
        self.text = ""
        self.reasoning = ""
        self.final_response: Optional[AIResponse] = None
        self.ttfb_elapsed: Optional[int] = None
        self.retry_attempts = 0
        self._pending_text: Optional[str] = None
        self._pending_reasoning: Optional[str] = None
        self._last_text_sent = 0
        self._last_reasoning_sent = 0
        self._started = now_ms()

    async def on_chunk(self, chunk: AIStreamChunk) -> None:
        if isinstance(chunk, TextDelta):
            self._mark_ttfb()
            self.text += chunk.text
            self._pending_text = self.text
            now = self._now_ms()
            if now - self._last_text_sent >= self.DELTA_THROTTLE_MS:
                await self._flush_text()
                self._last_text_sent = now
        elif isinstance(chunk, ReasoningDelta):
            self._mark_ttfb()
            self.reasoning += chunk.text
            self._pending_reasoning = self.reasoning
            now = self._now_ms()
            if now - self._last_reasoning_sent >= self.DELTA_THROTTLE_MS:
                await self._flush_reasoning()
                self._last_reasoning_sent = now
        elif isinstance(chunk, Retrying):
            self.retry_attempts = max(self.retry_attempts, chunk.attempt)
            self._reset()
            await self._emit({"type": "retrying", "attempt": chunk.attempt,
                              "max_retries": chunk.max_retries, "error": chunk.error})
        elif isinstance(chunk, KeySwitched):
            self._reset()
            await self._emit({"type": "key_switched", "new_index": chunk.new_index, "total": chunk.total})
        elif isinstance(chunk, Final):
            self._mark_ttfb()
            self.final_response = chunk.response
        elif isinstance(chunk, ToolCallDeclared):
            self._mark_ttfb()
            await self._emit({"type": "tool_call_preparing", "name": chunk.name})

    async def flush(self) -> None:
        await self._flush_text()
        await self._flush_reasoning()

    def _mark_ttfb(self) -> None:
        if self.ttfb_elapsed is None:
            self.ttfb_elapsed = self._now_ms() - self._started

    def _reset(self) -> None:
        self.text = ""
        self.reasoning = ""
        self._pending_text = None
        self._pending_reasoning = None
        self._last_text_sent = 0
        self._last_reasoning_sent = 0

    async def _flush_text(self) -> None:
        if self._pending_text is None:
            return
        pending, self._pending_text = self._pending_text, None
        await self._emit({"type": "assistant_delta", "text": pending})

    async def _flush_reasoning(self) -> None:
        if self._pending_reasoning is None:
            return
        pending, self._pending_reasoning = self._pending_reasoning, None
        await self._emit({"type": "reasoning_delta", "text": pending})


# ── 工作流 ─────────────────────────────────────────────────────────────────

PermissionResolver = Callable[[ToolCall, EvalResult], Awaitable[bool]]
"""向用户请求授权：返回 True 表示本次放行。"""

PlanApprovalResolver = Callable[[str], Awaitable[bool]]
"""退出 PLAN 前请求用户审查：返回 True 表示批准切换。"""


class StatefulAgentWorkflow:
    def __init__(
        self,
        provider: AIProvider,
        tools: Sequence[AgentTool],
        policy_engine: ToolPermissionPolicyEngine,
        compactor: ContextCompactor,
        metadata: Optional[ModelMetadata] = None,
        permission_resolver: Optional[PermissionResolver] = None,
        checkpoint_manager: Any = None,
        persist_user: Optional[Callable[[UserMessage], None]] = None,
        now_ms: Optional[Callable[[], int]] = None,
        on_llm_call: Optional[Callable[[dict], None]] = None,
        system_prompt_provider: Any = None,
        notification_center: Any = None,
    ):
        self._provider = provider
        self._tools = {tool.name: tool for tool in tools}
        self._tool_list = list(tools)
        self._policy = policy_engine
        self._compactor = compactor
        self._metadata = metadata or ModelMetadata()
        self._permission_resolver = permission_resolver
        self._checkpoints = checkpoint_manager
        self._persist_user = persist_user
        self._now_ms = now_ms or (lambda: int(asyncio.get_event_loop().time() * 1000))
        self._on_llm_call = on_llm_call
        self._system_prompt_provider = system_prompt_provider
        self._notifications = notification_center

    # ── 主循环 ────────────────────────────────────────────────────────────

    async def run(
        self,
        user_request: str,
        context: AgentContext,
        on_event: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> AsyncIterator[dict]:
        """执行一轮用户请求，逐个产出事件（异步生成器）。"""
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event: dict) -> None:
            await queue.put(event)

        async def drive() -> None:
            try:
                await self._drive(user_request, context, emit)
            except Exception as exc:  # 兜底：未捕获异常也要以事件形式交给调用方
                await emit({"type": "failed", "error": str(exc), "code": None})
            finally:
                await queue.put(None)

        task = asyncio.create_task(drive())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                if on_event:
                    await on_event(event)
                yield event
        finally:
            await task

    async def _drive(
        self,
        user_request: str,
        context: AgentContext,
        send: Callable[[dict], Awaitable[None]],
    ) -> None:
        current_context = context
        state = AgentSessionState()
        tools = self._tool_list

        action_queue: List[AgentAction] = [
            InitRequest(
                list(current_context.history)
                + [UserMessage(id=current_context.input_message_id, content=user_request,
                               images=list(current_context.input_images))]
            )
        ]

        input_budget = effective_input_budget(self._metadata)
        if hasattr(self._provider, "max_output_tokens"):
            self._provider.max_output_tokens = output_reserve_tokens(self._metadata)

        # 系统提示词在每轮开始时构建一次：静态基线在前，运行期内容（含项目规则）集中在末尾。
        system_prompt = (
            self._system_prompt_provider.build(current_context)
            if self._system_prompt_provider is not None else ""
        )

        last_assistant_index = -1
        for index, message in enumerate(current_context.history):
            if isinstance(message, AssistantMessage) and message.input_tokens > 0:
                last_assistant_index = index
        baseline_estimate = estimate_request(
            system_prompt, current_context.history[: max(last_assistant_index, 0)], tools
        )
        baseline_usage = (
            current_context.history[last_assistant_index].input_tokens
            if current_context.last_input_tokens > 0 and last_assistant_index >= 0
            else 0
        )
        overflow_recovery_attempted = False
        compaction_failed = False

        while not state.is_finished and action_queue:
            action = action_queue.pop(0)
            state, effects = reduce_state(state, action)

            for effect in effects:
                if isinstance(effect, PersistUser):
                    if self._persist_user:
                        self._persist_user(effect.message)

                elif isinstance(effect, CallLlm):
                    outcome = await self._call_llm(
                        state, current_context, send, input_budget,
                        baseline_estimate, baseline_usage,
                        overflow_recovery_attempted, compaction_failed,
                        system_prompt,
                    )
                    state = outcome["state"]
                    action_queue.extend(outcome["actions"])
                    baseline_estimate = outcome["baseline_estimate"]
                    baseline_usage = outcome["baseline_usage"]
                    overflow_recovery_attempted = outcome["overflow_recovery_attempted"]
                    compaction_failed = outcome["compaction_failed"]

                elif isinstance(effect, RequestPermission):
                    tool = self._tools.get(effect.tool_call.name)
                    args_preview = json.dumps(effect.tool_call.arguments, ensure_ascii=False)[:500]
                    capabilities = getattr(tool, "capabilities", frozenset()) if tool else frozenset()
                    caps = {c.value if hasattr(c, "value") else str(c) for c in capabilities}
                    eval_result = self._policy.evaluate(
                        effect.tool_call.name, effect.tool_call.arguments,
                        current_context.permission_mode, caps,
                    )

                    approved = eval_result.verdict is Verdict.ALLOW
                    if eval_result.verdict is Verdict.ASK:
                        approved = (
                            await self._permission_resolver(effect.tool_call, eval_result)
                            if self._permission_resolver else False
                        )

                    if approved:
                        await send({
                            "type": "tool_call_started",
                            "id": effect.tool_call.id,
                            "tool_name": effect.tool_call.name,
                            "args_preview": args_preview,
                        })
                    else:
                        # 未批准（策略拒绝或用户拒绝）直接回完成事件，UI 不会停在「执行中」
                        deny_reason = eval_result.deny_reason or USER_REJECTED_REASON
                        deny_code = "PERMISSION_DENIED" if eval_result.verdict is Verdict.DENY else USER_REJECTED_CODE
                        await send({
                            "type": "tool_call_finished",
                            "id": effect.tool_call.id,
                            "tool_name": effect.tool_call.name,
                            "result": result_to_transport_string(error(deny_reason, deny_code)),
                            "is_error": True,
                            "args_preview": args_preview,
                        })

                    action_queue.append(PermissionEvaluated(
                        tool_call=effect.tool_call,
                        approved=approved,
                        args_preview=args_preview,
                        deny_reason=eval_result.deny_reason or USER_REJECTED_REASON,
                        error_code="PERMISSION_DENIED" if eval_result.verdict is Verdict.DENY else USER_REJECTED_CODE,
                    ))

                elif isinstance(effect, CancelToolBatch):
                    for tool_call in effect.tool_calls:
                        await send({
                            "type": "tool_call_finished",
                            "id": tool_call.id,
                            "tool_name": tool_call.name,
                            "result": result_to_transport_string(
                                error(USER_REJECTED_BATCH_REASON, USER_REJECTED_CODE)
                            ),
                            "is_error": True,
                        })

                elif isinstance(effect, ExecuteToolBatch):
                    batch_results, current_context = await self._execute_batch(
                        effect.tool_calls, current_context, send
                    )

                    # 本轮内到达的后台任务/子代理完成通知：搭在本批最后一条工具结果上立即送达，
                    # AI 当轮即可感知，不必等本轮结束再起新一轮。ack 放在完成事件发出之后：
                    # 中途被取消时通知仍留在队列里，由后续批次或本轮结束的兜底路径送达。
                    notify_session = current_context.session_id
                    pending = (
                        self._notifications.peek(notify_session)
                        if self._notifications and notify_session and batch_results else []
                    )
                    if pending:
                        current_context = self._apply_mode_change(current_context, pending)
                        last = batch_results[-1]
                        batch_results[-1] = replace(
                            last, result=inject_notifications(last.result, pending)
                        )

                    for result in batch_results:
                        await send({
                            "type": "tool_call_finished",
                            "id": result.id,
                            "tool_name": result.tool_name,
                            "result": result.result,
                            "is_error": result.is_error,
                            "images": result.images,
                        })
                    if pending:
                        self._notifications.ack(notify_session, [item.seq for item in pending])
                        await send({
                            "type": "notifications_delivered",
                            "kinds": [item.kind.value for item in pending],
                        })
                    action_queue.append(ToolBatchFinished(batch_results))

        if state.error is not None:
            await send({"type": "failed", "error": state.error, "code": state.error_code})

        # 兜底路径：整轮没调用任何工具时，待送通知无法搭车，作为用户消息触发新一轮。
        if self._notifications and current_context.session_id:
            leftover = self._notifications.peek(current_context.session_id)
            if leftover:
                self._notifications.ack(current_context.session_id, [i.seq for i in leftover])
                await send({
                    "type": "notification_message",
                    "content": build_notification_message(leftover),
                    "kinds": [i.kind.value for i in leftover],
                })

        await send({"type": "completed"})

    # ── 单次 LLM 调用（含压缩与溢出恢复） ──────────────────────────────────

    async def _call_llm(
        self,
        state: AgentSessionState,
        context: AgentContext,
        send: Callable[[dict], Awaitable[None]],
        input_budget: int,
        baseline_estimate: int,
        baseline_usage: int,
        overflow_recovery_attempted: bool,
        compaction_failed: bool,
        system_prompt: str = "",
    ) -> dict:
        tools = self._tool_list
        actions: List[AgentAction] = []
        messages = list(state.messages)

        if not compaction_failed:
            estimate = estimate_request(system_prompt, messages, tools)
            predicted = calibrated(estimate, baseline_estimate, baseline_usage)
            result = self._compactor.compact_if_needed(
                messages, self._metadata, system_prompt, tools, predicted
            )
            if result.compacted:
                messages = result.messages
                state = replace(state, messages=messages)
                baseline_usage = 0
                baseline_estimate = 0
                await send({"type": "compaction", "forced": False})

        estimate = estimate_request(system_prompt, messages, tools)
        predicted = calibrated(estimate, baseline_estimate, baseline_usage)
        await send({"type": "context_usage", "tokens": predicted, "budget": input_budget, "estimated": True})

        if predicted >= input_budget:
            actions.append(LlmError("", "input_budget_exceeded"))
            return self._outcome(state, actions, baseline_estimate, baseline_usage,
                                 overflow_recovery_attempted, compaction_failed)

        accumulator = StreamAccumulator(send, self._now_ms)
        call_record: Dict[str, Any] = {
            "model": getattr(self._provider, "model", ""),
            "provider_id": getattr(self._provider, "provider_id", ""),
            "status": "cancelled",
        }
        try:
            async for chunk in self._provider.complete_stream(system_prompt, messages, tools, context.reasoning_effort):
                await accumulator.on_chunk(chunk)
            await accumulator.flush()
            response = accumulator.final_response or AIResponse(content=accumulator.text)
            if accumulator.reasoning:
                response = replace(response, reasoning=accumulator.reasoning)
            call_record["status"] = "success"

            # 上下文窗口超限：强制压缩后重放一次
            if response.stop_reason == "model_context_window_exceeded" and not overflow_recovery_attempted:
                recovery = self._compactor.compact_if_needed(
                    state.messages, self._metadata, system_prompt, tools, predicted, force=True
                )
                if recovery.compacted:
                    state = replace(state, messages=recovery.messages)
                    actions.append(InitRequest(recovery.messages))
                    return self._outcome(state, actions, 0, 0, True, compaction_failed)

            baseline_estimate = estimate
            baseline_usage = response.input_tokens
            await send({
                "type": "context_usage",
                "tokens": baseline_usage if baseline_usage > 0 else estimate,
                "budget": input_budget,
                "estimated": baseline_usage <= 0,
                "cached_tokens": response.cached_input_tokens,
            })

            response_message_id = str(uuid.uuid4())
            if response.content.strip() or response.tool_calls:
                await send({
                    "type": "assistant_text",
                    "content": response.content,
                    "tool_calls": response.tool_calls,
                    "reasoning": accumulator.reasoning,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cached_input_tokens": response.cached_input_tokens,
                    "message_id": response_message_id,
                })
            actions.append(LlmResponse(response, response_message_id))

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc)
            if (
                not overflow_recovery_attempted
                and not accumulator.text
                and not accumulator.reasoning
                and self._is_context_overflow(exc)
            ):
                recovery = self._compactor.compact_if_needed(
                    state.messages, self._metadata, system_prompt, tools, predicted, force=True
                )
                if recovery.compacted:
                    state = replace(state, messages=recovery.messages)
                    actions.append(InitRequest(recovery.messages))
                    return self._outcome(state, actions, 0, 0, True, compaction_failed)

            if accumulator.text or accumulator.reasoning:
                await send({
                    "type": "assistant_text",
                    "content": accumulator.text,
                    "tool_calls": [],
                    "reasoning": accumulator.reasoning,
                })
            actions.append(LlmError(f"LLM 调用失败: {message}"))
            call_record["status"] = "error"
            call_record["error"] = message
        finally:
            if self._on_llm_call:
                self._on_llm_call(call_record)

        return self._outcome(state, actions, baseline_estimate, baseline_usage,
                             overflow_recovery_attempted, compaction_failed)

    @staticmethod
    def _outcome(state, actions, baseline_estimate, baseline_usage,
                 overflow_recovery_attempted, compaction_failed) -> dict:
        return {
            "state": state,
            "actions": actions,
            "baseline_estimate": baseline_estimate,
            "baseline_usage": baseline_usage,
            "overflow_recovery_attempted": overflow_recovery_attempted,
            "compaction_failed": compaction_failed,
        }

    @staticmethod
    def _apply_mode_change(context: AgentContext, pending: Sequence[Any]) -> AgentContext:
        """用户在工作期间切换模式：本轮后续批次的权限判定立即改用新模式。"""
        mode_change = None
        for item in pending:
            if item.kind is AgentNotificationKind.MODE_CHANGE and item.new_mode is not None:
                mode_change = item
        if mode_change is None:
            return context

        new_mode = mode_change.new_mode
        if new_mode is AgentMode.PLAN:
            return replace(
                context,
                mode=AgentMode.PLAN,
                mode_before_plan=(
                    context.mode if context.mode is not AgentMode.PLAN else context.mode_before_plan
                ),
            )
        return replace(context, mode=new_mode, mode_before_plan=None)

    @staticmethod
    def _is_context_overflow(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            key in text
            for key in (
                "context_length_exceeded",
                "context_window_exceeded",
                "maximum context length",
                "prompt is too long",
            )
        )

    # ── 批量工具执行 ──────────────────────────────────────────────────────

    async def _execute_batch(
        self,
        tool_calls: List[ToolCall],
        context: AgentContext,
        send: Callable[[dict], Awaitable[None]],
    ) -> tuple[List[ToolBatchResult], AgentContext]:
        if not tool_calls:
            return [], context

        # 修改文件前统一记录快照
        if self._checkpoints and context.session_id:
            for call in tool_calls:
                if call.name in ("editFile", "writeFile"):
                    path = call.arguments.get("path")
                    if isinstance(path, str) and path:
                        self._checkpoints.before_file_modified(context.session_id, path)

        # 同一批里同路径调用必须串行：并行的读-改-写会互相覆盖
        locks: Dict[str, asyncio.Lock] = {}

        async def run_one(call: ToolCall) -> ToolRunResult:
            tool = self._tools.get(call.name)
            if tool is None:
                return ToolRunResult(
                    result_to_transport_string(error(f"未知工具：{call.name}", "TOOL_NOT_FOUND")), True
                )
            path = call.arguments.get("path")
            if not isinstance(path, str) or not path:
                return await self._invoke_tool(tool, call, send)
            lock = locks.setdefault(path, asyncio.Lock())
            async with lock:
                return await self._invoke_tool(tool, call, send)

        results = await asyncio.gather(*(run_one(call) for call in tool_calls), return_exceptions=True)

        batch: List[ToolBatchResult] = []
        for index, call in enumerate(tool_calls):
            run_result = results[index]
            if isinstance(run_result, BaseException):
                run_result = ToolRunResult(
                    result_to_transport_string(error(str(run_result), "TOOL_EXECUTION_FAILED")), True
                )
            batch.append(ToolBatchResult(
                call.id, call.name, run_result.raw, run_result.is_error,
                run_result.attachments, run_result.images,
            ))
        return batch, context

    async def _invoke_tool(
        self,
        tool: AgentTool,
        call: ToolCall,
        send: Callable[[dict], Awaitable[None]],
    ) -> ToolRunResult:
        try:
            if hasattr(tool, "execute_stream"):
                async def emit(event: dict) -> None:
                    await send({"type": "tool_progress", "id": call.id, **event})

                result = await tool.execute_stream(call.arguments, emit)
            else:
                result = tool.execute(call.arguments)
                if asyncio.iscoroutine(result):
                    result = await result
        except Exception as exc:
            return ToolRunResult(
                result_to_transport_string(error(f"工具执行失败：{exc}", "TOOL_EXECUTION_FAILED")), True
            )
        return ToolRunResult(result_to_transport_string(result), is_error_result(result))
