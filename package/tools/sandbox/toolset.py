"""沙箱工具集 - read / write / edit / bash / grep / find / ls，全部限制在 data/work 内。

两种执行环境：
- 文件类工具（read/write/edit/ls/grep/find）直接操作宿主上的工作目录；
- bash 在 chroot 沙箱内执行，沙箱里只有工作目录、解释器和 busybox，
  看不到宿主其他文件；执行前后与宿主工作目录双向同步。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

MAX_OUTPUT_CHARS = 20000
SANDBOX_ROOT = "/tmp/dbagent-sandbox"
SANDBOX_WORK = "work"


def _core():
    """加载 runtime/core（与 tools 同属 package 下的共用模块）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    package_dir = os.path.dirname(os.path.dirname(here))
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)
    return package_dir


class ToolError(Exception):
    """工具执行失败。"""


# ==================== 工作目录 ====================
def workspace_dir(explicit=None):
    """工作目录：显式指定 > 环境变量 > <project>/workspace/data/work。"""
    if explicit:
        root = os.path.realpath(explicit)
    elif os.environ.get("DBAGENT_WORKDIR"):
        root = os.path.realpath(os.environ["DBAGENT_WORKDIR"])
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        package_dir = os.path.dirname(os.path.dirname(here))
        root = os.path.realpath(os.path.join(package_dir, "..", "workspace", "data", "work"))
    os.makedirs(root, exist_ok=True)
    return root


def resolve(root, path):
    """把路径解析到工作目录内，越界报错。"""
    if not path:
        raise ToolError("缺少路径参数")
    target = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    if target != root and not target.startswith(root + os.sep):
        raise ToolError(f"路径越界，只能操作工作目录内的文件: {path}")
    return target


def rel(root, path):
    return os.path.relpath(path, root)


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def write_text(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def clamp(text):
    """超过上限时按行/字节截断，尽量保留可读边界。"""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    _core()
    from runtime.core.truncate import truncate_head
    result = truncate_head(text, max_bytes=MAX_OUTPUT_CHARS)
    return result.content + f"\n...(已截断，共 {len(text)} 字符)"


# ==================== 沙箱 ====================
def ensure_sandbox():
    """确保 chroot 沙箱存在，返回沙箱根。"""
    here = os.path.dirname(os.path.abspath(__file__))
    package_dir = os.path.dirname(os.path.dirname(here))
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)
    import skill_exec
    return skill_exec.ensure_sandbox()


