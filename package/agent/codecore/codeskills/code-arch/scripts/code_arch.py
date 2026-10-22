# -*- coding: utf-8 -*-
"""
code-arch — 代码结构与架构图生成（本地 + GitHub 双模式）。

本地模式: 调用 结构和架构.py (codearch) 分析本地仓库,
          输出 JSON + Mermaid, 再通过 kroki.io 渲染 PNG。
GitHub 模式: 从 GitDiagram (gitdiagram.com) 抓取已生成的交互架构图,
          提取 Mermaid + 结构化 diagram-state JSON + 离线渲染 PNG。

用法 (CLI):
    python code_arch.py local  <repo_path>  [--json out.json] [--mmd out.mmd] [--png out.png]
    python code_arch.py github <owner>/<repo> [--json out.json] [--mmd out.mmd] [--png out.png]
    python code_arch.py local  . --max-files 50

说明:
    - 本地: 需要 repo_path 是目录; codearch 在脚本同目录或环境变量 CODEARCH 指定处寻找 结构和架构.py。
    - GitHub: 需要 repo 形如 "OpenAI/codex"; 若 GitDiagram 服务端已生成过图则直接复用;
      未生成则回退到本地分析 GitHub 官方 raw 源(若 GITHUB_TOKEN 已设置)。
"""
import os
import re
import sys
import json
import codecs
import shutil
import argparse
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
# 工作区根: 优先 SKILLMCP_WORKSPACE; 否则按 UTF-8 字节直接构造本部署固定路径
# (避免 Windows 控制台 GBK/ANSI 损坏中文路径字符串)
_WORKSPACE_ENV = os.environ.get("SKILLMCP_WORKSPACE")
if not _WORKSPACE_ENV:
    _ca = os.environ.get("CODEARCH")
    if _ca:
        _cand = os.path.dirname(os.path.dirname(os.path.abspath(_ca)))
        if os.path.isdir(os.path.join(_cand, "skills")):
            _WORKSPACE_ENV = _cand
    if not _WORKSPACE_ENV:
        # UTF-8 字节序列: "G:\\deep工作区"
        _WORKSPACE_ENV = os.fsdecode("G:\\deep工作区".encode("utf-8"))
_WORKSPACE = os.path.abspath(_WORKSPACE_ENV)


def _default_output_dir(sub="code-arch"):
    """默认输出目录: <workspace>/workspase/<sub>/，可用 SKILLMCP_WORKSPACE 覆盖。"""
    ws = os.environ.get("SKILLMCP_WORKSPACE") or _WORKSPACE
    out = Path(ws) / "workspase" / sub
    out.mkdir(parents=True, exist_ok=True)
    return out

# 强制 stdout/stderr UTF-8, 避免 Windows GBK 编码错误
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── 颜色/日志 ─────────────────────────────────────────────────
def _c(msg):
    print(msg, flush=True)


# ════════════════════════════════════════════════════════════
# 一、本地模式: 运行 结构和架构.py (codearch)
# ════════════════════════════════════════════════════════════

