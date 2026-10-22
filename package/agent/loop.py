#!/usr/bin/env python3
"""code 路由：coding-agent 式的读写循环。

与 main.py 里 ai_plan → ai_edits → apply_edits 的一次性直线的区别在于：
模型每一步只发一个工具调用，看到真实结果后再决定下一步，
因此能处理「先读文件再改」「改完再验证」这类需要中间反馈的任务。

工具执行沿用 skill_exec.execute_tool（tools/sandbox/toolset.py）：
bash 走 chroot 沙箱，read/grep/find/ls 只读，
write/edit 的落盘位置由 writable 决定：
  writable=False  走 toolset，被 resolve() 限死在 data/work 内
  writable=True   进程内直接写 root（工作目录），复用 main.apply_edits 的替换语义
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 只读工具：直接交给 toolset
READ_ONLY_TOOLS = ("read", "grep", "find", "ls")
ALL_TOOLS = ("read", "write", "edit", "bash", "grep", "find", "ls")

RESULT_PREVIEW_CHARS = 3000

SYSTEM_PROMPT = """你是 DBAgent 的代码开发代理，在一个真实项目里工作。

每一步你只能做一件事，用 JSON 描述，不要输出其他文字：
  {"tool": "<工具名>", "params": {<参数对象>}}
确认任务完成时输出：
  {"done": true, "summary": "<做了什么，中文，100字以内>"}

可用工具（params 是对象，字段名必须一字不差）：
  read   {"path": "文件路径", "offset": 起始行, "limit": 行数}       读文件，带行号；offset/limit 可省略（读全文）
  write  {"path": "文件路径", "content": "完整内容"}                新建或整文件覆盖，自动创建父目录
  write  {"path": "文件路径", "content": "这一段", "append": true}   追加到文件末尾
  edit   {"path": "文件路径", "old_text": "原文", "new_text": "新文"}  精确替换
  bash   {"command": "命令", "timeout": 60}                        在沙箱内执行 shell
  grep   {"pattern": "正则", "paths": ["目录"], "ignore_case": false}  搜索文件内容
  find   {"name": "名称", "suffix": ".后缀"}                       按名称/后缀找文件
  ls     {"path": "目录"}                                         列目录

工作规则：
1. 改动代码前先 read 看清原文，不要凭猜测写 old_text。
2. edit 的 old_text 必须从文件里逐字抄（含缩进与换行），且足够长以保持唯一。
3. 一次只改一处，改完继续下一步；不要把多件事塞进一个调用。
4. 修改类任务完成后用 bash（如 node --check、py_compile）验证，确认无语法错误再 done。
5. 路径用相对路径；不要越出工作目录。
6. 单次 write 的 content 超过约 3000 字符时必须分片：先 write 第一段，再用
   append: true 追加后续段，每段不超过约 3000 字符；一次性输出大文件会被截断。
7. 你看到的是上一步工具的真实输出，据此决定下一步，不要重复已成功的调用。
8. 可用轮数充足（远超普通任务所需），由你自主判断任务何时完成：
   把需求完整做出来并验证通过才输出 done；不要因轮数担心而草率收尾，
   也不要无意义地空转重复。
9. 不要用 bash echo 打印「报告/总结」来代替修改或汇报：echo 不产生
   任何文件变化，也不能作为完成依据。要汇报完成，只输出 done。
