# -*- coding: utf-8 -*-
"""
codearch_loaded.py — 结构和架构.py (codearch) 的 ASCII 命名副本。

由 skill 维护者同步自 G:/deep工作区/结构和架构.py，
避免在 Windows 控制台 GBK 环境下引用中文文件名时路径被编码损坏。
若原文件更新，请重新复制覆盖本文件。
"""
import ast
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# ============================================================
# 1. 扫描层：遍历仓库，按语言过滤
# ============================================================

DEFAULT_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build",
    "__pycache__", ".venv", "venv", ".env", "target", "out",
    ".idea", ".vscode", "coverage", ".next", ".nuxt",
}

LANG_EXTS = {
    ".py": "py", ".js": "js", ".ts": "ts", ".jsx": "jsx", ".tsx": "tsx",
    ".java": "java", ".cs": "cs", ".c": "c", ".cpp": "cpp", ".cc": "cpp",
    ".go": "go", ".rs": "rs", ".rb": "rb", ".php": "php",
    ".lua": "lua", ".gd": "gd",
}

MAX_FILE_SIZE = 1_000_000  # 1MB，超过跳过


def scan_files(root, exclude_dirs=None, exts=None):
    """遍历仓库，返回待分析文件路径列表。"""
    exclude_dirs = exclude_dirs or DEFAULT_EXCLUDE_DIRS
    exts = exts or set(LANG_EXTS.keys())
    root = Path(root).resolve()
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地过滤目录，避免进入
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix not in exts:
                continue
            try:
                if p.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            files.append(p)
    return files


# ============================================================
# 2. 通用语言正则解析（粗略，兜底非 Python 文件）
# ============================================================

def _read_text(path):
    p = Path(path) if isinstance(path, str) else path
    for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError, OSError):
            continue
    return ""


CLASS_PATTERNS = {
    "java":  re.compile(r"^\s*(?:public|private|protected)?\s*(?:abstract|final)?\s*(?:class|interface)\s+(\w+)", re.M),
    "cs":    re.compile(r"^\s*(?:public|private|protected|internal|static|abstract|sealed|partial)?\s*(?:class|interface|struct|enum)\s+(\w+)", re.M),
    "go":    re.compile(r"^\s*type\s+(\w+)\s+(?:struct|interface)", re.M),
    "ts":    re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+(\w+)", re.M),
    "tsx":   re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+(\w+)", re.M),
    "js":    re.compile(r"^\s*(?:export\s+)?(?:class|function)\s+(\w+)", re.M),
    "jsx":   re.compile(r"^\s*(?:export\s+)?(?:class|function)\s+(\w+)", re.M),
    "rs":    re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait|mod|fn)\s+(\w+)", re.M),
    "rb":    re.compile(r"^\s*(?:module|class)\s+(\w+)", re.M),
}

FUNC_PATTERNS = {
    "python": re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)", re.M),
    "js":     re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.M),
    "java":   re.compile(r"^\s*(?:public|private|protected)?\s*(?:static|final|synchronized)?\s*[\w<>\[\],\s]+\s+(\w+)\s*\([^)]*\)\s*(?:throws[\w\s,]+)?\s*\{", re.M),
    "go":     re.compile(r"^\s*func\s+(?:\(\w+\)\s*)?(\w+)\s*\(", re.M),
    "rs":     re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)\s*\(", re.M),
}

IMPORT_PATTERNS = {
    "python": re.compile(r"^\s*import\s+([\w\s,\.]+)|^\s*from\s+([\w\.]+)\s+import", re.M),
    "js":     re.compile(r"^\s*(?:import|require)\s*[({]?\s*['\"]([\w@/\.{}\'\" ]+)['\"]", re.M),
    "ts":     re.compile(r"^\s*(?:import|require)\s*[({]?\s*['\"]([\w@/\.{}\'\" ]+)['\"]", re.M),
    "go":     re.compile(r"^\s*(\w+)\s+[\"']([\w./-]+)[\"']", re.M),
    "rs":     re.compile(r"^\s*(?:pub\s+)?use\s+([\w:\{\}\s,]+);|^\s*(?:pub\s+)?mod\s+(\w+)", re.M),
}


