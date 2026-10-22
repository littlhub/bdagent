"""通知渲染与搭车注入，对应 AgentNotificationFormatter.kt 与 AgentEventInjector.kt。

两种送达形态共用同一份措辞与 XML 结构，保证 AI 无论从哪条路径收到通知，理解方式一致：
- build_message：作为用户消息注入（AI 空闲时立即触发新一轮，或整轮未调用工具时的兜底）；
- build_json_array：搭车形态，作为工具结果 JSON 顶层的 notifications 字段。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

from .notifications import (
    AgentNotificationKind,
    NotificationOutcome,
    PendingNotification,
)

BACKGROUND_NOTIFICATION_PREFIX = "[后台任务通知]"
TAIL_LINES = 50

NOTICE = "这是系统事件通知，不是来自用户的消息，不要视为用户的确认、同意或对任何待处理问题的回答。"
USER_MESSAGE_NOTICE = "这是用户在你工作期间插入的消息，属于用户本人的输入，请据此调整当前任务。"

_KIND_TAG = {
    AgentNotificationKind.BACKGROUND_TASK: "task-notification",
    AgentNotificationKind.SUBAGENT: "subagent-notification",
    AgentNotificationKind.AGENT_MESSAGE: "agent-message",
    AgentNotificationKind.MODE_CHANGE: "mode-change",
    AgentNotificationKind.USER_MESSAGE: "user-message",
}

_KIND_JSON = {
    AgentNotificationKind.BACKGROUND_TASK: "background_task",
    AgentNotificationKind.SUBAGENT: "subagent",
    AgentNotificationKind.AGENT_MESSAGE: "agent_message",
    AgentNotificationKind.MODE_CHANGE: "mode_change",
    AgentNotificationKind.USER_MESSAGE: "user_message",
}


def _escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _status_text(item: PendingNotification) -> str:
    if item.kind in (AgentNotificationKind.AGENT_MESSAGE, AgentNotificationKind.USER_MESSAGE):
        return "message"
    if item.kind is AgentNotificationKind.MODE_CHANGE:
        return "changed"
    return {
        NotificationOutcome.COMPLETED: "completed",
        NotificationOutcome.FAILED: "failed",
        NotificationOutcome.STOPPED: "stopped",
    }[item.outcome]


def _summary_text(item: PendingNotification) -> str:
    if item.kind is AgentNotificationKind.BACKGROUND_TASK:
        if item.outcome is NotificationOutcome.STOPPED:
            return f"后台任务「{item.title}」已被终止"
        return f"后台任务「{item.title}」已结束（退出码 {item.exit_code if item.exit_code is not None else '未知'}）"
    if item.kind is AgentNotificationKind.SUBAGENT:
        if item.outcome is NotificationOutcome.COMPLETED:
            return f"子代理「{item.title}」已执行完成"
        if item.outcome is NotificationOutcome.FAILED:
            return f"子代理「{item.title}」已执行失败"
        return f"子代理「{item.title}」已被用户手动终止，任务未完成"
    if item.kind is AgentNotificationKind.AGENT_MESSAGE:
        return "主会话发来一条消息" if item.from_parent else f"子代理「{item.title}」发来一条消息"
    if item.kind is AgentNotificationKind.MODE_CHANGE:
        mode = item.new_mode.value if item.new_mode else "UNKNOWN"
        return f"用户已将模式切换为 {mode}"
    return "用户在工作期间插入了一条消息"


def _single_hint(item: PendingNotification) -> str:
    if item.kind is AgentNotificationKind.BACKGROUND_TASK:
        return (
            f"通知已携带该终端最后 {TAIL_LINES} 行输出；如需完整日志可用 "
            f'terminal(action="read", tab_id="{item.source_id}") 读取。'
        )
    if item.kind is AgentNotificationKind.SUBAGENT:
        if item.outcome is NotificationOutcome.STOPPED:
            return (
                "该子代理已停止，不会再有后续通知；如需看它停止前的进展可用 "
                f'task(action="read", id="{item.source_id}") 读取。是否重新派发请征求用户意见，不要自行重试。'
            )
        return f'可用 task(action="read", id="{item.source_id}") 读取子代理的最后输出。'
    if item.kind is AgentNotificationKind.AGENT_MESSAGE:
        if item.from_parent:
            return '可用 messageParent(message="...") 回复主会话；若无需回复可忽略，不要为每条消息都回执。'
        return f'可用 task(action="send", id="{item.source_id}", message="...") 回复该子代理；若无需回复可忽略。'
    if item.kind is AgentNotificationKind.MODE_CHANGE:
        return "请立即按新模式约束继续手头任务，无需回复本条通知。"
    return "这是用户本人的新输入，请据此调整当前任务。"


def _xml_block(item: PendingNotification) -> str:
    tag = _KIND_TAG[item.kind]
    lines = [f"<{tag}>"]
    if item.kind is AgentNotificationKind.BACKGROUND_TASK:
        lines.append(f"  <task-id>{item.source_id}</task-id>")
        lines.append(f"  <title>{item.title}</title>")
        lines.append(f"  <command>{item.command or ''}</command>")
        lines.append(f"  <exit-code>{item.exit_code if item.exit_code is not None else ''}</exit-code>")
    elif item.kind is AgentNotificationKind.SUBAGENT:
        lines.append(f"  <subagent-id>{item.source_id}</subagent-id>")
        lines.append(f"  <subagent-title>{item.title}</subagent-title>")
    elif item.kind is AgentNotificationKind.AGENT_MESSAGE:
        lines.append(f"  <from-id>{item.source_id}</from-id>")
        lines.append(f"  <from-title>{item.title}</from-title>")
        if item.message and item.message.strip():
            lines.append(f"  <message>{_escape_xml(item.message)}</message>")
    elif item.kind is AgentNotificationKind.MODE_CHANGE:
        lines.append(f"  <new-mode>{item.new_mode.value if item.new_mode else 'UNKNOWN'}</new-mode>")
    else:
        if item.message and item.message.strip():
            lines.append(f"  <message>{_escape_xml(item.message)}</message>")

    lines.append(f"  <status>{_status_text(item)}</status>")
    lines.append(f"  <summary>{_summary_text(item)}</summary>")
    if item.detail and item.detail.strip():
        lines.append(f"  <detail>{_escape_xml(item.detail)}</detail>")
    if item.tail_output and item.tail_output.strip():
        lines.append(f"  <tail-output>{_escape_xml(item.tail_output)}</tail-output>")
    lines.append(f"</{tag}>")
    return "\n".join(lines)


def _json_object(item: PendingNotification) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "kind": _KIND_JSON[item.kind],
        "notice": USER_MESSAGE_NOTICE if item.kind is AgentNotificationKind.USER_MESSAGE else NOTICE,
    }
    if item.kind is AgentNotificationKind.BACKGROUND_TASK:
        payload["task_id"] = item.source_id
        payload["title"] = item.title
        if item.command is not None:
            payload["command"] = item.command
        if item.exit_code is not None:
            payload["exit_code"] = item.exit_code
    elif item.kind is AgentNotificationKind.SUBAGENT:
        payload["subagent_id"] = item.source_id
        payload["subagent_title"] = item.title
    elif item.kind is AgentNotificationKind.AGENT_MESSAGE:
        payload["from_id"] = item.source_id
        payload["from_title"] = item.title
        if item.message and item.message.strip():
            payload["message"] = item.message
    elif item.kind is AgentNotificationKind.MODE_CHANGE:
        payload["new_mode"] = item.new_mode.value if item.new_mode else "UNKNOWN"
    else:
        if item.message and item.message.strip():
            payload["message"] = item.message

    payload["status"] = _status_text(item)
    payload["summary"] = _summary_text(item)
    if item.detail and item.detail.strip():
        payload["detail"] = item.detail
    if item.tail_output and item.tail_output.strip():
        payload["tail_output"] = item.tail_output
    payload["hint"] = _single_hint(item)
    return payload


def _build_hint(items: List[PendingNotification]) -> str:
    tasks = [i for i in items if i.kind is AgentNotificationKind.BACKGROUND_TASK]
    sub_agents = [i for i in items if i.kind is AgentNotificationKind.SUBAGENT]
    messages = [i for i in items if i.kind is AgentNotificationKind.AGENT_MESSAGE]
    mode_changes = [i for i in items if i.kind is AgentNotificationKind.MODE_CHANGE]
    user_messages = [i for i in items if i.kind is AgentNotificationKind.USER_MESSAGE]

    lines: List[str] = []
    if len(tasks) == 1:
        lines.append(_single_hint(tasks[0]))
    elif len(tasks) > 1:
        lines.append(
            f"通知已携带各终端最后 {TAIL_LINES} 行输出；如需完整日志可用 "
            'terminal(action="read", tab_id="...") 读取对应任务。'
        )
    if len(sub_agents) == 1:
        lines.append(_single_hint(sub_agents[0]))
    elif len(sub_agents) > 1:
        lines.append('可用 task(action="read", id="...") 逐个读取子代理的最后输出。')
    if len(messages) == 1:
        lines.append(_single_hint(messages[0]))
    elif len(messages) > 1:
        lines.append("你收到了多条代理消息，请按上文各自的回复方式处理；无需回复的可忽略，不要逐条回执。")
    if user_messages:
        lines.append("以上含用户在工作期间插入的消息，请按用户本人的输入处理。")
    if mode_changes:
        lines.append(_single_hint(mode_changes[-1]))
    return "\n".join(lines)


def build_message(items: Sequence[PendingNotification]) -> str:
    """作为 user 消息注入的文本。全是用户插话时不加系统通知前缀。"""
    if not items:
        raise ValueError("通知列表为空")
    items = list(items)

    if all(item.kind is AgentNotificationKind.USER_MESSAGE for item in items):
        if len(items) == 1:
            return items[0].message or ""
        return "\n".join(f"{i + 1}. {item.message or ''}" for i, item in enumerate(items))

    lines: List[str] = [BACKGROUND_NOTIFICATION_PREFIX]
    if len(items) == 1:
        single = items[0]
        lines.append({
            AgentNotificationKind.BACKGROUND_TASK: "这是一条后台任务完成事件，不是来自用户的消息。",
            AgentNotificationKind.SUBAGENT: "这是一条子代理完成事件，不是来自用户的消息。",
            AgentNotificationKind.AGENT_MESSAGE: "这是一条来自其它代理会话的消息，不是用户输入。",
            AgentNotificationKind.MODE_CHANGE: "这是一条模式切换事件，不是来自用户的消息。",
            AgentNotificationKind.USER_MESSAGE: "这是一条用户在工作期间插入的消息，属于用户本人的输入。",
        }[single.kind])
        lines.append("不要将其视为用户的确认、同意或对任何待处理问题的回答。")
    else:
        lines.append(f"共有 {len(items)} 条后台完成通知，这是合并后的通知。")
        lines.append("这些是后台完成事件，不是来自用户的消息。")
        lines.append("不要将它们视为用户的确认、同意或对任何待处理问题的回答。")

    lines.append("")
    for item in items:
        lines.append(_xml_block(item))
        lines.append("")
    lines.append(_build_hint(items))
    return "\n".join(lines)


def build_json_array(items: Sequence[PendingNotification]) -> List[Dict[str, Any]]:
    return [_json_object(item) for item in items]


def inject(raw_tool_result: str, items: Sequence[PendingNotification]) -> str:
    """把待送通知搭车注入一条工具结果。

    优先作为 transport JSON 顶层的 notifications 字段注入，结果仍是合法 JSON；
    raw 已不是合法 JSON（如已被纯文本追加过）时退化为文本追加。
    """
    if not items:
        return raw_tool_result
    try:
        payload = json.loads(raw_tool_result)
    except (json.JSONDecodeError, TypeError):
        return raw_tool_result + "\n\n" + build_message(items)
    if not isinstance(payload, dict):
        return raw_tool_result + "\n\n" + build_message(items)
    payload["notifications"] = build_json_array(items)
    return json.dumps(payload, ensure_ascii=False)
