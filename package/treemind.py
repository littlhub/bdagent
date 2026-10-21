"""把结构树渲染为交互式思维导图（单文件 HTML，无外部依赖）。"""

import html
import json


def build_files(tree):
    """把 tree 转成前端需要的文件节点列表。"""
    files = []
    for rel in sorted(tree):
        info = tree[rel] or {}
        functions = info.get("functions") or {}
        files.append({
            "rel": rel,
            "imports": info.get("imports") or [],
            "functions": [{"name": name, "calls": calls}
                          for name, calls in sorted(functions.items())],
        })
    return files


def build_html(files, project_name):
    data = json.dumps(files, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(project_name)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 结构导图</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         background: #f6f7f9; color: #1f2328; }}
  header {{ padding: 16px 20px; background: #fff; border-bottom: 1px solid #e5e7eb;
           position: sticky; top: 0; z-index: 2; }}
  h1 {{ margin: 0 0 8px; font-size: 18px; }}
  input {{ width: 100%; box-sizing: border-box; padding: 8px 10px; font-size: 14px;
          border: 1px solid #d0d7de; border-radius: 6px; }}
  #tree {{ padding: 16px 20px 60px; }}
  details {{ margin: 4px 0 4px 12px; }}
  summary {{ cursor: pointer; padding: 4px 6px; border-radius: 4px; }}
  summary:hover {{ background: #eef1f5; }}
  .file {{ font-weight: 600; }}
  .meta {{ color: #6b7280; font-size: 12px; margin-left: 6px; }}
  .func {{ margin-left: 20px; font-family: ui-monospace, Menlo, Consolas, monospace;
          font-size: 13px; color: #0b5cad; }}
  .calls {{ color: #6b7280; font-size: 12px; }}
  mark {{ background: #ffe58f; }}
  .empty {{ color: #6b7280; }}
</style>
</head>
<body>
<header>
  <h1>{title} · 结构导图</h1>
  <input id="q" placeholder="搜索文件名或函数名…" autocomplete="off">
</header>
<div id="tree"></div>
<script>
const FILES = {data};
const root = document.getElementById("tree");

function render(filter) {{
  root.innerHTML = "";
  const kw = (filter || "").trim().toLowerCase();
  let shown = 0;
  for (const f of FILES) {{
    const funcs = f.functions.filter(fn => !kw || fn.name.toLowerCase().includes(kw));
    const fileHit = !kw || f.rel.toLowerCase().includes(kw);
    if (kw && !fileHit && funcs.length === 0) continue;
    shown++;
    const details = document.createElement("details");
    details.open = Boolean(kw);
    const summary = document.createElement("summary");
    summary.innerHTML = '<span class="file">' + f.rel + '</span>' +
      '<span class="meta">' + f.functions.length + ' 函数 · ' + f.imports.length + ' 导入</span>';
    details.appendChild(summary);
    const list = fileHit && kw ? f.functions : funcs;
    for (const fn of list) {{
      const div = document.createElement("div");
      div.className = "func";
      const calls = fn.calls.length
        ? ' <span class="calls">→ ' + fn.calls.join(", ") + '</span>'
        : '';
      div.innerHTML = fn.name + calls;
      details.appendChild(div);
    }}
    if (f.imports.length) {{
      const imp = document.createElement("div");
      imp.className = "func";
      imp.style.color = "#6b7280";
      imp.textContent = "imports: " + f.imports.join(", ");
      details.appendChild(imp);
    }}
    root.appendChild(details);
  }}
  if (!shown) {{
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "没有匹配的内容。";
    root.appendChild(p);
  }}
}}

document.getElementById("q").addEventListener("input", e => render(e.target.value));
render("");
</script>
</body>
</html>
"""