def _parse_generic(lang, source):
    """非 Python 语言的粗略解析。"""
    info = {"language": lang, "imports": [], "classes": [], "functions": {}}
    fn = FUNC_PATTERNS.get(lang)
    if fn:
        for m in fn.finditer(source):
            name = m.group(1)
            if name not in ("if", "for", "while", "switch", "return", "else"):
                info["functions"][name] = {"lineno": source[:m.start()].count("\n") + 1, "calls": []}
    cl = CLASS_PATTERNS.get(lang)
    if cl:
        for m in cl.finditer(source):
            info["classes"].append({"name": m.group(1), "methods": []})
    im = IMPORT_PATTERNS.get(lang)
    if im:
        for m in im.finditer(source):
            mod = next((g for g in m.groups() if g), "")
            if mod and mod not in ("self", "None", "true", "false"):
                info["imports"].append(mod)
    return info


# ============================================================
# 3. 调用/资源提取
# ============================================================

API_PATTERNS = re.compile(r"https?://[\w\-./]+|api[_-]?key\s*[:=]|POST|GET|requests\.\w+|httpx\.\w+", re.I)
TABLE_PATTERNS = re.compile(r"\b(TABLE_|SELECT|INSERT|UPDATE|DELETE|FROM|JOIN|INTO|VALUES)\s+([\"']?)([\w_]+)\2", re.I)
CONFIG_PATTERNS = re.compile(r"os\.environ(?:\.get)?\s*[\(]?\s*[\"]?([\w_]+)[\"']|settings\.([\w_]+)|getenv\(\s*[\"]?([\w_]+)[\"']")
CACHE_PATTERNS = re.compile(r"cache\.set|cache\.get|redis\.\w+|inmemory_cache\.\w+", re.I)


def _extract_calls(source, lang):
    """从源码文本里粗略提取被调用的本地标识符（排除关键字）。"""
    if lang == "python":
        return list(set(re.findall(r"\b([a-z_]\w{2,})\s*\(", source)))
    return list(set(re.findall(r"\b([a-zA-Z_]\w{3,})\s*\(", source)))


# ============================================================
# 4. 架构统计 + Mermaid 生成
# ============================================================

class ArchAnalyzer:
    def __init__(self, files):
        self.files = files  # dict path -> info
        self.table_readers = defaultdict(list)
        self.table_writers = defaultdict(list)
        self.external_apis = defaultdict(list)
        self.config_keys = defaultdict(list)
        self.cache_keys = defaultdict(list)

    def run(self):
        for path, info in self.files.items():
            src = info.get("_source", "")
            # 外部 API
            for m in API_PATTERNS.finditer(src):
                self.external_apis[m.group(0)[:60]].append(path)
            # 数据表
            for m in TABLE_PATTERNS.finditer(src):
                verb = m.group(1).upper()
                tbl = m.group(3)
                if verb in ("SELECT", "FROM", "JOIN"):
                    self.table_readers[tbl].append(path)
                elif verb in ("INSERT", "UPDATE", "DELETE", "INTO", "VALUES"):
                    self.table_writers[tbl].append(path)
            # 配置键
            for m in CONFIG_PATTERNS.finditer(src):
                key = next((g for g in m.groups() if g), "")
                if key:
                    self.config_keys[key].append(path)
            # 缓存键
            for m in CACHE_PATTERNS.finditer(src):
                self.cache_keys[m.group(0)[:40]].append(path)
        return {
            "tables": {t: {"readers": sorted(set(v)), "writers": sorted(set(w))}
                       for t, v, w in
                       [(k, self.table_readers[k], self.table_writers.get(k, [])) for k in set(self.table_readers) | set(self.table_writers)]},
            "external_apis": {k: sorted(set(v)) for k, v in self.external_apis.items()},
            "config_keys": {k: sorted(set(v)) for k, v in self.config_keys.items()},
            "cache_keys": {k: sorted(set(v)) for k, v in self.cache_keys.items()},
        }

    def build_mermaid(self, max_nodes=80):
        """把文件→函数调用关系画成 Mermaid flowchart。"""
        lines = ["flowchart TD", "    subgraph repo[\"Repository\"]"]
        nodes_used = 0
        node_map = {}
        for path in sorted(self.files):
            short = path.replace("\\", "/").split("/")[-1][:30]
            nid = "f_%s" % re.sub(r"[^a-zA-Z0-9_]", "_", path)[:50]
            node_map[path] = nid
            if nodes_used < max_nodes:
                lines.append("        %s[\"%s\"]" % (nid, short))
                nodes_used += 1
            else:
                lines.append("        %s[\"%s...\" style=stroke-dash]" % (nid, short))
        lines.append("    end")
        # 调用边：函数间
        for path, info in self.files.items():
            fn_id = node_map.get(path)
            if not fn_id:
                continue
            calls = set()
            for fn_name, fn in (info.get("functions") or {}).items():
                calls.update(fn.get("calls") or [])
            for other_path, other_info in self.files.items():
                other_fns = set((other_info.get("functions") or {}).keys())
                hit = calls & other_fns
                if hit and other_path != path:
                    lines.append("    %s -->|%s| %s" % (fn_id, ",".join(sorted(hit))[:20], node_map.get(other_path, "?")))
        return "\n".join(lines)


