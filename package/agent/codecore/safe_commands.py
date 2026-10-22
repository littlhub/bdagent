"""内置安全白名单，对应 domain/permission/BuiltInSafeCommands.kt。

只收录「无论如何传参都只读」的命令。任何能写文件、改状态、联网或派生执行子进程的命令一律不收：
- 派生执行类绝不收：node/python/sh/ruby/sh -c/xargs/find -exec/env CMD/source/eval/sed -i/awk system()
- 可写命令不收：find/sed/awk/tee/cp/mv/rm/ln/mkdir/rmdir/chmod/chown/touch/dd
- 网络类不收：curl/wget/ssh/scp/rsync
- 泄密/可派生不收：env/printenv
- git 仅收「任意参数都只读」的子命令

匹配走 token 前缀语义：`git status` 命中 `git status -s`，但不命中 `git push`。
"""
from __future__ import annotations

from typing import List

from .shell_parser import matches

GIT_READ_ONLY_PREFIXES: List[str] = [
    "git status", "git log", "git diff", "git show", "git blame",
    "git rev-parse", "git ls-files", "git describe", "git for-each-ref",
    "git cat-file", "git ls-tree", "git config --get",
    "git stash list", "git remote -v",
]

SAFE_PREFIXES: List[str] = [
    # 目录/文件元信息查看
    "ls", "pwd", "tree", "stat", "file", "du", "df",
    "basename", "dirname", "realpath", "readlink", "lsof",
    # 文件内容查看
    "cat", "head", "tail", "less", "more", "wc", "nl", "tac", "paste", "column",
    # 搜索
    "grep", "egrep", "fgrep", "rg", "ack", "zgrep",
    # 文本处理（不含 sed/awk，二者可 -i / system 改文件）
    "sort", "uniq", "cut", "tr", "fold", "fmt", "expand", "unexpand", "rev", "comm", "diff", "cmp",
    # 信息/查找/系统监视（不含 env/printenv）
    "echo", "printf", "date", "whoami", "id", "uname", "hostname", "uptime", "nproc", "cal",
    "which", "type", "whereis", "locate", "getconf",
    "ps", "top", "free", "htop", "vmstat", "lscpu", "lsblk", "arch", "clear", "history", "true", "false",
] + GIT_READ_ONLY_PREFIXES


def is_safe(segment_tokens: List[str]) -> bool:
    return any(matches(prefix, segment_tokens) for prefix in SAFE_PREFIXES)