def _find_codearch():
    """定位 结构和架构.py (codearch 模块)。

    Windows 控制台/环境变量可能把中文路径的字节损坏 (GBK 往返),
    因此:
    - 先按 UTF-8/系统 ANSI 尝试还原 CODEARCH 的乱码字节
    - 兜底扫描工作区根目录, 用「文件名 UTF-8 字节序列」与候选模式匹配,
      避免直接对 (可能损坏的) 中文字面量做比较
    """
    # 目标文件名的 UTF-8 字节 (作为匹配锚点, 不受控制台编码影响)
    target_names_utf8 = {
        "结构和架构.py".encode("utf-8"),
        b"codearch.py",
    }

    # 1) 显式环境变量 (含编码损坏修复)
    env = os.environ.get("CODEARCH")
    if env:
        if os.path.isfile(env):
            return os.path.abspath(env)
        # 还原: 把 env 当 latin-1 字节 -> UTF-8 文本
        for codec_from in ("latin-1", "cp1252"):
            try:
                raw = env.encode(codec_from)
                fixed = raw.decode("utf-8", errors="strict")
            except Exception:
                continue
            if os.path.isfile(fixed):
                return os.path.abspath(fixed)
        _c("⚠️ CODEARCH 指向的文件不存在: %s" % env)

    # 2) 由 SKILLMCP_WORKSPACE / _WORKSPACE 推断
    def _scan(root_dir):
        if not os.path.isdir(root_dir):
            return None
        for cand in os.listdir(root_dir):
            p = os.path.join(root_dir, cand)
            if not os.path.isfile(p):
                continue
            # 取文件名的 UTF-8 字节做匹配 (控制台损坏不影响磁盘上的真实字节)
            try:
                name_b = os.fsencode(cand)  # 系统原生路径字节
            except Exception:
                name_b = cand.encode("utf-8")
            if name_b in target_names_utf8:
                return p
            # 名称含 "codearch" 或 "结构" 的字面匹配
            if cand.startswith("codearch") and cand.endswith(".py"):
                return p
        return None

    for ws in (os.environ.get("SKILLMCP_WORKSPACE"), _WORKSPACE):
        if ws:
            found = _scan(ws)
            if found:
                return os.path.abspath(found)

    # 3) 脚本同级 / 上级
    for cand in (
        os.path.join(_HERE, "结构和架构.py"),
        os.path.join(_HERE, "codearch.py"),
        os.path.join(_HERE, "..", "..", "结构和架构.py"),
        os.path.join(_HERE, "..", "..", "codearch.py"),
    ):
        p = os.path.abspath(cand)
        if os.path.isfile(p):
            return p

    # 4) 兜底: 扫描工作区根 (os.listdir 在某些环境受限, 失败也不致命)
    found = _scan(_WORKSPACE)
    if found:
        return os.path.abspath(found)
    # 5) 终极兜底: 按 UTF-8 字节直接构造硬编码路径 (绕过控制台编码损坏)
    hard = os.fsdecode("G:\\deep工作区\\结构和架构.py".encode("utf-8"))
    if os.path.isfile(hard):
        return hard
    hard2 = os.fsdecode("G:\\deep工作区\\codearch.py".encode("utf-8"))
    if os.path.isfile(hard2):
        return hard2
    return None


