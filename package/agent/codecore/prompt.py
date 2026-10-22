"""提示词片段与系统提示词组装。

对应 domain/prompt/PromptFragmentCatalog.kt、PromptFragmentResolver.kt、
SystemPromptProvider.kt。

片段分两类：
- 静态基线：`prompts/` 顶层 `<NN>-<名称>.md`，可被 `prompts.custom/` 按数字身份覆盖或新增；
- 按需叶子：`prompts/agent/` 下的无数字片段（模式提醒、压缩/标题提示词），按精确同名覆盖。

`prompts.custom/` 存在 `.no-builtin` 时，主代理提示词只由自定义数字片段组成。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Dict, List, Optional

from .messages import AgentContext, GoalStatus
from .rules import ProjectRules, load_project_rules

DISABLE_BUILTIN_FILE = ".no-builtin"
CUSTOM_DIR = "prompts.custom"
LOCAL_DIR = "prompts"
AGENT_SUBDIR = "agent"

_LEADING_COMMENT = re.compile(r"(?s)^\s*<!--.*?-->\s*")
_DESCRIPTION = re.compile(r"^\s*<!--\s*(.*?)\s*-->", re.S)
_NUMBERED = re.compile(r"^(\d{2})-(.+)\.md$")
_BLANK_LINES = re.compile(r"\n{3,}")

# 片段里可用的运行期变量
SKILLS_VAR = "{{AICODE_SKILLS}}"
MEMORY_GLOBAL_VAR = "{{AICODE_MEMORY_GLOBAL}}"
MEMORY_PROJECT_VAR = "{{AICODE_MEMORY_PROJECT}}"
SUBAGENTS_VAR = "{{AICODE_SUBAGENTS}}"
PROJECT_RULES_VAR = "{{AICODE_PROJECT_RULES}}"
PROJECT_RULES_GLOBAL_VAR = "{{AICODE_PROJECT_RULES_GLOBAL}}"
PROJECT_RULES_PROJECT_VAR = "{{AICODE_PROJECT_RULES_PROJECT}}"
WORKSPACE_VAR = "{{AICODE_WORKSPACE}}"
ENVIRONMENT_VAR = "{{AICODE_ENVIRONMENT}}"
DATE_VAR = "{{AICODE_DATE}}"
GOAL_VAR = "{{AICODE_GOAL}}"
GOAL_FILE = "agent/goal.md"
GOAL_TEXT_PLACEHOLDER = "{{GOAL_TEXT}}"


class FragmentSource(str, Enum):
    """优先级从高到低：项目 > 全局 > 本地 > 内置。"""

    PROJECT = "PROJECT"
    GLOBAL = "GLOBAL"
    LOCAL = "LOCAL"
    BUILTIN = "BUILTIN"


@dataclass
class PromptFragment:
    number: int
    title: str
    source: FragmentSource
    content: str
    file: Optional[str] = None
    description: str = ""

    @property
    def body(self) -> str:
        return _LEADING_COMMENT.sub("", self.content)

    @property
    def editable(self) -> bool:
        return self.source in (FragmentSource.PROJECT, FragmentSource.GLOBAL)


def parse_number(file_name: str) -> Optional[int]:
    """顶层数字片段的数字值；非两位数字前缀返回 None。"""
    match = _NUMBERED.match(file_name)
    return int(match.group(1)) if match else None


def numbered_fragments(directory: Optional[str]) -> Dict[int, str]:
    """目录顶层 `<两位数字>-<名称>.md`，按编号去重（同编号取字典序首个）。"""
    if not directory or not os.path.isdir(directory):
        return {}
    by_number: Dict[int, str] = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        number = parse_number(name)
        if number is not None and number not in by_number:
            by_number[number] = path
    return by_number


def is_builtin_disabled(custom_dir: str) -> bool:
    return os.path.isfile(os.path.join(custom_dir, DISABLE_BUILTIN_FILE))


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _title_of(file_name: str) -> str:
    base = file_name[:-3] if file_name.endswith(".md") else file_name
    return base.split("-", 1)[1] if "-" in base else base


def _extract_description(content: str) -> str:
    match = _DESCRIPTION.match(content)
    return match.group(1).strip() if match else ""


class PromptFragmentCatalog:
    """四级来源解析：同一编号只生效优先级最高的一层。"""

    def __init__(self, aicode_dir: str, builtin_dir: str):
        self._global_dir = os.path.join(aicode_dir, CUSTOM_DIR)
        self._local_dir = os.path.join(aicode_dir, LOCAL_DIR)
        self._builtin_dir = builtin_dir

    @property
    def custom_dir(self) -> str:
        return self._global_dir

    def project_dir(self, project_root: Optional[str]) -> Optional[str]:
        if not project_root:
            return None
        return os.path.join(project_root, ".aicode", CUSTOM_DIR)

    def builtin_files(self) -> Dict[int, str]:
        return numbered_fragments(self._builtin_dir)

    def list(self, project_root: Optional[str]) -> List[PromptFragment]:
        builtin = self.builtin_files()
        project = numbered_fragments(self.project_dir(project_root))
        global_files = numbered_fragments(self._global_dir)
        numbers = sorted(set(builtin) | set(project) | set(global_files))
        fragments = [
            fragment
            for fragment in (
                self._resolve(number, builtin, project, global_files) for number in numbers
            )
            if fragment is not None
        ]
        if is_builtin_disabled(self._global_dir):
            return [
                f for f in fragments
                if f.source in (FragmentSource.PROJECT, FragmentSource.GLOBAL)
            ]
        return fragments

    def render_static(self, project_root: Optional[str]) -> str:
        """静态基线正文：所有生效片段去掉前导注释后按编号拼接。"""
        bodies = []
        for fragment in self.list(project_root):
            body = _LEADING_COMMENT.sub("", fragment.content).strip()
            if body:
                bodies.append(body)
        return "\n\n".join(bodies)

    def resolve_prompt(self, name: str) -> Optional[str]:
        """按需叶子：先按数字身份找自定义覆盖，再精确同名，最后落到内置目录。"""
        number = parse_number(os.path.basename(name))
        if number is not None:
            override = numbered_fragments(self._global_dir).get(number)
            if override:
                text = _read_text(override)
                if text is not None:
                    return text
        for base in (self._global_dir, self._local_dir, self._builtin_dir):
            text = _read_text(os.path.join(base, name))
            if text is not None:
                return text
        return None

    def _resolve(
        self,
        number: int,
        builtin: Dict[int, str],
        project: Dict[int, str],
        global_files: Dict[int, str],
    ) -> Optional[PromptFragment]:
        if number in project:
            return self._read(project[number], number, FragmentSource.PROJECT)
        if number in global_files:
            return self._read(global_files[number], number, FragmentSource.GLOBAL)
        if number in builtin:
            name = os.path.basename(builtin[number])
            local_path = os.path.join(self._local_dir, name)
            if os.path.isfile(local_path):
                return self._read(local_path, number, FragmentSource.LOCAL)
            content = _read_text(builtin[number])
            if content is not None:
                return PromptFragment(
                    number, _title_of(name), FragmentSource.BUILTIN,
                    content, builtin[number], _extract_description(content),
                )
        return None

    @staticmethod
    def _read(path: str, number: int, source: FragmentSource) -> Optional[PromptFragment]:
        content = _read_text(path)
        if content is None:
            return None
        return PromptFragment(
            number, _title_of(os.path.basename(path)), source,
            content, path, _extract_description(content),
        )


@dataclass
class MemoryItem:
    name: str
    description: str = ""


class SystemPromptProvider:
    """按模块组装系统提示词：稳定基线在前，运行期内容集中在末尾片段。"""

    def __init__(
        self,
        catalog: PromptFragmentCatalog,
        aicode_dir: str,
        environment: str = "本地容器（PRoot）",
        skills: Optional[List[str]] = None,
        sub_agents: Optional[List[str]] = None,
        memories_global: Optional[List[MemoryItem]] = None,
        memories_project: Optional[List[MemoryItem]] = None,
    ):
        self._catalog = catalog
        self._aicode_dir = aicode_dir
        self._environment = environment
        self._skills = skills or []
        self._sub_agents = sub_agents or []
        self._memories_global = memories_global or []
        self._memories_project = memories_project or []

    def build(self, context: AgentContext) -> str:
        rules = load_project_rules(self._aicode_dir, context.project_root)
        static = self._catalog.render_static(context.project_root)
        rendered = self._render_variables(
            static,
            rules=rules,
            context=context,
            skills=self._render_list(self._skills),
            sub_agents=self._render_list(self._sub_agents),
            memory_global=self._render_list(
                [f"{m.name}: {m.description or '无'}" for m in self._memories_global]
            ),
            memory_project=self._render_list(
                [f"{m.name}: {m.description or '无'}" for m in self._memories_project]
            ),
        )
        return _BLANK_LINES.sub("\n\n", rendered.strip())

    def _render_variables(
        self,
        text: str,
        rules: ProjectRules,
        context: AgentContext,
        skills: str,
        sub_agents: str,
        memory_global: str,
        memory_project: str,
    ) -> str:
        out = text
        out = out.replace(SKILLS_VAR, skills)
        out = out.replace(MEMORY_GLOBAL_VAR, memory_global)
        out = out.replace(MEMORY_PROJECT_VAR, memory_project)
        out = out.replace(SUBAGENTS_VAR, sub_agents)
        out = out.replace(PROJECT_RULES_GLOBAL_VAR, rules.global_rules or "")
        out = out.replace(PROJECT_RULES_PROJECT_VAR, rules.project_rules or "")
        out = out.replace(PROJECT_RULES_VAR, rules.project_rules or "")
        out = out.replace(WORKSPACE_VAR, "~/workspace" if context.project_root else "（未选择工作区）")
        out = out.replace(ENVIRONMENT_VAR, self._environment)
        out = out.replace(DATE_VAR, date.today().isoformat())
        out = out.replace(GOAL_VAR, self._render_goal(context))
        return out

    def _render_goal(self, context: AgentContext) -> str:
        if context.goal_status is not GoalStatus.ACTIVE or not context.goal:
            return ""
        template = self._catalog.resolve_prompt(GOAL_FILE)
        if template is None:
            return context.goal
        return template.replace(GOAL_TEXT_PLACEHOLDER, context.goal).strip()

    @staticmethod
    def _render_list(items: List[str]) -> str:
        return "\n".join(f"- {item}" for item in items)