# ============================================================
# 5. 主入口
# ============================================================

def analyze_codebase(root, write_json=None, write_mermaid=None, max_files=None):
    files = scan_files(root)
    if max_files:
        files = files[:max_files]

    files_info = {}
    for p in files:
        rel = p.relative_to(Path(root)).as_posix()
        abs_path = Path(p) if isinstance(p, str) else p
        lang = LANG_EXTS.get(p.suffix, "other")
        source = _read_text(abs_path)
        if lang == "py":
            try:
                import codearch_ast as ast_mod  # noqa  不存在则回退
            except Exception:
                ast_mod = None
            if ast_mod:
                info = ast_mod.parse(source, str(abs_path))
            else:
                info = _parse_python_fallback(source, str(abs_path))
        else:
            info = _parse_generic(lang, source)
        info["language"] = lang
        info["_source"] = source
        files_info[rel] = info

    analyzer = ArchAnalyzer(files_info)
    architecture = analyzer.run()

    result = {
        "files": {k: {kk: vv for kk, vv in v.items() if kk != "_source"} for k, v in files_info.items()},
        "graph": {"nodes": [], "edges": []},
        "architecture": architecture,
        "mermaid": analyzer.build_mermaid(),
        "stats": {
            "file_count": len(files_info),
            "function_count": sum(len(v.get("functions") or {}) for v in files_info.values()),
            "class_count": sum(len(v.get("classes") or []) for v in files_info.values()),
            "table_count": len(architecture["tables"]),
            "external_api_count": len(architecture["external_apis"]),
        },
    }

    if write_json:
        with open(write_json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    if write_mermaid:
        with open(write_mermaid, "w", encoding="utf-8") as f:
            f.write(result["mermaid"])
    return result


def _parse_python_fallback(source, file_path):
    """无外部依赖时纯正则提取 Python 结构（与 AST 输出兼容）。"""
    info = {"language": "py", "imports": [], "classes": [], "functions": {}}
    for m in IMPORT_PATTERNS["python"].finditer(source):
        mod = m.group(1) or m.group(2) or ""
        if mod:
            info["imports"].append(mod.strip().split(",")[0].strip())
    for m in CLASS_PATTERNS.get("python", re.compile(r"^\s*class\s+(\w+)", re.M)).finditer(source):
        info["classes"].append({"name": m.group(1), "methods": []})
    for m in FUNC_PATTERNS["python"].finditer(source):
        name = m.group(1)
        lineno = source[:m.start()].count("\n") + 1
        calls = _extract_calls(source[m.end():m.end() + 4000], "python")
        info["functions"][name] = {"lineno": lineno, "calls": calls}
    return info


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    out_json = sys.argv[2] if len(sys.argv) > 2 else "codearch.json"
    out_mmd = sys.argv[3] if len(sys.argv) > 3 else "architecture.mmd"
    result = analyze_codebase(root, write_json=out_json, write_mermaid=out_mmd)
    print(f"\n✅ 完成。JSON: {out_json}, Mermaid: {out_mmd}")