"""


def _tool_hint(writable: bool) -> str:
    """工具说明：不可写时明确告知只能读。"""
    if not writable:
        return ("当前为只读模式：write 与 edit 不可用。\n"
                "你可以 read/grep/find/ls/bash 探索代码、回答问题、给出修改建议，"
                "但不要尝试写文件。\n")
    return ""


def _preview(payload: dict) -> str:
    """把工具结果压成一段可回灌给模型的文本。

    文件类工具（read/write/edit）的内容不截断：
    截断会让模型以为文件只有那一部分，于是换 offset 反复重读同一个文件，
    每次都被截断，直到 stagnant 上限强停（曾连续 12 轮只读不写）。
    """
    if not isinstance(payload, dict):
        return str(payload)[:RESULT_PREVIEW_CHARS]
    ok = payload.get("ok")
    if ok is False:
        err = (payload.get("error") or payload.get("stderr")
               or payload.get("raw") or "未知错误")
        return f"[失败] {str(err)[:RESULT_PREVIEW_CHARS]}"

    # ls 命中空目录：不给出路的话，模型会反复 ls 同一个目录，
    # 第 2 次就被防重复拦截，整轮卡死。
    if payload.get("entries") == []:
        return ("[成功] 目录为空，没有任何文件。\n"
                "不要重复 ls 同一个目录——结果不会变。"
                "请直接开始工作：用 write 创建文件（会自动建父目录），"
                "或用 bash 执行命令。")

    parts = []
    entries = payload.get("entries")
    if isinstance(entries, list) and entries:
        names = [str(e.get("name", "")) + ("/" if e.get("type") == "dir" else "")
                 for e in entries[:20]]
        more = f" 等 {len(entries)} 项" if len(entries) > 20 else f"，共 {len(entries)} 项"
        parts.append("目录：" + "、".join(names) + more)
    matches = payload.get("matches")
    if isinstance(matches, list) and matches:
        parts.append(f"命中 {len(matches)} 条")
    if payload.get("stdout"):
        parts.append(str(payload["stdout"]))
    if payload.get("content"):
        parts.append(str(payload["content"]))
    if payload.get("outputs"):
        parts.append("产出文件: " + ", ".join(str(o) for o in payload["outputs"]))
    if not parts:
        parts.append(json.dumps(payload, ensure_ascii=False))
    text = "\n".join(parts)

    # 文件工具的内容全量回传；其它工具（bash 等）才按上限截断，
    # 免得一条命令刷屏吃掉上下文。
    is_file_tool = payload.get("tool") in ("read", "write", "edit")
    if not is_file_tool and len(text) > RESULT_PREVIEW_CHARS:
        text = text[:RESULT_PREVIEW_CHARS] + "\n...(输出过长已截断)"
    first, _, rest = text.partition("\n")
    return f"[成功] {first}" + (f"\n{rest}" if rest else "")


# 常见参数名别名：低配模型偶尔混用近义字段名，归一到规范名
_PARAM_ALIASES = {
    "read": {"file": "path", "start": "offset"},
    "write": {"file": "path", "text": "content"},
    "edit": {"old": "old_text", "search": "old_text", "new": "new_text",
             "replace": "new_text"},
    "bash": {"cmd": "command"},
    "grep": {"query": "pattern", "path": "paths"},
    "find": {"pattern": "name"},
    "ls": {"dir": "path"},
    "search": {"keyword": "query", "q": "query", "word": "query"},
    "fetch": {"href": "url", "link": "url", "target": "url"},
}

# 每个工具的必填 params 字段
_REQUIRED_PARAMS = {
    "read": ("path",),
    "write": ("path",),
    "edit": ("path", "old_text"),
    "bash": ("command",),
    "grep": ("pattern",),
    "find": (),
    "search": ("query",),
    "fetch": ("url",),
    "ls": (),
}


def _args_to_params(tool: str, args) -> dict:
    """旧 args 数组协议 → params 对象（兼容旧版提示词遗留的会话）。"""
    if not isinstance(args, list):
        args = [args]
    strs = [a if isinstance(a, str) else str(a) for a in args if a is not None]

    def val(flag):
        for i, a in enumerate(strs):
            if a == flag and i + 1 < len(strs):
                return strs[i + 1]
        return None

    pos, i = [], 0
    while i < len(strs):
        a = strs[i]
        if a.startswith("-"):
            i += 2 if a in ("--start", "--end", "--old", "--new", "--timeout",
                            "--limit", "--name", "--suffix") else 1
            continue
        pos.append(a)
        i += 1

    p: dict = {}
    if tool == "read":
        if pos:
            p["path"] = pos[0]
        if val("--start"):
            p["offset"] = val("--start")
        if val("--end"):
            p["end"] = val("--end")
    elif tool == "write":
        if pos:
            p["path"] = pos[0]
        rest = [a for a in strs[1:] if a != "--append"]
        if rest:
            p["content"] = rest[0]
        if "--append" in strs:
            p["append"] = True
    elif tool == "edit":
        if pos and pos[0] in ("replace", "insert", "delete") and len(pos) > 1:
            p["path"] = pos[1]
        elif pos:
            p["path"] = pos[0]
        if val("--old"):
            p["old_text"] = val("--old")
        if val("--new"):
            p["new_text"] = val("--new")
    elif tool == "bash":
        if strs:
            p["command"] = strs[0]
        if val("--timeout"):
            p["timeout"] = val("--timeout")
    elif tool == "grep":
        if strs:
            p["pattern"] = strs[0]
        if len(pos) > 1:
            p["paths"] = pos[1:]
        if "-i" in strs or "--ignore-case" in strs:
            p["ignore_case"] = True
        if val("--limit"):
            p["limit"] = val("--limit")
    elif tool == "find":
        if val("--name"):
            p["name"] = val("--name")
        if val("--suffix"):
            p["suffix"] = val("--suffix")
        if val("--limit"):
            p["limit"] = val("--limit")
    elif tool == "ls":
        if pos:
            p["path"] = pos[0]
    return p


def _normalize_call(data: dict):
    """模型输出 → (tool, params)。

    新协议 params 优先；缺 params 时回退解析旧 args 数组。
    """
    tool = (data.get("tool") or "").strip()
    raw = data.get("params")
    params = dict(raw) if isinstance(raw, dict) else {}
    for k, v in list(params.items()):
        canonical = _PARAM_ALIASES.get(tool, {}).get(k)
        if canonical and canonical not in params:
            params[canonical] = v
    if not params and data.get("args") is not None:
        params = _args_to_params(tool, data.get("args"))
    return tool, params


def _params_to_args(tool: str, params: dict) -> list:
    """params 对象 → toolset 位置参数数组（skill_exec.execute_tool 边界）。"""
    def s(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return "" if v is None else str(v)

    a: list = []
    if tool == "read":
        if params.get("path"):
            a.append(s(params["path"]))
        if params.get("offset") is not None:
            a += ["--start", s(params["offset"])]
        if params.get("end") is not None:
            a += ["--end", s(params["end"])]
        elif params.get("limit") is not None:
            try:
                off = int(params.get("offset") or 1)
                a += ["--end", s(off + int(params["limit"]) - 1)]
            except (TypeError, ValueError):
                pass
    elif tool == "write":
        if params.get("path"):
            a.append(s(params["path"]))
        a.append(s(params.get("content")))
        if params.get("append"):
            a.append("--append")
    elif tool == "edit":
        if params.get("path"):
            a.append(s(params["path"]))
        a += ["--old", s(params.get("old_text")),
              "--new", s(params.get("new_text"))]
    elif tool == "bash":
        a.append(s(params.get("command")))
        if params.get("timeout") is not None:
            a += ["--timeout", s(params["timeout"])]
    elif tool == "grep":
        a.append(s(params.get("pattern")))
        paths = params.get("paths")
        if isinstance(paths, list):
            a += [s(x) for x in paths]
        elif paths:
            a.append(s(paths))
        if params.get("ignore_case"):
            a.append("-i")
        if params.get("limit") is not None:
            a += ["--limit", s(params["limit"])]
    elif tool == "find":
        if params.get("name"):
            a += ["--name", s(params["name"])]
        if params.get("suffix"):
            a += ["--suffix", s(params["suffix"])]
        if params.get("limit") is not None:
            a += ["--limit", s(params["limit"])]
    elif tool == "ls":
        if params.get("path"):
            a.append(s(params["path"]))
    elif tool == "search":
        # 兼容 query / keyword / q 等写法
        q = params.get("query") or params.get("keyword") or params.get("q")
        if q:
            a.append(s(q))
        if params.get("limit") is not None:
            a += ["--limit", s(params["limit"])]
    elif tool == "fetch":
        url = params.get("url") or params.get("href") or params.get("link")
        if url:
            a.append(s(url))
        if params.get("max_chars") is not None:
            a += ["--max-chars", s(params["max_chars"])]
    return a


def _params_brief(params: dict) -> str:
    """params 摘要，用于步骤日志。"""
    parts = []
    for k, v in params.items():
        text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}={text[:60]}")
    return " ".join(parts)


def _read_direct(root: str, params: dict) -> dict:
    """writable=True 时的 read：直读工作目录，与 write/edit 同一域。

    只读模式下 read 走 toolset（限 data/work）；可写模式必须同域，
    否则模型改完文件无法读回核对。
    """
    rel = str(params.get("path") or "")
    if not rel:
        return {"ok": False, "error": "read 缺少 path 参数"}
    target = os.path.realpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        return {"ok": False, "error": f"路径越界，只能读工作目录内: {rel}"}
    if not os.path.isfile(target):
        return {"ok": False, "error": f"文件不存在: {rel}"}
    try:
        with open(target, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return {"ok": False, "error": f"{e.strerror}: {e.filename}"}

    start = 1
    for key in ("offset", "start"):
        try:
            start = max(1, int(params[key]))
            break
        except (KeyError, TypeError, ValueError):
            continue
    end = len(lines)
    try:
        end = min(int(params["end"]), len(lines))
    except (KeyError, TypeError, ValueError):
        try:
            end = min(start + int(params["limit"]) - 1, len(lines))
        except (KeyError, TypeError, ValueError):
            end = len(lines)
    if start > len(lines):
        return {"ok": False, "error": f"起始行 {start} 超出范围（共 {len(lines)} 行）"}
    numbered = "\n".join(f"{n:5d}\t{lines[n - 1]}"
                        for n in range(start, end + 1))
    return {"ok": True, "path": rel, "start": start, "end": end,
            "total_lines": len(lines), "content": numbered}


def _write_direct(root: str, params: dict) -> dict:
    """writable=True 时的 write：直接写工作目录下的文件，append=true 追加。"""
    rel = str(params.get("path") or "")
    if not rel:
        return {"ok": False, "error": "write 缺少 path 参数"}
    content = params.get("content")
    content = "" if content is None else str(content)
    target = os.path.realpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        return {"ok": False, "error": f"路径越界，只能写工作目录内: {rel}"}
    parent = os.path.dirname(target)
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "a" if params.get("append") else "w",
                  encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "path": rel, "bytes": len(content)}
    except OSError as e:
        return {"ok": False, "error": f"{e.strerror}: {e.filename}"}


def _edit_direct(root: str, params: dict) -> dict:
    """writable=True 时的 edit：进程内精确替换。

    语义与 main.apply_edits 一致：old_text 必须在文件里唯一命中。
    """
    path = str(params.get("path") or "")
    old = params.get("old_text")
    old = "" if old is None else str(old)
    new = params.get("new_text")
    new = "" if new is None else str(new)
    if not path:
        return {"ok": False, "error": "edit 缺少 path 参数"}
    if not old:
        return {"ok": False, "error":
                "edit 缺少 old_text 参数（须为从文件里逐字抄录的原文片段）"}
    target = os.path.realpath(os.path.join(root, path))
    if target != root and not target.startswith(root + os.sep):
        return {"ok": False, "error": f"路径越界，只能改工作目录内: {path}"}
    try:
        with open(target, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return {"ok": False, "error": f"{e.strerror}: {e.filename}"}
    cnt = content.count(old)
    if cnt == 0:
        return {"ok": False, "error": "未找到匹配片段，请先用 read 核对原文"}
    if cnt > 1:
        return {"ok": False, "error": f"匹配到 {cnt} 处，请把 old_text 写得更具体"}
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(content.replace(old, new))
    except OSError as e:
        return {"ok": False, "error": f"{e.strerror}: {e.filename}"}
    return {"ok": True, "path": path,
            "replaced_chars": len(old), "new_chars": len(new)}


def run_agent_loop(req, root, key, base, model, yes=False,
                   writable=False, max_rounds=8, on_event=None, mode=None,
                   should_stop=None):
    """执行 Agent 循环。

    req        用户需求原文
    root       工作目录，写操作不得越出
    writable   True 时 write/edit 直接写 root；False 时被限死在 data/work
    max_rounds 循环轮数上限，防止模型停不下来
    on_event   可选回调 (kind, text)，kind 为 step/done/error，供界面接入
    mode       plan / goal / code；None 按 code 处理
    should_stop 可选回调 () -> bool，每轮开始时询问是否该打断；
               用于后台任务队列的取消，不影响同步调用（传 None 即可）

    返回 True 表示本轮已被处理（无论成败）。
    """
    if mode == "plan":
        return _run_plan_mode(req, root, key, base, model, yes,
                              writable, max_rounds, on_event, should_stop)
    if mode == "goal":
        return _run_goal_mode(req, root, key, base, model, yes,
                              writable, max_rounds, on_event, should_stop)
    return _run_code_mode(req, root, key, base, model, yes,
                          writable, max_rounds, on_event, should_stop)


def _run_code_mode(req, root, key, base, model, yes,
                   writable, max_rounds, on_event, should_stop=None):
    """code 模式：直接进入读写循环，边做边看结果。"""
    import skill_exec

    def emit(kind, text):
        print(text)
        if on_event:
            try:
                on_event(kind, text)
            except Exception:
                pass

    try:
        data_dir, _ = _workspace_dirs()
        workdir = skill_exec.data_work_dir(data_dir)
    except Exception:
        workdir = os.path.join(os.path.expanduser("~"), ".dbagent", "work")

    allowed = ALL_TOOLS if writable else READ_ONLY_TOOLS
    messages = [
        {"role": "system",
         "content": SYSTEM_PROMPT + "\n" + _tool_hint(writable)
         + f"\n工作目录：{root}\n当前可用工具：{', '.join(allowed)}\n"},
        {"role": "user", "content": f"需求：{req}"},
    ]

    mode = "可写" if writable else "只读"
    emit("step", f"\n🤖 代码代理（{mode}模式，每段 {max_rounds} 轮自动续）  工作目录：{root}"
                + ("\n   当前只读：输入 /writable on 可修改代码" if not writable else ""))

    return _loop(req, root, key, base, model, yes, writable, max_rounds,
                 emit, messages, allowed, workdir, label="",
                 should_stop=should_stop)


def _run_plan_mode(req, root, key, base, model, yes,
                   writable, max_rounds, on_event, should_stop=None):
    """plan 模式：先让模型出方案，确认后再执行。

    方案阶段用只读工具探索，不产生任何写入；用户确认后才进入执行循环。
    这样涉及面广的任务不会在用户没看清之前就被改掉。
    """
    import skill_exec

    def emit(kind, text):
        print(text)
        if on_event:
            try:
                on_event(kind, text)
            except Exception:
                pass

    try:
        data_dir, _ = _workspace_dirs()
        workdir = skill_exec.data_work_dir(data_dir)
    except Exception:
        workdir = os.path.join(os.path.expanduser("~"), ".dbagent", "work")

    emit("step", f"\n📐 方案模式  工作目录：{root}")

    from main import call_llm_json
    plan_prompt = (
        f"请先阅读项目，然后给出实施方案。只输出 JSON：\n"
        f"{{\"files\": [\"涉及的文件相对路径\"], \n"
        f" \"steps\": [\"第一步做什么\", \"第二步做什么\"], \n"
        f" \"risk\": \"主要风险或不确定点（没有则填无）\"}}\n\n"
        f"要求：先用 read/ls/grep/find 弄清相关代码，再给方案；\n"
        f"steps 要具体到每个文件改什么；不要修改任何文件。\n\n"
        f"需求：{req}"
    )
    messages = [
        {"role": "system",
         "content": SYSTEM_PROMPT + "\n" + _tool_hint(False)
         + f"\n工作目录：{root}\n当前阶段：制定方案，只读不写。\n"},
        {"role": "user", "content": plan_prompt},
    ]

    # 方案阶段：允许探索，禁用写入
    plan_data = None
    for round_no in range(1, max_rounds + 1):
        try:
            data = call_llm_json(messages, key, base, model, attempts=2)
        except Exception as e:
            emit("error", f"  [探查 {round_no} 模型输出异常：{e}，重试中]")
            messages.append({"role": "user", "content":
                             "上一步输出不是合法 JSON。请只输出一个 JSON 对象，"
                             "内容过长时分多次探查。"})
            continue
        if not isinstance(data, dict):
            continue
        if data.get("plan") or data.get("steps"):
            plan_data = data
            break
        tool = (data.get("tool") or "").strip()
        tool, params = _normalize_call(data)
        if tool not in READ_ONLY_TOOLS:
            messages.append({"role": "assistant", "content": json.dumps(
                data, ensure_ascii=False)})
            messages.append({"role": "user", "content":
                             "方案阶段只读不写，请用 read/ls/grep/find 探索后给出方案。"})
            continue
        emit("step", f"  [探查 {round_no}] {tool} {_params_brief(params)}")
        try:
            payload = skill_exec.execute_tool(
                tool, _params_to_args(tool, params), workdir)
        except Exception as e:
            payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        emit("step", f"        {'✓' if payload.get('ok') else '✗'} "
                      f"{_preview(payload).split(chr(10), 1)[0][:120]}")
        messages.append({"role": "assistant", "content": json.dumps(
            data, ensure_ascii=False)})
        messages.append({"role": "user", "content": _preview(payload)})

    if not plan_data:
        emit("error", "⚠️ 模型未给出方案，已放弃。可换个说法重试。")
        return True

    print("\n📋 实施方案")
    for f in (plan_data.get("files") or []):
        print(f"   · 文件 {f}")
    for i, s in enumerate(plan_data.get("steps") or [], 1):
        print(f"   {i}. {s}")
    if plan_data.get("risk"):
        print(f"   ⚠️ 风险：{plan_data['risk']}")

    if not yes:
        try:
            ans = input("\n是否按此方案执行？(y/N) ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes"):
            print("已取消，未做任何修改。\n")
            return True

    allowed = ALL_TOOLS if writable else READ_ONLY_TOOLS
    exec_messages = [
        {"role": "system",
         "content": SYSTEM_PROMPT + "\n" + _tool_hint(writable)
         + f"\n工作目录：{root}\n当前可用工具：{', '.join(allowed)}\n"},
        {"role": "user",
         "content": (f"已确认的方案：\n{json.dumps(plan_data, ensure_ascii=False)}\n\n"
                     f"请严格按方案逐步执行，不要偏离。原始需求：{req}")},
    ]
    emit("step", "\n🔧 按方案执行中…")
    return _loop(req, root, key, base, model, yes, writable, max_rounds,
                 emit, exec_messages, allowed, workdir, label="执行",
                 should_stop=should_stop)


def _run_goal_mode(req, root, key, base, model, yes,
                   writable, max_rounds, on_event, should_stop=None):
    """goal 模式：目标驱动，每轮先自检是否达到目标，未达到就继续改。

    适合「修复」「跑通」「通过测试」这类有客观验收标准的任务：
    模型自己判断该跑什么检查，跑过就收工，不过就带着错误信息重试。
    """
    import skill_exec

    def emit(kind, text):
        print(text)
        if on_event:
            try:
                on_event(kind, text)
            except Exception:
                pass

    try:
        data_dir, _ = _workspace_dirs()
        workdir = skill_exec.data_work_dir(data_dir)
    except Exception:
        workdir = os.path.join(os.path.expanduser("~"), ".dbagent", "work")

    allowed = ALL_TOOLS if writable else READ_ONLY_TOOLS
    goal_prompt = (
        f"这是一个目标驱动任务。你要达到的状态是：\n{req}\n\n"
        f"每一步先判断目标是否已达成：\n"
        f"  达成了    → {{\"done\": true, \"summary\": \"怎么验证的、结果如何\"}}\n"
        f"  还没达成  → {{\"tool\": \"...\", \"params\": {{...}}}} 做一件事\n\n"
        f"判定必须基于真实证据：改动后用 bash 跑测试/编译/复现命令验证，"
        f"不要凭感觉宣布完成。\n"
        f"失败时把错误信息当作下一步的输入，不要重复同样的调用。"
    )
    messages = [
        {"role": "system",
         "content": SYSTEM_PROMPT + "\n" + _tool_hint(writable) + "\n" + goal_prompt
         + f"\n工作目录：{root}\n当前可用工具：{', '.join(allowed)}\n"},
    ]

    mode = "可写" if writable else "只读"
    emit("step", f"\n🎯 目标模式（{mode}，每段 {max_rounds} 轮自动续）  工作目录：{root}")
    return _loop(req, root, key, base, model, yes, writable, max_rounds,
                 emit, messages, allowed, workdir, label="目标",
                 should_stop=should_stop)


def _loop(req, root, key, base, model, yes, writable, max_rounds,
          emit, messages, allowed, workdir, label="", should_stop=None):
    """公共循环体：模型每轮发一个工具调用，结果回灌后继续。"""
    import skill_exec

    # 轮数分段自动续跑：由模型通过 done 自主判断何时完成，
    # 到段不询问直接续；仅设硬上限防失控
    round_no = 0
    total_cap = max_rounds * 4
    # 防空转：seen 记录每次调用的签名与轮次，期间无修改则拦截完全重复的
    # 只读调用；stagnant 记连续多少轮没有成功的 write/edit，超阈值注入催行动
    seen = {}
    stagnant = 0
    while True:
        round_no += 1
        # 后台任务队列的打断请求：在每轮边界生效，
        # 不打断正在进行的 LLM 调用，避免留下半截状态
        if should_stop is not None:
            try:
                stop = should_stop()
            except Exception:
                stop = False
            if stop:
                emit("error", f"\n⏹️ 收到打断请求，已在第 {round_no - 1} 轮后停止。")
                return True
        if round_no > total_cap:
            emit("error", f"\n⚠️ 已达 {total_cap} 轮硬上限，任务终止。"
                          f"可把需求拆小分别做。")
            return True
        if round_no > 1 and (round_no - 1) % max_rounds == 0:
            emit("step", f"\n↩️ 已跑 {round_no - 1} 轮未完成，自动续跑下一段…")
        if stagnant >= 12:
            emit("error", f"\n⚠️ 模型连续 {stagnant} 轮未做任何修改也未宣布完成，"
                          f"已强制停止。可说「继续」再试，或换个更具体的说法描述需求。")
            return True
        if stagnant >= 6 and (stagnant - 6) % 3 == 0:
            emit("step", f"\n🤖 已连续 {stagnant} 轮没有任何修改，注入行动提醒")
            messages.append({"role": "user", "content":
                             f"提醒：已连续 {stagnant} 轮没有 write/edit，"
                             "文件内容没有任何变化。bash echo 打印报告不算修改、"
                             "不算完成——echo 不改变任何文件。请立即开始修改："
                             "用 edit（path/old_text/new_text）改进代码，"
                             "或用 write 写入新文件；完成后用 bash 验证再 done。"
                             "如果代码已符合需求，直接输出 done 并说明验证结果。"})
        from main import call_llm_json
        emit("step", f"  [{label} {round_no}] 思考中（请求模型…）")
        try:
            data = call_llm_json(messages, key, base, model, attempts=2)
        except Exception as e:
            # 输出超限/JSON 截断等暂时性失败不应终止任务：
            # 提示模型拆小步，消耗一轮配额继续（否则大文件任务必然中断）
            emit("error", f"  [第 {round_no} 轮模型输出异常：{e}]")
            messages.append({"role": "user", "content":
                             "上一步输出不是合法 JSON（很可能因内容过长被截断）。"
                             "请把工作拆小：大文件用 write 分段写入，第一段直接写，"
                             "后续段加 append: true，每次不超过 3000 字符，然后继续。"})
            continue

        if not isinstance(data, dict):
            messages.append({"role": "assistant", "content": json.dumps(
                data, ensure_ascii=False)})
            messages.append({"role": "user", "content":
                             "输出不是 JSON 对象。请按协议只输出一个 JSON 对象。"})
            continue

        if data.get("done"):
            emit("done", f"\n✅ {data.get('summary') or '已完成'}")
            return True

        tool, params = _normalize_call(data)

        if tool not in allowed:
            reason = (f"工具 {tool or '(未指定)'} 不可用"
                      + ("（只读模式下禁用写操作）" if not writable
                         and tool in ("write", "edit") else ""))
            emit("step", f"  [{label} {round_no}] {reason}")
            messages.append({"role": "assistant", "content": json.dumps(
                data, ensure_ascii=False)})
            messages.append({"role": "user", "content":
                             f"{reason}。可用工具：{', '.join(allowed)}。请重新选择。"})
            continue

        missing = [k for k in _REQUIRED_PARAMS.get(tool, ())
                   if not str(params.get(k) or "").strip()]
        if missing:
            example = json.dumps({"tool": tool, "params": {
                k: ("..." if k not in ("old_text", "new_text")
                    else "从文件逐字抄录的原文片段" if k == "old_text"
                    else "替换后的新文本")
                for k in _REQUIRED_PARAMS[tool]}}, ensure_ascii=False)
            reason = (f"{tool} 缺少参数 {', '.join(missing)}，"
                      f"正确格式如：{example}")
            emit("step", f"  [{label} {round_no}] ✗ {reason}")
            messages.append({"role": "assistant", "content": json.dumps(
                data, ensure_ascii=False)})
            messages.append({"role": "user", "content":
                             f"{reason}。请补全后重新调用。"})
            continue

        emit("step", f"  [{label} {round_no}] {tool} {_params_brief(params)}")

        sig = json.dumps([tool, params], ensure_ascii=False, sort_keys=True)
        prev_round = seen.get(sig)
        if prev_round is not None and tool not in ("write", "edit"):
            emit("step", f"        ⛔ 与第 {prev_round} 轮完全相同的调用，已拦截")
            messages.append({"role": "assistant", "content": json.dumps(
                data, ensure_ascii=False)})
            messages.append({"role": "user", "content":
                             f"第 {prev_round} 轮已执行过完全相同的调用 {tool}，"
                             "期间文件没有任何修改，重复它不会得到不同结果。\n"
                             "现在必须换一个行动，例如：\n"
                             "  1) 用 write 创建第一个文件（父目录自动创建）\n"
                             "  2) 用 read 打开一个具体文件看内容\n"
                             "  3) 用 bash 执行命令探测环境\n"
                             "  4) 若确认任务已完成，输出 done\n"
                             "禁止再发同样的调用。"})
            stagnant += 1
            continue
        seen[sig] = round_no

        if writable and tool == "read":
            payload = _read_direct(root, params)
        elif writable and tool == "write":
            payload = _write_direct(root, params)
        elif writable and tool == "edit":
            payload = _edit_direct(root, params)
        elif writable and tool == "ls":
            payload = _ls_direct(root, params)
        elif writable and tool == "find":
            payload = _find_direct(root, params)
        elif writable and tool == "grep":
            payload = _grep_direct(root, params)
        elif writable and tool == "bash":
            payload = _bash_direct(root, params)
        else:
            try:
                payload = skill_exec.execute_tool(
                    tool, _params_to_args(tool, params), workdir)
            except Exception as e:
                payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        preview = _preview(payload)
        status = "✓" if payload.get("ok") else "✗"
        first_line = preview.split("\n", 1)[0][:120]
        emit("step", f"        {status} {first_line}")

        if writable and tool in ("write", "edit") and payload.get("ok"):
            stagnant = 0
            seen.clear()
        else:
            stagnant += 1

        messages.append({"role": "assistant", "content": json.dumps(
            data, ensure_ascii=False)})
        messages.append({"role": "user", "content": preview})

    return True


def _resolve_in_root(root: str, path: str) -> str | None:
    """把工具路径限定在工作目录内；越界返回 None。"""
    p = str(path or ".")
    full = os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p))
    root_real = os.path.realpath(root)
    if full != root_real and not full.startswith(root_real + os.sep):
        return None
    return full


_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
              "dist", "build", ".pytest_cache"}


def _ls_direct(root, params):
    """可写模式下的 ls：直接列出工作目录（项目根）内容。"""
    rel = str(params.get("path") or ".")
    full = _resolve_in_root(root, rel)
    if full is None:
        return {"ok": False, "error": f"路径越界：{rel}（工作目录 {root}）"}
    if not os.path.isdir(full):
        return {"ok": False, "error": f"目录不存在：{rel}"}
    entries = []
    for name in sorted(os.listdir(full)):
        p = os.path.join(full, name)
        if os.path.isdir(p):
            entries.append({"name": name, "type": "dir"})
        else:
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            entries.append({"name": name, "type": "file", "size": size})
    return {"ok": True, "path": rel, "entries": entries[:500]}


def _find_direct(root, params):
    """可写模式下的 find：按名称/后缀在工作目录内查找。"""
    needle = str(params.get("name") or "")
    suffix = str(params.get("suffix") or "")
    limit = int(params.get("limit") or 200)
    matches = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames + dirnames:
            if needle and needle not in name:
                continue
            if suffix and not name.endswith(suffix):
                continue
            full = os.path.join(dirpath, name)
            matches.append({"path": os.path.relpath(full, root),
                            "type": "dir" if os.path.isdir(full) else "file"})
            if len(matches) >= limit:
                return {"ok": True, "matches": matches, "truncated": True}
    return {"ok": True, "matches": matches, "truncated": False}


def _grep_direct(root, params):
    """可写模式下的 grep：在工作目录内搜索文本。"""
    pattern = str(params.get("pattern") or "")
    flags = re.I if params.get("ignore_case") else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return {"ok": False, "error": f"正则错误：{e}"}
    limit = int(params.get("limit") or 200)
    targets = params.get("paths") or []
    files = []
    if targets:
        for t in targets:
            full = _resolve_in_root(root, t)
            if full is None:
                continue
            if os.path.isdir(full):
                for dp, dirs, fs in os.walk(full):
                    dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                    files += [os.path.join(dp, f) for f in fs]
            elif os.path.isfile(full):
                files.append(full)
    else:
        for dp, dirs, fs in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            files += [os.path.join(dp, f) for f in fs]
    matches = []
    for f in files:
        try:
            if os.path.getsize(f) > 2_000_000:
                continue
            with open(f, encoding="utf-8", errors="ignore") as fh:
                for no, line in enumerate(fh, 1):
                    if rx.search(line):
                        matches.append({"path": os.path.relpath(f, root),
                                        "line": no, "text": line.rstrip()[:200]})
                        if len(matches) >= limit:
                            return {"ok": True, "matches": matches, "truncated": True}
        except OSError:
            continue
    return {"ok": True, "matches": matches, "truncated": False}


def _bash_direct(root, params):
    """可写模式下的 bash：在工作目录直接执行（不经 chroot，Termux 可用）。"""
    command = str(params.get("command") or "")
    timeout = int(params.get("timeout") or 60)
    if not command.strip():
        return {"ok": False, "error": "缺少 command"}
    safe_command = command
    try:
        from tools.sandbox import guard
        try:
            safe_command, _hits = guard.redact_secrets(command)
        except Exception:
            pass
        verdict = None
        try:
            verdict = guard.check_shell_command(
                command, session_id="dbagent", cwd=str(root), tool="bash")
        except TypeError:
            verdict = guard.check_shell_command(command, session_id="dbagent", cwd=str(root))
        if verdict:
            return {"ok": False, "error": f"命令被沙箱守卫拦截：{verdict}", "blocked": True}
    except Exception:
        pass
    try:
        proc = subprocess.run(safe_command, shell=True, cwd=root, timeout=timeout,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"命令超时（>{timeout}s）"}
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
            "stdout": (proc.stdout or "")[-8000:], "stderr": (proc.stderr or "")[-4000:]}


def _scan_tree(root, max_files=600):
    """扫项目结构，返回 {rel: {functions, imports, lang}}。

    Python 走 codetree 的 AST 解析；其它语言用轻量正则提取
    函数/类名与 import，足够给模型看结构，不做完整语法分析。
    """
    import codetree

    skip = {"__pycache__", "node_modules", ".git", ".dbagent", "venv",
            ".venv", "site-packages", "dist", "build", "target",
            "vendor", "bin", "obj", ".next", "coverage"}
    tree = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for name in filenames:
            lang = _LANG_BY_EXT.get(os.path.splitext(name)[1].lower())
            if not lang:
                continue
            full = os.path.join(dirpath, name)
            try:
                rel = os.path.relpath(full, root)
            except ValueError:
                continue
            if lang == "python":
                parsed = codetree.parse_py_file(full, rel)
                if parsed:
                    parsed["functions"] = list((parsed.get("functions") or {}).keys())
                    parsed["lang"] = "python"
                    tree[rel] = parsed
            else:
                info = _scan_other_lang(full, lang)
                if info:
                    info.update({"rel": rel, "lang": lang})
                    tree[rel] = info
            if len(tree) >= max_files:
                return tree
    return tree


# 多语言支持：按扩展名识别，每类语言一组提取规则
_LANG_BY_EXT = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".vue": "vue", ".svelte": "svelte",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".kts": "kotlin", ".swift": "swift", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".rb": "ruby", ".php": "php", ".lua": "lua", ".sh": "shell",
    ".dart": "dart", ".scala": "scala", ".ex": "elixir", ".exs": "elixir",
}

# 各类语言的函数/类定义正则（够用就行，不求完整语法）
_LANG_PATTERNS = {
    "javascript": (
        r"(?:^|\n)\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
        r"(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(",
        r"(?:^|\n)\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)",
        r"(?:^|\n)\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{\s*$",
    ),
    "typescript": (
        r"(?:^|\n)\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
        r"(?:^|\n)\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*[:=]",
        r"(?:^|\n)\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)",
        r"(?:^|\n)\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)",
    ),
    "go": (
        r"(?:^|\n)func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)",
        r"(?:^|\n)type\s+([A-Za-z_]\w*)\s+(?:struct|interface)",
    ),
    "rust": (
        r"(?:^|\n)\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)",
        r"(?:^|\n)\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)",
    ),
    "java": (
        r"(?:^|\n)\s*(?:public|private|protected|static|final|\s)+[\w<>\[\]]+\s+([A-Za-z_]\w*)\s*\(",
        r"(?:^|\n)\s*(?:public|private|protected)?\s*(?:class|interface|enum)\s+([A-Za-z_]\w*)",
    ),
    "kotlin": (
        r"(?:^|\n)\s*(?:suspend\s+)?fun\s+(?:[\w.]+\.)?([A-Za-z_]\w*)",
        r"(?:^|\n)\s*(?:data\s+|sealed\s+|abstract\s+)?(?:class|object|interface)\s+([A-Za-z_]\w*)",
    ),
    "swift": (
        r"(?:^|\n)\s*(?:public\s+|private\s+|internal\s+)?func\s+([A-Za-z_]\w*)",
        r"(?:^|\n)\s*(?:public\s+|private\s+)?(?:class|struct|enum|protocol)\s+([A-Za-z_]\w*)",
    ),
    "c": (
        r"(?:^|\n)[A-Za-z_][\w\s\*]*\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{",
    ),
    "cpp": (
        r"(?:^|\n)\s*(?:[A-Za-z_][\w:\*&<>\s]*\s+)?([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:const\s*)?\{",
        r"(?:^|\n)\s*(?:class|struct)\s+([A-Za-z_]\w*)",
    ),
    "csharp": (
        r"(?:^|\n)\s*(?:public|private|protected|internal|static|async|\s)+[\w<>\[\]]+\s+([A-Za-z_]\w*)\s*\(",
        r"(?:^|\n)\s*(?:public|internal)?\s*(?:class|interface|struct|enum)\s+([A-Za-z_]\w*)",
    ),
    "ruby": (
        r"(?:^|\n)\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!]?)",
        r"(?:^|\n)\s*(?:class|module)\s+([A-Za-z_]\w*)",
    ),
    "php": (
        r"(?:^|\n)\s*(?:public|private|protected|static|\s)*function\s+([A-Za-z_]\w*)",
        r"(?:^|\n)\s*(?:abstract\s+|final\s+)?class\s+([A-Za-z_]\w*)",
    ),
    "lua": (
        r"(?:^|\n)\s*(?:local\s+)?function\s+([\w.:]+)",
    ),
    "shell": (
        r"(?:^|\n)\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\s*\)\s*\{",
    ),
    "dart": (
        r"(?:^|\n)\s*(?:Future<[^>]*>\s+|void\s+|\w+\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:async\s*)?\{",
        r"(?:^|\n)\s*class\s+([A-Za-z_]\w*)",
    ),
    "scala": (
        r"(?:^|\n)\s*def\s+([A-Za-z_]\w*)",
        r"(?:^|\n)\s*(?:case\s+)?class\s+([A-Za-z_]\w*)",
    ),
    "elixir": (
        r"(?:^|\n)\s*defp?\s+([a-z_]\w*[?!]?)",
        r"(?:^|\n)\s*defmodule\s+([A-Za-z_.]+)",
    ),
}

def _scan_other_lang(full, lang):
    """非 Python：用正则抽函数/类名与导入。"""
    try:
        with open(full, encoding="utf-8", errors="ignore") as f:
            src = f.read(200_000)
    except OSError:
        return None
    funcs = []
    for pat in _LANG_PATTERNS.get(lang, ()):
        try:
            funcs.extend(re.findall(pat, src, re.M))
        except re.error:
            continue
    # 去重保序，过滤太短或纯数字的名字
    seen, out = set(), []
    for n in funcs:
        n = (n or "").strip()
        if not n or len(n) < 2 or n.isdigit() or n in seen:
            continue
        seen.add(n)
        out.append(n)
    imports = []
    for pat in (r"^\s*import\s+[^;\n]*?from\s+['\"]([^'\"]+)",
                r"^\s*import\s+['\"]([^'\"]+)",
                r"require\(\s*['\"]([^'\"]+)",
                r"^\s*use\s+([\w:]+)",
                r"^\s*#include\s+[<\"]([^>\"]+)",
                r"^\s*import\s+([\w.]+)"):
        try:
            imports.extend(re.findall(pat, src, re.M))
        except re.error:
            continue
    return {"functions": out[:80], "imports": sorted(set(imports))[:40]}


def _workspace_dirs():
    """定位 workspace 下的 data 与 skills 目录。"""
    data_dir = os.path.join(os.path.dirname(HERE), "workspace", "data")
    skills_dir = os.path.join(os.path.dirname(HERE), "workspace", "skills")
    if not os.path.isdir(data_dir):
        data_dir, skills_dir = "data", "skills"
    return data_dir, skills_dir
