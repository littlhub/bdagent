"""消息与上下文模型，对应 domain/model/AgentMessage.kt。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

CONTEXT_COMPACTION_MARKER = "What did we do so far?"


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class AgentImage:
    mime_type: str
    base64_data: str
    path: str = ""


@dataclass
class UserMessage:
    content: str
    id: str = field(default_factory=_uuid)
    images: List[AgentImage] = field(default_factory=list)
    role = "user"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssistantMessage:
    content: str
    id: str = field(default_factory=_uuid)
    tool_calls: List[ToolCall] = field(default_factory=list)
    reasoning: str = ""
    signature: str = ""
    thinking_blocks_json: str = ""
    images: List[AgentImage] = field(default_factory=list)
    input_tokens: int = 0
    role = "assistant"


@dataclass
class ToolResultMessage:
    tool_name: str
    result: str
    id: str = field(default_factory=_uuid)
    images: List[AgentImage] = field(default_factory=list)
    model_result: Optional[str] = None
    role = "tool"

    @property
    def message_id(self) -> str:
        return f"tool_{self.id}"


AgentMessage = UserMessage | AssistantMessage | ToolResultMessage


def message_id(message: AgentMessage) -> str:
    return message.message_id if isinstance(message, ToolResultMessage) else message.id


class AgentMode(str, Enum):
    BUILD = "BUILD"
    PLAN = "PLAN"
    AUTO = "AUTO"


class GoalStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DONE = "DONE"


@dataclass
class AgentContext:
    project_root: str
    history: List[AgentMessage] = field(default_factory=list)
    session_id: Optional[str] = None
    input_message_id: str = field(default_factory=_uuid)
    input_images: List[AgentImage] = field(default_factory=list)
    last_input_tokens: int = 0
    mode: AgentMode = AgentMode.BUILD
    mode_before_plan: Optional[AgentMode] = None
    goal: Optional[str] = None
    goal_status: Optional[GoalStatus] = None
    reasoning_effort: Optional[str] = None

    @property
    def permission_mode(self) -> AgentMode:
        """goal 自主循环激活期间按 AUTO 放行，与用户可见 mode 解耦。"""
        return AgentMode.AUTO if self.goal_status is GoalStatus.ACTIVE else self.mode