def sync_into_sandbox(root):
    """把宿主工作目录同步进沙箱，使 bash 看到最新文件。"""
    target = os.path.join(SANDBOX_ROOT, SANDBOX_WORK)
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    for name in os.listdir(root):
        src = os.path.join(root, name)
        dst = os.path.join(target, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def sync_from_sandbox(root):
    """把沙箱内 bash 产生的文件同步回宿主工作目录。"""
    src = os.path.join(SANDBOX_ROOT, SANDBOX_WORK)
    if not os.path.isdir(src):
        return []
    names = []
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(root, name)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
        names.append(name)
    return names


# ==================== 子命令 ====================
def cmd_read(root, args):
    path = resolve(root, args.path)
    if not os.path.isfile(path):
        raise ToolError(f"文件不存在: {args.path}")
    lines = read_text(path).splitlines()
    total = len(lines)
    start = max(1, args.start or 1)
    if start > total:
        raise ToolError(f"起始行 {start} 超出范围（共 {total} 行）")
    end = min(args.end if args.end is not None else total, total)
    return {
        "ok": True,
        "path": rel(root, path),
        "start": start,
        "end": end,
        "total_lines": total,
        "content": clamp("\n".join(lines[start - 1:end])),
    }


def cmd_write(root, args):
    path = resolve(root, args.path)
    content = args.content
    if args.from_file:
        src = resolve(root, args.from_file)
        if not os.path.isfile(src):
            raise ToolError(f"内容文件不存在: {args.from_file}")
        content = read_text(src)
    write_text(path, content)
    return {"ok": True, "path": rel(root, path), "chars": len(content)}


def cmd_edit(root, args):
    path = resolve(root, args.path)
    if not os.path.isfile(path):
        raise ToolError(f"文件不存在: {args.path}")
    content = read_text(path)
    lines = content.splitlines()

    if args.action == "replace":
        old, new = args.old, args.new
        if old is None or new is None:
            rest = args.rest or []
            if len(rest) >= 2:
                old, new = rest[0], rest[1]
        if old is None or new is None:
            raise ToolError("replace 需要旧文本和新文本（位置参数或 --old/--new）")
        args.old, args.new = old, new
        hits = content.count(args.old)
        if hits == 0:
            raise ToolError("未找到要替换的文本")
        if hits > 1 and not args.all:
            raise ToolError(f"匹配到 {hits} 处，请加 --all 或提供更长的 --old")
        write_text(path, content.replace(args.old, args.new, -1 if args.all else 1))
        return {"ok": True, "action": "replace", "path": rel(root, path), "replaced": hits}

    if args.action == "insert":
        if args.content is None and args.rest:
            args.content = args.rest[0]
        if args.line is None or args.content is None:
            raise ToolError("insert 需要 --line 和内容（位置参数或 --content）")
        if args.line < 1 or args.line > len(lines) + 1:
            raise ToolError(f"行号 {args.line} 超出范围 1..{len(lines) + 1}")
        lines.insert(args.line - 1, args.content)
        write_text(path, "\n".join(lines) + ("\n" if content.endswith("\n") else ""))
        return {"ok": True, "action": "insert", "path": rel(root, path), "line": args.line}

    if args.action == "delete":
        if args.start is None:
            raise ToolError("delete 需要 --start")
        end = args.end if args.end is not None else args.start
        if args.start < 1 or end > len(lines) or args.start > end:
            raise ToolError(f"行号范围 {args.start}..{end} 无效（共 {len(lines)} 行）")
        removed = lines[args.start - 1:end]
        del lines[args.start - 1:end]
        tail = "\n" if content.endswith("\n") and lines else ""
        write_text(path, "\n".join(lines) + tail)
        return {"ok": True, "action": "delete", "path": rel(root, path),
                "lines": f"{args.start}..{end}", "removed": removed}

    raise ToolError(f"未知编辑动作: {args.action}")


def cmd_ls(root, args):
    # 位置参数与 --path 都接受，位置参数优先
    target = args.path or getattr(args, "path_opt", None) or "."
    path = resolve(root, target)
    if not os.path.isdir(path):
        raise ToolError(f"目录不存在: {target}")
    recursive = bool(getattr(args, "recursive", False))
    entries = []

    def add_dir(dir_path):
        for name in sorted(os.listdir(dir_path)):
            full = os.path.join(dir_path, name)
            is_dir = os.path.isdir(full)
            entries.append({
                "name": rel(root, full),
                "type": "dir" if is_dir else "file",
                "size": 0 if is_dir else os.path.getsize(full),
            })
            if recursive and is_dir:
                add_dir(full)

    add_dir(path)
    return {"ok": True, "path": rel(root, path), "entries": entries[:500]}


def cmd_grep(root, args):
    try:
        pattern = re.compile(args.pattern, re.IGNORECASE if args.ignore_case else 0)
    except re.error as e:
        raise ToolError(f"正则表达式无效: {e}")

    matches = []
    for target in (args.paths or ["."]):
        path = resolve(root, target)
        files = []
        if os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = [d for d in dirnames if d not in args.exclude]
                files.extend(os.path.join(dirpath, f) for f in filenames)
        else:
            files.append(path)
        for f in files:
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if pattern.search(line):
                            matches.append({"file": rel(root, f), "line": lineno,
                                            "text": line.rstrip("\n")[:300]})
                            if len(matches) >= args.limit:
                                return {"ok": True, "pattern": args.pattern,
                                        "matches": matches, "truncated": True}
            except OSError:
                continue
    return {"ok": True, "pattern": args.pattern, "matches": matches, "truncated": False}


def cmd_find(root, args):
    matches = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in args.exclude]
        for name in filenames + dirnames:
            if args.name and args.name not in name:
                continue
            if args.suffix and not name.endswith(args.suffix):
                continue
            full = os.path.join(dirpath, name)
            matches.append({"path": rel(root, full),
                            "type": "dir" if os.path.isdir(full) else "file"})
            if len(matches) >= args.limit:
                return {"ok": True, "matches": matches, "truncated": True}
    return {"ok": True, "matches": matches, "truncated": False}


