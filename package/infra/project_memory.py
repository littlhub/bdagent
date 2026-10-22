"""项目记忆：在被修改项目内落盘结构与修复记录，并注入系统提示词。

目录布局（都放在项目的 .dbagent/ 下，不进版本库也不污染源码）：

    <project>/.dbagent/structure.json   代码结构快照（每轮扫描后更新）
    <project>/.dbagent/fixes.md         问题与修复记录（追加式）
    <project>/.dbagent/context.md       给模型看的上下文摘要（自动生成）

设计取舍：
  · 结构快照用 JSON，方便程序读；fixes 用 Markdown，方便人看。
  · context.md 是二者拼成的短摘要，直接拼进系统提示词，
    避免把上百个文件的完整结构塞进上下文。
"""
from __future__ import annotations

import json
import os
import time

MEM_DIR = ".dbagent"
STRUCTURE_FILE = "structure.json"
FIXES_FILE = "fixes.md"
CONTEXT_FILE = "context.md"

# 注入提示词的上下文上限：太大会挤占模型的实际工作空间
MAX_CONTEXT_CHARS = 3000
MAX_FIXES_SHOWN = 12


def _mem_dir(root: str) -> str:
    path = os.path.join(root, MEM_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def structure_path(root: str) -> str:
    return os.path.join(_mem_dir(root), STRUCTURE_FILE)


def fixes_path(root: str) -> str:
    return os.path.join(_mem_dir(root), FIXES_FILE)


def context_path(root: str) -> str:
    return os.path.join(_mem_dir(root), CONTEXT_FILE)


def save_structure(root: str, tree: dict) -> str:
    """落盘代码结构快照，返回路径。"""
    path = structure_path(root)
    payload = {
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(tree),
        "files": tree,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        return ""
    return path


def load_structure(root: str) -> dict:
    path = structure_path(root)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def append_fix(root: str, problem: str, fix: str, files=None) -> str:
    """追加一条「问题 → 修复」记录。"""
    path = fixes_path(root)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    lines = [f"## {stamp}  {problem.strip()[:120]}", ""]
    if files:
        lines.append("涉及文件：" + "、".join(f"`{f}`" for f in files))
        lines.append("")
    lines.append(fix.strip())
    lines.append("")
    body = "\n".join(lines)
    try:
        is_new = not os.path.isfile(path)
        with open(path, "a", encoding="utf-8") as f:
            if is_new:
                f.write("# 问题与修复记录\n\n"
                        "由 DBAgent 自动维护：每次修复后追加一条，供后续对话参考。\n\n")
            f.write(body + "\n")
    except OSError:
        return ""
    return path


def _recent_fixes(root: str, limit=MAX_FIXES_SHOWN) -> list[str]:
    path = fixes_path(root)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    # 按二级标题切段，取最近几段
    chunks = [c.strip() for c in text.split("\n## ") if c.strip()]
    if chunks and chunks[0].startswith("# 问题与修复记录"):
        chunks = chunks[1:]
    return chunks[-limit:]


def _structure_digest(root: str, tree: dict | None = None) -> str:
    """把结构压成几行摘要：模块分布 + 关键文件。"""
    data = {"files": tree} if tree else load_structure(root)
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict) or not files:
        return ""
    groups: dict[str, int] = {}
    langs: dict[str, int] = {}
    for rel, info in files.items():
        head = rel.split(os.sep)[0] if os.sep in rel else (
            rel.split("/")[0] if "/" in rel else "(根目录)")
        groups[head] = groups.get(head, 0) + 1
        lang = (info or {}).get("lang") or "python"
        langs[lang] = langs.get(lang, 0) + 1
    parts = [f"{k}({v})" for k, v in
             sorted(groups.items(), key=lambda kv: -kv[1])[:10]]
    lang_text = "、".join(f"{k}({v})" for k, v in
                         sorted(langs.items(), key=lambda kv: -kv[1])[:6])
    lines = [f"项目共 {len(files)} 个源文件，分布：" + "、".join(parts)]
    if len(langs) > 1:
        lines.append("语言：" + lang_text)

    # 挑几个函数最多的文件当「核心模块」
    def nfuncs(rel):
        info = files.get(rel) or {}
        return len(info.get("functions") or {})

    top = sorted(files, key=lambda r: -nfuncs(r))[:6]
    detail = [f"{r}（{nfuncs(r)} 个函数）" for r in top if nfuncs(r)]
    if detail:
        lines.append("核心文件：" + "、".join(detail))
    return "\n".join(lines)


def build_context(root: str, tree: dict | None = None) -> str:
    """拼出注入提示词的上下文摘要，并写入 context.md。"""
    parts = []
    digest = _structure_digest(root, tree)
    if digest:
        parts.append("【项目结构】\n" + digest)
    fixes = _recent_fixes(root)
    if fixes:
        body = "\n\n".join(fixes)
        if len(body) > MAX_CONTEXT_CHARS // 2:
            body = body[-(MAX_CONTEXT_CHARS // 2):]
            body = "…（更早记录已省略）\n\n" + body
        parts.append("【近期修复记录】\n" + body)
    text = "\n\n".join(parts).strip()
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n…（已截断）"
    if text:
        try:
            with open(context_path(root), "w", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
    return text
