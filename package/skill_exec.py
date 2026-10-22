"""skill_exec 兼容入口：实现已移到 skills/exec.py。"""
from skills.exec import *  # noqa: F401,F403
from skills.exec import (  # noqa: F401
    MAX_OUTPUT_CHARS,
    clear_work,
    collect_outputs,
    data_work_dir,
    ensure_sandbox,
    execute_skill,
    execute_stopj,
    execute_tool,
    resolve_entry,
    run_in_sandbox,
    stage_skill,
)
