"""项目规则加载，对应 domain/prompt/SystemPromptProvider.kt 的 ProjectRuleSource。

规则来源固定两处：全局目录与工作区根目录。各自优先 `AGENTS.md`，不存在时回退 `CLAUDE.md`。
两处一并注入，全局在前、工作区在后；工作区规则更具体，冲突时以它为准。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

AGENTS_FILE = "AGENTS.md"
CLAUDE_FILE = "CLAUDE.md"
MAX_RULES_CHARS = 32_000
TRUNCATED_SUFFIX = "\n…（规则过长，已截断）"


@dataclass
class ProjectRules:
    """两处规则的正文；某一处没有规则文件时对应字段为 None。"""

    global_rules: Optional[str] = None
    project_rules: Optional[str] = None

    def is_empty(self) -> bool:
        return self.global_rules is None and self.project_rules is None

    def render(self) -> str:
        """按「全局在前、工作区在后」拼接，供注入系统提示词。"""
        parts = [text for text in (self.global_rules, self.project_rules) if text]
        return "\n\n".join(parts)


def resolve_rule_file(directory: str) -> Optional[str]:
    """返回该目录下生效的规则文件路径：AGENTS.md 优先，CLAUDE.md 兜底。"""
    if not directory:
        return None
    for name in (AGENTS_FILE, CLAUDE_FILE):
        path = os.path.join(directory, name)
        if os.path.isfile(path) and os.access(path, os.R_OK):
            return path
    return None


def read_rule(path: Optional[str]) -> Optional[str]:
    if path is None:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None
    if not text.strip():
        return None
    if len(text) > MAX_RULES_CHARS:
        return (text[:MAX_RULES_CHARS] + TRUNCATED_SUFFIX).strip()
    return text.strip()


def load_project_rules(aicode_dir: str, project_root: str) -> ProjectRules:
    """读取全局（`~/.aicode`）与工作区两处规则。"""
    global_path = resolve_rule_file(aicode_dir)
    project_path = resolve_rule_file(project_root)
    return ProjectRules(
        global_rules=read_rule(global_path),
        project_rules=read_rule(project_path),
    )
