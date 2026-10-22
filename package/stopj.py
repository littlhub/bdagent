"""stopj 兼容入口：实现已移到 skills/stopj.py。"""
from skills.stopj import *  # noqa: F401,F403
from skills.stopj import (  # noqa: F401
    execute_workflow,
    initialize_data,
    parse_protein_chain,
    parse_stopj_expression,
    tokenize_compact,
    tree_to_path,
)