# ==================== 上下文注入 ====================
CONTEXT_DIR = "上下文"
CONTEXT_FILE = "last_prompt.json"


def cmd_context(root, args):
    """读取上一次工作流产出的提示词，供后续步骤注入上下文。

    主程序在「文字产出者」技能成功后把抽好的描述文本落到
    <work>/上下文/last_prompt.json，这里只负责取回。
    """
    path = os.path.join(root, CONTEXT_DIR, CONTEXT_FILE)
    if not os.path.isfile(path):
        return {"ok": False,
                "error": "还没有可复用的上下文：上一次工作流没有产出描述文本"}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"上下文文件损坏: {e}"}
    text = str(data.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "上下文为空"}
    if args.max_chars and len(text) > args.max_chars:
        text = text[:args.max_chars]
    return {"ok": True, "text": text, "from": data.get("skill", ""),
            "time": data.get("time", ""), "chars": len(text)}


IS_WINDOWS = os.name == "nt"


# ==================== 命令守卫 ====================
def _guard_module():
    """加载沙箱守卫；不可用时返回 None，让 bash 退化为无守卫执行。

    扁平化后守卫就在同目录的 guard.py，直接作为包内模块导入。
    """
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # package/
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)
    try:
        from tools.sandbox import guard
        return guard
    except Exception:
        pass
    # 退化：按文件路径加载（此时其相对导入可能失败，则放弃守卫）
    try:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guard.py")
        spec = importlib.util.spec_from_file_location("dbagent_sandbox_guard", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules["dbagent_sandbox_guard"] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def guard_command(command, cwd):
    """执行前检查命令，返回 (是否放行, 拦截原因, 脱敏后的命令)。"""
    g = _guard_module()
    if g is None:
        return True, "", command

    # 密钥类：不拦截，但把命令里的密钥替换掉，避免写进日志/输出
    safe_command = command
    try:
        safe_command, _hits = g.redact_secrets(command)
    except Exception:
        safe_command = command

    # 危险删除 / 越界：直接拦截
    try:
        verdict = g.check_shell_command(
            command, session_id="dbagent", cwd=str(cwd), tool="bash")
    except TypeError:
        # 老签名兼容
        try:
            verdict = g.check_shell_command(command, session_id="dbagent", cwd=str(cwd))
        except Exception:
            return True, "", safe_command
    except Exception:
        return True, "", safe_command

    if verdict:
        return False, str(verdict), safe_command
    return True, "", safe_command


def cmd_bash(root, args):
    """在工作目录内执行 shell 命令。

    Linux  : chroot 沙箱内执行，只挂载工作目录
    Windows: 当前没有 chroot，用 PowerShell 在限定工作目录内执行，
             并把常见的 Unix 写法（mkdir -p、rm -rf、touch）翻译过去。

    执行前统一过 guard 守卫：危险删除与目录越界会被拦截，
    命令中的密钥会被脱敏。
    """
    allowed, reason, safe_command = guard_command(args.command, root)
    if not allowed:
        return {
            "ok": False,
            "exit_code": 126,
            "stdout": "",
            "stderr": f"命令被沙箱守卫拦截：\n{reason}",
            "blocked": True,
            "outputs": [],
        }
    # 用脱敏后的命令执行（语义等价，仅密钥被替换）
    if safe_command != args.command:
        args = argparse.Namespace(**{**vars(args), "command": safe_command})

    if IS_WINDOWS:
        return _cmd_bash_windows(root, args)

    ensure_sandbox()
    sync_into_sandbox(root)
    # 宿主工作目录在沙箱内映射为 /work，路径要相应改写
    command = args.command.replace(root, f"/{SANDBOX_WORK}")
    try:
        proc = subprocess.run(
            ["chroot", SANDBOX_ROOT, "/bin/bash", "-c", f"cd /{SANDBOX_WORK} && {command}"],
            timeout=args.timeout, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"命令超时（>{args.timeout}s）")
    outputs = sync_from_sandbox(root)
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": clamp(proc.stdout),
        "stderr": clamp(proc.stderr),
        "outputs": outputs,
    }


