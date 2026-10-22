"""codecore_bridge 兼容入口：实现已移到 agent/bridge.py。"""
from agent.bridge import *  # noqa: F401,F403
from agent.bridge import (  # noqa: F401
    ALL_TOOLS,
    CONFIRM_WORDS,
    MAX_TOOL_CALLS,
    READ_ONLY_TOOLS,
    _PENDING_PLAN,
    _discover_code_skills,
    build_tools,
    has_pending_plan,
    looks_like_confirm,
    pick_mode,
    run_long_task,
)
