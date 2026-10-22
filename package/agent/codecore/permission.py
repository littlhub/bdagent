"""工具授权策略引擎，对应 domain/permission/ToolPermissionPolicyEngine.kt。

评估顺序（shell 类）：
  0) 灾难性删除防护（根目录/系统目录/工作区根/家目录/tmp）→ DENY（带 elevate 则降级为 ASK）
  1) 任一段命中 DENY 规则 → DENY（可覆盖内置白名单）
  2) Shizuku 等高危工具 → 一律 ASK 且不可记忆
  3) 命令不可静态判定 → ASK 且不可记忆
  4) 所有段命中内置安全白名单 → ALLOW
  5) 所有段命中已记忆的 ALLOW 规则 → ALLOW
  6) 否则 → ASK，可记忆前缀为程序名或「程序名+子命令」
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from . import safe_commands, shell_parser
from .messages import AgentMode

SHIZUKU_TOOL = "Shizuku"
TERMINAL_TOOL = "terminal"
TERMINAL_SHELL_ACTION = "start"
TASK_TOOL = "task"
BROWSER_TOOL = "browser"
ELEVATE_ARG = "elevate"

SHELL_TOOLS = {"Bash", SHIZUKU_TOOL}
NON_REMEMBERABLE_CAPABILITIES = {"MODIFY_AGENT_CONFIG", "MODIFY_CONTAINER_ENV"}
TASK_AUTO_ACTIONS = {"read", "list", "send"}
BROWSER_READ_ONLY_ACTIONS = {
    "getText", "getHtml", "getBackbone", "screenshot", "console", "wait", "listTabs",
}
DANGEROUS_CAPABILITIES = {
    "WRITE_WORKSPACE", "EXECUTE_COMMANDS", "NETWORK_WRITE",
    "MODIFY_AGENT_CONFIG", "MODIFY_CONTAINER_ENV", "EXTERNAL_TOOL",
}
SAFE_PLAN_MODE_TOOLS = {"list", "search"}
DANGEROUS_TOOLS = {"writeFile", "editFile", "Bash"}

HOME_DIR = "/root"
HOME_TOKEN = "$HOME"
HOME_BRACED = "${HOME}"

REASON_RM_ROOT = "安全防护：禁止执行高危删除操作（根目录删除）"
REASON_RM_RELATIVE = "安全防护：禁止执行高危删除操作（全局或相对路径通配删除）"
REASON_RM_HOME = "安全防护：禁止执行高危删除操作（用户目录删除）"
REASON_RM_WORKSPACE = "安全防护：禁止执行高危删除操作（工作区根目录删除）"
REASON_RM_TMP = "安全防护：禁止执行高危删除操作（系统临时目录整体删除）"
REASON_RM_SYSTEM_DIR = "安全防护：禁止执行高危删除操作（系统关键目录删除）"
REASON_UNANALYZABLE_DESTRUCTIVE = (
    "安全防护：命令含命令替换、子 shell 或绝对路径重定向等无法静态判定的构造，"
    "且疑似破坏性操作，无法确认安全"
)
REASON_SHIZUKU = "Shizuku 直接以 adb shell 身份操作宿主 Android 系统，权限高危，仅支持单次放行，不可记忆"
REASON_PLAN_MODE = (
    "当前处于 PLAN（计划）模式，系统物理沙盒已禁止修改系统状态或执行写操作。"
    "请在计划模式下仅调用只读工具探索代码，不要尝试修改文件或执行命令。"
)

RELATIVE_WILDCARD_TARGETS = {"*", ".*", ".", "..", "../*", "../.*"}
ROOT_LEVEL_GLOB = re.compile(r"^/[^/*?]*[*?]")
WORKSPACE_ROOT_TARGETS = {"workspace", "workspace/*", f"{HOME_DIR}/workspace", f"{HOME_DIR}/workspace/*"}
PROTECTED_SYSTEM_DIRS = {
    "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/lost+found",
    "/media", "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/usr", "/var",
}
DESTRUCTIVE_PROGRAM = re.compile(
    r"(^|[\s;&|()`<>])(rm|dd|shred|truncate|mkfs(?:\.[a-z0-9]+)?|wipefs|fdisk|sfdisk|parted|mkswap|blkdiscard)([\s;&|()`<>]|$)"
)

WHOLE_TOOL = "*"


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ASK = "ASK"


class PermissionDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class PermissionScope(str, Enum):
    PROJECT = "PROJECT"
    GLOBAL = "GLOBAL"


@dataclass
class PermissionRule:
    tool_name: str
    pattern: str
    decision: PermissionDecision = PermissionDecision.ALLOW

    @staticmethod
    def from_compact(compact: str, decision: PermissionDecision = PermissionDecision.ALLOW) -> "PermissionRule":
        """`Bash` → tool=Bash pattern=*；`Bash(git pull)` → tool=Bash pattern="git pull"。"""
        paren = compact.find("(")
        if paren >= 0 and compact.endswith(")"):
            return PermissionRule(compact[:paren], compact[paren + 1:-1], decision)
        return PermissionRule(compact, WHOLE_TOOL, decision)

    def to_compact(self) -> str:
        return self.tool_name if self.pattern == WHOLE_TOOL else f"{self.tool_name}({self.pattern})"


@dataclass
class EvalResult:
    verdict: Verdict
    rememberable_patterns: List[str] = field(default_factory=list)
    deny_reason: Optional[str] = None
    remember_disabled_reason: Optional[str] = None
    ask_title: Optional[str] = None


def _arg_str(args: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str):
            return value
    return None


def normalize_path(raw: str) -> str:
    """词法归一化：展开 ~ / $HOME，折叠重复 /，解析 . 与 ..（不访问文件系统）。"""
    if raw == "~":
        expanded = HOME_DIR
    elif raw.startswith("~/"):
        expanded = HOME_DIR + raw[1:]
    elif raw in (HOME_TOKEN, HOME_BRACED):
        expanded = HOME_DIR
    elif raw.startswith(HOME_TOKEN + "/"):
        expanded = HOME_DIR + raw[len(HOME_TOKEN):]
    elif raw.startswith(HOME_BRACED + "/"):
        expanded = HOME_DIR + raw[len(HOME_BRACED):]
    else:
        expanded = raw

    absolute = expanded.startswith("/")
    parts: List[str] = []
    for part in expanded.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not absolute:
                parts.append("..")
        else:
            parts.append(part)
    joined = "/".join(parts)
    if absolute:
        return "/" + joined
    return joined or "."


def catastrophic_reason_for(raw_path: str) -> Optional[str]:
    raw = raw_path.strip()
    if not raw:
        return None
    path = normalize_path(raw)
    if path in ("/", "/*"):
        return REASON_RM_ROOT
    if path in RELATIVE_WILDCARD_TARGETS:
        return REASON_RM_RELATIVE
    if ROOT_LEVEL_GLOB.match(path):
        return REASON_RM_SYSTEM_DIR
    if path in (HOME_DIR, f"{HOME_DIR}/*"):
        return REASON_RM_HOME
    if path in WORKSPACE_ROOT_TARGETS:
        return REASON_RM_WORKSPACE
    # 工作区子目录（构建产物等）属正常操作，置于系统目录判定之前放行
    if path.startswith(f"{HOME_DIR}/workspace/") or path.startswith("workspace/"):
        return None
    if path in ("/tmp", "/tmp/*"):
        return REASON_RM_TMP
    for protected in PROTECTED_SYSTEM_DIRS:
        if path == protected or path.startswith(protected + "/"):
            return REASON_RM_SYSTEM_DIR
    return None


def check_catastrophic_rm(segments: Sequence[List[str]]) -> Optional[str]:
    for segment in segments:
        rm_info = shell_parser.parse_rm_info(segment)
        if not rm_info.is_rm:
            continue
        for raw_path in rm_info.target_paths:
            reason = catastrophic_reason_for(raw_path)
            if reason:
                return reason
    return None


def looks_destructive(command: str) -> bool:
    return ">" in command or "-delete" in command or bool(DESTRUCTIVE_PROGRAM.search(command))


class ToolPermissionPolicyEngine:
    def __init__(
        self,
        rules: Optional[Sequence[PermissionRule]] = None,
        safety_interception_disabled: bool = False,
    ):
        self._rules: List[PermissionRule] = list(rules or [])
        self._safety_disabled = safety_interception_disabled

    def set_rules(self, rules: Sequence[PermissionRule]) -> None:
        self._rules = list(rules)

    def set_safety_interception_disabled(self, disabled: bool) -> None:
        self._safety_disabled = disabled

    def remember(self, tool_name: str, patterns: Sequence[str], scope: PermissionScope = PermissionScope.PROJECT) -> None:
        for pattern in dict.fromkeys(patterns):
            rule = PermissionRule(tool_name, pattern, PermissionDecision.ALLOW)
            if rule not in self._rules:
                self._rules.append(rule)

    # ── 主入口 ────────────────────────────────────────────────────────────

    def evaluate(
        self,
        tool_name: str,
        args: Dict[str, Any],
        mode: AgentMode,
        capabilities: Optional[Sequence[str]] = None,
    ) -> EvalResult:
        caps = set(capabilities or ())

        if mode is AgentMode.PLAN and self._is_dangerous_tool(tool_name, args, caps):
            return EvalResult(Verdict.DENY, deny_reason=REASON_PLAN_MODE)

        if mode is AgentMode.AUTO:
            if not self._safety_disabled and self._is_shell_tool(tool_name, args):
                command = _arg_str(args, "command", "input")
                if command is not None:
                    analysis = shell_parser.analyze(command)
                    reason = check_catastrophic_rm(analysis.segments)
                    if reason:
                        return self._elevation_or_deny(reason, args)
                    if not analysis.analyzable and looks_destructive(command):
                        return self._elevation_or_deny(REASON_UNANALYZABLE_DESTRUCTIVE, args)
            if tool_name == SHIZUKU_TOOL and not self._safety_disabled:
                return EvalResult(Verdict.ASK, remember_disabled_reason=REASON_SHIZUKU)
            return EvalResult(Verdict.ALLOW)

        if caps == {"READ_AGENT_CONFIG"}:
            return EvalResult(Verdict.ALLOW)

        if tool_name == TASK_TOOL:
            action = (_arg_str(args, "action") or "create").strip().lower()
            if action in TASK_AUTO_ACTIONS:
                whole = [r for r in self._rules if r.tool_name == tool_name and r.pattern == WHOLE_TOOL]
                if any(r.decision is PermissionDecision.DENY for r in whole):
                    return EvalResult(Verdict.DENY, deny_reason="该工具被项目权限规则策略禁止执行")
                return EvalResult(Verdict.ALLOW)

        rules = [r for r in self._rules if r.tool_name == tool_name]
        if self._is_shell_tool(tool_name, args):
            return self._evaluate_shell(rules, args, force_ask=tool_name == SHIZUKU_TOOL)
        return self._evaluate_generic(rules, caps)

    # ── 分支判定 ──────────────────────────────────────────────────────────

    def _is_dangerous_tool(self, tool_name: str, args: Dict[str, Any], caps: set[str]) -> bool:
        if caps & DANGEROUS_CAPABILITIES:
            return True
        if tool_name in SAFE_PLAN_MODE_TOOLS:
            return False
        if tool_name in DANGEROUS_TOOLS:
            return True
        if tool_name == TERMINAL_TOOL:
            action = (_arg_str(args, "action") or "").strip().lower()
            return action != "read"
        if tool_name == BROWSER_TOOL:
            action = (_arg_str(args, "action") or "").strip()
            return action not in BROWSER_READ_ONLY_ACTIONS
        return False

    def _is_shell_tool(self, tool_name: str, args: Dict[str, Any]) -> bool:
        if tool_name in SHELL_TOOLS:
            return True
        if tool_name == TERMINAL_TOOL:
            action = (_arg_str(args, "action") or "").strip().lower()
            return action in (TERMINAL_SHELL_ACTION, "send")
        return False

    @staticmethod
    def _is_elevation_requested(args: Dict[str, Any]) -> bool:
        value = args.get(ELEVATE_ARG)
        return str(value).strip().lower() == "true" if value is not None else False

    def _elevation_or_deny(self, catastrophic_reason: str, args: Dict[str, Any]) -> EvalResult:
        """携带提权参数时降级为一次性授权，否则硬拒绝并提示可提权重试。"""
        if self._is_elevation_requested(args):
            return EvalResult(
                Verdict.ASK,
                ask_title="高危操作提权确认",
                remember_disabled_reason="命令命中内置安全防护，提权仅支持单次放行，不可记忆",
            )
        return EvalResult(
            Verdict.DENY,
            deny_reason=f"{catastrophic_reason}。如确需执行，可在调用时加 `{ELEVATE_ARG}: true` 重试，系统将向用户请求授权。",
        )

    def _evaluate_generic(self, rules: Sequence[PermissionRule], caps: set[str]) -> EvalResult:
        whole = [r for r in rules if r.pattern == WHOLE_TOOL]
        if any(r.decision is PermissionDecision.DENY for r in whole):
            return EvalResult(Verdict.DENY, deny_reason="该工具被项目权限规则策略禁止执行")
        if any(r.decision is PermissionDecision.ALLOW for r in whole):
            return EvalResult(Verdict.ALLOW)
        if caps & NON_REMEMBERABLE_CAPABILITIES:
            return EvalResult(
                Verdict.ASK,
                remember_disabled_reason=(
                    "该工具会修改 Agent 配置、容器环境或调用外部动态工具，"
                    "为降低误授权风险，仅支持单次放行"
                ),
            )
        return EvalResult(Verdict.ASK, [WHOLE_TOOL])

    def _evaluate_shell(
        self,
        rules: Sequence[PermissionRule],
        args: Dict[str, Any],
        force_ask: bool = False,
    ) -> EvalResult:
        command = _arg_str(args, "command", "input")
        if command is None:
            return EvalResult(Verdict.ASK)

        analysis = shell_parser.analyze(command)
        allow = [r for r in rules if r.decision is PermissionDecision.ALLOW]
        deny = [r for r in rules if r.decision is PermissionDecision.DENY]

        # 0) 灾难性删除防护
        catastrophic = check_catastrophic_rm(analysis.segments)
        if catastrophic:
            return self._elevation_or_deny(catastrophic, args)

        # 1) DENY 优先（含覆盖内置白名单）
        for segment in analysis.segments:
            if any(shell_parser.matches(rule.pattern, segment) for rule in deny):
                return EvalResult(Verdict.DENY, deny_reason="该命令被项目权限规则策略禁止执行")

        # 2) Shizuku 等高危工具一律弹窗且不可记忆
        if force_ask:
            return EvalResult(Verdict.ASK, remember_disabled_reason=REASON_SHIZUKU)

        # 3) 不可静态判定 → 必须弹窗且不可记忆
        if not analysis.analyzable:
            return EvalResult(Verdict.ASK)

        # 4) 内置安全白名单
        if analysis.segments and all(safe_commands.is_safe(seg) for seg in analysis.segments):
            return EvalResult(Verdict.ALLOW)

        # 5) 已记忆的 ALLOW（rm 需精细校验，避免宽泛的 "rm" 规则放行递归删除）
        if analysis.segments and all(
            self._segment_allowed(seg, allow) for seg in analysis.segments
        ):
            return EvalResult(Verdict.ALLOW)

        # 6) 弹窗；rm 高风险删除不可记忆
        for segment in analysis.segments:
            rm_info = shell_parser.parse_rm_info(segment)
            if rm_info.is_rm and (not rm_info.target_paths or rm_info.is_recursive or rm_info.is_wildcard):
                return EvalResult(
                    Verdict.ASK,
                    remember_disabled_reason=(
                        "高风险删除操作（递归/通配/强制批量删除），为确保数据安全不可记忆，仅可单次放行"
                    ),
                )

        rememberable = list(
            dict.fromkeys(
                p for p in (shell_parser.rememberable_prefix(seg) for seg in analysis.segments) if p
            )
        )
        return EvalResult(Verdict.ASK, rememberable)

    @staticmethod
    def _segment_allowed(segment: List[str], allow: Sequence[PermissionRule]) -> bool:
        rm_info = shell_parser.parse_rm_info(segment)
        for rule in allow:
            if not shell_parser.matches(rule.pattern, segment):
                continue
            if rm_info.is_rm:
                rule_rm = shell_parser.parse_rm_info(re.split(r"\s+", rule.pattern))
                if not rule_rm.target_paths:
                    continue
                if rm_info.is_recursive and not rule_rm.is_recursive:
                    continue
                return True
            return True
        return False


def load_permission_rules(path: str) -> List[PermissionRule]:
    """读取 `.aicode/permissions.json`，格式见 PermissionModels.kt 的 PermissionFile。"""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    section = data.get("permissions") or {}
    rules: List[PermissionRule] = []
    for compact in section.get("allow") or []:
        rules.append(PermissionRule.from_compact(compact, PermissionDecision.ALLOW))
    for compact in section.get("deny") or []:
        rules.append(PermissionRule.from_compact(compact, PermissionDecision.DENY))
    return rules
