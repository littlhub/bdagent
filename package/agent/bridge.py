"""codecore 适配层：把 AiCode 内核接到 dbagent 的工具、配置与输出上。

codecore 是异步状态机、不设轮数上限；dbagent 主循环是同步线程。本模块负责：
  · 把 dbagent 的 7 个工具包成 codecore 的 AgentTool
  · 用 dbagent 的 key/base/model 构造 provider
  · 把事件流转成 dbagent 的 on_event(kind, text)
  · 按任务难度自动选 PLAN / BUILD / GOAL
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import loop as agent_loop
from .codecore.context import ContextCompactor, ModelMetadata
from .codecore.messages import AgentContext, AgentMode
from .codecore.permission import ToolPermissionPolicyEngine
from .codecore.providers import OpenAIProvider
from .codecore.workflow import StatefulAgentWorkflow, error, success
from infra import project_memory

# 长程保险丝：codecore 本身不设轮数上限，靠这个防止死循环烧 token
MAX_TOOL_CALLS = 200

READ_ONLY_TOOLS = ("read", "grep", "find", "ls", "use_skill")
ALL_TOOLS = ("read", "write", "edit", "bash", "grep", "find", "ls",
             "use_skill", "record_fix")

# 代码编写期间可命中调用的技能：
#   · workspace/skills 下只放代码向的白名单（其余是图片/天气/新闻等，不属代码）
#   · codecore/codeskills 下全部收录（用户专门放进来的代码技能）
_WORKSPACE_SKILLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "workspace", "skills")
_CODESKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "codecore", "codeskills")
_WORKSPACE_CODE_ALLOW = ("code-reviewer", "text-editor")
_SKIP_DIRS = {"__MACOSX", "__pycache__", "scripts", "references"}


def _discover_code_skills() -> dict:
    """扫描可用代码技能，返回 {技能名: 目录}。"""
    found = {}
    for name in _WORKSPACE_CODE_ALLOW:
        d = os.path.join(_WORKSPACE_SKILLS_DIR, name)
        if os.path.isfile(os.path.join(d, "SKILL.md")):
            found[name] = d
    for dirpath, dirs, files in os.walk(_CODESKILLS_DIR):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        if "SKILL.md" not in files:
            continue
        name = os.path.basename(dirpath)
        found.setdefault(name, dirpath)
    return found


_TEXT_EDITOR_SCRIPT = os.path.join(
    _WORKSPACE_SKILLS_DIR, "text-editor", "scripts", "text_editor.py")
_TEXT_EDIT_ACTIONS = ("read", "write", "append", "replace", "insert", "delete", "info")

_TOOL_CAPS = {
    "read": ("READ_WORKSPACE",),
    "ls": ("READ_WORKSPACE",),
    "grep": ("READ_WORKSPACE",),
    "find": ("READ_WORKSPACE",),
    "write": ("WRITE_WORKSPACE",),
    "edit": ("WRITE_WORKSPACE",),
    "bash": ("EXECUTE_COMMANDS",),
    "use_skill": (),
    "record_fix": (),
}

_TOOL_SCHEMAS = {
    "read": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径，相对工作目录"},
            "offset": {"type": "integer", "description": "起始行号（从 1 计）"},
            "limit": {"type": "integer", "description": "读取行数"},
        },
        "required": ["path"],
    },
    "write": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径，相对工作目录"},
            "content": {"type": "string",
                        "description": ("文件内容。单次不得超过 8000 字符（约 100 行）；"
                                        "大文件先写开头，后续用 append=true 分段追加")},
            "append": {"type": "boolean", "description": "true 时追加到末尾"},
        },
        "required": ["path", "content"],
    },
    "edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_text": {"type": "string", "description": "要替换的原文，须唯一匹配"},
            "new_text": {"type": "string", "description": "替换后的内容"},
        },
        "required": ["path", "old_text", "new_text"],
    },
    "bash": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数"},
        },
        "required": ["command"],
    },
    "use_skill": {
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "enum": ["code-reviewer", "text-editor"],
                     "description": "要调用的技能名"},
            "args": {"type": "object",
                     "description": ("技能参数。text-editor 传 "
                                     "action/path/text/old/new/line/start/end/all"
                                     "（action: read/write/append/replace/insert/delete/info）；"
                                     "code-reviewer 无需参数，调用后按返回的规范审查代码")},
        },
        "required": ["name"],
    },
    "record_fix": {
        "type": "object",
        "properties": {
            "problem": {"type": "string", "description": "问题的一句话描述"},
            "fix": {"type": "string", "description": "怎么修的、涉及哪些文件"},
        },
        "required": ["problem", "fix"],
    },
    "grep": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "搜索目录或文件"},
            "ignore_case": {"type": "boolean", "description": "忽略大小写"},
        },
        "required": ["pattern"],
    },
    "find": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "文件名包含的关键字"},
            "suffix": {"type": "string", "description": "后缀，如 .py"},
        },
    },
    "ls": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "目录路径"}},
    },
}

_TOOL_DESCS = {
    "read": "读取文件内容，带行号",
    "write": "新建或整文件覆盖，自动创建父目录",
    "edit": "按精确字符串替换文件片段，old_text 须唯一匹配",
    "bash": "在工作目录执行 shell 命令",
    "grep": "按正则搜索文件内容",
    "find": "按名称或后缀查找文件",
    "ls": "列目录",
    "use_skill": "调用代码相关技能：code-reviewer（审查规范）、text-editor（按行编辑）",
    "record_fix": "记录一条「问题→修复」到 .dbagent/fixes.md，供后续对话参考",
}


@dataclass
class DbagentTool:
    """把 dbagent 的同步工具函数包成 codecore 的 AgentTool。"""

    name: str
    runner: Callable[[dict], dict]
    schema_override: Optional[dict] = None

    @property
    def description(self) -> str:
        return _TOOL_DESCS.get(self.name, self.name)

    @property
    def capabilities(self) -> frozenset:
        return frozenset(_TOOL_CAPS.get(self.name, ()))

    def to_json_schema(self) -> dict:
        if self.schema_override is not None:
            return self.schema_override
        return _TOOL_SCHEMAS.get(self.name, {"type": "object", "properties": {}})

    def execute(self, args: dict) -> dict:
        # 参数 JSON 不完整时，provider 会把这个键塞进来：把原因回给模型，
        # 否则它只会看到「缺少参数」而不知道是输出被截断
        if isinstance(args, dict) and args.get("__args_error__"):
            return error(str(args["__args_error__"]), "INVALID_ARGS")
        try:
            payload = self.runner(args or {})
        except Exception as exc:
            return error(f"{type(exc).__name__}: {exc}", "TOOL_EXCEPTION")
        if isinstance(payload, dict) and payload.get("ok"):
            return success(payload)
        msg = payload.get("error") if isinstance(payload, dict) else payload
        return error(str(msg or "执行失败"), "TOOL_FAILED")


def _runner_for(name: str):
    return {
        "read": agent_loop._read_direct,
        "write": agent_loop._write_direct,
        "edit": agent_loop._edit_direct,
        "bash": agent_loop._bash_direct,
        "grep": agent_loop._grep_direct,
        "find": agent_loop._find_direct,
        "ls": agent_loop._ls_direct,
    }[name]


def _text_edit_direct(root: str, params: dict) -> dict:
    """调用 text-editor 技能脚本，工作目录固定为 root。"""
    action = str(params.get("action") or "").strip()
    path = str(params.get("path") or "").strip()
    if action not in _TEXT_EDIT_ACTIONS:
        return {"ok": False, "error": f"action 必须是 {', '.join(_TEXT_EDIT_ACTIONS)}"}
    if not path:
        return {"ok": False, "error": "text_edit 缺少 path 参数"}
    if not os.path.isfile(_TEXT_EDITOR_SCRIPT):
        return {"ok": False, "error": f"找不到 text-editor 脚本：{_TEXT_EDITOR_SCRIPT}"}

    argv = [action, path]
    if action == "write" or action == "append":
        argv.append(str(params.get("text") or ""))
    elif action == "replace":
        argv += [str(params.get("old") or ""), str(params.get("new") or "")]
        if params.get("all"):
            argv.append("--all")
    elif action == "insert":
        if params.get("line") is None:
            return {"ok": False, "error": "insert 缺少 line 参数"}
        argv += ["--line", str(params["line"]), str(params.get("text") or "")]
    elif action == "delete":
        for key in ("start", "end"):
            if params.get(key) is not None:
                argv += [f"--{key}", str(params[key])]
    elif action in ("read", "info"):
        for key in ("start", "end"):
            if params.get(key) is not None:
                argv += [f"--{key}", str(params[key])]

    try:
        proc = subprocess.run(
            [sys.executable, _TEXT_EDITOR_SCRIPT] + argv,
            cwd=root, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "text-editor 执行超时（60s）"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    out = (proc.stdout or "").strip()
    try:
        payload = json.loads(out) if out else {}
    except json.JSONDecodeError:
        payload = {"ok": proc.returncode == 0, "raw": out[:500],
                   "stderr": (proc.stderr or "")[:300]}
    if not payload.get("ok"):
        msg = payload.get("error") or payload.get("stderr") or (proc.stderr or "")
        return {"ok": False, "error": str(msg)[:300] or "text-editor 执行失败"}
    return {"ok": True, "action": action, "path": path,
            "result": payload.get("content") or payload,
            "chars": len(str(payload.get("content") or ""))}


def _use_skill_direct(root: str, params: dict, mode: str = "code") -> dict:
    """命中调用一个代码相关技能。

    有脚本的技能直接执行（text-editor）；其余把 SKILL.md 与目录位置交回模型，
    由它按规范继续，必要时用 bash 跑技能自带脚本。
    """
    name = str(params.get("name") or "").strip()
    skills = _discover_code_skills()
    if name not in skills:
        return {"ok": False,
                "error": f"未知技能 {name!r}。可用：{', '.join(sorted(skills))}"}
    skill_dir = skills[name]
    args = params.get("args") if isinstance(params.get("args"), dict) else {}

    if name == "text-editor":
        if mode == "plan":
            return {"ok": False, "error": "PLAN 阶段不能调用写文件类技能"}
        return _text_edit_direct(root, args)

    md_path = os.path.join(skill_dir, "SKILL.md")
    try:
        with open(md_path, encoding="utf-8") as f:
            body = f.read()
    except OSError as exc:
        return {"ok": False, "error": f"读取技能说明失败：{exc}"}

    scripts = os.path.join(skill_dir, "scripts")
    hint = f"技能目录：{skill_dir}"
    if os.path.isdir(scripts):
        hint += f"\n自带脚本位于：{scripts}（需要时用 bash 调用，参数见上方说明）"
    return {"ok": True, "skill": name, "guide": body[:6000],
            "note": ("这是技能规范，请按它继续当前工作，不要把它当最终结果返回给用户。\n"
                     + hint)}


# 单次 write 的字符上限。模型一次输出几万字符会撞 max_tokens，
# 参数 JSON 被截断后变成「缺少 path」，整轮白跑。
MAX_WRITE_CHARS = 8000


def _write_limited(root: str, params: dict) -> dict:
    """带分段保护的 write：超长就退回给模型让它分次写。"""
    content = params.get("content")
    content = "" if content is None else str(content)
    if len(content) > MAX_WRITE_CHARS and not params.get("append"):
        return {
            "ok": False,
            "error": (f"内容过长（{len(content)} 字符，上限 {MAX_WRITE_CHARS}）。"
                      "请拆成多次：先 write 写第一部分，其余用 "
                      "write 带 append=true 分段追加。"),
        }
    return agent_loop._write_direct(root, params)


def _record_fix_direct(root: str, params: dict) -> dict:
    """把一条「问题→修复」记到 .dbagent/fixes.md。"""
    problem = str(params.get("problem") or "").strip()
    fix = str(params.get("fix") or "").strip()
    if not problem or not fix:
        return {"ok": False, "error": "record_fix 需要 problem 与 fix"}
    path = project_memory.append_fix(root, problem, fix)
    if not path:
        return {"ok": False, "error": "写入 fixes.md 失败"}
    return {"ok": True, "path": os.path.relpath(path, root)}


def build_tools(root: str, writable: bool, mode: str = "code"):
    names = ALL_TOOLS if writable else READ_ONLY_TOOLS
    skills = sorted(_discover_code_skills())
    use_skill_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": skills,
                     "description": "要调用的代码技能名"},
            "args": {"type": "object",
                     "description": ("技能参数。text-editor 需传 "
                                     "action/path/text/old/new/line/start/end/all；"
                                     "其它技能无需参数，调用后会返回其 SKILL.md 规范")},
        },
        "required": ["name"],
    }
    tools = []
    for name in names:
        if name == "use_skill":
            fn = lambda p, _r=root, _m=mode: _use_skill_direct(_r, p, _m)
            tools.append(DbagentTool(name=name, runner=fn,
                                     schema_override=use_skill_schema))
            continue
        if name == "record_fix":
            fn = lambda p, _r=root: _record_fix_direct(_r, p)
            tools.append(DbagentTool(name=name, runner=fn))
            continue
        base = _write_limited if name == "write" else _runner_for(name)
        fn = lambda p, _fn=base, _r=root: _fn(_r, p)
        tools.append(DbagentTool(name=name, runner=fn))
    return tools


# ── 按难度选模式 ────────────────────────────────────────────────────────────

_GOAL_HINTS = ("修复", "修好", "搞定", "解决", "通过测试", "测试通过", "全部通过",
               "跑通", "能跑", "跑不起来", "正常运行", "报错", "不再报错", "排查")

_PLAN_HINTS = ("重构", "重新设计", "架构", "大规模", "批量", "全面", "系统性地",
               "整体", "拆分", "迁移", "升级", "先给我方案", "先规划", "计划一下")

# 多个步骤/多个对象：这类需求往往要动多处，先出方案更稳
_MULTI_HINTS = ("并且", "同时", "然后", "接着", "以及", "分别", "各个", "所有",
                "全部", "整个", "多个", "多处")

# 开发类需求：思考等级 5 时这些一律先进 PLAN
_DEV_HINTS = ("开发", "实现", "新建", "创建", "搭建", "编写", "重构",
              "游戏", "项目", "程序", "应用", "网站", "系统")


def pick_mode(req: str, think_level: int = 3) -> str:
    """按任务难度返回 plan / goal / code。

    goal 是「要达到某状态」的任务，需循环试错；
    plan 是涉及面广的任务，先看清再动手；
    其余直接 code，避免简单改动也要等一轮方案确认。

    思考等级会抬高门槛：等级 5 时开发类需求一律先规划，
    等级 4 时较长的需求先规划。
    """
    low = (req or "").lower()
    if any(w in low for w in _GOAL_HINTS):
        return "goal"
    if any(w in low for w in _PLAN_HINTS):
        return "plan"
    # 思考等级高：用户要求先想清楚再动手
    try:
        level = int(think_level or 3)
    except (TypeError, ValueError):
        level = 3
    if level >= 5 and any(w in low for w in _DEV_HINTS):
        return "plan"
    if level >= 4 and len(low) >= 20:
        return "plan"
    # 长需求 + 多步骤信号，通常意味着多文件改动
    if len(low) >= 50 and any(w in low for w in _MULTI_HINTS):
        return "plan"
    if sum(1 for w in _MULTI_HINTS if w in low) >= 2:
        return "plan"
    return "code"


# ── 运行 ────────────────────────────────────────────────────────────────────

def _trim(text: str, limit: int = 1500) -> str:
    """超长时保留头尾，中间标注省略。"""
    s = text or ""
    if len(s) <= limit:
        return s
    head = int(limit * 0.7)
    tail = limit - head
    return f"{s[:head]}\n…（省略 {len(s) - limit} 字符）…\n{s[-tail:]}"


# 思考等级 1-5：控制动手前的规划深度
_THINK_HINTS = {
    1: "尽量少探索，能一步做完就不要拆；不要反复读同一个文件。",
    2: "快速判断后动手，只在必要时读文件。",
    3: "",
    4: "动手前先在心里列出 3-5 步计划，按顺序执行，每步完成后再进入下一步。",
    5: "先一次性规划完整的 5-8 步方案再开始执行，明确每步的输入输出与验证方式；\n"
       "执行中保持计划一致，只在遇到新信息时调整。",
}


def _system_prompt(root: str, mode: str, writable: bool, think_level: int = 3) -> str:
    """codecore 走 tool calling，需要自己的系统提示词。"""
    tools = ALL_TOOLS if writable else READ_ONLY_TOOLS
    base = (
        f"你是 DBAgent 的代码开发代理，在一个真实项目里工作。\n"
        f"工作目录：{root}\n"
        f"可用工具：{', '.join(tools)}\n\n"
        f"规则：\n"
        f"1. 改代码前先 read 看清原文，不要凭猜测写 old_text。\n"
        f"2. edit 的 old_text 必须从文件里逐字抄（含缩进与换行），足够长以保持唯一。\n"
        f"3. 一次只改一处，改完再继续；不要把多件事塞进一个调用。\n"
        f"4. 修改后用 bash 验证（如 python -m py_compile、node --check）。\n"
        f"5. 单次 write 不超过 {MAX_WRITE_CHARS} 字符（约 100 行）：大文件必须拆开写，\n"
        f"   先 write 写文件开头，再用 write + append=true 逐段追加。\n"
        f"6. 写大文件时不要长篇思考：想好文件结构就直接开写，\n"
        f"   把预算留给实际内容。一次想 100 多秒再写必定撞输出上限。\n"
        f"7. 修好一个 bug 后，把「问题→修复」写进 .dbagent/fixes.md，\n"
        f"   格式：## 问题一句话 + 空行 + 修复说明与涉及文件。\n"
    )
    # 项目记忆：结构摘要 + 近期修复记录，让模型不用每次从零探索
    try:
        memory = project_memory.build_context(root)
        if memory:
            base += "\n【项目上下文】\n" + memory + "\n"
    except Exception:
        pass
    if writable or mode == "plan":
        names = sorted(_discover_code_skills())
        base += (
            "\n可调用的代码技能（use_skill）：\n"
            "  " + "、".join(names) + "\n"
            "用法：\n"
            "  · 需要审查/评审代码 → code-review / code-review-* / code-review-and-quality\n"
            "  · 需要前端或全栈指导 → frontend-design / frontend-dev / fullstack-dev\n"
            "  · 需要看清结构 → code-arch（自带 scripts，可按其说明用 bash 跑）\n"
            "  · 需要按行号编辑 → text-editor（args 传 action/path/text/old/new/line…）\n"
            "  其余技能无需参数，调用后会返回其 SKILL.md 规范，按规范继续即可。\n"
        )
    hint = _THINK_HINTS.get(int(think_level or 3), "")
    if hint:
        base += f"\n思考深度：{hint}\n"
    if mode == "plan":
        return base + (
            "\n【当前阶段：PLAN 只读规划】\n"
            "write / edit / bash 会被系统拒绝，不要徒劳尝试。\n"
            "做法：用 read/ls/grep/find 把相关代码看清（总数不要超过 12 次），"
            "然后直接输出方案：\n"
            "  · 涉及哪些文件，每个文件改什么\n"
            "  · 分几步做，先后顺序\n"
            "  · 有哪些风险或不确定点\n"
            "方案输出完就结束本轮，不要继续调工具。\n"
        )
    if not writable:
        return base + "\n当前为只读模式：write 与 edit 不可用，只能读和分析。\n"
    return base


class _PromptProvider:
    """按当前模式给出系统提示词（codecore 每轮开始时构建）。"""

    def __init__(self, root: str, mode: str, writable: bool, think_level: int = 3):
        self._text = _system_prompt(root, mode, writable, think_level)

    def build(self, context) -> str:
        return self._text


def _short_error(raw) -> str:
    """从工具结果的 transport JSON 里取出可读错误。"""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict):
            return str(data.get("message") or data.get("code") or "执行失败")[:120]
    except Exception:
        pass
    return str(raw or "执行失败")[:120]


class _EventMapper:
    """把 codecore 的事件翻成 dbagent 已有的 step/error/done 文本。"""

    def __init__(self, on_event, should_stop=None, max_calls=MAX_TOOL_CALLS):
        self._on_event = on_event
        self._should_stop = should_stop
        self.calls = 0
        self._max_calls = max_calls
        self.done_text = ""
        self.texts = []
        # token 统计：输入/输出/缓存命中，用于结束时一行汇报
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.compactions = 0
        self._llm_started = time.time()
        self.llm_seconds = 0.0

    def emit(self, kind: str, text: str) -> None:
        print(text)
        if self._on_event:
            try:
                self._on_event(kind, text)
            except Exception:
                pass

    def over_budget(self) -> bool:
        return self.calls > self._max_calls

    def handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "tool_call_started":
            self.calls += 1
            preview = (event.get("args_preview") or "").replace("\n", " ")[:100]
            self.emit("step", f"  [{self.calls}] {event.get('tool_name')} {preview}".rstrip())
        elif kind == "tool_call_finished":
            name = event.get("tool_name")
            if event.get("is_error"):
                self.emit("step", f"        ✗ {name}：{_short_error(event.get('result'))}")
            else:
                self.emit("step", f"        ✓ {name}")
        elif kind == "context_usage":
            # estimated=True 是请求发出前的预测，用它当本轮计时起点
            if event.get("estimated"):
                self._llm_started = time.time()
            self.input_tokens = max(self.input_tokens,
                                    int(event.get("tokens") or 0))
            self.cached_tokens = max(self.cached_tokens,
                                     int(event.get("cached_tokens") or 0))
        elif kind == "assistant_text":
            self.output_tokens += int(event.get("output_tokens") or 0)
            elapsed = time.time() - self._llm_started
            self.llm_seconds += elapsed
            reasoning = (event.get("reasoning") or "").strip()
            if reasoning:
                self.emit("step", f"  🧠 思考 {elapsed:.1f}s：{_trim(reasoning, 800)}")
            text = (event.get("content") or "").strip()
            if text:
                self.texts.append(text)
                self.emit("step", f"\n💬 {_trim(text)}")
        elif kind == "compaction":
            self.compactions += 1
            self.emit("step", "  ⚙️ 上下文接近上限，已压缩较早的历史")
        elif kind == "failed":
            self.emit("error", f"❌ {event.get('error')}")
        elif kind == "completed":
            self.done_text = (event.get("summary") or "").strip()

    def token_summary(self) -> str:
        """一行 token 消耗，含缓存命中率与压缩次数。"""
        if not (self.input_tokens or self.output_tokens):
            return ""
        parts = [f"输入 {self.input_tokens:,}", f"输出 {self.output_tokens:,}"]
        if self.cached_tokens:
            pct = self.cached_tokens * 100 // max(self.input_tokens, 1)
            parts.append(f"缓存命中 {self.cached_tokens:,}（{pct}%）")
        if self.compactions:
            parts.append(f"压缩 {self.compactions} 次")
        if self.llm_seconds:
            parts.append(f"模型耗时 {self.llm_seconds:.0f}s")
        return "📊 " + "　".join(parts)


async def _run_async(req, root, key, base, model, mode, writable,
                     mapper: _EventMapper, summarize, think_level=3):
    tools = build_tools(root, writable, mode)
    provider = OpenAIProvider(base_url=base, api_key=key, model=model)
    agent_mode = AgentMode.PLAN if mode == "plan" else AgentMode.AUTO
    async def _allow(call, eval_result):
        # 授权完全由 writable 与 PLAN 拦截决定：能走到这里就是已放行
        return True

    workflow = StatefulAgentWorkflow(
        provider=provider,
        tools=tools,
        policy_engine=ToolPermissionPolicyEngine(),
        compactor=ContextCompactor(summarize=summarize,
                                   threshold_percent=lambda: 80),
        metadata=ModelMetadata(context_tokens=128_000, output_tokens=8_192),
        permission_resolver=_allow,
        system_prompt_provider=_PromptProvider(root, mode, writable, think_level),
    )
    context = AgentContext(project_root=root, mode=agent_mode)

    # PLAN 阶段只是探路，给更紧的上限；执行阶段用长程上限
    call_cap = 30 if mode == "plan" else MAX_TOOL_CALLS

    async for event in workflow.run(req, context):
        mapper.handle(event)
        if mode == "plan" and mapper.calls >= call_cap:
            mapper.emit("step", "\n🧭 已探查足够，停止工具调用，整理方案。")
            break
        if mapper.over_budget():
            mapper.emit("error", f"⚠️ 工具调用已达上限 {mapper._max_calls} 次，停止。")
            break
        if mapper._should_stop and mapper._should_stop():
            mapper.emit("step", "⏹ 已按请求中断。")
            break


# PLAN 阶段给出的方案暂存：用户下一句「按方案执行」时直接用它，
# 否则会被当成新需求重新规划
_PENDING_PLAN = {"plan": "", "req": ""}

# 确认执行的表达：用户手误/简写很常见（「按方案执」「执行吧」），
# 精确匹配太脆，改成片段匹配 + 短词白名单
_CONFIRM_PATTERNS = ("按方案", "执行方案", "确认执行", "开始执行", "就按这个",
                     "按计划", "照这个做", "执行吧")
_CONFIRM_EXACT = ("y", "yes", "继续", "确认", "执行", "好", "可以", "行", "ok")
_NEGATIVE = ("别", "不要", "取消", "算了", "先不", "暂停")


def looks_like_confirm(text) -> bool:
    """是否是在确认执行已给出的方案。"""
    t = (text or "").strip().lower()
    if not t or len(t) > 12:
        return False
    if any(w in t for w in _NEGATIVE):
        return False
    if any(p in t for p in _CONFIRM_PATTERNS):
        return True
    return t in _CONFIRM_EXACT


CONFIRM_WORDS = tuple(_CONFIRM_EXACT) + tuple(_CONFIRM_PATTERNS)


def has_pending_plan() -> bool:
    """是否有一份已给出、等用户确认的方案。"""
    return bool(_PENDING_PLAN["plan"])


def _report_tokens(mapper: _EventMapper) -> None:
    line = mapper.token_summary()
    if line:
        print(line)


def run_long_task(req, root, key, base, model, mode="auto", writable=True,
                  auto_approve=False, think_level=3,
                  on_event=None, should_stop=None, yes=False):
    """用 codecore 执行一次任务。

    mode="auto" 时按任务难度自动选 plan/goal/code。
    返回 True 表示已处理。
    """
    text = (req or "").strip()
    if looks_like_confirm(text) and _PENDING_PLAN["plan"]:
        req = _PENDING_PLAN.pop("plan")
        _PENDING_PLAN["req"] = ""
        mode = "code"
        print("▶️ 按已确认的方案执行。")

    chosen = pick_mode(req, think_level) if mode in (None, "", "auto") else mode
    if chosen not in ("plan", "goal", "code"):
        chosen = "code"

    # 开工前扫一次结构并落盘，同时刷新注入用的上下文摘要
    try:
        tree = agent_loop._scan_tree(root)
        if tree:
            project_memory.save_structure(root, tree)
            print(f"📐 已记录项目结构：{len(tree)} 个 Python 文件 → .dbagent/structure.json")
    except Exception:
        pass

    if chosen == "plan":
        print(f"🧭 任务判定为复杂改动，先进入 PLAN 只读规划。")
    elif chosen == "goal":
        print(f"🎯 任务判定为验收型目标，进入 GOAL 循环。")
    else:
        print(f"⚡ 任务判定为轻量改动，直接执行。")

    def summarize(messages):
        try:
            from main import call_llm
            digest = "\n".join(
                f"{type(m).__name__}: {getattr(m, 'content', '')[:400]}"
                for m in messages[-20:]
            )
            return call_llm([{"role": "user", "content":
                              "把下面的开发过程压缩成要点，保留已完成的改动与未决问题：\n\n"
                              + digest}], key, base, model).strip()
        except Exception:
            return "（历史已压缩）"

    mapper = _EventMapper(on_event, should_stop)
    effective = "plan" if chosen == "plan" else "code"
    writable_now = writable and effective != "plan"

    try:
        asyncio.run(_run_async(req, root, key, base, model, effective,
                               writable_now, mapper, summarize, think_level))
    except Exception as exc:
        mapper.emit("error", f"❌ 内核异常：{type(exc).__name__}: {exc}")
        return True
    _report_tokens(mapper)

    if chosen == "plan":
        plan_text = "\n\n".join(mapper.texts).strip()
        has_plan = bool(plan_text)
        if not has_plan:
            plan_text = "（本轮未产出文字方案，仅完成了代码探查）"
        exec_req = (f"按以下已确认的方案执行，原始需求：{req}\n\n"
                    f"方案：\n{plan_text}")

        if auto_approve and has_plan:
            print("⚙️ 自动审批已开启，直接执行方案。")
        elif auto_approve:
            # 方案为空时不能直接执行，否则等于让模型无计划乱改
            print("⚠️ 模型未产出文字方案，不做自动执行。请重试或换更具体的说法。")
            return True
        elif on_event is not None or threading.current_thread() is not threading.main_thread():
            # 后台线程不能调 input：主线程还停在 input() 上，
            # worker 抢 stdin 会把用户下一条输入当成确认
            _PENDING_PLAN["plan"] = exec_req
            _PENDING_PLAN["req"] = req
            print("\n💡 方案已在上方。确认无误后输入「按方案执行」继续。\n")
            return True
        elif not yes:
            try:
                ans = input("\n是否按以上方案执行？(y/N) ").strip().lower()
            except EOFError:
                ans = "n"
            if ans not in ("y", "yes"):
                print("已取消，未做任何修改。\n")
                return True

        mapper2 = _EventMapper(on_event, should_stop)
        try:
            asyncio.run(_run_async(exec_req, root, key, base, model, "code",
                                   writable, mapper2, summarize, think_level))
        except Exception as exc:
            mapper2.emit("error", f"❌ 执行异常：{type(exc).__name__}: {exc}")
    return True