def _run_codearch(root, out_json, out_mmd, max_files=None):
    """调用 codearch.analyze_codebase。"""
    # 1) 优先找 结构和架构.py (含中文文件名, Windows 控制台 GBK 下易损坏);
    #    找不到则用同目录的 ASCII 副本 codearch_loaded.py
    codearch_path = _find_codearch()
    ascii_copy = os.path.join(_HERE, "codearch_loaded.py")
    if codearch_path and os.path.isfile(codearch_path):
        source_path = codearch_path
    elif os.path.isfile(ascii_copy):
        source_path = ascii_copy
    else:
        raise RuntimeError("找不到 codearch 源文件 (结构和架构.py 或 codearch_loaded.py)")

    # 用子进程执行, 避免污染当前解释器; 通过环境变量传参
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["CODEARCH_ROOT"] = str(Path(root).resolve())
    env["CODEARCH_OUT_JSON"] = str(out_json)
    env["CODEARCH_OUT_MMD"] = str(out_mmd)
    if max_files:
        env["CODEARCH_MAX_FILES"] = str(max_files)

    # 把源文件复制成 ASCII 名 codearch_loaded.py, 避免中文路径在 import 时损坏
    codearch_copy = ascii_copy
    if os.path.abspath(source_path) != os.path.abspath(ascii_copy):
        shutil.copyfile(source_path, ascii_copy)

    runner = os.path.join(_HERE, "codearch_runner.py")
    stats_path = os.path.join(os.path.dirname(os.path.abspath(out_json)), "codearch_stats.json")
    os.makedirs(os.path.dirname(stats_path) or ".", exist_ok=True)
    with open(runner, "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n")
        f.write("import sys, os, json\n")
        f.write(f"sys.path.insert(0, {json.dumps(_HERE)})\n")
        f.write("import codearch_loaded as m\n")
        f.write("root = os.environ['CODEARCH_ROOT']\n")
        f.write("out_json = os.environ['CODEARCH_OUT_JSON']\n")
        f.write("out_mmd = os.environ['CODEARCH_OUT_MMD']\n")
        f.write("max_files = int(os.environ.get('CODEARCH_MAX_FILES') or 0) or None\n")
        f.write("result = m.analyze_codebase(root, write_json=out_json, write_mermaid=out_mmd, max_files=max_files)\n")
        f.write(f"json.dump({{'stats': result['stats']}}, open({json.dumps(stats_path)}, 'w', encoding='utf-8'))\n")
    env["CODEARCH_STATS"] = stats_path
    try:
        r = subprocess.run(
            [sys.executable, runner],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        try:
            os.remove(runner)
        except OSError:
            pass
    stdout_txt = (r.stdout or "").strip()
    stderr_txt = (r.stderr or "").strip()
    if r.returncode != 0:
        if stdout_txt:
            _c("[codearch] " + stdout_txt[-800:])
        if stderr_txt:
            _c("[codearch stderr] " + stderr_txt[-500:])
        raise RuntimeError("codearch 运行失败 (exit %s)" % r.returncode)
    if stdout_txt:
        _c(stdout_txt[-800:])
    # 读取 stats
    try:
        with open(out_json, encoding="utf-8") as fh:
            data = json.load(fh)
        return data
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════
# 二、GitHub 模式: 从 GitDiagram 抓取已生成的图
# ════════════════════════════════════════════════════════════

def _http_get(url, timeout=40):
    """带 308 重定向的 GET。"""
    headers_in = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    }
    cur = url
    for _ in range(10):
        req = urllib.request.Request(cur, headers=headers_in)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if not loc:
                    raise
                if loc.startswith("/"):
                    p = urllib.parse.urlparse(cur)
                    loc = f"{p.scheme}://{p.netloc}{loc}"
                cur = loc
                continue
            raise
        status = getattr(resp, "status", getattr(resp, "code", 200))
        if status in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if not loc:
                return resp.read(), cur
            resp.close()
            if loc.startswith("/"):
                p = urllib.parse.urlparse(cur)
                loc = f"{p.scheme}://{p.netloc}{loc}"
            cur = loc
            continue
        return resp.read(), cur
    return b"", cur


def _fetch_gitdiagram_page(owner_repo):
    url = f"https://gitdiagram.com/{owner_repo}"
    raw, final = _http_get(url)
    text = raw.decode("utf-8", "replace")
    return url, final, text


def _extract_mermaid(text):
    i = text.find("flowchart TD")
    if i < 0:
        return None
    seg_end = text.rfind("class ", i)
    mermaid = codecs.decode(text[i:seg_end + 1], "unicode_escape")
    lines = [ln.rstrip() for ln in mermaid.split("\n")]
    while lines and re.match(r"^\d+:", lines[-1]):
        lines.pop()
    while lines and re.match(r'^\s*$', lines[0]):
        lines.pop(0)
    return "\n".join(lines).rstrip() + "\n"


