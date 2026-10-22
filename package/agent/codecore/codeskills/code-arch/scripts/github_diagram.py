# -*- coding: utf-8 -*-
"""把 GitHub 模式产物也按目标名命名 (codex.* / next.js.* 等), 避免互相覆盖。"""
import os, re, json, codecs, shutil, subprocess
import urllib.request, urllib.parse, urllib.error
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE = os.fsdecode("G:\\deep工作区".encode("utf-8"))
_OUT = Path(_WORKSPACE) / "workspase" / "code-arch"
_OUT.mkdir(parents=True, exist_ok=True)

def get(url, timeout=40):
    cur = url
    for _ in range(10):
        req = urllib.request.Request(cur, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
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
        status = getattr(resp, "status", 200)
        if status in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if not loc:
                return resp.read()
            resp.close()
            if loc.startswith("/"):
                p = urllib.parse.urlparse(cur)
                loc = f"{p.scheme}://{p.netloc}{loc}"
            cur = loc
            continue
        return resp.read()
    return b""

def extract_mermaid(text):
    i = text.find("flowchart TD")
    if i < 0:
        return None
    seg_end = text.rfind("class ", i)
    mermaid = codecs.decode(text[i:seg_end + 1], "unicode_escape")
    lines = [ln.rstrip() for ln in mermaid.split("\n")]
    while lines and re.match(r"^\d+:", lines[-1]):
        lines.pop()
    while lines and re.match(r"^\s*$", lines[0]):
        lines.pop(0)
    return "\n".join(lines).rstrip() + "\n"

def extract_state(text):
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', text, re.S)
    if not chunks:
        return None
    decoded = json.loads('"' + "".join(chunks) + '"')
    k = decoded.find('"graph":{')
    if k < 0:
        return None
    start = decoded.rfind('{"username"', 0, k)
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
    state = json.loads(decoded[start:end2])
    initial = state.get("initialState", {})
    graph = initial.get("graph", state.get("graph", {}))
    explanation = initial.get("explanation", state.get("explanation", ""))
    gen_at = initial.get("lastSuccessfulAt") or state.get("updatedAt") or "?"
    return {"explanation": explanation, "graph": graph, "generated_at": gen_at}

def render_png(mmd, png):
    src = Path(mmd).read_text(encoding="utf-8")
    mmdc = shutil.which("mmdc") or shutil.which("mmdc.cmd")
    if mmdc:
        r = subprocess.run([mmdc, "-i", mmd, "-o", png, "-w", "1600", "-b", "white"],
                           capture_output=True, text=True, cwd=os.path.dirname(mmd) or ".")
        if r.returncode == 0 and os.path.exists(png):
            print("PNG (mmdc):", png)
            return
    req = urllib.request.Request("https://kroki.io/mermaid/png",
                                 data=src.encode("utf-8"),
                                 headers={"Content-Type": "text/plain", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    with open(png, "wb") as f:
        f.write(data)
    print("PNG (kroki.io):", png, f"({len(data)} bytes)")

if __name__ == "__main__":
    import sys
    owner_repo = sys.argv[1] if len(sys.argv) > 1 else "OpenAI/codex"
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", owner_repo.replace("/", "_"))
    out_json = _OUT / f"{slug}.json"
    out_mmd = _OUT / f"{slug}.mmd"
    out_png = _OUT / f"{slug}.png"

    print(f"gitdiagram: https://gitdiagram.com/{owner_repo}")
    text = get(f"https://gitdiagram.com/{owner_repo}").decode("utf-8", "replace")
    print(f"fetched: {len(text)} bytes")
    mermaid = extract_mermaid(text)
    if not mermaid:
        print("no mermaid in page")
        sys.exit(1)
    with open(out_mmd, "w", encoding="utf-8") as f:
        f.write(mermaid)
    print("mermaid:", out_mmd)
    state = extract_state(text)
    if state:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        g = state.get("graph", {})
        print("state:", out_json, "gen_at:", state.get("generated_at"),
              "groups:", len(g.get("groups", [])), "nodes:", len(g.get("nodes", [])),
              "edges:", len(g.get("edges", [])))
    render_png(str(out_mmd), str(out_png))
    print("done:", slug)