def _translate_unix_for_windows(command):
    """把常见 Unix 写法翻译成 PowerShell 等价形式。

    只覆盖高频且语义明确的几种，其余原样交给 PowerShell；
    不做真正的 POSIX 兼容层，复杂命令请直接写 PowerShell。
    """
    import re as _re

    cmd = command.strip()

    # mkdir -p a/b  ->  New-Item -ItemType Directory -Force
    m = _re.fullmatch(r"mkdir\s+(?:-p\s+)?(.+)", cmd)
    if m:
        target = m.group(1).strip().strip("'\"")
        return f"New-Item -ItemType Directory -Force -Path '{target}' | Out-Null"

    # touch f  ->  新建空文件或更新时间戳
    m = _re.fullmatch(r"touch\s+(.+)", cmd)
    if m:
        target = m.group(1).strip().strip("'\"")
        return f"if (Test-Path '{target}') {{ (Get-Item '{target}').LastWriteTime = Get-Date }} else {{ New-Item -ItemType File -Force -Path '{target}' | Out-Null }}"

    # rm -rf x  ->  Remove-Item -Recurse -Force
    m = _re.fullmatch(r"rm\s+-(?:rf|fr|r)\s+(.+)", cmd)
    if m:
        target = m.group(1).strip().strip("'\"")
        return f"Remove-Item -Recurse -Force -Path '{target}' -ErrorAction SilentlyContinue"

    # ls / ls -la  ->  Get-ChildItem
    if _re.fullmatch(r"ls(?:\s+-\w+)?", cmd):
        return "Get-ChildItem"

    # cat f  ->  Get-Content
    m = _re.fullmatch(r"cat\s+(.+)", cmd)
    if m:
        return f"Get-Content '{m.group(1).strip().strip(chr(39)+chr(34))}'"

    # curl 在 PowerShell 里是 Invoke-WebRequest 的别名，
    # 参数（-s、-L、-X POST、-d）完全不兼容，必须改走真正的 curl.exe。
    # 同名的 curl.exe 从 Windows 10 1803 起随系统自带。
    m = _re.match(r"curl(?:\.exe)?(\s+.*)$", cmd, _re.IGNORECASE | _re.DOTALL)
    if m:
        return "curl.exe " + m.group(1).lstrip()

    # wget 同理：PowerShell 里是 Invoke-WebRequest，参数不兼容
    m = _re.match(r"wget(\s+.*)$", cmd, _re.IGNORECASE | _re.DOTALL)
    if m:
        return "curl.exe -L -O " + m.group(1).lstrip()

    # echo x 原样可用，PowerShell 的 echo 是 Write-Output 别名
    return command


def _cmd_bash_windows(root, args):
    """Windows：用 PowerShell 在限定工作目录内执行，不经过 chroot。"""
    command = _translate_unix_for_windows(args.command)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=root,
            timeout=args.timeout,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"命令超时（>{args.timeout}s）")
    except FileNotFoundError as e:
        raise ToolError(f"找不到 powershell: {e}")

    # Windows 上输出可能是 GBK，按 UTF-8 → GBK 顺序尝试
    def dec(raw):
        if not raw:
            return ""
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    stdout = dec(proc.stdout)
    stderr = dec(proc.stderr)
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": clamp(stdout),
        "stderr": clamp(stderr),
        "outputs": [],
    }


