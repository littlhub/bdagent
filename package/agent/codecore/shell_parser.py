"""shell 命令静态分析器，对应 domain/permission/ShellCommandParser.kt。

把命令行拆成「顶层段」并分词，同时判断是否可被静态判定。自动放行只在
「可判定 且 每段都命中 ALLOW」时发生；任何无法判定的构造都降级为「必须弹窗、不可记忆」。

安全约定：
- 引号感知：单/双引号内的 && || | ; 不是分隔符。
- 不可静态判定：命令替换 $(...)、反引号、子 shell/分组括号、输出重定向到绝对路径、未闭合引号。
- 匹配按字面首 token：/usr/bin/node 不会被 node 规则命中。
- 段首环境赋值（FOO=bar cmd）在取程序名与匹配时跳过。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

_NONE, _SINGLE, _DOUBLE = 0, 1, 2

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_WHITESPACE = re.compile(r"\s")


@dataclass
class Analysis:
    segments: List[List[str]] = field(default_factory=list)
    analyzable: bool = True


@dataclass
class RmInfo:
    is_rm: bool = False
    is_recursive: bool = False
    is_wildcard: bool = False
    target_paths: List[str] = field(default_factory=list)


def analyze(command: str) -> Analysis:
    segments: List[List[str]] = []
    current: List[str] = []
    token: List[str] = []
    token_started = False
    analyzable = True
    quote = _NONE
    expect_redirect_target = False

    def flush_token() -> None:
        nonlocal token, token_started, expect_redirect_target, analyzable
        if token_started:
            value = "".join(token)
            if expect_redirect_target:
                if value.startswith("/"):
                    analyzable = False
                expect_redirect_target = False
            current.append(value)
            token = []
            token_started = False

    def flush_segment() -> None:
        nonlocal current
        flush_token()
        if current:
            segments.append(current)
            current = []

    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if quote == _SINGLE:
            if c == "'":
                quote = _NONE
            else:
                token.append(c)
                token_started = True
            i += 1
            continue

        if quote == _DOUBLE:
            if c == '"':
                quote = _NONE
                token_started = True
                i += 1
            elif c == "`":
                # 双引号内的命令替换仍会执行
                analyzable = False
                token.append(c)
                token_started = True
                i += 1
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                analyzable = False
                token.append(c)
                token_started = True
                i += 1
            else:
                token.append(c)
                token_started = True
                i += 1
            continue

        # 引号外
        if c == "'":
            quote = _SINGLE
            token_started = True
            i += 1
        elif c == '"':
            quote = _DOUBLE
            token_started = True
            i += 1
        elif c == "\\":
            if i + 1 < n:
                nxt = command[i + 1]
                if nxt != "\n":
                    token.append(nxt)
                    token_started = True
                i += 2
            else:
                i += 1
        elif c in ("`", "(", ")"):
            analyzable = False
            token.append(c)
            token_started = True
            i += 1
        elif c == "\n" or c == ";":
            flush_segment()
            i += 1
        elif c == "&":
            flush_segment()
            i += 2 if i + 1 < n and command[i + 1] == "&" else 1
        elif c == "|":
            flush_segment()
            i += 2 if i + 1 < n and command[i + 1] == "|" else 1
        elif c == ">":
            flush_token()
            i += 2 if i + 1 < n and command[i + 1] == ">" else 1
            expect_redirect_target = True
        elif c == "<":
            flush_token()
            i += 1
        elif _WHITESPACE.match(c):
            flush_token()
            i += 1
        else:
            token.append(c)
            token_started = True
            i += 1

    if quote != _NONE:
        analyzable = False  # 未闭合引号不可信
    flush_segment()
    return Analysis(segments, analyzable)


def effective_tokens(tokens: List[str]) -> List[str]:
    """跳过段首环境赋值后的有效 token。"""
    idx = 0
    while idx < len(tokens) and _ENV_ASSIGN.match(tokens[idx]):
        idx += 1
    return tokens if idx == 0 else tokens[idx:]


def program_prefix(tokens: List[str]) -> Optional[str]:
    eff = effective_tokens(tokens)
    return eff[0] if eff else None


# 已知的子命令分发器：其第一个非 flag 参数是子命令。
# 「始终允许」应连子命令一起记忆，避免仅记 git 就把 git push 一并放行。
SUBCOMMAND_DISPATCHERS = {
    "git", "gh",
    "npm", "npx", "yarn", "pnpm", "pnpx",
    "cargo", "rustup",
    "go",
    "pip", "pip3", "poetry", "uv", "conda",
    "gradle", "mvn", "sbt",
    "docker", "kubectl", "helm",
    "adb", "fastlane",
    "brew", "apt", "apt-get", "gem", "bundle",
    "az", "aws", "gcloud", "terraform", "ansible",
}


def parse_rm_info(tokens: List[str]) -> RmInfo:
    eff = effective_tokens(tokens)
    program = eff[0] if eff else None
    if program != "rm" and not (program or "").endswith("/rm"):
        return RmInfo()
    is_recursive = False
    is_wildcard = False
    target_paths: List[str] = []
    has_double_dash = False
    for i in range(1, len(eff)):
        t = eff[i]
        if not has_double_dash and t == "--":
            has_double_dash = True
            continue
        if not has_double_dash and t.startswith("-") and t != "-":
            if t == "--recursive" or (not t.startswith("--") and ("r" in t or "R" in t)):
                is_recursive = True
            continue
        if "*" in t or "?" in t:
            is_wildcard = True
        target_paths.append(t)
    return RmInfo(True, is_recursive, is_wildcard, target_paths)


def rememberable_prefix(tokens: List[str]) -> Optional[str]:
    """「始终允许」可记忆的命令前缀。"""
    eff = effective_tokens(tokens)
    if not eff:
        return None
    program = eff[0]
    rm_info = parse_rm_info(tokens)
    if rm_info.is_rm:
        # 递归/通配/无目标删除不可记忆
        if not rm_info.target_paths or rm_info.is_recursive or rm_info.is_wildcard:
            return None
        return " ".join(eff)
    if program not in SUBCOMMAND_DISPATCHERS:
        return program
    subcommand = eff[1] if len(eff) > 1 else None
    if subcommand is None or subcommand.startswith("-"):
        return program
    return f"{program} {subcommand}"


def matches(pattern: str, segment_tokens: List[str]) -> bool:
    """token 前缀匹配：规则 pattern 是段有效 token 序列的前缀即命中。"""
    pattern_tokens = [t for t in re.split(r"\s+", pattern.strip()) if t]
    if not pattern_tokens:
        return False
    eff = effective_tokens(segment_tokens)
    if len(eff) < len(pattern_tokens):
        return False
    return all(eff[k] == pattern_tokens[k] for k in range(len(pattern_tokens)))
