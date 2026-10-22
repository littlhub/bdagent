"""agent_loop 兼容入口：实现已移到 agent/loop.py。

保留这个模块名，让既有的 `import agent_loop` 无需改动；
新代码请直接 `from agent import loop`。
"""
from agent.loop import *  # noqa: F401,F403
from agent.loop import (  # noqa: F401
    ALL_TOOLS,
    READ_ONLY_TOOLS,
    RESULT_PREVIEW_CHARS,
    SYSTEM_PROMPT,
    _bash_direct,
    _edit_direct,
    _find_direct,
    _grep_direct,
    _ls_direct,
    _read_direct,
    _write_direct,
    run_agent_loop,
)