def _extract_state(text):
    """从 RSC 载荷里提取 diagram-state JSON (groups/nodes/edges/explanation)。

    GitDiagram 的 state 嵌在 Next.js RSC 流里, 形如:
        self.__next_f.push([1,"{...json with \\\" escapes...}"])
    需要先按 JS 字符串字面量规则还原一次 (json.loads('"' + blob + '"')),
    才能在还原后的文本里定位 "graph" / "username" 等 key。
    """
    # 1) 合并所有 RSC chunk 并还原转义
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', text, re.S)
    if not chunks:
        return None
    blob = "".join(chunks)
    try:
        decoded = json.loads('"' + blob + '"')
    except Exception:
        return None
    if '"graph"' not in decoded and '"nodes"' not in decoded:
        return None

    # 2) 在 decoded 里定位 {"username"...} 对象 (JSON 已单引号还原, 正常配平)
    k = decoded.find('"graph":{')
    if k < 0:
        k = decoded.find('"graph"')
    if k < 0:
        return None
    start = decoded.rfind('{"username"', 0, k)
    if start < 0:
        start = decoded.rfind('{', 0, k)
    depth, end2, in_str, esc = 0, start, False, False
    for idx in range(start, min(start + 400000, len(decoded))):
        ch = decoded[idx]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end2 = idx + 1
                    break
    raw = decoded[start:end2]
    state = json.loads(raw)
    # state 形如 {"username","repo","initialState":{"explanation","graph","generations"...}}
    initial = state.get("initialState", {})
    graph = initial.get("graph", state.get("graph", {}))
    explanation = initial.get("explanation", state.get("explanation", ""))
    generations = initial.get("generations") or state.get("generations") or []
    gen_at = (
        (generations[-1].get("createdAt") if generations else None)
        or initial.get("lastSuccessfulAt")
        or initial.get("updatedAt")
        or initial.get("createdAt")
        or state.get("lastSuccessfulAt")
        or state.get("updatedAt")
        or state.get("createdAt")
        or "?"
    )
    return {"explanation": explanation, "graph": graph, "generated_at": gen_at}


def _github(owner_repo, out_json, out_mmd, out_png):
    _c(f"🌐 GitDiagram: https://gitdiagram.com/{owner_repo}")
    url, final, text = _fetch_gitdiagram_page(owner_repo)
    _c(f"   fetched: {final} ({len(text)} bytes)")

    mermaid = _extract_mermaid(text)
    if not mermaid:
        _c("⚠️ 页面里没有已生成的图, GitDiagram 可能尚未生成该仓库; 回退到本地分析 raw。")
        return _fallback_github_local(owner_repo, out_json, out_mmd, out_png)

    with open(out_mmd, "w", encoding="utf-8") as f:
        f.write(mermaid)
    _c(f"💾 Mermaid 已写入 {out_mmd}")

    state = _extract_state(text)
    if state:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        _c("💾 结构化 state 已写入 %s" % out_json)
        g = state.get("graph", {})
        _c("   生成时间: %s  分组: %d  节点: %d  边: %d" % (
            state.get("generated_at"),
            len(g.get("groups", [])),
            len(g.get("nodes", [])),
            len(g.get("edges", [])),
        ))
    else:
        _c("⚠️ 未解析到结构化 state")

    _render_mermaid_png(out_mmd, out_png)
    return state or {"mermaid": mermaid}


def _fallback_github_local(owner_repo, out_json, out_mmd, out_png):
    """GitDiagram 没图时, 退回到本地 codearch 分析 GitHub raw 源 (可选)。"""
    token = os.environ.get("GITHUB_TOKEN", "")
    # 用 git clone --depth 1
    base = Path(_HERE) / ".gh_tmp"
    base.mkdir(exist_ok=True)
    target = base / owner_repo.replace("/", "_")
    if not target.exists():
        _c(f"⬇️  浅克隆 {owner_repo} (depth 1)")
        cmd = ["git", "clone", "--depth", "1"]
        if token:
            clone_url = f"https://x-access-token:{token}@github.com/{owner_repo}.git"
        else:
            clone_url = f"https://github.com/{owner_repo}.git"
        cmd += [clone_url, str(target)]
        subprocess.run(cmd, check=True)
    data = _run_codearch(target, out_json, out_mmd, max_files=400)
    _render_mermaid_png(out_mmd, out_png)
    return data


# ════════════════════════════════════════════════════════════
# 三、Mermaid → PNG (本地 mmdc 或 kroki.io)
# ════════════════════════════════════════════════════════════