def cmd_fetch_tool(root, args):
    """抓取网页/远程文档正文。

    优先用 tools/web_fetch 的完整实现（支持 PDF/DOCX/XLSX/PPTX 解析与
    SSRF 防护）；它 import 失败时退回 net.py 的简化版，保证能力不断。
    """
    url = (args.url or "").strip()
    try:
        import importlib.util
        import os as _os
        here = _os.path.dirname(_os.path.abspath(__file__))
        wf_path = _os.path.join(_os.path.dirname(here), "web_fetch", "web_fetch.py")
        spec = importlib.util.spec_from_file_location("dbagent_web_fetch", wf_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tool = mod.WebFetch({"cwd": root})
        payload = tool.execute({"url": url}).to_dict()
        # web_fetch 返回 {ok, title, content...}，补上 toolset 约定的字段
        payload.setdefault("tool", "fetch")
        return payload
    except Exception as e:
        from net import cmd_fetch
        result = cmd_fetch(url, max_chars=args.max_chars)
        if not result.get("ok"):
            result["error"] = f"{result.get('error')}（web_fetch 不可用：{e}）"
        return result


def cmd_search_tool(root, args):
    from net import cmd_search
    return cmd_search(args.query, limit=args.limit)


def build_parser():
    parser = argparse.ArgumentParser(prog="tools", description="沙箱工具集")
    parser.add_argument("--workdir", help="工作目录，默认 <data>/work")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("read", help="读取文件")
    p.add_argument("path")
    p.add_argument("--start", type=int, help="起始行（从 1 开始）")
    p.add_argument("--end", type=int, help="结束行（含）")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("write", help="写入文件")
    p.add_argument("path")
    p.add_argument("content", nargs="?", default="")
    p.add_argument("--from-file", help="从工作目录内的文件读取内容")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("edit", help="编辑文件")
    p.add_argument("action", choices=["replace", "insert", "delete"])
    p.add_argument("path")
    p.add_argument("rest", nargs="*", help="位置参数：replace 时为 旧 新；insert 时为 内容")
    p.add_argument("--old")
    p.add_argument("--new")
    p.add_argument("--all", action="store_true", help="替换全部匹配")
    p.add_argument("--line", type=int)
    p.add_argument("--content")
    p.add_argument("--start", type=int)
    p.add_argument("--end", type=int)
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("ls", help="列目录")
    p.add_argument("path", nargs="?")
    # 模型常按 shell 习惯传 --path / -la / -a / -l，直接报 unrecognized arguments
    # 会让它以为工具坏了并反复重试。这里全部收下，语义分别是指定目录与忽略。
    p.add_argument("--path", dest="path_opt")
    p.add_argument("-a", "--all", action="store_true")
    p.add_argument("-l", action="store_true")
    p.add_argument("-la", "-al", dest="la", action="store_true")
    p.add_argument("-R", "--recursive", action="store_true")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("grep", help="正则搜索")
    p.add_argument("pattern")
    p.add_argument("paths", nargs="*")
    p.add_argument("-i", "--ignore-case", action="store_true")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--exclude", nargs="*", default=[".git", "__pycache__", "node_modules"])
    p.set_defaults(func=cmd_grep)

    p = sub.add_parser("find", help="按名称查找")
    p.add_argument("--name", help="名称包含的字符串")
    p.add_argument("--suffix", help="文件后缀，如 .py")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--exclude", nargs="*", default=[".git", "__pycache__", "node_modules"])
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("context", help="读取上一次产出的提示词")
    p.add_argument("--max-chars", type=int, default=0)
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("bash", help="在沙箱内执行 shell 命令")
    p.add_argument("command")
    p.add_argument("--timeout", type=int, default=60)
    p.set_defaults(func=cmd_bash)

    # 联网工具：只读网络，不写本地文件，不受 work 目录限制
    p = sub.add_parser("fetch", help="抓取网页正文")
    p.add_argument("url")
    p.add_argument("--max-chars", type=int, default=8000)
    p.set_defaults(func=cmd_fetch_tool)

    p = sub.add_parser("search", help="联网搜索")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_search_tool)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    root = workspace_dir(args.workdir)
    try:
        result = args.func(root, args)
    except ToolError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 1
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"{e.strerror}: {e.filename}"},
                         ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
