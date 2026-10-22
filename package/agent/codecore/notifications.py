"""会话级待送通知队列，对应 domain/notification/AgentNotificationCenter.kt。

后台任务与子代理完成事件、代理间消息、模式切换、用户插话，在 AI 忙碌时先入队，
由两条路径之一送达：
- 搭车（首选）：每批工具执行完成后 peek 出通知注入工具结果，本轮内立即送达，
  省掉一次「等本轮结束再起一轮」的 LLM 往返；
- 兜底：整轮没调用任何工具时，由调用方 drain 后作为用户消息触发新一轮。

peek 与 ack 分两段而非直接取走：通知只有在工具结果真正落库后才算送达，
中途被取消时通知仍留在队列里，交给下一批工具或兜底路径。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, Optional, Sequence

from .messages import AgentMode


class AgentNotificationKind(str, Enum):
    BACKGROUND_TASK = "BACKGROUND_TASK"
    SUBAGENT = "SUBAGENT"
    AGENT_MESSAGE = "AGENT_MESSAGE"
    MODE_CHANGE = "MODE_CHANGE"
    USER_MESSAGE = "USER_MESSAGE"


class NotificationOutcome(str, Enum):
    """STOPPED 与 FAILED 必须区分：被用户手动终止不是执行出错。"""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


@dataclass
class PendingNotification:
    kind: AgentNotificationKind
    source_id: str = ""
    title: str = ""
    outcome: NotificationOutcome = NotificationOutcome.COMPLETED
    command: Optional[str] = None
    exit_code: Optional[int] = None
    tail_output: Optional[str] = None
    detail: Optional[str] = None
    message: Optional[str] = None
    from_parent: bool = False
    new_mode: Optional[AgentMode] = None
    seq: int = 0


class AgentNotificationCenter:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: Dict[str, List[PendingNotification]] = {}
        self._seq = 0

    def enqueue(self, session_id: str, item: PendingNotification) -> int:
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._pending.setdefault(session_id, []).append(replace(item, seq=seq))
            return seq

    def peek(self, session_id: str) -> List[PendingNotification]:
        with self._lock:
            return list(self._pending.get(session_id, ()))

    def ack(self, session_id: str, seqs: Sequence[int]) -> None:
        if not seqs:
            return
        with self._lock:
            items = self._pending.get(session_id)
            if items is None:
                return
            remaining = [item for item in items if item.seq not in set(seqs)]
            if remaining:
                self._pending[session_id] = remaining
            else:
                self._pending.pop(session_id, None)

    def drain(self, session_id: str) -> List[PendingNotification]:
        with self._lock:
            return self._pending.pop(session_id, [])

    def pending_count(self, session_id: str) -> int:
        with self._lock:
            return len(self._pending.get(session_id, ()))

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._pending.pop(session_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._pending.clear()