def _render_mermaid_png(mmd_path, png_path):
    src = Path(mmd_path).read_text(encoding="utf-8")
    # 1) 本地 mmdc
    mmdc = shutil.which("mmdc") or shutil.which("mmdc.cmd")
    if mmdc:
        r = subprocess.run(
            [mmdc, "-i", mmd_path, "-o", png_path, "-w", "1600", "-b", "white"],
            capture_output=True, text=True, cwd=os.path.dirname(mmd_path) or ".",
        )
        if r.returncode == 0 and os.path.exists(png_path):
            _c(f"🖼️  PNG (mmdc): {png_path}")
            return True
        _c("   mmdc 失败: " + r.stderr[:200])
    # 2) kroki.io
    try:
        req = urllib.request.Request(
            "https://kroki.io/mermaid/png",
            data=src.encode("utf-8"),
            headers={"Content-Type": "text/plain", "User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        if data:
            with open(png_path, "wb") as f:
                f.write(data)
            _c(f"🖼️  PNG (kroki.io): {png_path} ({len(data)} bytes)")
            return True
    except Exception as e:
        _c(f"   kroki 渲染失败: {e}")
    _c("   未生成 PNG; 可把 .mmd 粘贴到 https://mermaid.live 在线渲染")
    return False


# ════════════════════════════════════════════════════════════
# 四、本地模式入口
# ════════════════════════════════════════════════════════════

def _local(root, out_json, out_mmd, out_png, max_files=None):
    _c(f"💻 本地分析: {root}")
    data = _run_codearch(root, out_json, out_mmd, max_files=max_files)
    _render_mermaid_png(out_mmd, out_png)
    return data


# ════════════════════════════════════════════════════════════
# 五、CLI
# ════════════════════════════════════════════════════════════

def main(argv=None):
    ap = argparse.ArgumentParser(description="code-arch skill: 本地 + GitHub 架构/结构图生成")
    ap.add_argument("mode", choices=["local", "github"])
    ap.add_argument("target", help="local: 仓库路径; github: owner/repo")
    ap.add_argument("--json", default=None, help="输出 JSON 路径")
    ap.add_argument("--mmd", default=None, help="输出 Mermaid 路径")
    ap.add_argument("--png", default=None, help="输出 PNG 路径")
    ap.add_argument("--max-files", type=int, default=None, help="本地模式最大分析文件数")
    args = ap.parse_args(argv)

    # 默认输出目录: 统一落在 workspase/code-arch/ (工作区根)
    # 按目标命名避免多次运行互相覆盖: local 取目录 basename, github 取 owner_repo
    out_root = _default_output_dir()
    if args.mode == "local":
        slug = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(os.path.abspath(args.target)))
    else:
        slug = re.sub(r"[^A-Za-z0-9_.-]", "_", args.target.replace("/", "_"))
    out_json = args.json or str(out_root / (slug + ".json"))
    out_mmd = args.mmd or str(out_root / (slug + ".mmd"))
    out_png = args.png or str(out_root / (slug + ".png"))

    if args.mode == "local":
        if not os.path.isdir(args.target):
            _c(f"❌ 本地路径不存在: {args.target}")
            return 2
        data = _local(args.target, out_json, out_mmd, out_png, max_files=args.max_files)
        _c(f"\n✅ 本地完成。 JSON: {out_json}")
        _c(f"                  Mermaid: {out_mmd}")
        _c(f"                  PNG: {out_png}")
    else:
        if not re.match(r"^[\w.-]+/[\w.-]+$", args.target):
            _c("❌ github 目标必须形如 owner/repo (例: OpenAI/codex)")
            return 2
        data = _github(args.target, out_json, out_mmd, out_png)
        _c(f"\n✅ GitHub ({args.target}) 完成。 JSON: {out_json}")
        _c(f"                  Mermaid: {out_mmd}")
        _c(f"                  PNG: {out_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
