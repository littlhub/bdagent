# -*- coding: utf-8 -*-
"""main.py —— DBAgent 唯一主入口。

    python main.py                  # 有 Key 直接进对话，无 Key 弹配置窗
    python main.py <项目路径>        # 分析项目结构（报告 + 思维导图）
    python main.py plan  "需求"      # 只规划，不改代码
    python main.py apply "需求"      # 规划并生成修改，确认后应用
    python main.py chat             # AI 对话开发模式
    python main.py --setup          # 配置 API Key

能力 = 结构分析 + AI 编码 + 自然语言技能工作流。
旧的 agent.py 现为兼容包装（转调本文件）；AgesP.py 是本文件的旧名副本。
"""

# ==================== 导入标准库 ====================
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser


def enable_ipv4_first():
    """让 socket 优先只用 IPv4。

    PC 上 IPv6 路由常不通，系统却按 getaddrinfo 顺序先试 IPv6：
    实测 apihub.agnes-ai.com 的 IPv4 直连 0.26s，IPv6 要等 20s 才超时，
    默认连接因此拖到 42s，模型调用看起来就像卡死。
    没有 IPv4 地址时才回退 IPv6，保留纯 IPv6 环境的可用性。
    设 DBAGENT_IPV4_FIRST=0 可关闭。
    """
    if os.environ.get("DBAGENT_IPV4_FIRST", "1") == "0":
        return False
    if getattr(socket.getaddrinfo, "_dbagent_ipv4", False):
        return True

    real = socket.getaddrinfo

    def ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
        infos = real(host, port, family, type, proto, flags)
        v4 = [i for i in infos if i[0] == socket.AF_INET]
        return v4 or infos

    ipv4_first._dbagent_ipv4 = True
    socket.getaddrinfo = ipv4_first
    return True


enable_ipv4_first()

# ==================== 配置区域（请根据实际情况修改） ====================
# API 配置（建议通过环境变量 AGENT_API_KEY 设置，或通过 --key 临时指定）
DEFAULT_API_KEY = ""          # 若留空，则从配置文件 ~/.dbagent/agent_coder.json 读取
DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
DEFAULT_MODEL = "agnes-2.5-flash"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".dbagent", "agent_coder.json")

# LLM 调用限制
MAX_FILE_CHARS = 20000        # 送入 LLM 的单个文件最大字符数
LLM_MAX_CONTEXT_CHARS = 60000 # 一次请求携带的文件上下文总量上限
LLM_TIMEOUT = 180             # 秒
LLM_RETRIES = 2               # 网络错误/限流/5xx 的重试次数
LLM_RETRY_BASE_DELAY = 1.5    # 指数退避基数（秒）
RETRYABLE_HTTP_STATUS = {408, 409, 429, 500, 502, 503, 504}

# 项目扫描时跳过的目录
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv", "env",
    ".dbagent", ".idea", ".vscode", "dist", "build", "site-packages",
    ".pytest_cache", ".mypy_cache", ".tox", "output",
}

# 自然语言触发工作流的关键词（匹配到则走工作流分支，否则走代码分析/修改）
WORKFLOW_KEYWORDS = ["执行", "新建", "创建", "渲染", "工作流", "规划", "循环", "技能链",
                     "修改", "改成", "替换", "写入", "追加", "删除", "统计", "查找", "搜索"]

# 工作流执行默认限制
WORKFLOW_MAX_STEPS = 200
WORKFLOW_MAX_LOOP = 100
WORKFLOW_MAX_LLM_CALLS = 20   # 单次工作流允许的模型调用次数上限
WORKFLOW_STEP_RETRIES = 1     # 链式步骤失败后的重试次数（超时/网络类错误多为瞬时）

# 复用同目录的 codetree.py 与 treemind.py（仅加载模块，不执行其 main）
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import codetree  # noqa: E402
import treemind  # noqa: E402

# ==================== 辅助函数 ====================
# Key 文件的查找顺序：用户目录优先，项目内目录仅作兼容回退。
# 用户目录放 key 可避免敏感信息随项目提交/分发。
KEY_DIR = os.path.join(os.path.expanduser("~"), ".dbagent")
KEY_FILE_PATH = os.path.join(KEY_DIR, "key.json")
LEGACY_KEY_FILE_PATH = os.path.join(HERE, "key.json")


def read_key_file(path=None):
    """读取 key 文件，返回 (api_key, base_url, model)。

    path 未指定时，先找 ~/.dbagent/key.json，再回退到项目内 key.json。
    兼容两种格式，历史上都出现过：
      1) 裸字符串/裸 token：  sk-xxxx 或 {"api_key": "..."} 之外的单行内容
      2) JSON 对象：          {"api_key": "...", "base_url": "...", "model": "..."}
    读取失败或内容为空时返回 (None, None, None)。
    """
    if path is None:
        path = next(
            (p for p in (KEY_FILE_PATH, LEGACY_KEY_FILE_PATH) if os.path.isfile(p)),
            KEY_FILE_PATH,
        )
    target = path
    if not os.path.isfile(target):
        return None, None, None
    try:
        with open(target, encoding="utf-8-sig") as f:
            raw = f.read().strip()
    except OSError:
        return None, None, None
    # utf-8-sig 已去 BOM；再兜底清一次，避免 BOM 混进 token
    raw = raw.lstrip("\ufeff").strip()
    if not raw:
        return None, None, None

    # 先尝试 JSON 对象
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            key = (data.get("api_key") or data.get("key") or "").strip() or None
            base = (data.get("base_url") or "").strip() or None
            model = (data.get("model") or "").strip() or None
            return key, base, model
        if isinstance(data, str) and data.strip():
            return data.strip(), None, None
    except ValueError:
        pass

    # 退化为裸 token（一行字符串，可能带引号）
    token = raw.strip().strip('"').strip("'")
    return (token or None), None, None


def load_config():
    """返回 (api_key, base_url, model)。

    优先级：环境变量 AGENT_API_KEY > ~/.dbagent/agent_coder.json > 内置 key.json > 默认值
    """
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    # 多厂商：以当前 profile 为准
    profiles = cfg.get("profiles") if isinstance(cfg, dict) else None
    if isinstance(profiles, list) and profiles:
        active = cfg.get("active_profile")
        cur = next((p for p in profiles if p.get("name") == active), profiles[0])
        key = os.environ.get("AGENT_API_KEY") or cur.get("api_key") or None
        return (key, cur.get("base_url") or DEFAULT_BASE_URL,
                cur.get("model") or DEFAULT_MODEL)
    file_key, file_base, file_model = read_key_file()
    try:
        from runtime.core.settings import load_settings
        settings = load_settings(HERE).to_dict()
    except Exception:
        settings = {}
    key = (os.environ.get("AGENT_API_KEY") or cfg.get("api_key")
           or file_key or DEFAULT_API_KEY or None)
    base = (cfg.get("base_url") or file_base
            or settings.get("base_url") or DEFAULT_BASE_URL)
    model = (cfg.get("model") or file_model
             or settings.get("model") or DEFAULT_MODEL)
    return key, base, model

def save_config(api_key, base_url, model):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"api_key": api_key, "base_url": base_url, "model": model}, f, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass

def mask_key(key):
    if not key:
        return "(未设置)"
    return key[:6] + "***" + key[-4:] if len(key) > 12 else "***"


# ---------- 多厂商 / 多模型 ----------
def _read_cfg_file():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_cfg_file(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass


def load_profiles():
    """返回 (profiles, active_name)。旧配置自动升级成一条 profile。"""
    cfg = _read_cfg_file()
    profiles = cfg.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        key, base, model = load_config()
        if not key:
            return [], ""
        profiles = [{
            "name": "default",
            "base_url": base,
            "api_key": key,
            "model": model,
            "models": [model],
        }]
        cfg["profiles"] = profiles
        cfg["active_profile"] = "default"
        _write_cfg_file(cfg)
    active = cfg.get("active_profile") or profiles[0].get("name") or "default"
    return profiles, active


def activate_profile(name, model=None):
    """切到指定厂商（可同时指定模型），返回 (key, base, model)。"""
    profiles, active = load_profiles()
    target = next((p for p in profiles if p.get("name") == name), None)
    if target is None:
        raise ValueError(f"没有这个厂商：{name}")
    if model:
        target["model"] = model
        models = target.setdefault("models", [])
        if model not in models:
            models.append(model)
    cfg = _read_cfg_file()
    cfg["profiles"] = profiles
    cfg["active_profile"] = name
    # 同步顶层字段，保持老代码路径（load_config）一致
    cfg["api_key"] = target.get("api_key")
    cfg["base_url"] = target.get("base_url")
    cfg["model"] = target.get("model")
    _write_cfg_file(cfg)
    return (target.get("api_key"), target.get("base_url"), target.get("model"))


def add_profile(name, base_url, api_key, models):
    profiles, _ = load_profiles()
    if any(p.get("name") == name for p in profiles):
        raise ValueError(f"厂商已存在：{name}")
    models = [m.strip() for m in (models or []) if m.strip()]
    profiles.append({"name": name, "base_url": base_url,
                     "api_key": api_key, "model": models[0] if models else "",
                     "models": models})
    cfg = _read_cfg_file()
    cfg["profiles"] = profiles
    _write_cfg_file(cfg)
    return profiles


def remove_profile(name):
    profiles, active = load_profiles()
    if len(profiles) <= 1:
        raise ValueError("至少保留一个厂商")
    profiles = [p for p in profiles if p.get("name") != name]
    cfg = _read_cfg_file()
    cfg["profiles"] = profiles
    if active == name:
        cfg["active_profile"] = profiles[0]["name"]
        cfg["api_key"] = profiles[0].get("api_key")
        cfg["base_url"] = profiles[0].get("base_url")
        cfg["model"] = profiles[0].get("model")
    _write_cfg_file(cfg)
    return profiles

# ---------- 图形界面配置（无 tkinter 回退命令行） ----------
def setup_gui():
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except Exception:
        return None
    key, base, model = load_config()
    done = {"saved": False}
    win = tk.Tk()
    win.title("Agent - 配置 API Key")
    win.geometry("480x250")
    win.configure(bg="#f7f7f8")
    frame = ttk.Frame(win, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Agent 开发助手 · API 配置（OpenAI 兼容接口）",
              font=("Microsoft YaHei UI", 10, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
    ttk.Label(frame, text="API Key:").grid(row=1, column=0, sticky="w", pady=4)
    var_key = tk.StringVar(value=key or "")
    ent_key = ttk.Entry(frame, textvariable=var_key, width=44)
    ent_key.grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
    ent_key.focus_set()
    ttk.Label(frame, text="Base URL:").grid(row=2, column=0, sticky="w", pady=4)
    var_base = tk.StringVar(value=base or DEFAULT_BASE_URL)
    ttk.Entry(frame, textvariable=var_base, width=44).grid(
        row=2, column=1, columnspan=2, sticky="ew", pady=4)
    ttk.Label(frame, text="Model:").grid(row=3, column=0, sticky="w", pady=4)
    var_model = tk.StringVar(value=model or DEFAULT_MODEL)
    ttk.Entry(frame, textvariable=var_model, width=44).grid(
        row=3, column=1, columnspan=2, sticky="ew", pady=4)
    ttk.Label(frame, text="配置文件: " + CONFIG_PATH, foreground="#888",
              font=("Microsoft YaHei UI", 8)).grid(
        row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
    def on_save():
        k = var_key.get().strip()
        if not k:
            messagebox.showwarning("提示", "API Key 不能为空！", parent=win)
            return
        save_config(k, var_base.get().strip(), var_model.get().strip())
        done["saved"] = True
        messagebox.showinfo("成功", "配置已保存！", parent=win)
        win.destroy()
    def on_cancel():
        win.destroy()
    btns = ttk.Frame(frame)
    btns.grid(row=5, column=0, columnspan=3, sticky="e", pady=(16, 0))
    ttk.Button(btns, text="保存", command=on_save).pack(side="left", padx=6)
    ttk.Button(btns, text="取消", command=on_cancel).pack(side="left")
    try:
        win.eval("tk::PlaceWindow . center")
    except Exception:
        pass
    try:
        win.mainloop()
    except Exception:
        return False
    return done["saved"]

def setup_image_key():
    """单独配置生图专用 Key（AGNES_API_KEY），与对话主 Key 分开存。

    生图接口和对话接口鉴权范围不同，共用同一个 Key 会 401。
    这里把生图 Key 写进同一个 key.json 的 agnes_api_key 字段。
    """
    key_path = os.path.join(os.path.expanduser("~"), ".dbagent", "key.json")
    alt_path = os.path.join(HERE, "..", "key.json")
    if not os.path.isfile(key_path) and os.path.isfile(alt_path):
        key_path = alt_path

    data = {}
    if os.path.isfile(key_path):
        try:
            with open(key_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}

    cur = data.get("agnes_api_key") or os.environ.get("AGNES_API_KEY") or ""
    shown = mask_key(cur) if cur else "（未配置）"
    print("🖼️  配置生图专用 Key（与对话主 Key 独立）")
    try:
        new = input(f"  AGNES_API_KEY [{shown}]: ").strip() or cur
    except EOFError:
        print("（非交互环境，跳过）")
        return 1
    if not new:
        print("未填写，保持不变。")
        return 1

    data["agnes_api_key"] = new
    try:
        os.makedirs(os.path.dirname(key_path) or ".", exist_ok=True)
        with open(key_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"❌ 写入失败: {e}")
        return 1
    print(f"✅ 已保存到 {key_path}")
    return 0


def setup_launcher():
    print(f"🐍 当前 Python 解释器: {sys.executable}")
    tk_ver = None
    try:
        import tkinter as _tk
        tk_ver = _tk.TkVersion
        print(f"   tkinter: ✅ 可用 (Tk {tk_ver})")
    except Exception as e:
        print(f"   tkinter: ❌ 不可用 ({e.__class__.__name__}: {e})")
        print("   → 请改用系统 Python 或双击 agent.bat")
    result = setup_gui()
    if result is None:
        print("⚠️ 当前 Python 环境没有 tkinter，使用命令行交互配置。")
        return setup_config()
    if result:
        print("✅ 已通过图形界面保存配置。")
        return 0
    print("已取消配置。")
    return 1

def setup_config():
    key, base, model = load_config()
    print("🔧 配置 AI 编码助手（OpenAI 兼容接口）")
    try:
        new_key = input(f"  API Key [{mask_key(key)}]: ").strip() or key or ""
        new_base = input(f"  Base URL 回车默认 AGNES [{base or DEFAULT_BASE_URL}]: ").strip() \
            or base or DEFAULT_BASE_URL
        new_model = input(f"  Model [{model or DEFAULT_MODEL}]: ").strip() or model or DEFAULT_MODEL
    except EOFError:
        print("（非交互环境，请改用：python agent.py --key <KEY> plan \"需求\"）")
        return 1
    if not new_key:
        print("❌ API Key 不能为空。")
        return 1
    save_config(new_key, new_base, new_model)
    print(f"✅ 已保存配置到 {CONFIG_PATH}")
    return 0

# ---------- LLM 调用 ----------
def retry_delay(retry_after, attempt):
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    return min(LLM_RETRY_BASE_DELAY * (2 ** attempt), 30.0)

def call_llm(messages, key, base_url, model, temperature=0.2, max_retries=LLM_RETRIES,
             max_tokens=None, timeout=None):
    """调用 OpenAI 兼容 /chat/completions 接口，对限流、5xx、网络错误与空响应做退避重试。

    Key 来自 Key 池：失败（429/5xx/401 等）时冷却当前 Key 并换下一个，
    并行分支/重试自然分摊限流。

    max_tokens：限制输出长度。生成耗时与输出 token 数近似线性，
    摘要类调用不设上限时模型可能写上千字，单次要一两分钟。

    timeout：单次请求超时；不传时用全局 LLM_TIMEOUT（180s）。
    交互式场景可调小，避免长时间无反馈看起来像卡死。
    """
    timeout = timeout or LLM_TIMEOUT
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    body_bytes = json.dumps(payload).encode("utf-8")

    import keypool
    pool = keypool.get_pool()
    candidates = []
    for k in [key] + pool.keys:
        k = (k or "").strip()
        if k and k not in candidates:
            candidates.append(k)

    last_error = "请求失败"
    for attempt in range(max_retries + 1):
        delay = retry_delay(None, attempt)
        # 先占一个空闲 key（每 key 并发=1），用完立即释放
        cur_key = pool.acquire(timeout=60) or (candidates[0] if candidates else "")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cur_key}",
        }
        try:
            req = urllib.request.Request(url, data=body_bytes, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices") or []
            content = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
            if content.strip():
                pool.report_success(cur_key)
                pool.release(cur_key)
                return content
            last_error = "API 返回空内容"
            pool.release(cur_key)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            last_error = f"API 错误 {e.code}: {body[:300]}"
            if e.code in (401, 403):
                pool.report_failure(cur_key, keypool.INVALID_KEY_COOLDOWN)
            elif e.code in RETRYABLE_HTTP_STATUS:
                pool.report_failure(cur_key)
            pool.release(cur_key)
            if e.code not in RETRYABLE_HTTP_STATUS:
                raise RuntimeError(last_error)
            delay = retry_delay(e.headers.get("Retry-After") if e.headers else None, attempt)
        except urllib.error.URLError as e:
            last_error = f"网络错误: {e.reason}"
            pool.report_failure(cur_key)
            pool.release(cur_key)
        except (json.JSONDecodeError, TimeoutError, OSError) as e:
            last_error = f"请求失败: {e}"
            pool.report_failure(cur_key)
            pool.release(cur_key)
        if attempt < max_retries:
            print(f"⚠️ {last_error}；{delay:.1f}s 后重试（{attempt + 1}/{max_retries}）")
            time.sleep(delay)
    raise RuntimeError(last_error)


def parse_json_response(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("响应中未找到 JSON")
    return json.loads(text[start:end + 1])

def call_llm_json(messages, key, base, model, attempts=2):
    """要求模型返回 JSON；解析失败时把错误回传给模型重问一次"""
    last_error = None
    for attempt in range(attempts):
        text = call_llm(messages, key, base, model)
        try:
            return parse_json_response(text)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            if attempt + 1 >= attempts:
                break
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user",
                 "content": f"上面的输出不是合法 JSON（{e}）。请只输出合法 JSON，不要代码块或额外解释。"},
            ]
    raise RuntimeError(f"模型未返回合法 JSON: {last_error}")

# ---------- 项目扫描与结构 ----------
# 本项目根（package/ 的上一级），扫描不得越出这一层
PROJECT_ROOT = os.path.dirname(HERE)

# 不属于本项目源码的目录：外部依赖、移植来源、产物
FOREIGN_DIRS = {
    "pi-main", "agnes", "botchat", "code-arch", "game",
    ".python", "portable-test", "_legacy_dupes", "reference",
    "skills", "workspace", "data", "__pycache__", "node_modules",
}


def _is_inside_project(path):
    """path 是否位于本项目根之内。"""
    try:
        real = os.path.realpath(path)
        root = os.path.realpath(PROJECT_ROOT)
        return real == root or real.startswith(root + os.sep)
    except OSError:
        return False


def resolve_scan_root(requested=None):
    """把扫描根收敛到本项目内，避免扫到工作区里其他项目。"""
    target = os.path.abspath(requested or os.getcwd())
    if _is_inside_project(target):
        return target
    return PROJECT_ROOT


def _walk_py(root_dir):
    """遍历 .py，跳过依赖目录与非本项目目录。"""
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and d not in FOREIGN_DIRS
            and not d.startswith(".")
        ]
        for f in filenames:
            if f.endswith(".py"):
                full = os.path.join(dirpath, f)
                yield full, os.path.relpath(full, root_dir)


def scan_py_files(root_dir):
    return sorted(_walk_py(root_dir))


def _scan_structure(root_dir):
    """阶段一：只扫结构（目录树 + 文件大小/行数），不读内容。

    大项目上全量读文件太慢，先用文件系统信息给候选排序：
    路径命中、文件较大（通常是核心模块）优先。
    """
    entries = []
    for full, rel in _walk_py(root_dir):
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        entries.append({"rel": rel, "full": full, "size": size})
    return entries


def _grep_candidates(root_dir, entries, tokens, max_hits=40):
    """阶段二：用正则扫文件内容，只对命中文件计分。

    不用外部 grep：Termux 是 BusyBox，不支持 --include/--exclude-dir，
    而且进程开销在手机上不划算。纯 Python 逐文件扫更快也更可移植。
    """
    if not tokens:
        return {}
    pattern = re.compile("|".join(re.escape(t) for t in tokens[:60] if t),
                         re.IGNORECASE)
    hits = {}
    for e in entries:
        try:
            with open(e["full"], encoding="utf-8", errors="ignore") as f:
                content = f.read(200_000)
        except OSError:
            continue
        n = len(pattern.findall(content))
        if n:
            hits[e["rel"]] = n
        if len(hits) >= max_hits:
            break
    return hits


def scan_py_paths(root_dir):
    """只列路径，不读不解析——用于快速范围定位。"""
    return sorted(rel for _full, rel in _walk_py(root_dir))


def collect_tree(py_files, content_cache=None):
    """解析文件为结构树。

    content_cache 提供 {rel: 文本} 时优先复用，省掉一轮磁盘读取。
    """
    tree = {}
    for full, rel in py_files:
        cached = (content_cache or {}).get(rel)
        parsed = codetree.parse_py_source(cached, rel) if cached is not None \
            else codetree.parse_py_file(full, rel)
        if parsed:
            tree[rel] = parsed
    return tree


# ==================== 修改范围确定 ====================
# 定位阶段只读文件、不做 AST 解析，几毫秒内给出候选文件，
# 避免每轮全量解析上百个文件后才让模型猜"改哪里"。



def _split_identifiers(text):
    """把需求切成可用于匹配的片段：英文标识符 + 中文词 + 中英混排词。"""
    tokens = []
    for m in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", text or ""):
        tokens.append(m.lower())
    # 中文整体与 2-4 字片段（「沙箱守卫」能命中 guard 相关的中文注释）
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
        tokens.append(seg)
        if len(seg) > 2:
            for n in (2, 3, 4):
                for i in range(0, len(seg) - n + 1):
                    tokens.append(seg[i:i + n])
    return list(dict.fromkeys(tokens))


# 需求里的中文词 → 代码里可能出现的英文标识（提升跨语言命中率）
SEMANTIC_ALIASES = {
    "沙箱": ["sandbox", "guard", "chroot"],
    "守卫": ["guard", "check", "block", "deny"],
    "拦截": ["block", "deny", "guard", "reject"],
    "规划": ["plan", "planner", "workflow"],
    "技能": ["skill", "atom", "protein"],
    "蛋白": ["protein"],
    "多肽": ["polypeptide", "peptide"],
    "闸门": ["gate", "runtime", "stopj"],
    "会话": ["session"],
    "提示词": ["prompt"],
    "思维导图": ["treemind", "mind", "graph"],
    "导图": ["treemind", "mind"],
    "配置": ["settings", "config"],
    "工具": ["tool", "toolset"],
    "执行": ["exec", "run", "execute"],
    "截断": ["truncate"],
    "守卫": ["guard"],
    "沙箱": ["sandbox"],
    "加密": ["crypto", "encrypt"],
    "网络": ["web", "http", "url"],
    "图片": ["image", "vision", "agnes"],
    "文档": ["docx", "document"],
    "表格": ["xlsx", "excel", "chart"],
}


def _expand_tokens(tokens):
    """给中文词补上对应的英文标识，提高命中率。"""
    out = list(tokens)
    for t in tokens:
        for alias_key, alias_vals in SEMANTIC_ALIASES.items():
            if alias_key in t or t in alias_key:
                out.extend(alias_vals)
    return list(dict.fromkeys(out))


def locate_scope(req, root, py_paths, cache=None):
    """确定修改范围：三阶段定位，避免全量读文件。

    阶段一 扫结构：只拿路径/大小，按文件名命中给基础分；
    阶段二 grep：只对内容扫描一次，命中数作为内容分；
    阶段三 读取：只读入选文件，供后续 AST 解析复用（cache）。

    大项目上比「逐文件 read(200KB)」快一个量级。
    """
    base_tokens = _split_identifiers(req)
    tokens = _expand_tokens(base_tokens)
    has_edit_intent = any(w in (req or "") for w in _wordset("edit_hint_words"))

    # ---- 阶段一：结构 + 文件名打分（不读内容）----
    entries = _scan_structure(root)
    scores = {}
    for e in entries:
        rel_low = e["rel"].lower()
        score = 0
        for t in tokens:
            tl = t.lower()
            if tl and tl in rel_low:
                score += 10
        # 核心入口文件给一点基础权重
        if os.path.basename(rel_low) in ("main.py", "agent.py"):
            score += 3
        if score:
            scores[e["rel"]] = score

    # ---- 阶段二：grep 内容，命中数加权 ----
    hits = _grep_candidates(root, entries, tokens)
    for rel, n in hits.items():
        scores[rel] = scores.get(rel, 0) + min(n, 20) * 2

    if scores:
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        top = ranked[0][1]
        cutoff = max(top * 0.25, 6)
        picked = [rel for rel, s in ranked if s >= cutoff][:12]
        how = "structure+grep"
    else:
        # 兜底：编辑类需求优先主入口
        core = [e["rel"] for e in entries
                if os.path.basename(e["rel"]).lower()
                in ("main.py", "agent.py", "agesp.py")]
        if has_edit_intent and core:
            picked, how = core, "fallback-core"
        else:
            picked, how = [e["rel"] for e in entries][:60], "fallback-all"

    # ---- 阶段三：只读入选文件，供 AST 复用 ----
    if cache is not None:
        for rel in picked:
            if rel in cache:
                continue
            try:
                with open(os.path.join(root, rel), encoding="utf-8",
                          errors="ignore") as f:
                    cache[rel] = f.read(200_000)
            except OSError:
                cache[rel] = ""
    return picked, how

def structure_summary(tree):
    lines = []
    for rel in sorted(tree):
        info = tree[rel]
        funcs = info.get("functions", {})
        fnames = ", ".join(funcs.keys()) or "(无函数)"
        imports = ", ".join(info.get("imports", [])) or "-"
        lines.append(f"- {rel}\n    imports: {imports}\n    functions: {fnames}")
    return "\n".join(lines)

# ---------- AI 代码分析/修改 ----------
def ai_plan(tree, req, key, base, model):
    summary = structure_summary(tree)
    return call_llm_json([
        {"role": "system",
         "content": "你是资深 Python 开发助手，负责分析项目结构并制定修改计划。只输出 JSON，不要输出任何其他文字。"},
        {"role": "user",
         "content": f"项目结构：\n{summary}\n\n用户需求：{req}\n\n"
                    f"请输出 JSON（不要用代码块包裹）：\n"
                    f"{{\"action\": \"modify\" 或 \"answer\" 或 \"none\", "
                    f"\"files\": [\"需要修改或新建的文件相对路径列表\"], "
                    f"\"plan\": \"总体修改思路（中文，150字以内）\", "
                    f"\"details\": \"每个文件的具体改动点（中文，简洁）\", "
                    f"\"answer\": \"仅当 action=answer 时填写对问题的解答（中文，300字以内），否则为空\"}}\n"
                    f"规则：\n"
                    f"1. 需求是修改/创建代码 → action=modify，files 列出涉及文件\n"
                    f"2. 需求是提问/咨询（如\"项目是干什么的\"\"怎么用\"）→ action=answer，把解答写进 answer\n"
                    f"3. 需求与代码无关且无需动作（闲聊等）→ action=none，plan 简述即可"},
    ], key, base, model)


def ai_edits(plan, root, req, key, base, model):
    file_bundle = []
    for fp in (plan.get("files") or []):
        path = os.path.join(root, fp)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n...(内容过长已截断)"
        file_bundle.append(f"### 文件 {fp}\n```python\n{content}\n```")
    context = "\n".join(file_bundle) if file_bundle else "（方案涉及的文件都不存在或无法读取，可能全部为新建文件）"
    if len(context) > LLM_MAX_CONTEXT_CHARS:
        context = context[:LLM_MAX_CONTEXT_CHARS] + "\n...(上下文过长已截断)"
    edits = call_llm_json([
        {"role": "system",
         "content": "你是资深 Python 工程师，负责生成精确、可直接应用的代码修改。只输出 JSON，不要输出任何其他文字。"},
        {"role": "user",
         "content": f"用户需求：{req}\n\n修改方案：\n{plan.get('plan', '')}\n{plan.get('details', '')}\n\n"
                    f"相关文件内容：\n{context}\n\n"
                    f"请输出 JSON（不要用代码块包裹）：\n"
                    f"{{\"edits\": [{{\"file\": \"相对路径\", \"old\": \"与现有文件逐字一致的原文片段\", "
                    f"\"new\": \"替换后的新文本\"}}]}}\n"
                    f"要求：\n"
                    f"1. old 必须逐字复制文件中的现有内容（包括缩进和空行），绝不能捏造或改写\n"
                    f"2. 新建文件用 {{\"file\": \"路径\", \"action\": \"create\", \"content\": \"完整新文件内容\"}}\n"
                    f"3. 只包含确实需要修改的文件，修改要小而精确\n"
                    f"4. 若方案中提到的文件内容没有提供，请基于修改方案直接给出修改"},
    ], key, base, model)
    return (edits or {}).get("edits", [])

def apply_edits(edits, root):
    results = []
    for e in edits:
        fp = e.get("file", "")
        if not fp or isinstance(fp, list):
            results.append((str(fp), "✗ 无效的 file 字段，跳过"))
            continue
        path = os.path.join(root, fp)
        if e.get("action") == "create" or ("content" in e and "old" not in e):
            content = e.get("content", "")
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                results.append((fp, "✓ 已新建"))
            except Exception as ex:
                results.append((fp, f"✗ 新建失败: {ex}"))
            continue
        if not os.path.isfile(path):
            results.append((fp, "✗ 文件不存在，跳过"))
            continue
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            with open(path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
        old, new = e.get("old", ""), e.get("new", "")
        if not old:
            results.append((fp, "✗ 缺少 old 片段，跳过"))
            continue
        cnt = content.count(old)
        if cnt == 0:
            results.append((fp, "✗ 未找到匹配片段，跳过"))
            continue
        if cnt > 1:
            results.append((fp, f"✗ 匹配到 {cnt} 处，无法确定位置，跳过"))
            continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content.replace(old, new))
            results.append((fp, "✓ 已修改"))
        except Exception as ex:
            results.append((fp, f"✗ 写入失败: {ex}"))
    return results

def show_edits(edits):
    """打印修改清单（run_ai 与 run_chat 共用）。"""
    print(f"\n📝 AI 共生成 {len(edits)} 处修改：")
    for e in edits:
        fp = e.get("file", "?")
        if e.get("action") == "create" or ("content" in e and "old" not in e):
            print(f"   ✚ 新建文件 {fp}（{len(e.get('content', ''))} 字符）")
        else:
            print(f"   ✎ 修改 {fp}：替换 {len(e.get('old', ''))} 字符 → {len(e.get('new', ''))} 字符")


def refresh_mind(tree, root):
    out_dir = root
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "treegraph_full.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2, ensure_ascii=False)
    project_name = os.path.basename(root) or root
    files = treemind.build_files(tree)
    html_path = os.path.join(out_dir, "treemind.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(treemind.build_html(files, project_name))
    return json_path, html_path

def analyze(tree):
    files = []
    module_set = set()
    for rel, info in tree.items():
        fname = rel.replace("\\", "/").split("/")[-1]
        module = os.path.splitext(fname)[0]
        funcs = info.get("functions", {})
        imports = info.get("imports", [])
        files.append({
            "name": fname, "rel": rel, "module": module, "imports": imports,
            "n_funcs": len(funcs), "n_calls": sum(len(v) for v in funcs.values()),
            "internal_links": sum(1 for calls in funcs.values() for c in set(calls) if c in funcs),
        })
        module_set.add(module)
    lib_usage = {}
    for fd in files:
        for imp in fd["imports"]:
            lib_usage[imp] = lib_usage.get(imp, 0) + 1
    hot_libs = sorted(
        ((k, v) for k, v in lib_usage.items() if k not in module_set),
        key=lambda x: -x[1])
    file_deps = {}
    for fd in files:
        deps = sorted({imp for imp in fd["imports"] if imp in module_set})
        file_deps[fd["name"]] = deps
    dep_reverse = {fd["name"]: 0 for fd in files}
    for deps in file_deps.values():
        for d in deps:
            dep_reverse[d] = dep_reverse.get(d, 0) + 1
    return {"files": files, "hot_libs": hot_libs, "file_deps": file_deps,
            "dep_reverse": dep_reverse, "module_set": module_set}

def build_advice(analysis):
    advice = []
    files = sorted(analysis["files"], key=lambda f: -f["n_funcs"])
    if files and files[0]["n_funcs"] >= 10:
        f = files[0]
        advice.append(("warn", f"{f['name']} 定义了 {f['n_funcs']} 个函数，是项目最复杂模块，建议优先熟悉或考虑拆分。"))
    for f in files:
        if f["n_funcs"] == 0:
            advice.append(("info", f"{f['name']} 没有函数定义，可能只是配置/常量脚本。"))
    lonely = [f["name"] for f in files
              if not analysis["file_deps"].get(f["name"])
              and analysis["dep_reverse"].get(f["name"], 0) == 0]
    if len(lonely) == len(files) and len(files) > 1:
        advice.append(("info", "各文件之间没有互相 import，模块边界清晰、耦合低。"))
    elif lonely:
        advice.append(("info", f"孤立模块（无项目内依赖）：{', '.join(lonely)}。"))
    for lib, n in analysis["hot_libs"][:3]:
        if n >= max(2, len(files) // 2):
            advice.append(("info", f"库 {lib} 被 {n} 个文件引用，是公共依赖，升级时需全量回归。"))
    if len(files) > 20:
        advice.append(("info", "文件数量较多，建议用思维导图按包/目录维度查看结构。"))
    return advice

def print_report(project_name, tree, analysis, advice):
    total_funcs = sum(f["n_funcs"] for f in analysis["files"])
    total_imports = len(analysis["hot_libs"])
    bar = "=" * 56
    print(f"\n📂 项目结构分析报告: {project_name}")
    print(bar)
    print("🔍 概览")
    print(f"   Python 文件: {len(tree)}  函数总数: {total_funcs}  去重依赖库: {total_imports}")
    print("\n📄 文件结构")
    for f in sorted(analysis["files"], key=lambda x: x["rel"]):
        links = f"  同文件调用 {f['internal_links']}" if f["internal_links"] else ""
        print(f"   - {f['rel']:<24} {f['n_funcs']:>3} 函数 · {len(f['imports'])} 导入{links}")
    if analysis["hot_libs"]:
        print("\n📦 热门依赖库")
        for lib, n in analysis["hot_libs"][:8]:
            print(f"   - {lib:<16} {n} 个文件引用")
    deps = {k: v for k, v in analysis["file_deps"].items() if v}
    if deps:
        print("\n🔗 文件间依赖")
        for k, v in sorted(deps.items()):
            print(f"   - {k} → {', '.join(v)}")
    print("\n💡 开发提示")
    if not advice:
        print("   - 结构清晰，未发现明显问题。")
    for level, text in advice:
        mark = "⚠️" if level == "warn" else "•"
        print(f"   {mark} {text}")
    print(bar)
    print("🧠 提示：打开 treemind.html 用交互式思维导图浏览结构。")
    print("🤖 提示：配置 API Key 后可用 `python agent.py plan \"需求\"` 让 AI 分析改哪里。\n")

def run_analysis(rest):
    root = "."
    no_mind = no_open = False
    out = None
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--no-mind":
            no_mind = True
            i += 1
        elif a == "--no-open":
            no_open = True
            i += 1
        elif a == "--out" and i + 1 < len(rest):
            out = rest[i + 1]
            i += 2
        else:
            if not a.startswith("-"):
                root = a
            i += 1
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        print(f"❌ 路径不存在或不是目录: {root}")
        return 1
    out_dir = os.path.abspath(out) if out else root
    os.makedirs(out_dir, exist_ok=True)
    py_files = scan_py_files(root)
    if not py_files:
        print(f"❌ 在 {root} 下未找到 .py 文件（已跳过 {', '.join(sorted(SKIP_DIRS))} 等目录）。")
        return 1
    print(f"🔍 扫描 {root} → 找到 {len(py_files)} 个 .py 文件")
    tree = collect_tree(py_files)
    if not tree:
        print("❌ 没有成功解析任何文件。")
        return 1
    print(f"✅ 成功解析 {len(tree)} 个文件")
    json_path = os.path.join(out_dir, "treegraph_full.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2, ensure_ascii=False)
    print(f"✅ 已保存 {json_path}")
    project_name = os.path.basename(root) or root
    analysis = analyze(tree)
    advice = build_advice(analysis)
    print_report(project_name, tree, analysis, advice)
    if not no_mind:
        files = treemind.build_files(tree)
        html_content = treemind.build_html(files, project_name)
        html_path = os.path.join(out_dir, "treemind.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"✅ 已生成思维导图 {html_path}")
        if not no_open:
            try:
                webbrowser.open("file://" + html_path)
                print("🌐 已在浏览器中打开思维导图")
            except Exception:
                print("提示：请手动打开 treemind.html 查看")
    return 0

# ---------- 自然语言触发工作流（整合 stopj） ----------
STOPJ_SYNTAX = (
    "STOPJ 表达式语法（无空格紧凑写法，从左到右执行）：\n"
    "  S        启动\n"
    "  P{n}     规划 n 步\n"
    "  O{n}     开始循环，循环体为后续指令，由 T 结束，共 n 轮\n"
    "  T        闸门，检查循环是否继续\n"
    "  {n}J     定义位点 n\n"
    "  J{n}     跳转到位点 n\n"
    "  其它     技能名或工具名（必须是下面清单中的名称）\n"
    "示例：SO2text-editorT  表示 启动、循环2次执行 text-editor"
)

# 可直接作为工作流步骤调用的工具（在 data/work 内执行）
TOOL_HINTS = {
    "read": "读取文件，参数：文件路径 [--start 起始行] [--end 结束行]",
    "write": "写入文件，参数：文件路径 内容",
    "edit": "编辑文件，参数：replace|insert|delete 文件路径 ...",
    "ls": "列目录，参数：[目录]",
    "grep": "正则搜索，参数：模式 [路径...]",
    "find": "查找文件，参数：[--name 关键字] [--suffix 后缀]",
    "bash": "执行 shell 命令，参数：命令字符串",
    # 联网工具：查新闻、查资料、读网页都走这两个，不要用 bash+curl 绕
    "search": "联网搜索，参数：查询词 [--limit 条数]；返回标题/链接/摘要",
    "fetch": "抓取网页正文，参数：URL [--max-chars 字数]",
    # 上下文注入：后续技能声明 consumes=["prev_text"] 时自动拿到它读出的文本
    "context": "读取上一次工作流产出的提示词/描述，参数：[--max-chars 字数]",
}

def _ask_tool_args(req, tools, key, base, model):
    """让模型为每个工具给出参数；拿不到就返回空参数。"""
    if not tools:
        return {}
    usage = "\n".join(f"  - {name}：{TOOL_HINTS[name]}" for name in dict.fromkeys(tools))
    prompt = (
        f"用户需求：{req}\n\n"
        f"本次要调用的工具：\n{usage}\n\n"
        f"请为每个工具给出参数列表（不含工具名本身），严格按上面的参数格式。\n"
        f"bash 工具的命令字符串请作为单个参数项传入。\n"
        f"只输出 JSON，格式：{{\"工具名\": [\"参数1\", \"参数2\"]}}\n"
        f"无法确定参数时给空列表。不要输出解释。"
    )
    try:
        data = call_llm_json([{"role": "user", "content": prompt}], key, base, model, attempts=1)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: [str(x) for x in v] for k, v in data.items()
            if isinstance(v, list) and k in tools}

def _ask_skill_args(req, skills, library, key, base, model):
    """让模型为每个要执行的技能给出命令行参数；拿不到就返回空参数。"""
    usage = []
    for name in dict.fromkeys(skills):
        info = library.describe(name) or {}
        usage.append(f"  - {name}：{info.get('description') or ''}")
        hint = library.usage_hint(name)
        if hint:
            usage.append(hint)
    prompt = (
        f"用户需求：{req}\n\n"
        f"本次要执行的技能：\n" + "\n".join(usage) + "\n\n"
        f"请为每个技能给出要传给脚本的参数列表（不含技能名本身）。\n"
        f"必须严格按上面给出的命令格式。\n"
        f"要点：\n"
        f"1. 参数要是脚本能直接执行的完整命令参数，不要照搬需求原句；\n"
        f"2. 需要标题、正文等内容时，根据需求自己拟写具体文字，\n"
        f"   例如把「介绍项目进展」写成标题「项目季度进展汇报」并拆成分页要点；\n"
        f"3. 不能用一句话充当标题或正文。\n"
        f"只输出 JSON，格式：{{\"技能名\": [\"参数1\", \"参数2\"]}}\n"
        f"无法确定参数时给空列表。不要输出解释。"
    )
    try:
        data = call_llm_json([{"role": "user", "content": prompt}], key, base, model, attempts=1)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: [str(x) for x in v] for k, v in data.items()
            if isinstance(v, list) and k in skills}

def normalize_stopj(expr):
    expr = expr.strip().replace("```", "")
    expr = re.sub(r"(?i)stopj", "", expr)
    expr = re.sub(r"\s+", "", expr)
    if not re.fullmatch(r"[A-Za-z0-9_-]*", expr):
        return ""
    return expr

def _protein_index_path():
    """蛋白库索引位置。"""
    data_dir, _ = _workspace_dirs()
    return os.path.join(data_dir, "proteins", "index.json")


def _load_protein_db():
    """读取蛋白库，返回 [{gene_chain, name, skill_chain}]。

    蛋白的 skill_chain 由 polypeptide_tree 的 atoms 索引换算成技能名，
    换算依赖 atoms/index.json 的 index -> skill 映射。
    """
    idx_path = _protein_index_path()
    if not os.path.isfile(idx_path):
        return []
    try:
        with open(idx_path, encoding="utf-8") as f:
            db = json.load(f)
        prots = db.get("proteins") or []
    except (OSError, ValueError):
        return []

    # atoms 索引：数字 index -> 技能名
    atom_map = {}
    data_dir, _ = _workspace_dirs()
    atoms_path = os.path.join(data_dir, "atoms", "index.json")
    if os.path.isfile(atoms_path):
        try:
            with open(atoms_path, encoding="utf-8") as f:
                for a in (json.load(f).get("atoms") or []):
                    if a.get("index") is not None and a.get("skill"):
                        atom_map[a["index"]] = a["skill"]
        except (OSError, ValueError):
            pass

    result = []
    seen = set()
    for p in prots:
        chain = p.get("gene_chain") or ""
        name = p.get("name_cn") or ""
        if not chain or chain in seen:
            continue
        seen.add(chain)

        # 技能链来源一：手动登记时直接写的 skill_chain
        skills = [str(s) for s in (p.get("skill_chain") or []) if s]
        # 技能链来源二：从 folding tree 的原子索引换算
        if not skills:
            tree = p.get("polypeptide_tree") or {}
            for node in tree.get("nodes") or []:
                for ai in node.get("atoms") or []:
                    sk = atom_map.get(ai)
                    if sk and sk not in skills:
                        skills.append(sk)
        kw = _protein_keywords(name, p)
        # 手动登记时带了 keywords，一并纳入（顺序靠前，优先命中）
        extra = [str(k) for k in (p.get("keywords") or []) if k]
        merged = []
        for k in extra + kw:
            if k and k not in merged:
                merged.append(k)
        result.append({
            "gene_chain": chain,
            "name": name,
            "core": _protein_core(name),
            "skill_chain": skills,
            "description": p.get("description") or "",
            "keywords": merged,
            # 规则与优先级是配置驱动的核心，必须透传，
            # 否则 _rule_match_protein 读不到 rules，整条链都匹配不上
            "rules": p.get("rules") or [],
            "priority": p.get("priority", 100),
            "polypeptide_tree": p.get("polypeptide_tree") or {},
        })
    return result


def _protein_core(name):
    """蛋白名去掉泛化后缀后的核心词，用于强匹配。"""
    core = str(name or "")
    for suffix in ("链", "系统", "工具", "插件", "流程"):
        if core.endswith(suffix) and len(core) > len(suffix):
            core = core[: -len(suffix)]
    return core


def _protein_keywords(name, entry=None):
    """从蛋白名与其描述里提取匹配关键词。

    中文没有空格分词，这里按字切：完整名 + 去掉常见后缀的核心词 +
    连续的 2-4 字片段，保证「丝袜写真链」能命中「丝袜写真」。
    """
    words = set()
    source = str(name or "")
    core = source
    if source:
        words.add(source)
        # 去掉「链 / 系统 / 工具 / 处理」这类泛化后缀
        for suffix in ("链", "系统", "工具", "插件", "流程"):
            if core.endswith(suffix) and len(core) > len(suffix):
                core = core[: -len(suffix)]
        if core:
            words.add(core)
        # 连续中文片段（2-4 字），覆盖拆分场景
        for seg in re.findall(r"[\u4e00-\u9fff]{2,}", source):
            for n in (2, 3, 4):
                for i in range(0, max(0, len(seg) - n + 1)):
                    words.add(seg[i:i + n])

    nick = (entry or {}).get("nickname")
    if nick:
        words.add(str(nick).lower())

    desc = (entry or {}).get("description") or ""
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", str(desc)):
        for n in (3, 4):
            for i in range(0, max(0, len(seg) - n + 1)):
                words.add(seg[i:i + n])

    # 太短的词（单纯 2 字且过于通用）容易误配，按需要保留
    return sorted(w for w in words if len(w) >= 2)


# 蛋白规则匹配：领域词 → 基因链。
# 关键词枚举永远列不全（「近日新闻」就漏在「今日新闻」之外），
# 这里按语义模式兜一层，命中即走对应蛋白。
# 蛋白匹配规则全部来自蛋白库配置（proteins/index.json 的 rules 字段），
# 代码只负责按 priority 顺序执行正则匹配。
# 新增领域说法 = 改配置，不改代码。

# 只说「新闻」两个字时信息不足，需要更强的上下文才算联网新闻需求。
# 弱词表来自 data/runtime.json 的 web.weak_news。


def _weak_news_words():
    return set((_runtime().get("web") or {}).get("weak_news") or [])


# URL 出现时一律走联网摘要（先抓正文再总结）
_URL_RE = re.compile(r"https?://[^\s，。；、]+")


def _rule_match_protein(req):
    """按蛋白库配置的 rules 匹配，返回 (gene_chain, 命中说明) 或 None。

    规则写在每条蛋白的 rules 字段里，priority 数字小的优先匹配
    （如「复刻」必须先于「按名找图」判断，否则「XX图片复刻」会被当成找人名）。
    """
    low = (req or "").lower().strip()
    if not low:
        return None

    entries = []
    for entry in _load_protein_db():
        rules = entry.get("rules")
        if not rules:
            continue
        entries.append((entry.get("priority", 100), entry))
    entries.sort(key=lambda x: x[0])

    for _prio, entry in entries:
        for pat in entry["rules"]:
            try:
                m = re.search(pat, low)
            except re.error:
                continue
            if not m:
                continue
            hit = m.group(0)
            # 「新闻」单词过弱：要求需求里还有其他内容
            if hit in _weak_news_words() and len(low) <= len(hit) + 1:
                continue
            return entry.get("gene_chain"), hit
    return None
    for chains, patterns in _PROTEIN_RULES:
        for pat in patterns:
            m = re.search(pat, low)
            if not m:
                continue
            hit = m.group(0)
            # 「新闻」单词过弱：要求需求里还有其他内容
            if hit in _weak_news_words() and len(low) <= len(hit) + 1:
                continue
            return chains[0], hit
    return None


def plan_from_protein(req):
    """一级规划：查蛋白库，命中则复用已有技能链。

    这是最省的一层——纯本地查表，0 次 LLM 调用。
    返回 {"skill_chain", "gene_chain", "name", "match"} 或 None。

    匹配顺序：
      1. 规则匹配（领域语义，如「新闻/摘要/网页」→ 联网摘要链）
      2. 关键词匹配（蛋白库里登记的 keywords，供精确说法使用）

    关键词是有限枚举，永远列不全（「近日新闻」就漏在「今日新闻」之外），
    所以规则层优先，保证这类需求稳定命中多步链，而不是退化成单步 search。
    """
    low = req.lower().strip()
    if not low:
        return None

    # ---- 1) URL 一律走联网摘要 ----
    if _URL_RE.search(req):
        entry = _protein_by_chain("AWA")
        if entry:
            return {
                "skill_chain": entry["skill_chain"],
                "gene_chain": entry["gene_chain"],
                "name": entry["name"],
                "match": f"蛋白库:{entry['gene_chain']}(URL)",
            }

    # ---- 2) 领域规则 ----
    rule = _rule_match_protein(req)
    if rule:
        chain_key, hit = rule
        entry = _protein_by_chain(chain_key)
        if entry:
            return {
                "skill_chain": entry["skill_chain"],
                "gene_chain": entry["gene_chain"],
                "name": entry["name"],
                "match": f"蛋白库:{entry['gene_chain']}({hit})",
            }

    # ---- 3) 关键词匹配 ----
    best = None
    for entry in _load_protein_db():
        hit_kw = None
        for kw in entry["keywords"]:
            if not kw or kw.lower() not in low:
                continue
            if hit_kw is None or len(kw) > len(hit_kw):
                hit_kw = kw
        if not hit_kw:
            continue
        # 名字全命中 / 核心词命中，视为强匹配
        full_hit = bool(entry["name"]) and entry["name"].lower() in low
        core_hit = bool(entry.get("core")) and entry["core"].lower() in low
        if not (full_hit or core_hit) and len(hit_kw) < 4:
            # 弱匹配要求 4 字以上，避免「文档」「分析」这类泛词误配
            continue
        score = (100 if full_hit else 0) + (50 if core_hit else 0) + len(hit_kw)
        if best is None or score > best[0]:
            best = (score, entry, hit_kw)
    if not best:
        return None
    _, entry, kw = best
    chain = entry["skill_chain"]
    # 链里任何一个技能不可执行，整条链就不能用（否则中途必失败）
    bad = [s for s in chain if not _skill_runnable(s)]
    if bad:
        return None
    return {
        "skill_chain": chain,
        "gene_chain": entry["gene_chain"],
        "name": entry["name"],
        "match": f"蛋白库:{entry['gene_chain']}({kw})",
    }


def _protein_by_chain(gene_chain):
    """按基因链取一条蛋白记录（需带 skill_chain 才能执行）。"""
    for entry in _load_protein_db():
        if entry.get("gene_chain") == gene_chain and entry.get("skill_chain"):
            return entry
    return None


# 只输入一个中文人名时（如「杨幂」），最可能的意图是找图。
# 判成 answer 会让模型反问「你想做什么」，还得再输一遍「搜索图片」。
# 动词表与长度限制来自 data/runtime.json 的 person_name 段。


def _atom_field(skill, field_name, default=None):
    """读取某个技能原子在 atoms/index.json 里的扩展字段。

    这样做是为了让「哪些词不是人名」「搜索词要剥离什么」这类领域知识
    留在配置里，而不是散落在代码常量中。
    """
    data_dir, _ = _workspace_dirs()
    idx_path = os.path.join(data_dir, "atoms", "index.json")
    try:
        with open(idx_path, encoding="utf-8") as f:
            atoms = json.load(f).get("atoms") or []
    except (OSError, ValueError):
        return default
    for a in atoms:
        if a.get("skill") == skill:
            v = a.get(field_name)
            return v if v is not None else default
    return default


def _looks_like_person_name(req):
    """判断是不是单纯一个人名。

    长度与动词表来自 data/runtime.json 的 person_name 段；
    「不算人名」的话题词来自原子的 not_person_name 字段。
    """
    s = (req or "").strip()
    cfg = _runtime().get("person_name") or {}
    lo = int(cfg.get("min_length") or 2)
    hi = int(cfg.get("max_length") or 5)
    if not re.fullmatch(r"[\u4e00-\u9fff·]{%d,%d}" % (lo, hi), s):
        return False
    verb_pat = cfg.get("verb_pattern") or ""
    if verb_pat:
        try:
            if re.search(verb_pat, s):
                return False
        except re.error:
            pass
    # 「鹿晗恋情」「图片复刻」这类是人名+话题/操作，走资讯或复刻链而不是找图。
    # 词表在原子配置里，不在代码里。
    topics = _atom_field("person-image-search", "not_person_name", []) or []
    if any(w in s for w in topics):
        return False
    # 含动作/功能词的不是人名：「定时任务」「分析日志」都符合 2-5 汉字，
    # 但它们明显是任务描述，不能当人名去下图
    if any(w in s for w in _wordlist("not_name_words")):
        return False
    return True




def plan_from_atom(req):
    """原子级规划：需求直接指向单个技能时，返回单步链。

    蛋白库存的是多步工作流；像「写个 word 文档」「做个 ppt」这种
    只对应一个原子的需求，靠 atoms/index.json 的名字与描述匹配。
    同样是本地查表，0 次 LLM 调用。
    返回 {"skill_chain": [...], "match": str} 或 None。
    """
    data_dir, _ = _workspace_dirs()
    idx_path = os.path.join(data_dir, "atoms", "index.json")
    if not os.path.isfile(idx_path):
        return None
    try:
        with open(idx_path, encoding="utf-8") as f:
            atoms = json.load(f).get("atoms") or []
    except (OSError, ValueError):
        return None

    low = req.lower().strip()
    if not low:
        return None

    # 中文「动词 + 技能」短语：技能创建类的英文名与英文描述无法覆盖中文表达，
    # 单靠描述切片又会让「创建」「更新」这类裸动词误伤普通需求。
    if _SKILL_OP_RE.search(req):
        return {"skill_chain": ["skill-creator"], "match": "短语:创建/安装技能"}

    best = None
    for a in atoms:
        skill = a.get("skill")
        if not skill:
            continue
        # 候选词按来源分强弱：技能名最强，中文名次之，描述最弱。
        # 只按词长打分会让描述里的偶然撞词压过真正对口的技能
        # （「查一下我的密码」曾命中 plan-filter 描述里的「密码」）。
        cands = []
        # 原子显式声明的触发词优先级最高（如「找图」「加字」）
        for t in (a.get("triggers") or []):
            if t:
                cands.append((str(t), "trigger"))
        for key in ("skill", "name_en", "nickname"):
            v = a.get(key)
            if v:
                cands.append((str(v), "name"))
        cn = str(a.get("name_cn") or "")
        if cn:
            cands.append((cn, "cn"))
            # 前缀切片只对汉字名做：英文名（PPT演示→PP）的前两字
            # 是无意义碎片，会撞上文件名里的字母（hello_app 的 pp）
            if len(cn) > 2 and re.match(r"[\u4e00-\u9fff]", cn):
                cands.append((cn[:2], "cn"))
        desc = str(a.get("description") or "")
        for seg in re.findall(r"[A-Za-z]{2,}|[\u4e00-\u9fff]{2,}", desc):
            # 英文片段至少 3 字：两字碎片（AI、PP）撞词率太高，
            # 「main.py」里的 ai 曾把写代码需求误触到 visioncustom
            if re.fullmatch(r"[A-Za-z]{2,}", seg):
                if len(seg) >= 3:
                    cands.append((seg, "desc"))
            else:
                cands.append((seg, "desc"))
            # 长描述再切 2-4 字子串
            if re.fullmatch(r"[\u4e00-\u9fff]+", seg) and len(seg) > 4:
                for n in (2, 3, 4):
                    for i in range(0, len(seg) - n + 1):
                        cands.append((seg[i:i + n], "desc"))

        hit = None
        hit_src = None
        for c, src in cands:
            if not c:
                continue
            # 泛词直接排除：否则「技能」「文档」这类词会盖住真正对口的命中
            # （「安装一个技能」曾因最长命中是「技能」而完全无法匹配）
            if c in _wordset("generic_words") or is_function_word(c):
                continue
            if c.lower() in low:
                if hit is None or len(c) > len(hit):
                    hit, hit_src = c, src
        if not hit:
            continue
        # 描述切片不得单独触发技能：描述是次要信息，里面常有大量泛词，
        # 只靠它命中会把「飞机模型」判成图像识别。
        # 但如果描述命中的正是该技能的核心能力词（如 pptx 描述里的「PPT」、
        # visioncustom 描述里的「识别」），说明用户确实在要这个能力，
        # 此时要求命中词长度 ≥2 即可。
        if hit_src == "desc":
            # 泛词已在上面排除；这里再挡掉 2 字的无关碎片
            if len(hit) < 2:
                continue
        # 打分：命中词越长越好；来自技能名或中文名的再加权，
        # 这样「查一下密码」优先 password-manager（中文名「密码管理」）而不是
        # 描述里偶然出现「密码」的 plan-filter。
        score = len(hit) * 10
        if hit_src == "trigger":
            # 显式触发词是这个技能的招牌用法，权重最高
            score += 60
        elif hit_src in ("name", "cn"):
            score += 30
        else:
            # 描述来的命中整体降权，压不过任何名称类命中
            score -= 25
        low_skill = str(skill).lower()
        if hit.lower() in low_skill:
            score += 30
        # 中文名精确命中再加权
        if str(a.get("name_cn") or "").lower() == hit.lower():
            score += 40
        # 技能名越短越可能是「主技能」：pdf 优先于 pdf-chinese-fix，
        # text-encrypt 优先于 CRYPTO 这类宽泛名。
        if len(hit) <= 4 and len(str(skill)) <= max(6, len(hit) + 2):
            score += 20
        # 命中词只出现在描述尾部（属于附加说明）时降权
        if str(skill).lower().find(hit.lower()) < 0 and len(str(skill)) > 12:
            score -= 10
        if best is None or score > best[0]:
            best = (score, skill, hit)

    if not best:
        # 兜底：单纯一个人名，按「找图」处理（person-image-search 的招牌用法）
        if _looks_like_person_name(req):
            for a in atoms:
                if a.get("skill") == "person-image-search":
                    return {"skill_chain": ["person-image-search"],
                            "match": f"原子:person-image-search(人名:{req.strip()})"}
        return None
    _, skill, hit = best
    # 选中的技能必须有可执行入口，否则规划到它必定失败
    # （pdr-timer 只有 SKILL.md，被「分析」命中后报「没有可执行脚本」）
    if not _skill_runnable(skill):
        return None
    return {"skill_chain": [skill], "match": f"原子:{skill}({hit})"}


def _skill_runnable(skill):
    """技能是否有可执行脚本。

    兼容两种目录名：多数技能用 scripts/，pdr-timer 用 script/。
    """
    data_dir, skills_dir = _workspace_dirs()
    sdir = os.path.join(skills_dir, skill)
    if not os.path.isdir(sdir):
        return False
    for name in ("scripts", "script"):
        if os.path.isdir(os.path.join(sdir, name)):
            return True
    meta = os.path.join(sdir, "skill.json")
    if os.path.isfile(meta):
        try:
            with open(meta, encoding="utf-8") as f:
                info = json.load(f)
            if info.get("main") or info.get("entry") or info.get("scripts"):
                return True
        except (OSError, ValueError):
            pass
    return False


# 需求里指向「技能本身」的操作：动词与「技能」相邻才算，避免裸动词误伤
_SKILL_OP_RE = re.compile(r"(创建|新建|安装|更新|升级|添加|写|做|生成)\S{0,4}技能")





def is_function_word(word):
    low = (word or "").lower()
    return low in _wordset("function_words") or low in _wordset("en_function_words") or len(low) <= 1


# ==================== 多步技能链（多肽）====================
# 单靠「原子匹配」会把"总结为ppt"当成单个 pptx 技能空跑——
# 没有内容可讲，产出的是空白模板。真实需求是多步：
#     总结内容 -> 写成 markdown -> 生成 pptx -> 美化
# 这里把它建成可复用的链，执行时把上一步结果作为下一步输入。

DOC_CHAIN_KEYWORDS = {
    "ppt": ("ppt", "幻灯片", "演示", "deck", "slides"),
    "word": ("word", "docx", "文档"),
    "md": ("markdown", "md文件", "写成md"),
}



def detect_doc_chain(req):
    """识别文档生成类需求，返回链名与目标格式；不匹配返回 None。"""
    low = (req or "").lower()
    for fmt, words in DOC_CHAIN_KEYWORDS.items():
        if not any(w in low for w in words):
            continue
        # 明确要"总结/整理/做成"时才走多步链；
        # 单纯"做个ppt"仍走原子匹配（空模板由模型自行规划）
        if not any(w in req for w in _wordlist("summarize_words")):
            continue
        return fmt
    return None


# ==================== 配图生成 ====================
IMAGE_API_BASE = "https://apihub.agnes-ai.cn/v1"
IMAGE_MODEL = "agnes-image-2.1-flash"


def generate_image(prompt, out_path, size="1280x720", timeout=150):
    """调用 Agnes 图像 API 生成图片并下载到 out_path。

    图像 API 域名为 agnes-ai.cn（与对话 API 的 .com 不同），
    返回的是 URL，必须再下载一次。
    返回 (ok, 路径或错误信息)。
    """
    key, _base, _model = load_config()
    if not key:
        return False, "未配置 API Key"
    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "size": size,
        "n": 1,
    }
    try:
        req = urllib.request.Request(
            IMAGE_API_BASE + "/images/generations",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("data") or []
        if not items:
            return False, "API 未返回图片"
        url = items[0].get("url")
        if not url:
            return False, "返回里没有 url 字段"
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with urllib.request.urlopen(url, timeout=timeout) as img:
            raw = img.read()
        with open(out_path, "wb") as f:
            f.write(raw)
        return True, out_path
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        return False, f"HTTP {e.code}: {body[:150]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:150]}"


def _image_prompt_for(title, bullets, style):
    """根据页面内容与风格生成英文图片提示词（英文效果更好）。"""
    styles = {
        "visual": "cinematic, dramatic lighting, high contrast, premium brand aesthetic",
        "mckinsey": "corporate business, clean, professional, blue and gold tones",
        "product": "modern tech product, vibrant gradient, energetic",
        "geek": "dark tech, neon accents, code and circuit motifs",
        "academic": "clean minimal, academic, soft neutral tones",
    }
    base = styles.get(style, "clean modern presentation background")
    subject = "，".join([title] + list(bullets or [])[:3])
    return (f"{subject}. Abstract professional presentation background, "
            f"{base}, no text, no words, no letters, 16:9, high quality, detailed")


# 用户口语 -> 模板风格的映射
THEME_ALIASES = {
    "visual": ["视觉", "全屏", "大图", "冲击", "创意", "品牌提案", "高端"],
    "geek": ["极客", "科技风", "技术风", "暗黑", "代码", "赛博"],
    "product": ["产品", "运营", "互联网", "活力", "增长"],
    "academic": ["学术", "答辩", "论文", "学院", "简洁"],
    "mckinsey": ["商务", "咨询", "麦肯锡", "汇报", "专业", "稳重"],
}

# 风格别名 -> 该风格的中文名，用于回显
THEME_LABELS = {
    "visual": "全屏视觉", "geek": "科技极客", "product": "互联网产品",
    "academic": "高校学术", "mckinsey": "麦肯锡商务",
}

# 排版规则文件位置（LLM 生成主题时的依据）
LAYOUT_RULES_PATH = os.path.join(HERE, "..", "workspace", "skills", "pptx",
                                 "reference", "layout_rules.md")


def _load_layout_rules():
    """读取排版规则；读不到时返回空串（此时退回到固定模板）。"""
    try:
        with open(LAYOUT_RULES_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


# 主题字段的中文名，用于把 LLM 输出映射成脚本参数
_THEME_FIELDS = ("bg", "surface", "title", "body", "accent",
                 "primary", "secondary", "font", "style")


def _parse_theme_json(raw):
    """解析 LLM 的主题 JSON，容忍常见的不规范输出。

    LLM 写 JSON 时的典型毛病：
      · rationale 里含未转义的中英文引号
      · 用 ```json 代码块包裹
      · 前后带解释文字
    这里先试标准解析，失败再按主题字段逐个正则捞。
    """
    text = (raw or "").strip()
    # 去掉代码块围栏
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if "```" in text:
            text = text.rsplit("```", 1)[0]

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass

    # 退路：把 theme 对象单独抠出来再解析
    start = text.find('"theme"')
    if start >= 0:
        brace = text.find("{", start)
        if brace >= 0:
            data = _extract_balanced(text, brace)
            if data is not None:
                return data

    # 最后退路：按字段名逐个捞值
    theme = {}
    for f in _THEME_FIELDS:
        m = re.search(rf'"{f}"\s*:\s*"([^"]{{1,40}})"', text)
        if m:
            theme[f] = m.group(1)
    rationale = re.search(r'"rationale"\s*:\s*"(.{0,300})', text)
    return {"theme": theme, "rationale": rationale.group(1) if rationale else ""}


def _extract_balanced(text, open_idx):
    """从 open_idx 处的 { 开始，按括号配对截出完整 JSON 对象。

    能正确处理字符串内含引号的情况（\" 转义与中文引号）。
    """
    depth = 0
    in_str = False
    escape = False
    for i in range(open_idx, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[open_idx:i + 1])
                except ValueError:
                    return None
    return None


def generate_custom_theme(content_summary, base_theme, key, base, model):
    """让 LLM 依据排版规则，为当前内容现场生成一套配色主题。

    base_theme 作为起点基调（用户指定或模型选择的风格）；
    返回 (主题 dict, 说明文字)。失败时返回 (None, 原因)。
    """
    rules = _load_layout_rules()
    if not rules:
        return None, "读不到排版规则文件"

    prompt = (
        f"下面是 PPT 排版规则：\n\n{rules}\n\n"
        f"现在要为下面这份内容设计一套**贴合其气质**的主题。\n\n"
        f"内容摘要：\n{content_summary[:2500]}\n\n"
        f"基调参考：{base_theme}（可在此基础上调整，也可另起一套）\n\n"
        f"要求：\n"
        f"1. 严格遵守规则里的硬性约束（颜色数量、比例 6:3:1、字体 ≤2 种等）\n"
        f"2. 配色要从内容本身的气质出发，说明你为什么选这组色\n"
        f"3. 只输出 JSON，格式：\n"
        f'{{"theme": {{"bg":"...","surface":"...","title":"...","body":"...",'
        f'"accent":"...","primary":"...","secondary":"...","font":"...",'
        f'"style":"..."}}, "rationale":"一两句中文说明"}}\n'
        f"4. 颜色用 6 位十六进制，不带 #；style 取 business/academic/product/"
        f"geek/visual/plain 之一"
    )
    try:
        raw = call_llm([{"role": "user", "content": prompt}], key, base, model)
        data = _parse_theme_json(raw)
    except Exception as e:
        return None, f"生成主题失败：{e}"

    theme = data.get("theme") if isinstance(data, dict) else None
    if not isinstance(theme, dict):
        return None, "模型未返回 theme 字段"

    # 只保留认识的字段，避免脏数据
    clean = {}
    for f in _THEME_FIELDS:
        v = theme.get(f)
        if isinstance(v, str) and v.strip():
            clean[f] = v.strip().lstrip("#").upper()
    if not clean.get("bg") or not clean.get("accent"):
        return None, "主题缺少 bg 或 accent"
    return clean, (data.get("rationale") or "").strip()


def detect_theme(req):
    """从需求里识别用户显式指定的模板风格；没指定返回 None。

    用户说「总结为ppt，要科技风」时，宁可听用户的而不是模型的判断。
    """
    low = (req or "").lower()
    for theme, words in THEME_ALIASES.items():
        if any(w in low for w in words):
            return theme
    return None


def build_doc_chain(req, fmt, library, key, base, model, context_text=""):
    """执行文档生成链：总结 -> markdown -> 配图 -> 生成 -> 美化。

    context_text 是"要总结的内容"（例如上一轮的问答结果）。
    返回 (是否成功, 产出文件路径或说明)。
    """
    import skill_exec
    from runtime.core.prompt import build_system_prompt

    data_dir, skills_dir = _workspace_dirs()
    workdir = skill_exec.data_work_dir(data_dir)

    # ---- 第一步：总结分析，输出结构化大纲 ----
    source = (context_text or "").strip()
    if not source:
        source = req
    print("🧬 多步链：总结 → markdown → 生成 → 美化")
    print("  [1/4] 总结要点…")
    try:
        outline = call_llm([
            {"role": "system", "content": (
                "你是内容整理助手，把给定内容整理成 PPT 大纲。只输出 JSON，不要解释。\n\n"
                "输出格式（严格遵守）：\n"
                "{\"title\":\"总标题\",\"subtitle\":\"副标题\",\"style\":\"mckinsey\","
                "\"slides\":["
                "{\"title\":\"页标题\",\"bullets\":[\"要点1\",\"要点2\",\"要点3\"]}"
                "]}\n\n"
                "规则：\n"
                "1. slides 里每一项都**必须**同时有 title 和 bullets\n"
                "2. 每页 2-4 条 bullets，每条不超过 15 字\n"
                "3. 共 4-7 页\n"
                "4. style 取值之一：mckinsey(商务) / academic(学术) / product(产品) / "
                "geek(技术) / visual(创意)\n"
                "5. 不要输出 layout 字段，不要用 content/points/items 等其它字段名")},
            {"role": "user", "content": f"内容：\n{source[:6000]}"},
        ], key, base, model)
        data = parse_json_response(outline)
    except Exception as e:
        return False, f"总结失败：{e}"

    slides = data.get("slides") or []
    # 规范化：模型有时把内容放进 content/points/items，或只给 title，
    # 统一成 pptx 技能认识的 {title, layout, bullets|statement|data|compare}
    normalized = []
    for s in slides:
        if not isinstance(s, dict):
            continue
        item = dict(s)
        if not item.get("bullets"):
            for alt in ("content", "points", "items", "lines", "texts"):
                v = item.get(alt)
                if isinstance(v, list) and v:
                    item["bullets"] = [str(x) for x in v]
                    break
                if isinstance(v, str) and v.strip():
                    # 按行或分号拆开
                    parts = [p.strip(" -•·\t") for p in
                             re.split(r"[\n;；]+", v) if p.strip()]
                    if len(parts) > 1:
                        item["bullets"] = parts
                        break
        # 只有标题、没有任何内容的页，跳过（否则生成空白页）
        has_body = any(item.get(k) for k in
                       ("bullets", "statement", "data", "compare", "text"))
        if not has_body:
            continue
        # 章节扉页需要有标题
        if item.get("number") and not str(item.get("title") or "").strip():
            continue
        # 正文页必须有标题
        if not item.get("number") and not str(item.get("title") or "").strip():
            continue
        normalized.append(item)
    if normalized:
        slides = normalized
    if not slides:
        return False, "模型未给出有效的幻灯片内容"
    title = data.get("title") or "内容总结"
    # 风格：用户显式指定 > 模型选择 > 默认商务
    theme = detect_theme(req) or data.get("style") or "mckinsey"
    print(f"       得到 {len(slides)} 页大纲：{title}（风格 {theme}）")

    # ---- 让 LLM 依据排版规则，为这份内容现场生成主题 ----
    custom_theme = None
    print("      按排版规则生成专属主题…")
    summary = title + "：" + (data.get("subtitle") or "")
    for s in slides[:3]:
        summary += " " + str(s.get("title") or "")
    try:
        custom_theme, rationale = generate_custom_theme(
            summary + "\n" + (context_text or "")[:800], theme, key, base, model)
    except Exception as e:
        rationale = f"跳过（{e}）"
    if custom_theme:
        print(f"      主题已定制：{rationale[:70]}")
    else:
        print(f"      {rationale[:70]}")

    # ---- 第二步：写成 markdown ----
    print("  [2/4] 写成 markdown…")
    md_lines = [f"# {title}", ""]
    if data.get("subtitle"):
        md_lines += [f"*{data['subtitle']}*", ""]
    for s in slides:
        md_lines.append(f"## {s.get('title', '')}")
        md_lines.append("")
        for b in (s.get("bullets") or []):
            md_lines.append(f"- {b}")
        md_lines.append("")
    md_text = "\n".join(md_lines)

    import re as _re
    safe = _re.sub(r'[\\/:*?"<>|\s]+', '_', title)[:40] or "summary"
    md_path = os.path.join(workdir, f"{safe}.md")
    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_text)
        print(f"       {os.path.basename(md_path)}")
    except OSError as e:
        return False, f"写入 markdown 失败：{e}"

    # ---- 第三步：按需生成配图（visual/geek/product 风格用）----
    need_images = theme in ("visual", "geek", "product")
    cover_image = ""
    if need_images:
        print("  [3/5] 生成封面配图…")
        img_path = os.path.join(workdir, f"{safe}_cover.png")
        prompt = _image_prompt_for(title, [], theme)
        ok_img, msg_img = generate_image(prompt, img_path, size="1280x720")
        if ok_img:
            cover_image = img_path
            print(f"       {os.path.basename(img_path)}")
        else:
            print(f"       跳过（{msg_img}）")
    else:
        print("  [3/5] 配图跳过（当前风格无需全屏图）")

    # ---- 第四步：生成 pptx（把大纲与模板风格作为参数传入）----
    step_no = "4/5" if need_images else "3/4"
    label = "LLM 定制主题" if custom_theme else f"{theme} 模板"
    print(f"  [{step_no}] 生成 pptx（{label}）…")
    out_name = f"{safe}.pptx"
    slides_json = json.dumps(slides, ensure_ascii=False)
    if fmt == "ppt":
        args = [
            "--title", title, "--out", out_name,
            "--subtitle", data.get("subtitle") or "",
            "--slides", slides_json,
            "--theme", theme,
            "--ending", "感谢聆听",
        ]
        if cover_image:
            args += ["--cover-image", cover_image, "--overlay", "40"]
        # 自定义主题优先，退回首字母固定模板名
        if custom_theme:
            args += ["--theme-json", json.dumps(custom_theme, ensure_ascii=False)]
        # 页数较多时加目录页
        if len(slides) >= 5:
            args.append("--toc")
        result = skill_exec.execute_skill(library, "pptx", args=args)
        if not result.get("ok"):
            return False, f"生成 pptx 失败：{result.get('error') or result.get('stderr')}"
        produced = out_name
    else:
        # word / md 走 markdown-converter
        result = skill_exec.execute_skill(library, "markdown-converter", args=[
            md_path, "--to", "docx" if fmt == "word" else "md",
        ])
        produced = (result.get("outputs") or [out_name])[0]

    print(f"       {produced}")

    # ---- 最后一步：校验排版规范（不做重复生成，只核对）----
    last_step = "5/5" if need_images else "4/4"
    print(f"  [{last_step}] 排版规范校验…")
    try:
        check = _check_ppt_layout(data)
    except Exception as e:
        check = f"跳过（{e}）"
    print(f"       {check}")

    return True, os.path.join(workdir, produced)


def _check_ppt_layout(data):
    """按《PPT排版规则》核对大纲，返回问题摘要。

    只核对可量化的规则，不假装做了视觉美化：
        · 每页信息点 ≤4
        · 要点每行 ≤15 字
        · 单一观点 ≤24 字
        · 3 个并列要点用 three 版式
    """
    slides = data.get("slides") or []
    issues = []
    for i, s in enumerate(slides, 1):
        bullets = [str(b) for b in (s.get("bullets") or [])]
        if len(bullets) > 4:
            issues.append(f"第{i}页 {len(bullets)} 个信息点（应≤4）")
        long_lines = [b for b in bullets if len(b) > 15]
        if long_lines:
            issues.append(f"第{i}页有 {len(long_lines)} 行超过 15 字")
        stmt = s.get("statement")
        if stmt and len(str(stmt)) > 24:
            issues.append(f"第{i}页观点 {len(str(stmt))} 字（应≤24）")
    if not issues:
        return f"通过（{len(slides)} 页，信息密度与字数均符合规范）"
    head = "；".join(issues[:3])
    more = f" 等 {len(issues)} 项" if len(issues) > 3 else ""
    return f"发现 {len(issues)} 项偏差：{head}{more}"





# 名词出现位置：动词后 12 字内
# 正则不预编译：词表来自 data/words.json，改配置后应即时生效


def _dev_object_re():
    words = _wordlist("dev_code_objects")
    if not words:
        return None
    try:
        return re.compile("(" + "|".join(re.escape(w) for w in words) + ")", re.I)
    except re.error:
        return None


def _looks_like_dev_request(req):
    """需求是否指向改代码。

    判定取决于「动词 + 宾语」组合，不是光看动词：
      · 强动词（修改/实现/重构…）后跟代码对象 → 是
      · 弱动词（分析/排查…）必须跟代码对象才算，否则交给技能或问答
        （「分析日志」「分析数据」不是改代码）
    """
    text = (req or "").strip()
    if not text:
        return False
    low = text.lower()
    if any(w in low for w in _wordlist("dev_exclude")):
        return False

    obj_re = _dev_object_re()
    if obj_re is None:
        return False
    has_code_obj = bool(obj_re.search(text))
    if not has_code_obj:
        return False

    # 强动词 + 代码对象：确认
    for v in _wordlist("dev_strong_verbs"):
        idx = text.find(v)
        if idx < 0:
            continue
        window = text[idx:idx + len(v) + 12]
        if obj_re.search(window):
            return True

    # 弱动词 + 代码对象：只有宾语是代码类才算
    for v in _wordlist("dev_weak_verbs"):
        idx = text.find(v)
        if idx < 0:
            continue
        window = text[idx:idx + len(v) + 12]
        m = obj_re.search(window)
        if m:
            return True
    return False


def _is_plan_confirm(req):
    """「按方案执行」这类确认词：要接 codecore 暂存的方案，不能被本地规划抢走。"""
    try:
        import codecore_bridge
        return (codecore_bridge.has_pending_plan()
                and codecore_bridge.looks_like_confirm(req))
    except Exception:
        return False


def plan_locally(req, fuzzy=True):
    """本地规划阶段：先用蛋白库复用，再退回 skill-planner 规则库。

    这两步都是纯本地匹配，不调用大模型。命中即可直接进入执行。
    fuzzy=False 时只保留蛋白库与文档生成链这两个明确信号，
    跳过原子与规则库的模糊匹配（用户用 /mode 显式指定了代码模式）。
    返回 {"skill_chain", "tokens", "match"} 或 None。
    """
    # 一级：超蛋白（同构/异构、串行/并行组合）优先于单蛋白规则，
    # 否则「复刻A和B」会被 APV 的复刻规则抢成单链。
    sup = plan_superprotein(req)
    if sup:
        return {"superprotein": sup,
                "skill_chain": [s for b in sup.branches for s in b["chain"]],
                "tokens": 0, "match": sup.match, "level": "superprotein"}

    # 一级：蛋白库复用已有技能链
    hit = plan_from_protein(req)
    if hit:
        return {"skill_chain": hit["skill_chain"], "tokens": 0,
                "match": hit["match"], "level": "protein"}

    # 二级：文档生成类需求走多步链，不要当成单个原子
    low_req = (req or "").lower()
    # GitHub 热榜必须同时出现 github 与热榜/热门，否则「近期热门电影」
    # 会被「热门」这个泛词抢给 GitHub 技能
    if "github" in low_req and re.search(r"热榜|热门|trending", low_req):
        return {"skill_chain": ["github-trending"], "tokens": 0,
                "match": "规则:github+热榜", "level": "rule"}
    # 其它榜单类需求走搜索：模型容易把它们误配成「按人名下图」或其它单步技能
    # （「电影美女明星演的热榜」曾被执行成下载 3 次人脸图）
    if re.search(r"热榜|榜单|排行榜|排行|热门|趋势榜", low_req):
        return {"skill_chain": ["search"], "tokens": 0,
                "match": "规则:榜单→搜索", "level": "rule"}

    fmt = detect_doc_chain(req)
    if fmt:
        return {"skill_chain": [], "tokens": 0,
                "match": f"多步链:总结→md→{fmt}", "level": "chain",
                "doc_format": fmt}

    if not fuzzy:
        return None

    # 三级：原子级匹配（单个技能就能完成的需求）
    atom_hit = plan_from_atom(req)
    if atom_hit:
        return {"skill_chain": atom_hit["skill_chain"], "tokens": 0,
                "match": atom_hit["match"], "level": "atom"}

    # 三级：skill-planner 规则库
    planner_dir = os.path.join(HERE, "runtime", "skillexe", "skill-planner")
    script = os.path.join(planner_dir, "scripts", "skill_planner.py")
    if not os.path.isfile(script):
        return None
    try:
        import subprocess
        env = {**os.environ, "SKILL_DB_DIR": planner_dir, "PYTHONUTF8": "1"}
        proc = subprocess.run(
            [sys.executable, script, req, "--verbose"],
            capture_output=True, timeout=15, env=env, cwd=planner_dir,
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        if "详细结果" not in out:
            return None
        # 规划器把结果作为多行 JSON 打在"详细结果"之后
        body = out.split("详细结果", 1)[1]
        start = body.find("{")
        end = body.rfind("}")
        if start < 0 or end <= start:
            return None
        data = json.loads(body[start:end + 1])
        intent = data.get("intent") or {}
        if intent.get("match_type") in (None, "none"):
            return None
        # 技能链来自 chain；没有 chain 时用 atoms
        chain = [str(x) for x in (data.get("chain") or intent.get("atoms") or []) if x]
        if not chain:
            # 命中了命令但没解析出技能链，交给后续规划
            return None
        return {
            "skill_chain": chain,
            "tokens": int(intent.get("tokens") or 0),
            "match": intent.get("match_type") or "",
            "level": "planner",
        }
    except Exception:
        return None
    return None


def create_skill(skill_name, description="", workdir=None):
    """三级规划：用 skill-creator 现场生成缺失的技能骨架。

    调用 skill-creator/scripts/init_skill.py 在 workspace/skills 下建目录，
    返回 (ok, 消息)。
    """
    creator = os.path.join(HERE, "runtime", "skillexe", "skill-creator",
                           "scripts", "init_skill.py")
    if not os.path.isfile(creator):
        return False, "找不到 skill-creator"
    _, skills_dir = _workspace_dirs()
    skills_dir = os.path.abspath(skills_dir)
    target = workdir or skills_dir
    try:
        import subprocess
        proc = subprocess.run(
            [sys.executable, creator, skill_name, "--path", target],
            capture_output=True, timeout=60,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            return True, out or f"已创建技能 {skill_name}"
        return False, (err or out or f"创建失败（退出码 {proc.returncode}）")
    except Exception as e:
        return False, f"创建技能失败: {e}"


# ==================== 动态扩充 ====================
def list_disk_skills():
    """扫描所有技能根目录，返回 {技能名: 目录}。

    技能库是动态的：往 skills 目录里放一个新文件夹，下一次扫描就能看到，
    无需改代码或改索引。
    """
    found = {}
    for root in _skill_roots():
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isdir(path) and name not in found:
                found[name] = path
    return found


def refresh_skill_index():
    """把磁盘上新出现的技能补登记到 atoms/index.json。

    返回新增的技能名列表。已有条目不动，因此可以安全反复调用。
    """
    data_dir, _ = _workspace_dirs()
    idx_path = os.path.join(data_dir, "atoms", "index.json")
    if not os.path.isfile(idx_path):
        return []
    try:
        with open(idx_path, encoding="utf-8") as f:
            db = json.load(f)
    except (OSError, ValueError):
        return []

    atoms = db.setdefault("atoms", [])
    known = {a.get("skill") for a in atoms if a.get("skill")}
    next_index = max((int(a.get("index") or 0) for a in atoms), default=0)

    added = []
    for name, path in list_disk_skills().items():
        if name in known:
            continue
        next_index += 1
        desc = ""
        md = os.path.join(path, "SKILL.md")
        if os.path.isfile(md):
            try:
                with open(md, encoding="utf-8") as f:
                    for line in f:
                        if line.strip().startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip("'\"")
                            break
            except OSError:
                pass
        atoms.append({
            "index": next_index,
            "skill": name,
            "name_cn": name,
            "name_en": name,
            "nickname": name,
            "abbreviation": name.upper()[:6],
            "gene_code": "C",
            "gene_name": "Cytosine",
            "type": "扩展",
            "codon_level": 1,
            "description": desc or f"自动登记：{name}",
            "file": f"{name}.json",
            "auto_registered": True,
        })
        added.append(name)

    if added:
        db["total"] = len(atoms)
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    return added


def _next_free_codon(db):
    """取一个未被占用的 3 碱基密码子。

    按字母序找第一个空闲组合，避免和已登记的蛋白撞名。
    """
    used = set()
    for p in db.get("proteins", []):
        gc = (p.get("gene_chain") or "").upper()
        if gc:
            used.add(gc)
    for cm in (db.get("codon_mapping") or {}):
        used.add(cm.upper())
    bases = "ATCG"
    for a in bases:
        for b in bases:
            for c in bases:
                cand = a + b + c
                if cand not in used:
                    return cand
    n = 1
    while f"X{n}" in used:
        n += 1
    return f"X{n}"


def register_protein(skill_chain, name, gene_chain="", keywords=None,
                     rules=None, verify_only=False):
    """把一条**已验证执行成功**的技能链登记成蛋白，供后续复用。

    只有 ok=True 的链才应该调用这里——失败的链固化进库会让后续
    同样的需求命中错误流程。

    参数：
      skill_chain  技能名列表，如 ['search', 'web-summarizer']
      name         蛋白中文名，如「联网摘要链」
      gene_chain   指定密码子；留空则自动分配
      keywords     关键词列表，用于二级匹配
      rules        正则规则列表，用于一级匹配（可选）
      verify_only  只做校验不写入（用于预览）

    返回 (ok, 消息)。
    """
    if not skill_chain:
        return False, "技能链为空"

    # 校验技能真实存在，避免把错名字的链写进库
    data_dir, skills_dir = _workspace_dirs()
    try:
        lib = _make_library(data_dir, skills_dir)
        known = set(lib.skill_names()) | set(TOOL_HINTS)
    except Exception:
        known = None
    if known is not None:
        missing = [s for s in skill_chain if s not in known]
        if missing:
            return False, "技能不存在：" + ", ".join(missing)

    idx_path = os.path.join(data_dir, "proteins", "index.json")
    try:
        if os.path.isfile(idx_path):
            with open(idx_path, encoding="utf-8") as f:
                db = json.load(f)
        else:
            db = {"version": "1.0", "description": "蛋白层",
                  "proteins": [], "codon_mapping": {}, "available": []}
    except (OSError, ValueError):
        return False, "蛋白库读取失败"

    prots = db.setdefault("proteins", [])

    # 同一条链已存在就不重复登记
    norm = list(skill_chain)
    for p in prots:
        if list(p.get("skill_chain") or []) == norm:
            return False, f"该技能链已登记为 {p.get('gene_chain')}（{p.get('name_cn')}）"

    chain = gene_chain or _next_free_codon(db)
    for p in prots:
        if (p.get("gene_chain") or "").upper() == chain.upper():
            return False, f"基因链 {chain} 已存在"

    if verify_only:
        return True, f"可登记：{chain} {name} → {' → '.join(norm)}"

    prots.append({
        "index": max((int(p.get("index") or 0) for p in prots), default=0) + 1,
        "name_cn": name,
        "name_en": name,
        "nickname": name,
        "abbreviation": (name or chain)[:6].upper(),
        "gene_chain": chain,
        "skill_chain": norm,
        "core": name,
        "keywords": list(keywords or []) + [name],
        # 一级匹配规则；没有规则时靠 keywords 走二级匹配
        "rules": list(rules or []),
        "priority": 50,
        "description": "自动登记：" + " → ".join(norm),
        # 数据流按「顺序传递、后续吃上一步文字」的通用默认；
        # 需要文件流转的链由用户改 consumes
        "polypeptide_tree": {
            "start_codon": "AUG",
            "chain": "-".join(
                (("SV" + str(i + 1)) if i else "ST") for i in range(len(norm))),
            "nodes": [
                {"id": str(i + 1), "skill": s,
                 "consumes": (["prev_text"] if i else []),
                 "defaults": {}}
                for i, s in enumerate(norm)
            ],
            "stop_codon": "UAA",
        },
        "hash": f"auto_{chain.lower()}_v1",
        "file": f"{chain}.{name}.json",
        "auto_registered": True,
    })

    db.setdefault("codon_mapping", {})[chain] = {
        "protein_index": prots[-1]["index"],
        "polypeptides": [],
        "name": name,
    }
    db["total"] = len(prots)
    db["available"] = [c for c in (db.get("available") or [])
                       if c.upper() != chain.upper()]

    try:
        os.makedirs(os.path.dirname(idx_path), exist_ok=True)
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return False, f"写入失败: {e}"
    return True, f"已登记蛋白 {chain}：{name} → {' → '.join(norm)}"


def _print_protein_list():
    """列出蛋白库。"""
    data_dir, _ = _workspace_dirs()
    idx = os.path.join(data_dir, "proteins", "index.json")
    try:
        with open(idx, encoding="utf-8") as f:
            db = json.load(f)
    except (OSError, ValueError) as e:
        print(f"❌ 蛋白库读取失败: {e}")
        return
    prots = db.get("proteins") or []
    print(f"🧬 蛋白库共 {len(prots)} 条")
    print()
    for p in sorted(prots, key=lambda x: x.get("gene_chain") or ""):
        chain = " → ".join(p.get("skill_chain") or []) or "(无链)"
        auto = " [自动]" if p.get("auto_registered") else ""
        print(f"  {p.get('gene_chain', '?'):6s} {p.get('name_cn', '')[:16]:18s}{auto}")
        print(f"         {chain}")


def _protein_new_cli(args):
    """解析 `/protein new 名称 --skills a,b,c [--keywords x,y] [--rules 正则1,正则2]`。"""
    import shlex
    try:
        parts = shlex.split(args, posix=False)
    except ValueError:
        parts = args.split()

    name, skills, keywords, rules = "", [], [], []
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok in ("--skills", "-s") and i + 1 < len(parts):
            skills = [x.strip() for x in parts[i + 1].replace("，", ",").split(",") if x.strip()]
            i += 2
        elif tok in ("--keywords", "-k") and i + 1 < len(parts):
            keywords = [x.strip() for x in parts[i + 1].replace("，", ",").split(",") if x.strip()]
            i += 2
        elif tok in ("--rules", "-r") and i + 1 < len(parts):
            rules = [x.strip() for x in parts[i + 1].replace("，", ",").split(",") if x.strip()]
            i += 2
        elif not name:
            name = tok
            i += 1
        else:
            i += 1

    if not name or not skills:
        print("用法：/protein new 名称 --skills 技能1,技能2 [--keywords 说法1,说法2] [--rules 正则1]")
        return

    ok, msg = register_protein(skills, name, keywords=keywords, rules=rules)
    if ok:
        print(f"✅ {msg}")
        print("   已生效，无需重启")
    else:
        print(f"❌ {msg}")


def _protein_del_cli(chain):
    """按基因码删除蛋白。"""
    chain = chain.strip().upper()
    if not chain:
        print("用法：/protein del 基因码（如 GAA）")
        return
    data_dir, _ = _workspace_dirs()
    idx = os.path.join(data_dir, "proteins", "index.json")
    try:
        with open(idx, encoding="utf-8") as f:
            db = json.load(f)
    except (OSError, ValueError) as e:
        print(f"❌ 蛋白库读取失败: {e}")
        return
    prots = db.get("proteins") or []
    target = [p for p in prots if (p.get("gene_chain") or "").upper() == chain]
    if not target:
        print(f"❌ 找不到蛋白 {chain}")
        return
    db["proteins"] = [p for p in prots if (p.get("gene_chain") or "").upper() != chain]
    (db.get("codon_mapping") or {}).pop(chain, None)
    db["total"] = len(db["proteins"])
    if chain not in (db.get("available") or []):
        db.setdefault("available", []).append(chain)
        db["available"] = sorted(set(db["available"]))
    try:
        with open(idx, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"❌ 写入失败: {e}")
        return
    print(f"✅ 已删除 {chain}（{target[0].get('name_cn')}），剩余 {db['total']} 条")


def _maybe_remember_chain(planned, ok, requirement):
    """执行成功的 LLM 规划链，存进蛋白库供下次复用。

    只在「规划来自 LLM」且「执行全部成功」时登记——
    失败或本地已有的链不写入，避免污染蛋白库。
    """
    try:
        if not ok:
            return
        if not planned or planned.get("level") != "llm":
            return
        chain = list(planned.get("skill_chain") or [])
        # 单技能没必要建蛋白（原子层已经能命中），至少两步才值得固化
        if len(chain) < 2:
            return
        keywords = [(requirement or "").strip()][:1]
        name = (requirement or "自动链").strip()[:16]
        good, msg = register_protein(chain, name, keywords=keywords)
        if good:
            print(f"💾 {msg}")
    except Exception:
        pass


# 明确的联网需求特征：命中就直接进 task，不必再问 LLM。
# 词表来自 data/runtime.json 的 web.hints。


def _looks_like_web_task(req):
    """需求是否明确需要联网。"""
    low = (req or "").lower()
    hints = (_runtime().get("web") or {}).get("hints") or []
    return any(w in low for w in hints)


def classify_intent(req, library, key, base, model, recent=""):
    """判断需求应该交给技能、工具，还是改代码。

    返回 "task"（交给技能/工具执行）、"code"（改项目代码）或 "answer"（直接回答）。

    recent 是最近几轮做过的事（如「刚改了 game/game.js 的障碍物数量」）。
    没有它时，像「出现的太集中了障碍物」这种追问会被当成孤立提问，
    误判为 answer，用户还得再解释一遍上下文。
    """
    skills = "\n".join(
        f"  - {name}：{library.describe(name).get('description') or ''}"
        for name in library.skill_names()
    )
    tools = "\n".join(f"  - {name}：{hint}" for name, hint in TOOL_HINTS.items())
    context_block = ""
    if recent:
        context_block = (
            f"【最近的工作上下文】\n{recent}\n\n"
            f"注意：如果需求是在补充/修正上面刚做的事（如「改一下刚才那个」"
            f"「太集中了」「再小一点」），即使句子本身像提问，也应判为 "
            f"code 或 task，而不是 answer。\n\n"
        )
    prompt = (
        f"判断下面这条需求属于哪一类，只输出一个词：task / code / answer\n\n"
        f"task   = 用现有技能或工具完成一件事（如生成 PPT、生图、识别图片、查日志、"
        f"**联网查新闻/资料/网页**、写文件）\n"
        f"code   = 修改或新增本项目（当前代码库）的源代码\n"
        f"answer = 提问或闲聊，不需要动手\n\n"
        f"重点区分：\n"
        f"- 只要需求涉及「查最新的」「搜一下」「网上」「新闻」「资料」——属于 task，"
        f"用 search/fetch 工具查，不要判成 answer\n"
        f"- answer 只用于：问你是谁/会什么、问概念、闲聊、问这行代码什么意思\n\n"
        f"{context_block}"
        f"可用技能：\n{skills}\n\n可用工具：\n{tools}\n\n"
        f"需求：{req}"
    )
    try:
        raw = call_llm([{"role": "user", "content": prompt}], key, base, model).strip().lower()
    except Exception:
        return "code"
    for kind in ("task", "answer", "code"):
        if kind in raw:
            return kind
    return "code"


def answer_question(req, library, key, base, model):
    """把问题直接交给模型回答，不扫描代码、不生成修改。

    这是「回答」类需求（问能力、问用法、闲聊、咨询）的正路：

    这是「回答」类需求（问能力、问用法、闲聊、咨询）的正路：
    带上自己的技能清单与工具能力做上下文，让模型自然作答。
    """
    from runtime.core.prompt import build_system_prompt

    # 技能清单：名字 + 描述，供模型介绍自己会什么
    try:
        entries = []
        for name in library.skill_names():
            info = library.describe(name) or {}
            desc = (info.get("description") or "").strip()
            entries.append(f"- {name}：{desc}" if desc else f"- {name}")
        skill_block = "\n".join(entries[:60])
    except Exception:
        skill_block = ""

    tool_block = "\n".join(f"- {n}：{h}" for n, h in TOOL_HINTS.items())

    system = build_system_prompt(
        selected_tools=list(TOOL_HINTS),
        cwd=os.getcwd(),
        append_system_prompt=(
            "现在处于「问答模式」：只回答用户的问题，不要修改任何代码。\n\n"
            "【你的身份】\n"
            "你是 DBAgent，一个运行在本机的开发助手程序。\n"
            "不要自称底层模型的名字（如 Agnes、GPT、Claude 等），也不要提开发厂商。\n"
            "仅在被问「你是谁」时回答身份；被问「会什么/能做什么」时必须逐项列出"
            "下面的真实技能与工具，不要只回一句身份介绍。\n\n"
            f"【你的技能】共 {len(library.skill_names())} 个，以下是真实清单：\n{skill_block}\n\n"
            f"【你的内置工具】\n{tool_block}\n\n"
            "【能力边界——必须遵守】\n"
            "1. 你**没有**在后台自动查文件的能力，也没有预置的项目上下文。\n"
            "   绝不能回答「我来帮你查找……的文件或配置」这类话——那需要真的调用工具，\n"
            "   而你现在处于「只回答、不动手」模式。\n"
            "2. 你**不能**直接访问互联网。要联网查新闻/资料，必须由用户提出需求后\n"
            "   走 search / fetch 工具，那属于「执行」而不是「回答」。\n"
            "3. 不能输出 <tool_call>、<function_call> 这类标签——当前模式没有工具执行器，\n"
            "   标签会原样显示给用户。\n"
            "4. 如果用户的需求需要动手（改代码、查网络、读文件），直接告诉他做成这件事\n"
            "   需要切到执行模式，或把需求说得更具体些，而不是假装自己在做。\n\n"
            "回答要求：简洁具体；介绍能力时基于上面的真实清单，不要编造没有的技能。"
        ),
    )
    try:
        return call_llm(
            [{"role": "system", "content": system},
             {"role": "user", "content": req}],
            key, base, model,
        ).strip()
    except Exception as e:
        return f"（回答失败：{e}）"


def route_intent(req, library, key, base, model, forced=None):
    """把需求分派到 work / plan / goal / code 四条路由。

    work = 用现有技能/工具完成一件事（任务执行、问答）
    plan = 代码任务，但先出方案让用户确认再动手
    goal = 目标驱动：描述要达到的状态，循环试错直到验收通过
    code = 直接进入读写循环，边做边看结果

    forced 指定时跳过自动判定（/mode 命令显式选择）。
    """
    if forced in ("work", "plan", "goal", "code"):
        return forced
    if _looks_like_goal_task(req):
        return "goal"
    if _looks_like_plan_task(req):
        return "plan"
    # 明确的联网需求直接走 work（task），省一次 LLM 调用，
    # 也避免被判成 answer 后模型空口许诺「我来帮你查」
    if _looks_like_web_task(req):
        return "work"
    try:
        kind = classify_intent(req, library, key, base, model)
    except Exception:
        return "code"
    if kind in ("task", "answer"):
        return "work"
    return "code"





def _looks_like_goal_task(req: str) -> bool:
    """目标是「修好/跑通/通过」这类可验收状态，适合循环试错。"""
    low = (req or "").lower()
    return any(w in low for w in _wordlist("goal_hints"))


def _looks_like_plan_task(req: str) -> bool:
    """涉及面广或用户明说要方案，适合先规划后执行。"""
    low = (req or "").lower()
    return any(w in low for w in _wordlist("plan_hints"))


def run_code_agent(req, key, base, model, yes, mode=None,
                   on_event=None, should_stop=None):
    """code 路由入口：Agent 循环在沙箱工作目录内读写。

    工作域固定为 workspace/data/work：可写模式的 write/edit 直写这里，
    ls/bash 沙箱也只挂载这里。两层同域，模型看到的目录就是它
    实际能操作的目录；产出统一落盘到 data/work。

    on_event / should_stop 供后台任务队列接入：
      on_event(kind, text) 转发每一步输出
      should_stop()        每轮询问是否打断
    """
    import agent_loop
    import skill_exec
    try:
        data_dir, _ = _workspace_dirs()
        workdir = skill_exec.data_work_dir(data_dir)
    except Exception:
        workdir = os.path.join(os.path.expanduser("~"), ".dbagent", "work")
    from runtime.core.settings import load_settings
    try:
        cfg = load_settings(HERE).to_dict()
    except Exception:
        cfg = {}

    # codecore 引擎：长程任务、按难度自动选 PLAN/BUILD，不设轮数上限
    if str(cfg.get("agent_engine") or "classic") == "codecore":
        try:
            import codecore_bridge
            return codecore_bridge.run_long_task(
                req, workdir, key, base, model,
                mode=mode or "auto",
                writable=bool(cfg.get("agent_writable")),
                auto_approve=bool(cfg.get("agent_auto_approve")),
                think_level=int(cfg.get("agent_think_level") or 3),
                on_event=on_event, should_stop=should_stop, yes=yes,
            )
        except Exception as e:
            print(f"⚠️ codecore 引擎不可用（{type(e).__name__}: {e}），回退到 classic。")

    return agent_loop.run_agent_loop(
        req, workdir, key, base, model, yes=yes, mode=mode,
        writable=bool(cfg.get("agent_writable")),
        max_rounds=int(cfg.get("agent_max_rounds") or 24),
        on_event=on_event,
        should_stop=should_stop,
    )


def agent_writable() -> bool:
    """读取当前写权限开关。"""
    from runtime.core.settings import load_settings
    try:
        return bool(load_settings(HERE).agent_writable)
    except Exception:
        return False


def set_agent_writable(value: bool) -> str:
    """持久化写权限开关到项目级配置，返回提示语。"""
    from runtime.core.settings import load_settings, save_settings
    try:
        settings = load_settings(HERE)
        settings.agent_writable = bool(value)
        path = save_settings(settings, scope="project", cwd=HERE)
    except Exception as e:
        return f"⚠️ 保存失败：{e}"
    state = "已开启" if value else "已关闭"
    return (f"✏️ 代码代理写权限{state}（写入 {path}）。\n"
            f"   {'write/edit 直接写 data/work，与命令执行同一目录，执行前不再逐次询问。' if value else 'write/edit 只能写 data/work。'}")



# ==================== 自动路由 ====================
# 技能状态变化时才调用 LLM 生成回答并缓存；状态不变直接复用本地缓存。
# 这样「你会啥」这类问题只在技能库变化后的第一次走 LLM，之后都是毫秒级。

_ROUTE_CACHE_DIR = None
_route_cache_memo: dict = {}


def _route_cache_path():
    """路由缓存文件位置：~/.dbagent/route_cache.json，不可写则退回项目内。"""
    global _ROUTE_CACHE_DIR
    candidates = [
        os.path.join(os.path.expanduser("~"), ".dbagent", "route_cache.json"),
        os.path.join(HERE, "route_cache.json"),
    ]
    if _ROUTE_CACHE_DIR is not None:
        return _ROUTE_CACHE_DIR
    for path in candidates:
        try:
            parent = os.path.dirname(path)
            os.makedirs(parent, exist_ok=True)
            probe = parent + f"\\.probe-{os.getpid()}"
            with open(probe, "w", encoding="utf-8") as f:
                f.write("")
            os.remove(probe)
            _ROUTE_CACHE_DIR = path
            return path
        except OSError:
            continue
    _ROUTE_CACHE_DIR = candidates[-1]
    return _ROUTE_CACHE_DIR


def skill_state_fingerprint(library):
    """技能库状态指纹：名字 + 目录 + SKILL.md 修改时间。

    任何技能被新增、删除、移动或改过 SKILL.md，指纹都会变，
    从而触发一次重新问 LLM。
    """
    import hashlib

    h = hashlib.md5()
    try:
        names = library.skill_names()
    except Exception:
        names = []
    for name in names:
        h.update(name.encode("utf-8"))
        try:
            path = library.skill_dir(name) or ""
        except Exception:
            path = ""
        h.update(path.encode("utf-8"))
        md = os.path.join(path, "SKILL.md") if path else ""
        if md and os.path.isfile(md):
            try:
                h.update(str(int(os.path.getmtime(md))).encode())
            except OSError:
                pass
    return f"{len(names)}:{h.hexdigest()[:16]}"


def _load_route_cache():
    path = _route_cache_path()
    if path in _route_cache_memo:
        return _route_cache_memo[path]
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    _route_cache_memo[path] = data
    return data


def _save_route_cache(data):
    path = _route_cache_path()
    _route_cache_memo[path] = data
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# 缓存格式版本：回答内容或判定标准变化时递增，旧条目会自动作废
_ROUTE_CACHE_VERSION = 2


def _answer_usable(answer, kind="capability"):
    """回答能不能进缓存。

    能力清单类回答一旦缓存就会被长期复用，所以必须确认它真的列了东西；
    只回一句身份介绍的短回答不能存，否则会一直答这个。
    简洁的清单可能不足 80 字，所以有多个列表项也算合格。
    """
    text = (answer or "").strip()
    if not text or text.startswith("（回答失败"):
        return False
    if kind != "capability":
        return True
    if len(text) >= 80:
        return True
    bullets = re.findall(r"(?m)^\s*(?:[-*•·]|\d+[.、)])\s*\S", text)
    return len(bullets) >= 3


def route_answer(req, library, key, base, model, kind="capability"):
    """自动路由的答案获取：状态变了问 LLM，状态没变用本地缓存。

    返回 (答案, 来源)，来源为 "llm" / "cache" / "local"。
    缓存命中会在缓存文件里累加统计，便于判断缓存是否真的在省调用。
    """
    cache = _load_route_cache()
    fp = skill_state_fingerprint(library)
    entry = cache.get(kind) or {}
    cached_fp = entry.get("fingerprint")
    cached_ver = entry.get("version")

    # 状态未变且格式版本一致：优先复用缓存答案，并记录命中
    if (cached_fp == fp and cached_ver == _ROUTE_CACHE_VERSION
            and _answer_usable(entry.get("answer"), kind)):
        entry["hits"] = int(entry.get("hits") or 0) + 1
        entry["last_hit"] = time.strftime("%Y-%m-%d %H:%M:%S")
        # 记录最近命中的问题（去重，保留最近 10 条）
        asked = entry.get("asked") or []
        if req not in asked:
            asked.append(req)
            entry["asked"] = asked[-10:]
        cache[kind] = entry
        _save_route_cache(cache)
        return entry["answer"], "cache"

    # 状态变了：问 LLM，成功后连同指纹一起缓存
    answer = answer_question(req, library, key, base, model)
    if _answer_usable(answer, kind):
        cache[kind] = {
            "version": _ROUTE_CACHE_VERSION,
            "fingerprint": fp,
            "answer": answer,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "question": req,
            "hits": 0,
            "misses": int(entry.get("misses") or 0) + 1,
            "asked": [req],
        }
        _save_route_cache(cache)
        return answer, "llm"

    # LLM 失败：退回本地清单，保证有答复
    return describe_capabilities(), "local"


def cache_stats():
    """返回缓存命中统计，供 /cache 命令查看。"""
    cache = _load_route_cache()
    if not cache:
        return "缓存为空（还没有生成过任何缓存）"
    lines = [f"缓存文件：{_route_cache_path()}"]
    for kind, e in cache.items():
        fp = e.get("fingerprint") or "?"
        hits = int(e.get("hits") or 0)
        misses = int(e.get("misses") or 0)
        total = hits + misses
        rate = (hits / total * 100) if total else 0
        lines.append(
            f"  [{kind}] 指纹 {fp}  命中 {hits} 次 / 生成 {misses} 次"
            f"（命中率 {rate:.0f}%）  更新于 {e.get('updated') or '-'}")
        asked = e.get("asked") or []
        if asked:
            lines.append(f"      命中过的问题：{'、'.join(asked[:5])}")
    return "\n".join(lines)


def handle_workflow(req, key, base, model):
    """识别并执行工作流，返回 True 表示已处理，False 表示不匹配"""
    if not any(kw in req for kw in WORKFLOW_KEYWORDS) and not req.lower().startswith("stopj"):
        return False
    return run_task(req, key, base, model)


def run_task(req, key, base, model):
    """把需求拆成技能/工具步骤并执行，返回 True 表示已处理。

    run_task.last_result 记录上一次工作流的可读结果，
    供「结果呢」「发给我」这类追问直接取用。
    """
    if not hasattr(run_task, "last_result"):
        run_task.last_result = ""
    try:
        import skill_exec
        data_dir, skills_dir = _workspace_dirs()
        library = _make_library(data_dir, skills_dir)
        forced = _triggered_skill(req)
        if forced and forced not in library.skill_names():
            forced = None

        if req.lower().startswith("stopj"):
            expr = normalize_stopj(req)
            planned_args = {}
            if not expr:
                print(f"❌ 无法解析表达式: {req.strip()[:200]!r}")
                return True
        else:
            plan = _plan_workflow(req, library, key, base, model, forced=forced)
            if not plan:
                print("❌ AI 未能给出可执行的步骤")
                return True
            expr, planned_args = plan

        known = set(library.skill_names()) | set(TOOL_HINTS)
        steps, _ = skill_exec.stopj_parse(expr, sorted(known))
        if not steps:
            print(f"❌ 表达式 {expr} 解析后没有任何步骤")
            return True
        unknown = [s["skill"] for s in steps
                   if s["type"] == "SKILL" and s["skill"] not in known]
        if unknown:
            print(f"❌ 引用了不存在的技能或工具: {', '.join(unknown)}")
            return True

        print(f"🔄 工作流: {expr}")
        _src = "stopj" if req.lower().startswith("stopj") else "llm"
        ok, detail = _run_planned(expr, planned_args, library, data_dir,
                                  requirement=req,
                                  source=_src,
                                  key=key, base=base, model=model)
        # LLM 现场规划出来的多步链，跑成功就固化进蛋白库，
        # 下次同样的需求直接本地命中，不再花 LLM 的 token。
        if _src == "llm" and ok:
            _chain = [s["skill"] for s in steps if s.get("type") == "SKILL"]
            _maybe_remember_chain({"level": "llm", "skill_chain": _chain},
                                  ok, req)
        # 把结果暂存，主循环可据此记住上下文（如「结果呢」追问）
        run_task.last_result = detail
        return True
    except Exception as e:
        print(f"❌ 工作流执行失败: {e}")
        return True


def _workspace_dirs():
    """定位 workspace 下的数据目录与 skills 目录。

    动态数据（原子库/蛋白库/词表/运行时配置）统一放在 data/dynamic/，
    与 work/（运行产出）分开，方便整体备份或替换。
    兼容旧布局：data/dynamic 不存在时直接用 data。
    """
    data_root = os.path.join(HERE, "..", "workspace", "data")
    skills_dir = os.path.join(HERE, "..", "workspace", "skills")
    if not os.path.isdir(data_root):
        return "data", "skills"
    dynamic = os.path.join(data_root, "dynamic")
    return (dynamic if os.path.isdir(dynamic) else data_root), skills_dir


# ==================== 运行时配置 ====================
# 尺寸说法、动词表、噪声前缀这些属于「语言/领域知识」，不是引擎逻辑。
# 全部放 data/runtime.json，内核只读不写 —— 改配置即可调整行为。
_RUNTIME_CACHE = {"key": None, "cfg": {}}

# 配置文件缺失时的最小兜底，保证程序还能跑
_RUNTIME_DEFAULT = {
    "size": {"ratio_pattern": r"(\d{1,2})\s*[:：]\s*(\d{1,2})", "words": {}},
    "person_name": {"verb_pattern": "", "max_length": 5, "min_length": 2},
    "web": {"hints": [], "weak_news": []},
    "output": {"image_suffixes": [".jpg", ".jpeg", ".png", ".webp"],
               "doc_suffixes": [".docx", ".pdf", ".md", ".txt"]},
    "text_noise_prefix": ["📊", "✅", "❌", "==", "--"],
}


def _runtime():
    """读取 data/runtime.json，按 mtime 缓存。"""
    try:
        data_dir, _ = _workspace_dirs()
        path = os.path.join(data_dir, "runtime.json")
        mtime = os.path.getmtime(path)
    except OSError:
        return _RUNTIME_DEFAULT
    if _RUNTIME_CACHE["key"] == mtime:
        return _RUNTIME_CACHE["cfg"]
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    # 浅合并兜底，缺字段不至于崩
    merged = dict(_RUNTIME_DEFAULT)
    merged.update(cfg or {})
    _RUNTIME_CACHE.update({"key": mtime, "cfg": merged})
    return merged


# ==================== 词表配置 ====================
# 所有判定用的词表都放 data/words.json，内核只读不写。
# 改词表不需要动代码，也不会因列表散落在 main.py 里而漏改。
_WORDS_CACHE = {"key": None, "cfg": {}}

# words.json 缺失时的最小兜底，保证程序还能跑
_WORDS_DEFAULT = {
    "dev_strong_verbs": ["新建", "创建", "开发", "搭建", "实现", "编写",
                        "重构", "修改", "改进", "优化", "修复", "调整", "写"],
    "dev_weak_verbs": ["分析", "排查", "定位", "看看", "检查", "审查", "梳理"],
    "dev_code_objects": ["项目", "代码", "函数", "模块", "脚本", "程序",
                         "应用", "系统", "游戏", "bug", "报错", "模式", "逻辑"],
    "dev_exclude": ["文档", "ppt", "word", "excel", "表格", "小说", "图片",
                    "新闻", "天气", "数据", "日志", "报告"],
    "goal_hints": ["修复", "修好", "搞定", "解决", "跑通", "报错"],
    "plan_hints": ["重构", "重新设计", "架构", "大规模", "全面", "迁移"],
    "not_name_words": ["任务", "定时", "日志", "代码", "项目", "文件",
                       "数据", "分析", "报告", "配置", "执行"],
    "edit_hint_words": ["修改", "改成", "调整", "重构", "修复", "实现",
                        "新增", "添加", "删除", "替换", "更新"],
    "weather_noise": ["今日", "今天", "明天", "天气", "气温", "温度", "的"],
    "github_noise": ["github", "热榜", "热门", "项目", "最近", "关于", "的"],
    "generic_words": ["文档", "文件", "处理", "生成", "管理", "工具",
                      "系统", "内容", "格式", "转换", "技能"],
    "function_words": ["一个", "一下", "帮我", "我要", "需要", "进行"],
    "en_function_words": ["the", "and", "for", "with", "this"],
    "summarize_words": ["总结", "整理", "归纳", "汇总", "提炼", "概括"],
    "greetings": ["你好", "您好", "嗨", "hello", "hi"],
    "thanks": ["谢谢", "感谢", "多谢", "thanks"],
}


def _words():
    """读取词表，按 mtime 缓存。

    两种布局都支持：
      · data/words.json           单文件（旧）
      · data/words/index.json     与蛋白库一致：索引 + 每组一个文件
    """
    data_dir, _ = _workspace_dirs()
    index_path = os.path.join(data_dir, "words", "index.json")
    single_path = os.path.join(data_dir, "words.json")

    use_index = os.path.isfile(index_path)
    target = index_path if use_index else single_path
    try:
        mtime = os.path.getmtime(target)
        # 分组文件改了也要重读：把分组目录的 mtime 一起算进来
        if use_index:
            gdir = os.path.join(data_dir, "words")
            mtime = max(mtime, os.path.getmtime(gdir))
    except OSError:
        return _WORDS_DEFAULT
    if _WORDS_CACHE["key"] == mtime:
        return _WORDS_CACHE["cfg"]

    cfg = {}
    try:
        if use_index:
            with open(index_path, encoding="utf-8") as f:
                index = json.load(f)
            for item in index.get("groups") or []:
                gname = item.get("group")
                fname = item.get("file")
                if not gname or not fname:
                    continue
                fpath = os.path.join(data_dir, "words", fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        payload = json.load(f)
                    cfg[gname] = payload.get("words") or []
                except (OSError, ValueError):
                    continue
        else:
            with open(single_path, encoding="utf-8") as f:
                cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}

    merged = dict(_WORDS_DEFAULT)
    merged.update(cfg or {})
    _WORDS_CACHE.update({"key": mtime, "cfg": merged})
    return merged


def _wordset(key):
    """取一组词表为集合（匹配用，自动转小写不适用中文）。"""
    return set(_words().get(key) or ())


def _wordlist(key):
    return list(_words().get(key) or ())


def _skill_roots(skills_dir=None):
    """返回所有技能根目录，按优先级排列。

    workspace/skills 是主目录；runtime/skillexe 存放技能管理类
    技能（skill-manager / skill-planner / skill-runner / skill-tracker /
    SECURE_INSTALL / STOPJRuntime），作为补充来源一起纳入运行时。
    """
    if skills_dir is None:
        _, skills_dir = _workspace_dirs()
    roots = [skills_dir]
    extra = os.path.join(HERE, "runtime", "skillexe")
    if os.path.isdir(extra):
        roots.append(extra)
    return roots


def _make_library(data_dir, skills_dir):
    """构造技能运行时，纳入全部技能根目录。"""
    import skill_exec
    return skill_exec.SkillLibrary(data_dir, _skill_roots(skills_dir))


def _shortlist_skills(req, library, key, base, model, limit=4):
    """根据需求挑选可能用到的技能，返回名称列表（按相关度）。

    若需求命中了某条蛋白链，该链的技能会被强制塞进候选项——
    否则模型只看描述做挑选，很可能漏掉链里真正需要的技能
    （「近日新闻」就漏了 web-summarizer，只挑出 search）。
    """
    names = library.skill_names()

    # 蛋白链命中的技能必须进候选
    forced_in = []
    hit = plan_from_protein(req)
    if hit:
        forced_in = [s for s in (hit.get("skill_chain") or []) if s in names]

    catalog = "\n".join(
        f"- {n}：{library.describe(n).get('description') or ''}" for n in names
    )
    prompt = (
        f"从下列技能中挑出完成该需求会用到的，最多 {limit} 个，按相关度排序。\n"
        f"只输出 JSON：{{\"skills\": [\"名称\"]}}\n"
        f"没有合适的技能就输出 {{\"skills\": []}}\n\n"
        f"技能列表：\n{catalog}\n\n需求：{req}"
    )
    try:
        data = call_llm_json([{"role": "user", "content": prompt}], key, base, model, attempts=1)
    except Exception:
        picked = []
        data = None
    else:
        picked = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(picked, list):
            picked = []
    valid = [p for p in (str(x) for x in picked) if p in names]

    # 链命中的技能排在最前，保证模型一定能看到
    merged = list(dict.fromkeys(forced_in + valid))
    return merged[:limit] if merged else names


# 需求里的词 -> 技能名。命中即直接用该技能，不再让模型自由规划。
SKILL_TRIGGERS = {
    "pptx": ["ppt", "幻灯片", "演示文稿", "slides", "deck"],
    "docx": ["word", "docx", "文档"],
    "xlsx": ["excel", "表格", "xlsx"],
    "pdf": ["pdf"],
    "agnes-image": ["生图", "画图", "生成图片"],
    "visioncustom": ["识别图片", "看图"],
    "youtube-watcher": ["youtube", "视频字幕"],
    "web-summarizer": ["网页摘要", "总结网页"],
}


def _triggered_skill(req):
    """需求里出现明确的技能关键词时，返回对应技能名。"""
    low = req.lower()
    for skill, words in SKILL_TRIGGERS.items():
        if any(w in low for w in words):
            return skill
    return None


# ==================== 本地快捷通道 ====================
# 明确到无需模型判断的操作，直接在本地执行，不消耗任何 LLM 调用。
# 每条规则：(正则, 处理函数, 说明)。命中即返回结果，跳过意图判断与规划。
_DIR_CREATE_PAT = re.compile(
    r"^(?:请|帮我|帮忙|麻烦)?\s*"
    r"(?:新建|创建|建|mkdir)\s*"
    r"(?:(?:一个|个)\s*)?"
    r"(?P<name>.*?)\s*$"
)
_DIR_LIST_PAT = re.compile(
    r"^(?:请|帮我|帮忙|麻烦)?\s*"
    r"(?:查看|列出|显示|ls|dir)\s*"
    r"(?P<path>.*?)\s*$"
)


def _safe_relpath(name, workdir):
    """把用户给的名字规整成工作目录内的相对路径，拒绝越界。"""
    if not name:
        return None
    name = name.strip().strip("'\"").strip()
    if not name:
        return None
    # 去掉常见后缀噪声
    for noise in ("的文件夹", "文件夹", "目录"):
        if name.endswith(noise) and len(name) > len(noise):
            name = name[: -len(noise)].strip()
    if not name:
        return None

    target = os.path.realpath(os.path.join(workdir, name))
    root = os.path.realpath(workdir)
    if target != root and not target.startswith(root + os.sep):
        return None  # 越界，交回给模型处理
    return target


def _unique_dir(workdir, stem="新建文件夹"):
    """没有给名字时，生成一个不冲突的目录名。"""
    path = os.path.join(workdir, stem)
    if not os.path.exists(path):
        return path
    for i in range(2, 1000):
        path = os.path.join(workdir, f"{stem}{i}")
        if not os.path.exists(path):
            return path
    return None


def try_local_action(req, workdir):
    """尝试本地直接处理明确的文件操作。

    返回结果字符串表示已处理；返回 None 表示不匹配，交回模型。
    这些操作不需要任何 LLM 调用，因此是毫秒级。
    """
    text = req.strip()
    if not text or len(text) > 60:
        return None

    # ---- 新建文件夹 / 创建目录 ----
    m = _DIR_CREATE_PAT.match(text)
    if m:
        token = m.group("name").strip()
        is_dir_hint = ("文件夹" in text or "目录" in text
                       or "mkdir" in text.lower() or token == "")
        # "新建文件 xxx.py" 交给模型，不当成建目录
        if is_dir_hint and "文件" not in text.replace("文件夹", ""):
            # token 只含"文件夹/目录"等噪声时，视同没给名字
            cleaned = token
            # 先在空白处切开，去掉前面单独出现的"文件夹"/"目录"
            for noise in ("的文件夹", "文件夹", "目录"):
                if cleaned.startswith(noise):
                    cleaned = cleaned[len(noise):].strip()
            for noise in ("的文件夹", "文件夹", "目录"):
                if cleaned.endswith(noise):
                    cleaned = cleaned[: -len(noise)].strip()
            target = _safe_relpath(cleaned, workdir) if cleaned else None
            if target is None:
                target = _unique_dir(workdir)
                if target is None:
                    return None
            if os.path.exists(target):
                return f"目录已存在：{target}"
            try:
                os.makedirs(target, exist_ok=True)
                return f"已创建目录：{target}"
            except OSError as e:
                return f"创建失败：{e}"

    # ---- 查看当前目录 ----
    m = _DIR_LIST_PAT.match(text)
    if m:
        raw = m.group("path").strip()
        if raw in ("", "当前", "当前目录", "一下", "所有", "目录", "文件"):
            raw = ""
        target = _safe_relpath(raw, workdir) if raw else workdir
        if target is None or not os.path.isdir(target):
            return None
        try:
            names = sorted(os.listdir(target))
        except OSError as e:
            return f"读取目录失败：{e}"
        if not names:
            return f"{target}\n（空目录）"
        lines = [f"{target}"]
        for n in names[:100]:
            full = os.path.join(target, n)
            lines.append(("  [DIR] " if os.path.isdir(full) else "  ") + n)
        if len(names) > 100:
            lines.append(f"  ...共 {len(names)} 项")
        return "\n".join(lines)

    return None


def _protein_catalog_text():
    """把可执行的蛋白链整理成给模型看的「预置流程」清单。

    模型原本只能看到单个技能/工具，于是「近日新闻」只会挑 search，
    不知道库里已有「搜索→抓取→摘要」这条链。把流程摆给它，
    让它自己判断该用预置链还是自己组合。
    """
    lines = []
    for entry in _load_protein_db():
        chain = entry.get("skill_chain") or []
        if not chain:
            continue
        name = entry.get("name") or ""
        desc = entry.get("description") or ""
        steps = " → ".join(chain)
        lines.append(f"  - {name}（{entry.get('gene_chain')}）：{steps}"
                     + (f"  // {desc}" if desc else ""))
    return "\n".join(lines)


def _plan_workflow(req, library, key, base, model, forced=None):
    """让模型直接输出结构化步骤，由代码拼装表达式。

    forced 指定时只允许使用该技能，避免模型改选工具。
    返回 (表达式, 参数表)；模型无法给出步骤时返回 None。
    """
    import skill_exec
    from runtime.core.prompt import build_system_prompt

    # 先粗筛候选，避免几十个技能的完整用法把提示淹没（8000 字符量级会让模型忽略目标技能）
    candidates = [forced] if forced else _shortlist_skills(req, library, key, base, model)
    skill_blocks = []
    for name in candidates:
        desc = library.describe(name).get("description") or ""
        hint = library.usage_hint(name)
        skill_blocks.append(f"  - {name}：{desc}" + (f"\n{hint}" if hint else ""))
    skill_lines = "\n".join(skill_blocks)
    tool_lines = "\n".join(f"  - {name}：{hint}" for name, hint in TOOL_HINTS.items())
    protein_lines = _protein_catalog_text()
    system = build_system_prompt(
        selected_tools=list(TOOL_HINTS),
        cwd=skill_exec.data_work_dir(_workspace_dirs()[0]),
        append_system_prompt=(
            "你要把用户需求拆成可执行步骤。只输出 JSON，不要输出其他文字。\n"
            "格式：{\"steps\": [{\"name\": \"技能或工具名\", \"args\": [\"参数\"]}], "
            "\"repeat\": 1}\n"
            "repeat 表示整组步骤重复次数。name 必须从下面清单中选择，逐字一致。\n"
            "args 必须严格符合该技能的用法，要给出可直接执行的具体参数：\n"
            "内容类参数（标题、正文、要点）由你根据需求自行拟写，不要照抄需求原句。\n"
            "用户要求复用上一次生成的提示词/描述（如「用上一次的提示词生图」）时，\n"
            "该参数写占位符 {last_prompt}，不要自己编造。\n"
            "注意：要执行某个技能时，直接把技能名作为 steps 的 name；\n"
            "不要把技能脚本包在 bash 里执行，bash 只用于普通 shell 命令。\n"
            "person-image-search 必须给出具体人名（如「赵露思」）；需求里只有\n"
            "「明星」「美女」「演员」这类泛指、没有具体名字时，改用 search 查\n"
            "榜单或资讯，不要凭空猜一个人名去下图。\n"
            "同一个步骤不要重复列多次：需要多次执行请用 repeat 字段。"
        ),
    )
    protein_block = (
        f"\n\n【预置流程】这些是现成的多步流程，能覆盖需求时优先直接用，\n"
        f"比你自己拼单步更完整（例如「新闻」类需求直接用「联网摘要链」，"
        f"它会自动搜索→抓正文→写摘要，而不是只返回一堆链接）：\n{protein_lines}"
        if protein_lines else ""
    )
    if forced:
        prompt = (
            f"完成这个需求必须使用技能 {forced}，不要使用其他技能或工具。\n\n"
            f"该技能用法：\n{skill_lines}\n\n"
            f"用户需求：{req}"
        )
    else:
        prompt = (
            f"可用技能：\n{skill_lines}\n\n"
            f"可用工具：\n{tool_lines}"
            f"{protein_block}\n\n"
            f"用户需求：{req}\n\n"
            f"请判断：需求属于某条预置流程时，按该流程的技能顺序输出 steps"
            f"（参数只给第一个需要的步骤）；否则自己组合。"
        )
    try:
        data = call_llm_json(
            [{"role": "system", "content": system},
             {"role": "user", "content": prompt}],
            key, base, model, attempts=2)
    except Exception as e:
        print(f"⚠️ 规划失败: {e}")
        return None

    steps = data.get("steps") if isinstance(data, dict) else None
    if not steps:
        return None

    known = set(library.skill_names()) | set(TOOL_HINTS)
    if forced:
        known = {forced}
        tool_lines = ""
    names, args = [], {}
    for step in steps:
        name = str(step.get("name", "")).strip()
        if name not in known:
            print(f"⚠️ 跳过未知步骤: {name}")
            continue
        argv = step.get("args") or []
        argv = [str(a) for a in argv] if isinstance(argv, list) else []
        # 模型有时把同一步骤重复列三次，表达出来的是一模一样的动作，合并掉
        if names and names[-1] == name and args.get(name, []) == argv:
            continue
        names.append(name)
        if argv:
            args[name] = argv

    if not names:
        return None

    repeat = data.get("repeat") or 1
    try:
        repeat = max(1, min(int(repeat), WORKFLOW_MAX_LOOP))
    except (TypeError, ValueError):
        repeat = 1

    body = "".join(names)
    expr = f"S{body}" if repeat == 1 else f"SO{repeat}{body}T"
    return expr, args


def _strip_request_words(req):
    """去掉「帮我」「查一下」「看看」等指令词，留下真正的查询词。"""
    q = req
    for w in ("帮我", "请", "给我", "帮我搜", "搜索一下", "搜一下", "查一下",
              "查询一下", "查询", "搜索", "最新的", "一下", "看看", "看下",
              "瞧瞧", "都有啥", "有什么",
              # 摘要类指令词：留在查询词里会让技能退化成普通搜索，
              # 丢掉「今日新闻→权威源直采」这类专用路径
              "总结一下", "总结", "摘要", "汇总一下", "汇总", "整理",
              "梳理", "简报"):
        q = q.replace(w, " ")
    return " ".join(q.split()) or req


# 需求里的尺寸说法由 data/runtime.json 的 size 段定义，
# 不在代码里维护词表。


def _argv_without_positionals(argv):
    """只保留选项及其取值，去掉自由文本/文件位置参数。

    fan-out 时每个项目要自己拼一个输入（文件或提示词），
    原 argv 里的第一个输入不能复用。
    """
    items = [str(a) for a in (argv or [])]
    out = []
    i = 0
    while i < len(items):
        a = items[i]
        if a.startswith("-"):
            out.append(a)
            if i + 1 < len(items) and not items[i + 1].startswith("-"):
                out.append(items[i + 1])
                i += 2
                continue
        i += 1
    return out


def _drop_flag(argv, flag):
    """去掉某个选项及其取值（如 fan-out 时不需要 --count）。"""
    items = [str(a) for a in (argv or [])]
    out = []
    i = 0
    while i < len(items):
        if items[i] == flag:
            i += 2 if (i + 1 < len(items) and not items[i + 1].startswith("-")) else 1
            continue
        out.append(items[i])
        i += 1
    return out


def _fanout_item_argv(base_argv, mode, item, prefix=""):
    """为 fan-out 的单个输入拼 argv：文件模式前置文件，文字模式前置提示词。"""
    opts = _argv_without_positionals(base_argv)
    if mode == "prev_files":
        return [item] + opts
    opts = _drop_flag(opts, "--count")
    return [f"{prefix}{item}"] + opts


def _argv_has_positional(argv):
    """判断 argv 里是否已有一个「位置参数」（如提示词）。

    需要区分「选项的取值」和「真正的位置参数」：
        ["--ratio", "9:16"]          -> 没有位置参数
        ["--ratio", "9:16", "一只猫"] -> 有位置参数
    做法：跳过所有以 - 开头的选项，以及紧随其后不属于选项的取值。
    """
    items = [str(a) for a in (argv or [])]
    i = 0
    while i < len(items):
        a = items[i]
        if a.startswith("-"):
            # 选项：若它不带 = 号且下一个不是选项，则下一个是它的取值，跳过
            if "=" not in a and i + 1 < len(items) and not items[i + 1].startswith("-"):
                i += 2
                continue
            i += 1
            continue
        return True
    return False


def _extract_size(req):
    """从需求里提取尺寸，返回 (ratio, size) 或 (None, None)。

    正则与词表来自 data/runtime.json 的 size 段：
      ratio_pattern  匹配「9:16」「16：9」
      words          匹配「竖图/横屏/正方形」等中文说法
    """
    s = req or ""
    cfg = _runtime().get("size") or {}
    pat = cfg.get("ratio_pattern") or r"(\d{1,2})\s*[:：]\s*(\d{1,2})"
    try:
        m = re.search(pat, s)
    except re.error:
        m = None
    if m and (m.lastindex or 0) >= 2:
        w, h = m.group(1), m.group(2)
        if w.isdigit() and h.isdigit() and int(w) > 0 and int(h) > 0:
            return f"{int(w)}:{int(h)}", None
    for word, ratio in (cfg.get("words") or {}).items():
        if word in s:
            return ratio, None
    return None, None


def _strip_size_words(req):
    """把尺寸说法从需求里去掉，避免混进搜索词。"""
    s = req or ""
    cfg = _runtime().get("size") or {}
    pat = cfg.get("ratio_pattern") or r"(\d{1,2})\s*[:：]\s*(\d{1,2})"
    try:
        s = re.sub(pat, " ", s)
    except re.error:
        pass
    for word in sorted((cfg.get("words") or {}), key=len, reverse=True):
        s = s.replace(word, " ")
    return s


_COUNT_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_count_words(text):
    """解析「2张 / 两张 / 十二幅」这类数量说法，返回 (数量, 去数量后的文本)。"""
    m = re.search(r"([0-9]+|[一二两三四五六七八九十]{1,3})\s*[张幅]", text or "")
    if not m:
        return 0, text or ""
    raw = m.group(1)
    if raw.isdigit():
        n = int(raw)
    elif "十" in raw:
        head, _, tail = raw.partition("十")
        n = (_COUNT_NUM.get(head, 1) if head else 1) * 10
        n += _COUNT_NUM.get(tail, 0) if tail else 0
    else:
        n = _COUNT_NUM.get(raw, 0)
    return n, (text[:m.start()] + " " + text[m.end():])




def _weather_city(req):
    """从「今日烟台天气」这类需求里取出城市名。"""
    s = req or ""
    for w in _wordlist("weather_noise"):
        s = s.replace(w, " ")
    return " ".join(s.split())




def _github_topic(req):
    """从「github关于AI的热榜」这类需求里取出主题词（AI），没有则返回空。"""
    s = (req or "").lower()
    for w in sorted(_wordlist("github_noise"), key=len, reverse=True):
        s = s.replace(w, " ")
    # 注意事项：AI 这类大写缩写去掉噪声后需要还原
    words = s.split()
    return " ".join(words).strip()


def _infer_tool_args(req, chain):
    """本地规划只给技能名/工具名，没有参数，这里从需求原文推导。

    早期只处理了 search / fetch 两个工具，漏了 web-summarizer 这类
    需要参数的**技能**，导致「今日新闻」命中蛋白链后空参数执行，
    直接报「需要提供关键词或 URL」。
    """
    args = {}
    req = (req or "").strip()
    if not req:
        return args

    for name in chain:
        if name == "search":
            args["search"] = [_strip_request_words(req)]

        elif name == "fetch":
            m = re.search(r"https?://[^\s，。；]+", req)
            if m:
                args["fetch"] = [m.group(0)]

        elif name == "web-summarizer":
            # 有 URL 就按 URL 摘要，否则把需求当关键词去搜索
            urls = re.findall(r"https?://[^\s，。；]+", req)
            if urls:
                args["web-summarizer"] = list(urls)
            else:
                args["web-summarizer"] = ["-k", _strip_request_words(req)]

        elif name == "person-image-search":
            # 从需求里提取人名。要剥离哪些操作词由原子配置声明
            # （atoms/index.json 的 strip_words），内核不维护词表。
            # 尺寸说法（9:16、竖图）不是人名，先剔掉
            s = _strip_size_words(_strip_request_words(req))
            count, s = _parse_count_words(s)
            words = _atom_field("person-image-search", "strip_words", []) or []
            # 长词先剥，避免「找图加字」被「找图」拆坏
            for w in sorted(words, key=len, reverse=True):
                if w:
                    s = s.replace(w, " ")
            # 「宁蓉蓉的图片」剩下的人名后面会拖一个「的」，残余数字同理
            s = re.sub(r"[0-9]+", " ", s.replace("的", " "))
            s = " ".join(s.split()).strip() or req.strip()
            args["person-image-search"] = [s, "-n", str(count or 2)]

        elif name == "agnes-image":
            # 「复刻2张……」的张数落在最终生图数量上
            count, _ = _parse_count_words(req)
            if count:
                args["agnes-image"] = ["--count", str(count)]

        elif name == "agnes-image":
            # 生图尺寸：需求里写了就照办，否则用技能默认
            ratio, size = _extract_size(req)
            if ratio:
                args["agnes-image"] = ["--ratio", ratio]
            if size:
                args.setdefault("agnes-image", [])
                args["agnes-image"] += ["--size", size]

        elif name == "github-trending":
            topic = _github_topic(req)
            if topic:
                args["github-trending"] = ["--topic", topic]

        elif name == "weather-query":
            city = _weather_city(req)
            if city:
                args["weather-query"] = [city]

        elif name == "web-fetch":
            m = re.search(r"https?://[^\s，。；]+", req)
            if m:
                args["web-fetch"] = [m.group(0)]

    return args


# ==================== 超蛋白：同构/异构、串行/并行 ====================
def _extract_person_names(req):
    """从「复刻A和B的图」里拆出多个人名（同构并行的输入）。

    只负责拆词与校验；“哪些词属于操作词”仍然来自原子配置 strip_words。
    """
    s = _strip_size_words(_strip_request_words(req))
    _, s = _parse_count_words(s)
    words = _atom_field("person-image-search", "strip_words", []) or []
    for w in sorted(words, key=len, reverse=True):
        if w:
            s = s.replace(w, " ")
    s = re.sub(r"[0-9]+", " ", s.replace("的", " "))
    names = []
    for part in re.split(r"[和与、,，跟]", s):
        name = " ".join(part.split()).strip()
        if name and _looks_like_person_name(name) and name not in names:
            names.append(name)
    return names


def plan_superprotein(req):
    """超蛋白规划：显式组合优先，其次多人同构并行。

    · 显式：superproteins/composites.json 里的 keywords（正则）命中，
      按声明的 mode/proteins 展开。
    · 动态：≥2 个人名 + 命中含 person-image-search 的蛋白 →
      同一蛋白链并行 N 份（同构），每人一个分支、各自独立参数。
    """
    from runtime.superprotein import SuperproteinPlan, match_composite

    data_dir, _ = _workspace_dirs()
    hit = match_composite(req, data_dir)
    if hit:
        return hit

    names = _extract_person_names(req)
    if len(names) < 2:
        return None
    protein = plan_from_protein(req)
    if not protein or "person-image-search" not in protein["skill_chain"]:
        return None
    count, _ = _parse_count_words(req)
    branches = []
    for name in names:
        req_i = f"复刻{name}的图片"
        if count:
            req_i += f"，{count}张"
        branches.append({"chain": protein["skill_chain"], "requirement": req_i})
    return SuperproteinPlan(
        name="多人复刻", mode="PARALLEL", homogeneous=True, branches=branches,
        match=f"超蛋白:同构并行（{len(names)}人）",
    )


# 需要「上一步产出文件」作为输入的技能：
#   image-text-overlay 要一张图加字
# ==================== 链式数据流（全部由配置驱动）====================
# 内核不认识具体技能。每一步「吃上一步的文件还是文字」「缺参数拿什么补」
# 都由蛋白库 polypeptide_tree.nodes[].consumes / defaults 声明。
# 新增一条链 = 往 proteins/index.json 写一条记录，不改这里。

# 上一步的文字结果（供 consumes=["prev_text"] 的步骤取用）。
# 串行时等价于全局变量；并行分支各自处在线程里，用线程局部隔离，
# 否则两条并行链会互相覆盖描述词。
_LAST_TEXT_RESULT = {"text": ""}
_BRANCH_TEXT = threading.local()
_BRANCH_KEY = threading.local()  # 分支沙箱 key（对应 work-<key> / skills-<key>）
_BRANCH_ARGS = threading.local()  # 分支自己的技能参数表
_BRANCH_REQ = threading.local()   # 分支自己的需求原文（参数推导用）
_BRANCH_FILES = threading.local()  # 本分支上一步产出的文件（宿主绝对路径）
_BRANCH_TEXTS = threading.local()  # 本分支上一步产出的文字列表（fan-out 用）
_BRANCH_COUNT = threading.local()  # 需求里的张数（fan-out 上限）


def _branch_files():
    return list(getattr(_BRANCH_FILES, "files", []) or [])


def _set_branch_files(files):
    _BRANCH_FILES.files = [str(f) for f in (files or [])]


def _branch_texts():
    return list(getattr(_BRANCH_TEXTS, "texts", []) or [])


def _branch_text():
    if getattr(_BRANCH_TEXT, "isolated", False):
        return getattr(_BRANCH_TEXT, "text", "")
    return _LAST_TEXT_RESULT["text"]


def _set_branch_text(text):
    text = str(text or "")
    if getattr(_BRANCH_TEXT, "isolated", False):
        _BRANCH_TEXT.text = text
    else:
        _LAST_TEXT_RESULT["text"] = text


def _isolate_branch():
    """在工作线程里声明独立上下文，不读写全局状态。"""
    _BRANCH_TEXT.isolated = True
    _BRANCH_TEXT.text = ""
# 再上一轮（即上一次工作流）的文字结果。仅当需求明确要求复用
# （“用上一次的提示词”之类）时才取用，避免失败步骤误用陈旧描述词。
_PREV_ROUND_TEXT = {"text": ""}

# 图片文件后缀，用于从产出里挑输入文件
# 图片后缀见 data/runtime.json 的 output.image_suffixes

# ---- 链路声明缓存：一次性从蛋白库读出「技能 -> 数据流约定」----
_CHAIN_SPEC_CACHE = {"key": None, "file": {}, "text": {}, "producer": set(),
                     "defaults": {}, "prefix": {}, "foreach": {}}


def _chain_specs():
    """从蛋白库收集链式数据流约定。

    返回 {
      file:     {skill: True}     // 需要上一步的文件
      text:     {skill: True}     // 需要上一步的文字
      producer: {skill, ...}      // 输出可作为后续提示词的技能
      defaults: {skill: [args]}   // 必填参数缺省值
      prefix:   {skill: str}      // 用 prev_text 时的前缀
    }
    结果按蛋白库修改时间缓存，库文件变了自动重读。
    """
    data_dir, _ = _workspace_dirs()
    idx_path = os.path.join(data_dir, "proteins", "index.json")
    try:
        mtime = os.path.getmtime(idx_path)
    except OSError:
        mtime = None
    if _CHAIN_SPEC_CACHE["key"] == mtime:
        return _CHAIN_SPEC_CACHE

    file_map, text_map, producer = {}, {}, set()
    defaults, prefix, foreach_map = {}, {}, {}

    try:
        with open(idx_path, encoding="utf-8") as f:
            proteins = json.load(f).get("proteins") or []
    except (OSError, ValueError):
        proteins = []

    for p in proteins:
        nodes = (p.get("polypeptide_tree") or {}).get("nodes") or []
        for node in nodes:
            skill = node.get("skill")
            if not skill:
                continue
            consumes = node.get("consumes") or []
            if "prev_file" in consumes:
                file_map[skill] = True
            if "prev_text" in consumes:
                text_map[skill] = True
            d = node.get("defaults")
            if isinstance(d, dict):
                if d.get("prefix"):
                    prefix[skill] = d["prefix"]
                args = []
                for k, v in d.items():
                    if k == "prefix":
                        continue
                    args += [k, str(v)]
                if args:
                    defaults[skill] = args
            # 只有显式声明 produces_text 的技能，其输出才能当后续提示词。
            # 消费 prev_text 不等于产出文字（如 agnes-image 的输出是图片，
            # 当成提示词会把描述覆盖成「尺寸/文件名」这类噪声）。
            if node.get("produces_text"):
                producer.add(skill)
            # fan-out 声明：prev_files = 按上一步每个文件跑一次
            #              prev_texts = 按上一步每条文字跑一次
            if node.get("foreach"):
                foreach_map[skill] = str(node["foreach"])

    # 产出文字的技能还包括：显式声明 produces_text 的原子
    try:
        with open(os.path.join(data_dir, "atoms", "index.json"), encoding="utf-8") as f:
            for a in (json.load(f).get("atoms") or []):
                if a.get("produces_text") and a.get("skill"):
                    producer.add(a["skill"])
    except (OSError, ValueError):
        pass

    _CHAIN_SPEC_CACHE.update({"key": mtime, "file": file_map, "text": text_map,
                              "producer": producer, "defaults": defaults,
                              "prefix": prefix, "foreach": foreach_map})
    return _CHAIN_SPEC_CACHE


def _first_output_path(prev_text):
    """从上一步的技能输出里取出第一个可用文件的绝对路径。

    技能输出的形态不统一（有的给相对路径、有的给 work/xxx），
    这里把路径都解析成绝对路径再判断存在性，优先取图片。
    """
    if not prev_text:
        return ""
    text = str(prev_text)

    # 1) JSON 形态：{"files": [...]} / {"outputs": [...]}
    m = re.search(r'"(?:files|outputs)"\s*:\s*\[(.*?)\]', text, re.S)
    if m:
        for item in re.findall(r'"([^"]+)"', m.group(1)):
            p = _absolutize(item)
            if p and os.path.isfile(p):
                return p

    # 2) 逐行找路径（含盘符或 /）
    candidates = []
    for line in text.splitlines():
        for token in re.findall(r"[A-Za-z]:\\[^\s，。；、\"']+|/[^\s，。；、\"']+", line):
            candidates.append(token)
    # 图片优先，其次任意存在的文件
    img_suffixes = tuple((_runtime().get("output") or {}).get("image_suffixes") or ())
    for suffix in (img_suffixes, ("",)):
        for c in candidates:
            p = _absolutize(c)
            if p and os.path.isfile(p) and (not suffix or p.lower().endswith(suffix)):
                return p
    return ""


def _absolutize(path):
    """把可能的相对路径还原成**宿主上可用**的绝对路径。

    技能在沙箱里执行，stdout 打印的是沙箱内的路径
    （<temp>/dbagent-sandbox/work/...）。该文件同时被同步回宿主 work/，
    但沙箱目录随后会被清理，所以沙箱路径绝不能直接返回——
    必须先映射到宿主 work/ 再判断存在。
    """
    if not path:
        return ""
    p = path.strip().strip('"\'')
    lowered = p.replace("/", "\\").lower()
    is_sandbox = "dbagent-sandbox" in lowered

    # 沙箱路径优先映射，哪怕它当前还存在
    if is_sandbox:
        try:
            data_dir, _ = _workspace_dirs()
            workdir = os.path.join(data_dir, "work")
        except Exception:
            workdir = os.getcwd()
        tail = p.split("dbagent-sandbox", 1)[1].lstrip("\\/")
        if tail.lower().startswith("work\\"):
            tail = tail[5:]
        cand = os.path.join(workdir, tail)
        if os.path.isfile(cand):
            return os.path.abspath(cand)

    if os.path.isabs(p) and os.path.isfile(p) and not is_sandbox:
        return p

    try:
        data_dir, _ = _workspace_dirs()
        workdir = os.path.join(data_dir, "work")
    except Exception:
        workdir = os.getcwd()

    for base in (workdir, os.getcwd()):
        cand = os.path.join(base, p.lstrip("\\/"))
        if os.path.isfile(cand):
            return os.path.abspath(cand)

    # 兜底：按文件名在 work/ 里递归找
    name = os.path.basename(p.replace("\\", "/"))
    if name:
        for dirpath, _dirs, files in os.walk(workdir):
            if name in files:
                return os.path.abspath(os.path.join(dirpath, name))
    return ""


def _normalize_skill_args(name, argv, req):
    """修正模型给技能的参数，避免因格式不对直接失败。

    web-summarizer 需要 -k <关键词> 或 URL；模型常直接给位置参数
    ["近日新闻"]，会被脚本当成"既不是 URL 也不是关键词"而报错。
    这里按技能约定重排一遍。
    """
    argv = [str(a) for a in (argv or [])]
    # SKILL.md 里的快捷命令（如 /WF fetch <url>）会被模型照抄，
    # 剥掉 /WF 前缀和紧随的子命令，只留脚本真正接受的参数
    if argv and re.match(r"^/[A-Za-z]{1,6}$", argv[0].strip()):
        argv = argv[1:]
    # 模型偶尔把技能名/工具名本身也当第一个参数（web-fetch → "fetch <url>"），
    # argparse 会把 "fetch" 当成位置参数 URL，报「多了参数」
    if argv:
        head = argv[0].strip().lower()
        aliases = {name.lower(), name.replace("-", "_").lower()}
        if "-" in name:
            aliases.add(name.rsplit("-", 1)[-1].lower())
        if head in aliases:
            argv = argv[1:]

    if name != "web-summarizer":
        return argv
    if any(a in ("-k", "--keyword") for a in argv):
        return argv
    urls = [a for a in argv if re.match(r"^https?://", a)]
    if urls:
        return urls
    # 位置参数里非 URL 的部分就是关键词
    words = [a for a in argv if not a.startswith("-")]
    kw = " ".join(words).strip() or _strip_request_words(req)
    return ["-k", kw]


def _relay_skill_phases(stderr_text):
    """把技能 stderr 里的阶段进度行转发到主输出。

    约定：技能用 [GA]/[GC]/[GG]/[GT] 这类方括号标签标记阶段，
    stderr 里可能带 \\u2713 这类转义，统一还原后再打印。
    """
    if not stderr_text:
        return
    for line in stderr_text.splitlines():
        _relay_phase_line(line)


def _relay_phase_line(line):
    """单行版转发，供技能执行期间的实时回调复用。

    转发三类行：
      · 阶段标记 [GA]/[GC]/[GG] 等
      · 缩进的子行：逐源进度 (1/14)、逐条要点 · xxx
      · 其他缩进的内容行

    否则用户只能看到阶段标题，看不到中间过程。
    """
    raw = line.rstrip("\n")
    s = raw.strip()
    if not s:
        return
    is_phase = bool(re.match(r"^\[[A-Z]{2,3}\]", s))
    is_sub = raw.startswith(" ") and (
        s.startswith("·") or s.startswith("(") or s.startswith("✓")
        or s.startswith("✗") or s.startswith("-")
    )
    if not (is_phase or is_sub):
        return
    if is_phase:
        try:
            s = s.encode("latin-1", "backslashreplace").decode("unicode_escape")
        except Exception:
            pass
        print(f"       {s}", flush=True)
    else:
        # 子行保留原缩进层级，只补上左缩进对齐
        print(f"   {raw}", flush=True)


def _strip_sandbox_paths(text):
    """把技能输出里的沙箱临时路径换成中性说明。

    技能在 <temp>/dbagent-sandbox/work/ 下执行，日志里全是那条路径。
    对用户没有意义（产物已同步回宿主 work），还会把真正的宿主路径挤掉。
    """
    if not text:
        return ""
    # 沙箱根目录形如 C:\Users\xxx\AppData\Local\Temp\dsh-xxxx\dbagent-sandbox
    text = re.sub(r"[A-Za-z]:[\\/][^\s，。；、\"']*?dbagent-sandbox[\\/]work[\\/]?",
                  "", text)
    # 剩下的零散沙箱绝对路径
    text = re.sub(r"[A-Za-z]:[\\/][^\s，。；、\"']*?dbagent-sandbox[^\s，。；、\"']*",
                  "（沙箱临时路径）", text)
    # 清理替换后留下的空行/悬空路径行
    lines = []
    for ln in text.splitlines():
        s = ln.rstrip()
        if re.fullmatch(r"\s*[A-Za-z]:[\\/]?\s*", s):
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def _extract_descriptive_text(detail):
    """从技能输出里抽出一段「可作为提示词的描述文本」。

    visioncustom 的输出形如：
        **发型**：深褐色头发盘起…
        **服装**：粉白相间的华丽礼服…
        📊 使用量: 700 tokens (输入) | 748 tokens (输出)
    这里去掉用量统计、路径行、进度日志等噪声，留下真正的描述。
    """
    if not detail:
        return ""
    # 技能自身的日志前缀，绝不能进提示词（否则会层层嵌套）。
    # 前缀表来自 data/runtime.json 的 text_noise_prefix。
    NOISE_PREFIX = tuple(_runtime().get("text_noise_prefix") or ())
    lines = []
    for ln in str(detail).splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith(NOISE_PREFIX):
            continue
        if ("tokens" in s.lower() or "提示词:" in s or "完成:" in s
                or re.match(r"^[A-Za-z]:[\\/]", s)):
            continue
        lines.append(s)
    text = " ".join(lines).strip()
    # 去掉重复出现的前缀，防止历史嵌套
    for _ in range(3):
        if text.startswith("根据以下外貌描述绘制"):
            idx = text.find("：")
            if idx > 0:
                text = text[idx + 1:].strip()
    return text[:600]


def _save_last_prompt(workdir, skill, text):
    """把文字产出者的结果落盘，供 context 工具跨轮复用。

    只存在内存里的话，进程重启或工作流之间就丢了；
    落盘后「用上一次的提示词」不再依赖同一进程的全局变量。
    """
    if not text:
        return
    path = os.path.join(workdir, "上下文", "last_prompt.json")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"text": text, "skill": skill,
                       "time": time.strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _collect_output_files(workdir, outputs, since=None, max_files=50):
    """列出本次执行真正产出的文件。

    outputs 是技能产出目录（如 ['图片']）。但「图片」是公共目录，
    历次运行的东西都堆在里面，直接递归会把历史文件全列出来。
    所以用 since（本次开始执行的时间戳）过滤，只保留新文件。
    """
    import glob as _glob

    files = []
    seen = set()
    for out in (outputs or []):
        base = os.path.join(workdir, str(out))
        if os.path.isfile(base):
            candidates = [base]
        elif os.path.isdir(base):
            candidates = sorted(_glob.glob(os.path.join(base, "**", "*"),
                                          recursive=True))
        else:
            candidates = []
        for p in candidates:
            if not os.path.isfile(p):
                continue
            if since is not None:
                try:
                    if os.path.getmtime(p) < since:
                        continue
                except OSError:
                    continue
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.add(ap)
                files.append(ap)
    # 最新的在前
    try:
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    except OSError:
        pass
    return files[:max_files]


def _format_tool_result(name, detail):
    """把工具返回的 JSON 摘要排版成用户可读文本。

    search 返回 {results:[{title,url,snippet}]}，直接 dump JSON 很难读；
    fetch 返回 {content:...}，应该原样显示正文。
    """
    if not isinstance(detail, dict):
        return str(detail)[:2000]

    if name == "search":
        items = detail.get("results") or []
        if not items:
            return f"未搜到结果（{detail.get('error') or '无匹配'}）"
        lines = [f"来源 {detail.get('source', '?')}，共 {len(items)} 条："]
        for i, it in enumerate(items, 1):
            title = (it.get("title") or "").strip()
            url = (it.get("url") or "").strip()
            sn = (it.get("snippet") or "").strip()
            lines.append(f"\n{i}. {title}")
            if url:
                lines.append(f"   {url}")
            if sn:
                lines.append(f"   {sn[:120]}")
        return "\n".join(lines)

    if name == "fetch":
        content = detail.get("content") or detail.get("text") or ""
        title = detail.get("title") or ""
        head = f"标题：{title}\n\n" if title else ""
        return head + str(content)[:4000]

    # 其它工具：优先显示正文/标准输出，否则 dump
    for key in ("content", "stdout", "output"):
        if detail.get(key):
            return str(detail[key])[:4000]
    err = detail.get("error")
    if err:
        return f"失败：{err}"
    return json.dumps(detail, ensure_ascii=False)[:2000]


def _run_superprotein(plan, runner, loop_hook, library, requirement=""):
    """按超蛋白计划跑分支：并行分支各自线程 + 独立沙箱 + 独立文字状态。

    返回是否全部成功。分支内的技能调度沿用同一个 runner，
    它通过线程局部变量拿到本分支的沙箱 key 与 prev_text。
    """
    import skill_exec
    from runtime.superprotein import SuperproteinRuntime

    def _branch_runner(branch, index, prev):
        key_name = f"b{index}"
        saved_key = getattr(_BRANCH_KEY, "key", None)
        saved_iso = getattr(_BRANCH_TEXT, "isolated", False)
        saved_txt = getattr(_BRANCH_TEXT, "text", "")
        _BRANCH_KEY.key = key_name
        _BRANCH_TEXT.isolated = True
        _BRANCH_TEXT.text = prev or ""
        req_i = branch.get("requirement") or requirement
        _BRANCH_ARGS.args = _infer_tool_args(req_i, branch["chain"])
        _BRANCH_REQ.req = req_i
        _BRANCH_COUNT.count = _parse_count_words(req_i)[0]
        try:
            args_i = _BRANCH_ARGS.args
            expr_i = "S" + "".join(branch["chain"])
            res = skill_exec.execute_stopj(
                expr_i, library,
                max_steps=WORKFLOW_MAX_STEPS,
                max_loop_iterations=WORKFLOW_MAX_LOOP,
                skill_callback=runner,
                extra_names=list(TOOL_HINTS),
                loop_hook=loop_hook,
            )
            good = bool(res) and sum(1 for r in res if "成功" in r[:20]) == len(res)
            return good, "\n".join(res), _branch_text()
        finally:
            _BRANCH_KEY.key = saved_key
            for attr in ("args",):
                if hasattr(_BRANCH_ARGS, attr):
                    delattr(_BRANCH_ARGS, attr)
            if hasattr(_BRANCH_REQ, "req"):
                delattr(_BRANCH_REQ, "req")
            if saved_iso:
                _BRANCH_TEXT.isolated = True
                _BRANCH_TEXT.text = saved_txt
            else:
                for attr in ("isolated", "text"):
                    if hasattr(_BRANCH_TEXT, attr):
                        delattr(_BRANCH_TEXT, attr)

    ok, _merged = SuperproteinRuntime(_branch_runner).run(plan)
    return ok


def _acquire_api_key(timeout=300):
    """申请一个空闲 Key（每 key 并发=1，忙则等）。用完必须释放。"""
    try:
        import keypool
        return keypool.get_pool().acquire(timeout=timeout)
    except Exception:
        return ""


def _release_api_key(key, failed=False, reason=""):
    """释放 Key；失败的 Key 先标记冷却再放回。"""
    if not key:
        return
    try:
        import keypool
        pool = keypool.get_pool()
        if failed:
            low = str(reason or "").lower()
            invalid = any(h in low for h in ("401", "403", "unauthorized",
                                             "invalid api key", "无效"))
            pool.report_failure(key, keypool.INVALID_KEY_COOLDOWN if invalid
                                else keypool.DEFAULT_COOLDOWN)
        pool.release(key)
    except Exception:
        pass


def _truncate_detail(text, limit=2400):
    """结果正文超长时保留头尾，中间标省略。

    只取尾部会把摘要开头的分类节（国内/国际等）整段切掉，
    用户看到的是从半句开始的残文。切点尽量落在换行处，避免半句。
    """
    s = text or ""
    if len(s) <= limit:
        return s
    head = int(limit * 0.7)
    tail = limit - head
    cut_h = s.rfind("\n", 0, head)
    if cut_h > head // 2:
        head = cut_h
    cut_t = s.find("\n", len(s) - tail)
    if cut_t != -1 and len(s) - cut_t < tail * 2:
        tail = len(s) - cut_t
    omitted = len(s) - head - tail
    return f"{s[:head]}\n\n…（中间省略 {omitted} 字符）…\n\n{s[-tail:]}"


def _run_planned(expr, planned_args, library, data_dir, requirement="", source="llm",
                 show_output=True, key=None, base=None, model=None, superplan=None):
    """按表达式执行：技能走脚本，工具走 toolset。

    执行前后由技能运行时接管三个阶段：
        规划注入 before_run()  ->  执行中 record_step()  ->  收尾 finish()

    返回 (是否成功, 各步结果文本列表)。show_output=True 时把结果打印给用户
    —— 早期版本只返回 True，工具真实输出（搜索结果正文、网页内容）
    被静默丢弃，用户看到「✓ search 成功」却不知道查到了什么。
    """
    import skill_exec
    from runtime.skillexe_runtime import RuntimeContext, SkillRuntime

    workdir = skill_exec.data_work_dir(data_dir)
    skill_args = {k: v for k, v in planned_args.items() if k not in TOOL_HINTS}
    tool_args = {k: v for k, v in planned_args.items() if k in TOOL_HINTS}
    # 每轮工作流独立：不清空的话，上一步失败时后续步骤会沿用上一轮
    # 遗留的描述词，生成出完全无关的图。旧值先存档，供「用上一次
    # 的提示词」这类显式复用。
    _PREV_ROUND_TEXT["text"] = _branch_text().strip()
    _set_branch_text("")
    reuse_prev = bool(re.search(r"上一次|上次|刚才|上一个", requirement or ""))
    _BRANCH_COUNT.count = _parse_count_words(requirement or "")[0]

    # ---- 阶段一：规划注入 ----
    rt = SkillRuntime()
    names = list(library.skill_names()) + list(TOOL_HINTS)
    parsed, _ = skill_exec.stopj_parse(expr, names)
    chain = [s["skill"] for s in parsed if s["type"] == "SKILL"]
    ctx = RuntimeContext(
        requirement=requirement,
        skill_chain=chain,
        args=dict(planned_args or {}),
        budget=-1,
        source=source,
        expression=expr,
    )
    rt.before_run(ctx)
    if chain:
        print(f"🧬 规划注入：{ctx.describe()}")

    # 收集每一步的可读结果，执行完统一展示
    step_details = []

    # ---- 流水线：相邻的「多文件→逐条文字」两步按条目串起来并发 ----
    # A(foreach prev_files) → B(foreach prev_texts) 时，每个条目自己跑 A→B，
    # 阶段间不用互等；条目耗时不均时收益明显。
    spec_for_pipe = _chain_specs()
    chain_list = [s["skill"] for s in parsed if s["type"] == "SKILL"]
    pipe_next = {}
    for i in range(len(chain_list) - 1):
        if (spec_for_pipe["foreach"].get(chain_list[i]) == "prev_files"
                and spec_for_pipe["foreach"].get(chain_list[i + 1]) == "prev_texts"):
            pipe_next[chain_list[i]] = chain_list[i + 1]
    pipelined = {}

    # 安全策略类失败：原提示词基本重试多少次都一样，需要规划改一轮再试
    _POLICY_HINTS = ("安全", "policy", "moderation", "审核", "敏感", "违规",
                     "blocked", "content_filter", "不安全")

    def _revise_prompt(argv, reason):
        """针对触发安全策略的失败，让模型改写提示词；拿不到新词返回 None。"""
        if not (key and base and model):
            return None
        low = str(reason).lower()
        if not any(h in low for h in _POLICY_HINTS):
            return None
        idx = next((i for i, a in enumerate(argv)
                    if not str(a).startswith("-") and len(str(a)) > 12), None)
        if idx is None:
            return None
        try:
            revised = call_llm([
                {"role": "system",
                 "content": "你是提示词改写器。把用户提示词改成内容合规的版本："
                            "保留主体外貌、服饰与画面风格，去掉可能触发内容审核的表述。"
                            "只输出改写后的提示词，不要解释。"},
                {"role": "user",
                 "content": f"失败原因：{str(reason)[:200]}\n原提示词：{argv[idx]}"},
            ], key, base, model)
        except Exception:
            return None
        revised = (revised or "").strip()
        if not revised or revised == str(argv[idx]).strip():
            return None
        out = list(argv)
        out[idx] = revised
        _set_branch_text(revised)
        return out

    def _loop_hook(info):
        """O 环节：迭代边界可注入/编辑上下文（当前策略：提示词改写已在重试前完成）。"""
        return None

    def runner(name, context):
        step_start = time.time()
        if name in TOOL_HINTS:
            argv = tool_args.get(name) or []
            print(f"  ↳ 工具 {name} {' '.join(argv)}".rstrip())
            result = skill_exec.execute_tool(name, argv, workdir)
            elapsed = round(time.time() - step_start, 2)
            if result.get("ok"):
                rt.record_step(name, True, elapsed, "工具执行成功")
                detail = {k: v for k, v in result.items()
                          if k not in ("ok", "tool", "args", "elapsed")}
                # context 工具的作用就是注入上下文：读到的文本立即成为
                # 后续步骤的 prev_text 来源，不需要再走文件/参数传递。
                if name == "context":
                    _set_branch_text(detail.get("text") or "")
                text = json.dumps(detail, ensure_ascii=False)
                step_details.append((name, True, _format_tool_result(name, detail)))
                return f"工具执行成功（{result['elapsed']}s）\n{_truncate_detail(text)}"
            err = str(result.get("error") or result.get("stderr") or "")
            rt.record_step(name, False, elapsed, err[:120])
            step_details.append((name, False, f"执行失败：{err[:300]}"))
            return f"工具执行失败: {result.get('error') or result.get('stderr')}"

        if name in pipelined:
            pre = pipelined.pop(name)
            rt.record_step(name, pre["ok"], pre["elapsed"], "流水线执行")
            step_details.append((name, pre["ok"], pre["body"]))
            return pre["summary"]

        info = library.describe(name) or {}
        req_now = getattr(_BRANCH_REQ, "req", requirement)
        own_args = getattr(_BRANCH_ARGS, "args", None)
        arg_source = own_args if own_args is not None else skill_args
        argv = _normalize_skill_args(name, arg_source.get(name), req_now) or []
        # 数据流全由蛋白库声明（consumes / defaults），内核不认识具体技能
        spec = _chain_specs()
        prev = (context or {}).get("prev") or ""
        last_text = _branch_text().strip()
        recent_text = last_text or _PREV_ROUND_TEXT["text"]

        if any("{last_prompt}" in str(a) for a in argv):
            argv = [str(a).replace("{last_prompt}", recent_text) for a in argv]
        elif any("{prev_text}" in str(a) for a in argv):
            argv = [str(a).replace("{prev_text}", last_text) for a in argv]
        elif any("{prev}" in str(a) for a in argv):
            argv = [str(a).replace("{prev}", _first_output_path(prev)) for a in argv]
        elif name in spec["file"]:
            # 需要文件输入的技能：不管 argv 里有没有别的参数（如 -p），
            # 只要还没给文件路径，就把上一步的产出补进去。
            # 之前用 `not argv` 判断，导致只要 config 补了 -p 就永远拿不到图。
            has_path = any(os.path.isfile(str(a)) for a in argv)
            if not has_path:
                got = _first_output_path(prev)
                if got:
                    argv = [got] + list(argv)
                    print(f"       （使用上一步产出：{got}）")
        elif name in spec["text"] and (last_text or (reuse_prev and _PREV_ROUND_TEXT["text"])):
            # 需要文字输入的技能：argv 里已有 --ratio 9:16 这类选项时，
            # 提示词仍要作为位置参数补上。
            # 判定「是否已有提示词」不能只看首字符：9:16 也是以 - 开头之外的值，
            # 它只是 --ratio 的参数，不是提示词。所以跳过所有选项及其取值。
            # 用户明确说「用上一次的提示词」且本轮尚未产出文字时，取上一轮存档。
            text_in = last_text or _PREV_ROUND_TEXT["text"]
            pre = spec["prefix"].get(name, "")
            has_prompt = _argv_has_positional(argv)
            if not has_prompt:
                argv = [f"{pre}{text_in}"] + list(argv)
                print("       （使用上一步的文字结果作为输入）")

        # 必填参数缺省值，同样来自配置
        defaults = spec["defaults"].get(name)
        if defaults:
            flags = {str(a) for a in argv if str(a).startswith("-")}
            for i in range(0, len(defaults) - 1, 2):
                if defaults[i] not in flags:
                    argv += [defaults[i], defaults[i + 1]]
        print(f"  ↳ 技能 {name}（{info.get('name', '')}）{' '.join(argv)}".rstrip())

        def _on_skill_line(stream, line):
            # 阶段行（stderr 的 [GC]/[GG] 等）实时转发；stdout 是最终结果文本，
            # 结束后还要展示，不重复打印，只把进度感留给 stderr。
            if stream == "err":
                _relay_phase_line(line)

        # ---- fan-out：上一步产出多个文件/多条文字时，逐个执行 ----
        foreach = (spec.get("foreach") or {}).get(name)
        items = []
        if foreach == "prev_files":
            files = _branch_files()
            limit = getattr(_BRANCH_COUNT, "count", 0) or 1
            items = files[:limit]
        elif foreach == "prev_texts":
            items = [t for t in _branch_texts() if t]

        fanout = bool(items)
        fanout_texts = []
        elapsed_override = None
        if fanout:
            from concurrent.futures import ThreadPoolExecutor
            parent_key = getattr(_BRANCH_KEY, "key", None)
            prefix = spec["prefix"].get(name, "")
            next_name = pipe_next.get(name)
            next_base = list(arg_source.get(next_name) or []) if next_name else []
            next_prefix = spec["prefix"].get(next_name, "") if next_name else ""
            next_defaults = (spec["defaults"].get(next_name) or []) if next_name else []

            def _one(pair):
                idx, item = pair
                key_i = parent_key if len(items) == 1 else \
                    f"{parent_key or 'fo'}-{idx}"
                a = _fanout_item_argv(argv, foreach, item, prefix)
                k1 = _acquire_api_key()
                r1 = skill_exec.execute_skill(library, name, args=a,
                                              on_line=_on_skill_line,
                                              sandbox_key=key_i,
                                              api_key=k1)
                _release_api_key(k1, failed=not r1.get("ok"),
                                 reason=str(r1.get("error") or r1.get("stderr") or ""))
                if not next_name or not r1.get("ok"):
                    return r1, "", None
                txt = _extract_descriptive_text(r1.get("stdout") or "")
                if not txt:
                    return r1, "", None
                b_args = list(next_base)
                flags = {str(x) for x in b_args if str(x).startswith("-")}
                for j in range(0, len(next_defaults) - 1, 2):
                    if next_defaults[j] not in flags:
                        b_args += [next_defaults[j], next_defaults[j + 1]]
                a2 = _fanout_item_argv(b_args, "prev_texts", txt, next_prefix)
                k2 = _acquire_api_key()
                r2 = skill_exec.execute_skill(library, next_name, args=a2,
                                              on_line=_on_skill_line,
                                              sandbox_key=f"{key_i}b",
                                              api_key=k2)
                _release_api_key(k2, failed=not r2.get("ok"),
                                 reason=str(r2.get("error") or r2.get("stderr") or ""))
                return r1, txt, r2

            tag = f"（流水线：{len(items)} 个输入并联 {name}→{next_name}）" if next_name \
                else f"（fan-out：{len(items)} 个输入逐个处理）"
            print(f"       {tag}", flush=True)
            with ThreadPoolExecutor(max_workers=min(4, len(items))) as pool:
                parts = list(pool.map(_one, enumerate(items)))

            if next_name:
                r1s = [p[0] for p in parts]
                r2s = [p[2] for p in parts if p[2]]
                ok_all = all(p[0].get("ok") for p in parts)
                texts = [p[1] for p in parts if p[1]]
                _BRANCH_TEXTS.texts = texts
                _set_branch_text("\n".join(texts))
                _set_branch_files([os.path.join(workdir, f)
                                   for p in r1s for f in (p.get("files") or [])])
                ok_b = bool(r2s) and all(r.get("ok") for r in r2s)
                # 流水线里 B 在同一线程紧跟 A 执行，阶段墙钟包含 B 的耗时；
                # 单步计时只记 A 自己的实际耗时。
                elapsed_override = max((p.get("elapsed") or 0) for p in r1s)
                out_b = "\n\n".join((r.get("stdout") or "").strip() for r in r2s)
                files_b = [f for r in r2s for f in (r.get("files") or [])]
                listed = "\n".join(f"  · {os.path.join(workdir, f)}" for f in files_b[:12])
                pipelined[next_name] = {
                    "ok": ok_b,
                    "elapsed": max((r.get("elapsed") or 0) for r in r2s) if r2s else 0,
                    "summary": f"执行成功（{max((r.get('elapsed') or 0) for r in r2s) if r2s else 0}s）\n{_truncate_detail(out_b)}",
                    "body": (_truncate_detail(out_b) + (f"\n产出文件（宿主路径）:\n{listed}" if files_b else ""))
                            or "（无输出）",
                }
            else:
                r1s = parts

            ok_all = all(p.get("ok") for p in r1s)
            result = {
                "ok": ok_all,
                "exit_code": 0 if ok_all else 1,
                "stdout": "\n\n".join((p.get("stdout") or "").strip() for p in r1s),
                "stderr": "\n".join((p.get("stderr") or "").strip() for p in r1s),
                "elapsed": max((p.get("elapsed") or 0) for p in r1s),
                "outputs": sorted({o for p in r1s for o in (p.get("outputs") or [])}),
                "files": [f for p in r1s for f in (p.get("files") or [])],
            }
            if name in _chain_specs()["producer"]:
                fanout_texts = [_extract_descriptive_text(p.get("stdout") or "")
                                for p in r1s]
                fanout_texts = [t for t in fanout_texts if t]
        else:
            used_key = _acquire_api_key()
            result = skill_exec.execute_skill(library, name, args=argv,
                                              on_line=_on_skill_line,
                                              sandbox_key=getattr(_BRANCH_KEY, "key", None),
                                              api_key=used_key)
            _release_api_key(used_key, failed=not result.get("ok"),
                             reason=str(result.get("error") or result.get("stderr") or ""))
            attempt = 0
            while not result.get("ok") and attempt < WORKFLOW_STEP_RETRIES:
                attempt += 1
                reason = str(result.get("error") or "").strip()
                if not reason:
                    # stderr 里混着阶段行与逐源要点，不能整段当错误：
                    # 只取像错误的那一行（含 Error/error/失败/异常/Traceback）
                    lines = [ln.strip() for ln in
                             str(result.get("stderr") or "").splitlines() if ln.strip()]
                    err_lines = [ln for ln in lines
                                 if re.search(r"Error|error|失败|异常|Traceback|ERROR", ln)]
                    reason = (err_lines[-1] if err_lines
                              else (lines[-1] if lines else "未知错误"))
                first = reason.splitlines()[0] if reason else "未知错误"
                # Key 已在释放时标记冷却，这里直接申请新的空闲 Key 重试
                revised = _revise_prompt(argv, reason)
                if revised:
                    print(f"       [O] 触发安全策略（{first[:60]}），规划注入改写提示词后重试"
                          f" {attempt}/{WORKFLOW_STEP_RETRIES}…", flush=True)
                    argv = revised
                else:
                    print(f"       ↳ 执行失败（{first[:80]}），换 Key 重试"
                          f" {attempt}/{WORKFLOW_STEP_RETRIES}…", flush=True)
                used_key = _acquire_api_key()
                result = skill_exec.execute_skill(library, name, args=argv,
                                                  on_line=_on_skill_line,
                                                  sandbox_key=getattr(_BRANCH_KEY, "key", None),
                                                  api_key=used_key)
                _release_api_key(used_key, failed=not result.get("ok"),
                                 reason=str(result.get("error") or result.get("stderr") or ""))
        elapsed = round(elapsed_override if elapsed_override is not None
                        else time.time() - step_start, 2)

        # 技能阶段进度（如 web-summarizer 的 GA/GC/GG）已在执行期间由
        # _on_skill_line 实时转发，这里不能再整段重播一遍。

        if result.get("ok"):
            rt.record_step(name, True, elapsed, "技能执行成功")
            detail = (result.get("stdout") or "").strip()
            # 技能跑在沙箱里，stdout 打印的全是沙箱临时路径，
            # 用户照那个路径找不到文件，且会把真正的宿主路径挤掉。
            detail = _strip_sandbox_paths(detail)
            # 只有配置里声明为「文字产出者」的技能，其输出才可作后续提示词。
            # 其它技能（如 person-image-search）的 stdout 是日志，不能当提示词。
            if name in _chain_specs()["producer"]:
                if fanout_texts:
                    _BRANCH_TEXTS.texts = list(fanout_texts)
                    _set_branch_text("\n".join(fanout_texts))
                else:
                    text_one = _extract_descriptive_text(detail)
                    _BRANCH_TEXTS.texts = [text_one] if text_one else []
                    _set_branch_text(text_one)
                _save_last_prompt(workdir, name, _branch_text())
            _set_branch_files([os.path.join(workdir, r)
                               for r in (result.get("files") or [])])
            outputs = result.get("outputs") or []
            # 只列本次执行新产生的文件（since = 本步开始时间），
            # 否则「图片」这种公共目录会把历史产出全带出来
            real_files = _collect_output_files(workdir, outputs,
                                               since=step_start - 1)
            if real_files:
                listed = "\n".join(f"  · {p}" for p in real_files[:12])
                tail = f"\n产出文件（宿主路径）:\n{listed}"
                if len(real_files) > 12:
                    tail += f"\n  … 另 {len(real_files) - 12} 个"
            else:
                tail = "\n产出文件: （无新文件）" if not outputs else f"\n产出目录: {outputs}"
            body = f"{_truncate_detail(detail)}{tail}" if detail or outputs else "（无输出）"
            step_details.append((name, True, body))
            return f"执行成功（{result['elapsed']}s）\n{_truncate_detail(detail)}{tail}"
        err = str(result.get("error") or result.get("stderr")
                  or result.get("exit_code") or "")
        rt.record_step(name, False, elapsed, err[:120])
        step_details.append((name, False, f"执行失败：{err[:300]}"))
        return f"执行失败: {err}"

    if superplan:
        ok = _run_superprotein(superplan, runner, _loop_hook, library, requirement)
        print(f"🧮 超蛋白 {len(superplan.branches)} 个分支，"
              f"{'全部成功' if ok else '存在失败分支'}；产出目录 {workdir}")
        results = ["超蛋白执行完毕"]
    else:
        results = skill_exec.execute_stopj(
            expr, library,
            max_steps=WORKFLOW_MAX_STEPS,
            max_loop_iterations=WORKFLOW_MAX_LOOP,
            skill_callback=runner,
            extra_names=list(TOOL_HINTS),
            loop_hook=_loop_hook,
        )
        ok = sum(1 for r in results if "成功" in r[:20])
        print(f"🧮 共 {len(results)} 步，成功 {ok} 步；产出目录 {workdir}")

    # ---- 展示各步结果：Tool 类工具的正文（搜索结果、网页内容）必须给用户看 ----
    if show_output:
        for name, step_ok, body in step_details:
            if not body:
                continue
            mark = "✓" if step_ok else "✗"
            print(f"\n══════ {mark} {name} 的结果 ══════")
            print(body)
            print("══════════════════════")

    # ---- 阶段三：结束收尾（汇总统计 + 写回追踪）----
    rt.finish()

    # ---- 闸门复核：把已执行的步骤交给 STOPJRuntime 过一遍闸门 ----
    try:
        from runtime.gate import GateRuntime
        if chain:
            GateRuntime(runner=lambda name, ctx: "已完成").run(
                index=expr, skill_chain=chain,
                max_iterations=WORKFLOW_MAX_LOOP)
    except Exception as e:
        print(f"⚠️ 闸门复核跳过: {e}")

    # 返回结果文本，供调用方（如对话主循环）记住或继续加工
    combined = "\n\n".join(b for _, _, b in step_details if b)
    return (ok == len(results)), combined

# ---------- AI 对话模式 ----------
CHAT_HELP = """📖 可用命令（对话中直接输入）：
    /stop       打断当前正在执行的代码任务
    /queue      查看执行队列（当前任务 + 排队 + 最近完成）
    /clear      清空排队任务（不影响正在执行的）
    /analyze    运行项目结构分析并刷新思维导图
    /mind       只刷新思维导图（treemind.html）
    /cache      查看缓存命中统计
    /cache-clear 清除缓存（下次询问会重新问模型）
    /writable   查看或切换代码代理写权限（/writable on|off）
    /mode       查看或切换代码路由模式（/mode auto|code|plan|goal）
    /engine     查看或切换代码代理引擎（/engine classic|codecore）
    /auto       查看或切换自动审批（/auto on|off）
    /think      查看或设置思考等级（/think 1-5）
    /session    会话管理（/session list|new|switch <id>）
    /model      查看或切换模型（/model <模型名>）
    /provider   厂商管理（/provider list|add|remove|<名字>）
    /config     打开 API Key 配置窗口
    /imagekey   单独配置生图专用 Key（AGNES_API_KEY，与对话主 Key 分开）
    /protein    管理蛋白库：list 列出 / new 登记 / del 删除
    /help       显示本帮助
    /quit       退出对话
其它：直接输入需求（如"给所有函数加注释"），AI 将分析并修改代码；
      也可以提问（如"这个项目是干什么的"），AI 直接回答不修改代码。
      代码任务在后台执行，执行期间可继续输入新需求，自动排队；
      输入包含"执行/新建/项目/渲染"等关键词，自动触发工作流引擎。
      代码需求默认按属性自动路由：
        goal  目标是「修好/跑通/通过测试」，循环试错直到验收
        plan  涉及面广或明说要方案，先出方案确认再动手
        code  其余情况，直接进入读写循环
      用 /mode 显式指定后，除文档生成链外不再走技能模糊匹配。"""


# 询问自身能力的问题：本地直接答，不调模型（这类问题占对话相当比例）
CAPABILITY_PATTERNS = (
    "你会什么", "你能做什么", "你会做什么", "你有什么用", "你能干什么",
    "有哪些技能", "有什么技能", "技能列表", "列出技能", "能做什么",
    "怎么用", "使用说明", "帮助", "你是谁", "你是什么",
    "这个项目是干什么", "项目是干嘛", "介绍下自己", "介绍一下自己",
)


def is_smalltalk(req):
    r = req.strip().lower()
    r_clean = r.rstrip("！!。~～？?，, ")
    if len(r_clean) > 12:
        return None
    for g in _wordlist("greetings"):
        if r_clean == g or r_clean == g + "啊" or r_clean == g + "呀":
            return "greet"
    for t in _wordlist("thanks"):
        if r_clean == t or r_clean.startswith(t):
            return "thanks"
    return None


def is_capability_question(req):
    """是否为询问自身能力的简短问题。

    口语变体很多（你会啥 / 你能干啥 / 你都会些什么），
    只靠固定短语会漏，因此叠加一层「主语 + 能力词 + 疑问尾」的组合判定。
    """
    r = (req or "").strip().strip("，,。.！!？?、~～ ")
    if not r or len(r) > 24:
        return False
    low = r.lower()

    # 一、固定短语直配
    if any(p in low for p in CAPABILITY_PATTERNS):
        return True

    # 二、组合判定：主语 + 能力词（+ 疑问尾）
    subjects = ("你", "您")
    verbs = ("会", "能", "可以", "擅长", "支持", "有")
    ability_words = ("什么", "啥", "哪些", "什么技能", "哪些技能", "什么功能",
                     "干什么", "干啥", "做什么", "做啥", "干嘛", "有什么用",
                     "什么用", "何用")
    has_subject = any(s in low for s in subjects)
    has_verb = any(v in low for v in verbs)
    has_ability = any(w in low for w in ability_words)
    if has_ability and (has_subject or has_verb):
        return True

    # 三、不带主语的疑问：「有啥技能」「支持哪些」「能做啥」
    if has_ability and len(low) <= 12:
        return True

    # 四、"你是谁 / 干什么的" 这类自我介绍问题
    if low in ("你是谁", "你是什么", "介绍一下", "介绍下", "自我介绍一下"):
        return True
    if low.startswith("你是") and low.endswith(("谁", "什么", "干啥的", "干什么的")):
        return True

    return False


def describe_capabilities():
    """本地生成能力清单：从技能库真实读取，不调模型。"""
    try:
        data_dir, skills_dir = _workspace_dirs()
        lib = _make_library(data_dir, skills_dir)
        names = lib.skill_names()
    except Exception:
        names = []

    lines = [
        "我能做这些事：",
        "",
        "【代码开发】",
        "  · 分析项目结构，生成报告与思维导图   例：直接输入项目路径",
        "  · 按需求定位并修改代码               例：给规划链路加缓存",
        "  · 读取/写入/编辑/搜索文件            内置 read/write/edit/grep/find/ls",
        "  · 执行 shell 命令（带危险命令拦截）   内置 bash",
        "",
        "【技能工作流】",
        "  · 先查蛋白库，命中就直接复用已有链路  例：生成丝袜写真 / 游泳游戏开发",
        "  · 单技能需求走原子匹配               例：做个ppt / 写个word文档",
        "  · 都不命中才交模型规划，缺技能可现场创建",
    ]
    if names:
        # 按类别分组展示，避免一次刷屏
        groups = {}
        for n in names:
            key = "文档办公" if any(k in n for k in ("docx", "xlsx", "pdf", "pptx", "markdown")) else \
                  "图像视觉" if any(k in n for k in ("image", "vision", "agnes", "chart")) else \
                  "技能管理" if any(k in n for k in ("skill", "SECURE", "STOPJ", "CRYPTO")) else \
                  "其他"
            groups.setdefault(key, []).append(n)
        lines.append("")
        lines.append(f"【可用技能 {len(names)} 个】")
        for key in ("文档办公", "图像视觉", "技能管理", "其他"):
            items = groups.get(key) or []
            if items:
                lines.append(f"  {key}：" + "、".join(items[:8])
                             + (f" …另 {len(items) - 8} 个" if len(items) > 8 else ""))

    lines += [
        "",
        "输入 /help 看命令，/analyze 分析项目结构，直接说需求我就动手。",
    ]
    return "\n".join(lines)

def do_analyze():
    root = os.getcwd()
    tree = collect_tree(scan_py_files(root))
    if not tree:
        print("❌ 当前目录没有可解析的 Python 文件。")
        return
    analysis = analyze(tree)
    advice = build_advice(analysis)
    print_report(os.path.basename(root) or root, tree, analysis, advice)
    jp, hp = refresh_mind(tree, root)
    print(f"✅ 已更新 {os.path.basename(jp)} 与 {os.path.basename(hp)}")

def _new_session():
    """创建会话记录；记录失败不影响对话。"""
    try:
        from runtime.core.session import SessionStore
        return SessionStore()
    except Exception:
        class _Null:
            def add(self, *a, **k):
                return None
        return _Null()

_TABLE_WORD_RE = re.compile(r"表格|表单|excel|xlsx|清单", re.I)
_TABLE_VERB_RE = re.compile(r"处理|整理|转|改|做|弄|排|生成|变成|列成|输出")


def _is_table_followup(req):
    """判断是不是「把上面内容处理成表格」这类追问。"""
    s = (req or "").strip()
    if not s or len(s) > 20:
        return False
    return bool(_TABLE_WORD_RE.search(s)) and bool(_TABLE_VERB_RE.search(s))


def _format_as_table(content, key, base, model):
    """把上一轮回答重排成 Markdown 表格（只做排版，不重新分析/搜索）。"""
    prompt = (
        "把下面的内容整理成 Markdown 表格：保留全部关键信息，"
        "列名简洁，单元格内容尽量短；只输出表格本身，不要解释。\n\n"
        + (content or "")[:6000]
    )
    return call_llm([{"role": "user", "content": prompt}], key, base, model)


_MD_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")


def _md_table_rows(text):
    """从 Markdown 文本里取表格行（跳过分隔行）。"""
    rows = []
    for line in (text or "").splitlines():
        m = _MD_ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if cells and all(set(c) <= set("-: ") for c in cells):
            continue
        if any(cells):
            rows.append(cells)
    return rows


def _write_table_xlsx(rows, title):
    """把表格行写成 xlsx，返回文件路径；无 openpyxl 时返回 None。"""
    if not rows:
        return None
    try:
        from openpyxl import Workbook
    except ImportError:
        print("⚠️ 未安装 openpyxl，无法生成 Excel（pip install openpyxl）")
        return None
    data_dir, _ = _workspace_dirs()
    workdir = os.path.join(os.path.realpath(data_dir), "work")
    out = os.path.join(workdir, "表格", f"{title}.xlsx")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = "表格"
        for row in rows:
            ws.append(row)
        widths = {}
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths.get(i, 0), len(str(cell)))
        for i, width in widths.items():
            ws.column_dimensions[ws.cell(row=1, column=i + 1).column_letter].width = \
                min(max(width * 2 + 2, 10), 60)
        ws.freeze_panes = "A2"
        wb.save(out)
        return out
    except Exception as e:
        print(f"⚠️ 写入 Excel 失败：{e}")
        return None


_TITLE_KEEP_RE = re.compile(r"[\w\u4e00-\u9fff]")


def _table_title(content, fallback="表格"):
    """从上一轮回答里取一个能当文件名的标题：优先 Markdown 表头，其次短行。"""
    for row in _md_table_rows(content):
        s = "".join(c for c in "".join(row) if _TITLE_KEEP_RE.match(c))
        if 2 <= len(s) <= 24:
            return s
    for line in (content or "").splitlines():
        s = "".join(c for c in line if _TITLE_KEEP_RE.match(c))
        if 2 <= len(s) <= 24:
            return s
    return fallback



def run_chat(key, base, model, yes=False):
    base = base or DEFAULT_BASE_URL
    model = model or DEFAULT_MODEL
    root = os.getcwd()
    session = _new_session()
    # 记录上一轮的回答，供"总结为ppt"这类追问当素材
    last_answer = ""
    # /mode 显式指定的代码路由，None = 按任务属性自动判定
    forced_mode = None
    # 最近一次未完成的代码任务：到轮数上限后说「继续」接着做，
    # 不再走工作流规划（「继续」曾被劫持成 ls→find 链）
    pending_task = None
    CONTINUE_WORDS = ("继续", "接着做", "接着干", "继续做", "继续完成", "go on")
    # 「结果呢」「发我看看」这类追问：直接回放上一次工作流的输出
    RESULT_WORDS = ("结果呢", "结果", "发给我", "发我", "看看结果", "输出呢",
                    "查到什么", "搜到什么", "上面呢")

    # 后台任务队列：代码任务在 worker 线程执行，主线程不被阻塞
    from task_queue import TaskRunner

    # 最近工作上下文：记下刚做完什么，供后续追问（「太集中了」「再改改」）
    # 正确落到 code 路由，而不是被当成孤立提问
    recent_work = []

    def _remember(desc):
        """记录一条最近工作，只保留最近 3 条。"""
        recent_work.append(desc)
        del recent_work[:-3]

    # ---- 后台任务队列：代码任务不再阻塞输入 ----
    # 执行器放在独立线程，主线程始终停在 input() 上，
    # 用户随时能提新需求（自动排队）或 /stop 打断。
    def _exec_task(task):
        """worker 线程里跑一个需求。"""
        return run_code_agent(
            task.req, key, base, model, yes,
            mode=task.mode,
            on_event=_print_event,
            should_stop=_runner.stopped,
        )

    def _print_event(text):
        # worker 线程的输出直接打印；主线程此时在 input()，
        # 两边会交错，但不丢内容（input 的提示符会重绘）
        try:
            print(text, flush=True)
        except Exception:
            pass

    _runner = TaskRunner(_exec_task, on_event=_print_event)

    print("\n🤖 AI 对话开发模式（支持自然语言触发工作流）")
    print("   输入需求，AI 会分析项目、给出计划并修改代码；")
    print("   执行期间可继续输入新需求（自动排队），/stop 打断当前任务。")
    try:
        from runtime.core.settings import load_settings
        _st = load_settings(HERE)
        _engine = getattr(_st, "agent_engine", "codecore")
        _auto = "开" if getattr(_st, "agent_auto_approve", False) else "关"
        _think = getattr(_st, "agent_think_level", 3)
        _writable = "开" if getattr(_st, "agent_writable", True) else "关"
    except Exception:
        _engine, _auto, _think, _writable = "codecore", "关", 3, "开"
    print(f"   引擎 {_engine}　自动审批 {_auto}　思考等级 {_think}　写权限 {_writable}")
    print("   常用：/engine 切内核　/auto 免确认　/think 1-5 调思考深度")
    print("         /model 模型　/provider 厂商　/session 会话")
    print("         /mode 路由　/writable 写权限")
    print("   输入 /help 查看全部命令，/quit 退出。\n")

    while True:
        try:
            req = input("需求> ").strip()
        except EOFError:
            print("已退出。")
            return 0
        if req.lower() in ("quit", "exit", "q", "退出", "bye"):
            if _runner.is_busy():
                print("⏳ 还有任务在执行，正在收尾…")
                _runner.wait_idle(timeout=5)
            print("👋 再见！")
            return 0
        session.add("user", req)
        if req.startswith("/"):
            cmd = req[1:].strip().lower()
            if cmd in ("quit", "exit", "q", "退出"):
                print("👋 再见！")
                return 0
            if cmd in ("help", "h", "帮助"):
                print(CHAT_HELP)
            elif cmd in ("analyze", "分析", "结构"):
                do_analyze()
            elif cmd in ("mind", "导图", "refresh", "刷新"):
                try:
                    root = os.getcwd()
                    jp, hp = refresh_mind(collect_tree(scan_py_files(root)), root)
                    print(f"🧠 已刷新 {os.path.basename(jp)} 与 {os.path.basename(hp)}")
                except Exception as e:
                    print(f"⚠️ 刷新失败：{e}")
            elif cmd in ("config", "配置", "key"):
                setup_launcher()
            elif cmd in ("imagekey", "生图key", "imgkey"):
                setup_image_key()
            elif cmd in ("stop", "停", "打断", "取消"):
                if not _runner.cancel_current():
                    print("ℹ️ 当前没有正在执行的任务")
            elif cmd in ("queue", "队列", "排队"):
                cur, queued, done = _runner.status()
                if cur is None and not queued:
                    print("ℹ️ 队列为空")
                else:
                    if cur is not None:
                        print(f"▶ 执行中：#{cur.id} {cur.req}")
                    for t in queued:
                        print(f"  ⏳ #{t.id} {t.req}")
                    for t in done:
                        took = (t.finished - t.started) if t.started and t.finished else 0
                        print(f"  ✓ #{t.id} {t.status} ({took:.0f}s) {t.req[:30]}")
            elif cmd in ("clear", "清空队列"):
                _runner.clear_queue()
            elif cmd in ("cache", "缓存"):
                print(cache_stats())
            elif cmd in ("cache-clear", "缓存清除"):
                try:
                    p = _route_cache_path()
                    if os.path.isfile(p):
                        os.remove(p)
                    _route_cache_memo.clear()
                    print("✅ 缓存已清除")
                except OSError as e:
                    print(f"⚠️ 清除失败：{e}")
            elif cmd == "writable" or cmd.startswith("writable "):
                arg = cmd.split(None, 1)[1].strip() if " " in cmd else ""
                if arg in ("on", "true", "1", "开", "开启"):
                    print(set_agent_writable(True))
                elif arg in ("off", "false", "0", "关", "关闭"):
                    print(set_agent_writable(False))
                elif arg == "":
                    state = "已开启" if agent_writable() else "已关闭"
                    print(f"✏️ 代码代理写权限当前为：{state}")
                    print("   用法：/writable on 开启（可直接改项目源码，不再逐次询问）")
                    print("         /writable off 关闭（只能写 data/work）")
                else:
                    print(f"❓ 无法识别 {arg!r}，用法：/writable on 或 /writable off")
            elif cmd == "mode" or cmd.startswith("mode "):
                arg = cmd.split(None, 1)[1].strip() if " " in cmd else ""
                aliases = {"auto": None, "自动": None, "": None,
                           "code": "code", "代码": "code", "直接做": "code",
                           "plan": "plan", "方案": "plan", "规划": "plan",
                           "goal": "goal", "目标": "goal"}
                if arg in aliases:
                    forced_mode = aliases[arg]
                    names = {None: "自动判定", "code": "直接执行",
                             "plan": "先方案后执行", "goal": "目标驱动（带验收）"}
                    print(f"🧭 代码路由模式：{names[forced_mode]}")
                else:
                    print(f"❓ 无法识别 {arg!r}，用法：/mode auto|code|plan|goal")
            elif cmd == "engine" or cmd.startswith("engine "):
                arg = cmd.split(None, 1)[1].strip().lower() if " " in cmd else ""
                from runtime.core.settings import load_settings, save_settings
                if arg in ("classic", "codecore"):
                    try:
                        st = load_settings(HERE)
                        st.agent_engine = arg
                        save_settings(st, scope="project", cwd=HERE)
                        label = {"classic": "原有循环（限轮数）",
                                 "codecore": "AiCode 内核（长程，按难度自动选 PLAN/执行）"}
                        print(f"⚙️ 代码代理引擎：{arg}（{label[arg]}）")
                    except Exception as e:
                        print(f"⚠️ 保存失败：{e}")
                elif arg == "":
                    try:
                        cur = load_settings(HERE).agent_engine
                    except Exception:
                        cur = "classic"
                    print(f"⚙️ 代码代理引擎当前为：{cur}")
                    print("   用法：/engine classic 原有循环")
                    print("         /engine codecore AiCode 内核（长程 + 自动模式）")
                else:
                    print(f"❓ 无法识别 {arg!r}，用法：/engine classic|codecore")
            elif cmd == "protein" or cmd.startswith("protein "):
                arg = cmd.split(None, 1)[1].strip() if " " in cmd else ""
                if not arg or arg in ("list", "列表"):
                    _print_protein_list()
                elif arg.startswith("new ") or arg.startswith("新建 "):
                    _protein_new_cli(arg.split(None, 1)[1].strip())
                elif arg.startswith("del ") or arg.startswith("删除 "):
                    _protein_del_cli(arg.split(None, 1)[1].strip())
                else:
                    print("用法：")
                    print("  /protein list                     列出所有蛋白")
                    print("  /protein new 名称 --skills a,b,c   登记新链")
                    print("  /protein del 基因码                删除")
            elif cmd == "auto" or cmd.startswith("auto "):
                arg = cmd.split(None, 1)[1].strip().lower() if " " in cmd else ""
                from runtime.core.settings import load_settings, save_settings
                if arg in ("on", "off"):
                    try:
                        st = load_settings(HERE)
                        st.agent_auto_approve = (arg == "on")
                        save_settings(st, scope="project", cwd=HERE)
                        state = ("已开启（PLAN 出方案后直接执行）" if arg == "on"
                                 else "已关闭（出方案后等你确认）")
                        print(f"⚙️ 自动审批：{state}")
                    except Exception as e:
                        print(f"⚠️ 保存失败：{e}")
                elif arg == "":
                    try:
                        cur = load_settings(HERE).agent_auto_approve
                    except Exception:
                        cur = False
                    print(f"⚙️ 自动审批当前为：{'已开启' if cur else '已关闭'}")
                    print("   用法：/auto on 方案直接执行；/auto off 方案等你确认")
                else:
                    print(f"❓ 无法识别 {arg!r}，用法：/auto on|off")
            elif cmd == "think" or cmd.startswith("think "):
                arg = cmd.split(None, 1)[1].strip() if " " in cmd else ""
                from runtime.core.settings import load_settings, save_settings
                if arg in ("1", "2", "3", "4", "5"):
                    try:
                        st = load_settings(HERE)
                        st.agent_think_level = int(arg)
                        save_settings(st, scope="project", cwd=HERE)
                        labels = {1: "极简，直接动手", 2: "轻量，必要时再读",
                                  3: "默认", 4: "先想 3-5 步再动手",
                                  5: "先规划 5-8 步再执行"}
                        print(f"🧠 思考等级：{arg}（{labels[int(arg)]}）")
                    except Exception as e:
                        print(f"⚠️ 保存失败：{e}")
                elif arg == "":
                    try:
                        cur = load_settings(HERE).agent_think_level
                    except Exception:
                        cur = 3
                    print(f"🧠 思考等级当前为：{cur}（1-5，越高越先规划多步）")
                    print("   用法：/think 1|2|3|4|5")
                else:
                    print(f"❓ 无法识别 {arg!r}，用法：/think 1|2|3|4|5")
            elif cmd == "session" or cmd.startswith("session "):
                arg = cmd.split(None, 1)[1].strip() if " " in cmd else ""
                sub, _, target = arg.partition(" ")
                sub = sub or "list"
                target = target.strip()
                from runtime.core.session import SessionStore
                _sdir = os.path.join(os.path.expanduser("~"), ".dbagent", "sessions")

                if sub in ("list", "ls"):
                    try:
                        files = [f for f in os.listdir(_sdir) if f.endswith(".jsonl")]
                    except OSError:
                        files = []
                    if not files:
                        print("📂 还没有会话记录。")
                    else:
                        files.sort(key=lambda f: os.path.getmtime(os.path.join(_sdir, f)),
                                   reverse=True)
                        print(f"📂 会话（当前 {session.session_id}）：")
                        for f in files[:10]:
                            sid = f[:-len(".jsonl")]
                            path = os.path.join(_sdir, f)
                            try:
                                with open(path, encoding="utf-8") as fh:
                                    n = sum(1 for ln in fh if ln.strip())
                            except OSError:
                                n = 0
                            mark = "←当前" if sid == session.session_id else ""
                            ts = time.strftime("%m-%d %H:%M",
                                               time.localtime(os.path.getmtime(path)))
                            print(f"   {sid}  {ts}  {n} 条 {mark}")
                        print("   切换：/session switch <id>　新建：/session new")
                elif sub == "new":
                    session = SessionStore()
                    last_answer = ""
                    print(f"🆕 新会话：{session.session_id}（上文已清空）")
                elif sub == "switch" and target:
                    path = os.path.join(_sdir, f"{target}.jsonl")
                    if not os.path.isfile(path):
                        print(f"❓ 没有这个会话：{target}")
                    else:
                        session = SessionStore(session_id=target)
                        last_answer = ""
                        msgs = session.messages()
                        print(f"🔀 已切到 {target}（{len(msgs)} 条消息）")
                        for m in msgs[-4:]:
                            role = "你" if m["role"] == "user" else "AI"
                            body = (m.get("content") or "").replace("\n", " ")[:60]
                            print(f"   {role}: {body}")
                elif sub in ("info", ""):
                    msgs = session.messages()
                    print(f"📂 当前会话：{session.session_id}，{len(msgs)} 条消息")
                else:
                    print("用法：/session [list] | new | switch <id>")
            elif cmd == "model" or cmd.startswith("model "):
                arg = cmd.split(None, 1)[1].strip() if " " in cmd else ""
                profiles, active = load_profiles()
                if not arg:
                    print("🤖 模型 / 厂商：")
                    for p in profiles:
                        mark = "←当前" if p.get("name") == active else ""
                        cur_model = p.get("model") or "(未设)"
                        print(f"   [{p.get('name')}] {p.get('base_url')} {mark}")
                        print(f"       key {mask_key(p.get('api_key'))}　模型 {cur_model}")
                        for m in (p.get("models") or []):
                            star = "*" if m == cur_model else " "
                            print(f"         {star} {m}")
                    print("   切换模型：/model <模型名>")
                    print("   切换厂商：/provider <厂商名>")
                else:
                    try:
                        nonlocal_key = activate_profile(active, arg)
                        key, base, model = nonlocal_key
                        print(f"✅ 模型已切换：{arg}（厂商 {active}）")
                    except Exception as e:
                        print(f"❌ 切换失败：{e}")
            elif cmd == "provider" or cmd.startswith("provider "):
                arg = cmd.split(None, 1)[1].strip() if " " in cmd else ""
                sub, _, rest = arg.partition(" ")
                profiles, active = load_profiles()
                if not arg or sub in ("list", "ls"):
                    print("🏢 厂商：")
                    for p in profiles:
                        mark = "←当前" if p.get("name") == active else ""
                        print(f"   {p.get('name'):14} {p.get('base_url')}  {mark}")
                    print("   切换：/provider <名字>")
                    print("   添加：/provider add <名字> <base_url> <api_key> <模型1,模型2>")
                    print("   删除：/provider remove <名字>")
                elif sub == "add":
                    parts = rest.split()
                    if len(parts) < 4:
                        print("用法：/provider add <名字> <base_url> <api_key> <模型1,模型2>")
                    else:
                        name, base_url, api_key = parts[0], parts[1], parts[2]
                        models = parts[3].split(",")
                        try:
                            add_profile(name, base_url, api_key, models)
                            print(f"✅ 已添加厂商 {name}，用 /provider {name} 切换过去")
                        except Exception as e:
                            print(f"❌ {e}")
                elif sub == "remove":
                    try:
                        remove_profile(rest.strip())
                        print(f"✅ 已删除厂商 {rest.strip()}")
                    except Exception as e:
                        print(f"❌ {e}")
                elif sub:
                    try:
                        key, base, model = activate_profile(sub)
                        print(f"✅ 已切到厂商 {sub}，模型 {model}")
                        print(f"   {base}")
                    except Exception as e:
                        print(f"❌ {e}")
            else:
                print(f"❓ 未知命令 /{cmd}，输入 /help 查看可用命令。")
            continue

        small = is_smalltalk(req)
        if small == "greet":
            print("👋 你好！我是你的项目开发助手。直接说需求（如\"给函数加注释\"）我可以帮你改代码；")
            print("   也可以提问（如\"这个项目是干什么的\"），或输入 /help 查看命令。")
            continue
        if small == "thanks":
            print("😊 不客气！有需要随时说。")
            continue
        if not req:
            continue

        # ----- 「结果呢」：直接回放上一次工作流的输出 -----
        if (len(req) <= 12 and any(w in req for w in RESULT_WORDS)):
            cached = getattr(run_task, "last_result", "") or last_answer
            if cached:
                print(f"\n📋 上次的结果：\n{cached}")
                continue
            # 没有可回放的结果，继续走正常流程

        # ----- 续跑挂起的代码任务：优先于一切规划 -----
        if pending_task and (req in CONTINUE_WORDS or req.lower() in CONTINUE_WORDS):
            print(f"↩️ 继续上次的任务：{pending_task['req'][:60]}")
            run_code_agent(pending_task["req"], key, base, model, yes,
                           mode=pending_task["mode"])
            continue
        pending_task = None

        # ----- 能力问答：自动路由（技能状态变了问 LLM，没变用缓存）-----
        if is_capability_question(req):
            try:
                import skill_exec as _se0
                _d0, _s0 = _workspace_dirs()
                _lib0 = _make_library(_d0, _s0)
                answer, source = route_answer(req, _lib0, key, base, model)
            except Exception as e:
                print(f"（回答失败：{e}）")
                continue
            tag = {"llm": "技能已更新，本次询问模型",
                   "cache": "技能未变，用缓存",
                   "local": "模型不可用，用本地清单"}.get(source, "")
            print(f"\n💬 {answer}")
            if tag:
                print(f"\n（{tag}）")
            # 记住这次回答，后续「总结为ppt」可以直接拿它当素材
            last_answer = answer
            continue

        # ----- 本地快捷通道：明确操作直接执行，0 次 LLM 调用 -----
        try:
            import skill_exec as _se
            _data, _skills = _workspace_dirs()
            _wd = _se.data_work_dir(_data)
            local = try_local_action(req, _wd)
            if local is not None:
                print(local)
                continue
        except Exception:
            pass

        # ----- 追问「处理成表格」：拿上一条回答重排，不走人名搜图 -----
        if last_answer and _is_table_followup(req):
            try:
                table = _format_as_table(last_answer, key, base, model)
                print("\n" + table)
                session.add("assistant", table)
                last_answer = table
                out = _write_table_xlsx(_md_table_rows(table), _table_title(last_answer))
                if out:
                    print(f"\n📊 Excel 已生成：{out}")
            except Exception as e:
                print(f"⚠️ 生成表格失败：{e}")
            continue

        # ----- 执行期间：非明确的技能需求一律当补充 -----
        # 代码任务跑着的时候，「敌人」「有血条」以及其它模糊输入不该被路由到
        # 图片搜索/其它技能；只有明确点名的技能需求（天气/新闻/搜索…）才放行。
        if _runner.is_busy() and not _is_plan_confirm(req):
            _p = plan_locally(req, fuzzy=True)
            _m = str((_p or {}).get("match") or "")
            if not _p or "人名" in _m:
                print("💡 当前代码任务执行中，这句当作补充说明记下了。")
                print("   需要单独执行请等它结束，或用完整需求（如「给敌人加上血条」）。")
                _remember(f"补充要求：{req.strip()}")
                continue

        # ----- 本地规划阶段：规则库命中即直接执行 -----
        # /mode 显式指定了代码模式时，不让原子/规则库的模糊匹配抢走需求
        # （「审查一下代码」会命中 code-reviewer，用户却已明确要求用代码模式）
        # 明确的开发需求同理：要的是写文件，不是调现成技能
        _dev_req = _looks_like_dev_request(req) or _is_plan_confirm(req)
        planned = None if _dev_req else plan_locally(req, fuzzy=forced_mode is None)
        if planned:
            # 多步链（总结→md→ppt）：需要借用上一轮内容作为素材
            if planned.get("level") == "chain":
                try:
                    import skill_exec as _se3
                    _d3, _s3 = _workspace_dirs()
                    _lib3 = _make_library(_d3, _s3)
                    ok, msg = build_doc_chain(
                        req, planned["doc_format"], _lib3, key, base, model,
                        context_text=last_answer)
                    if ok:
                        print(f"✅ 已生成：{msg}")
                        last_answer = f"已生成文档 {os.path.basename(msg)}"
                    else:
                        print(f"❌ {msg}")
                except Exception as e:
                    print(f"❌ 多步链执行失败：{e}")
                continue

            chain = planned["skill_chain"]
            superplan = planned.get("superprotein")
            kind = "（同构并行）" if superplan and superplan.homogeneous else \
                ("（异构）" if superplan else "")
            print(f"🎯 本地规划命中（{planned['match']}）：{' → '.join(chain)}{kind}"
                  f"  预估 {planned['tokens']} tokens")
            expr = "S" + "".join(chain)
            try:
                import skill_exec as _se2
                _d2, _s2 = _workspace_dirs()
                _lib2 = _make_library(_d2, _s2)
                # 本地规划只给技能名，没有参数。search/fetch 这类工具
                # 没有参数会直接失败，这里从需求原文推导
                _args2 = _infer_tool_args(req, chain)
                ok2, detail2 = _run_planned(expr, _args2, _lib2, _d2,
                                            requirement=req, source="local",
                                            key=key, base=base, model=model,
                                            superplan=superplan)
                if detail2:
                    last_answer = detail2
            except Exception as e:
                print(f"❌ 执行失败：{e}")
            continue

        # ----- 先判断意图：技能任务 / 改代码 / 直接回答 -----
        try:
            import skill_exec
            _data_dir, _skills_dir = _workspace_dirs()
            library = _make_library(_data_dir, _skills_dir)
        except Exception:
            library = None

        # 用户用 /mode 指定了模式时优先；否则按任务属性自动路由
        if forced_mode:
            route = route_intent(req, library, key, base, model, forced=forced_mode)
        elif _dev_req:
            route = "code"
        else:
            route = route_intent(req, library, key, base, model)

        if route == "work":
            try:
                intent = classify_intent(req, library, key, base, model,
                                         recent="\n".join(recent_work))
            except Exception:
                intent = "code"
            if intent == "task":
                run_task(req, key, base, model)
                # 工作流结果要记下来，否则「整理成表格」「总结为ppt」这类
                # 追问拿不到上文，会被当成新需求重新规划
                last_answer = getattr(run_task, "last_result", "") or last_answer
                continue
            if intent == "answer":
                # 纯问答：直接把问题交给模型回答，不扫描代码、不生成修改
                answer = answer_question(req, library, key, base, model)
                print(f"\n💬 {answer}")
                last_answer = answer
                continue
            # 判定落到 code 但没命中 plan/goal 特征时按 code 处理

        # ----- plan / goal / code 三种代码路由：交给后台队列执行 -----
        # 不在这里同步阻塞，主线程立刻回到 input()，
        # 用户随时能提新需求（自动排队）或 /stop 打断
        _remember(f"需求「{req[:40]}」→ 代码代理修改项目文件")
        task_mode = route if route != "work" else "code"
        pending_task = {"req": req, "mode": task_mode}
        submitted = _runner.submit(req, mode=task_mode, kind="code")
        if submitted is None:
            print("⚠️ 需求为空，已忽略")
        continue

        # ----- 修改范围确定：先定位，再只解析相关文件 -----
        root = resolve_scan_root(os.getcwd())
        py_paths = scan_py_paths(root)
        content_cache = {}
        scope, how = locate_scope(req, root, py_paths, cache=content_cache)
        if not scope:
            tree = {}
            print("🔍 当前没有 Python 文件，AI 将从零创建…")
        else:
            if len(scope) < len(py_paths):
                print(f"🎯 修改范围：{len(scope)}/{len(py_paths)} 个文件（{how}）")
                for rel in scope[:6]:
                    print(f"     · {rel}")
                if len(scope) > 6:
                    print(f"     … 另 {len(scope) - 6} 个")
            py_files = [(os.path.join(root, rel), rel) for rel in scope]
            # 复用定位阶段已读入的内容，避免重复读盘
            tree = collect_tree(py_files, content_cache=content_cache)
            print(f"🔍 解析 {len(tree)} 个文件，AI 分析中…")

        try:
            plan = ai_plan(tree, req, key, base, model)
        except Exception as e:
            print(f"❌ AI 分析失败：{e}")
            continue

        action = plan.get("action", "modify" if plan.get("files") else "none")
        if action == "answer":
            print(f"\n💬 {plan.get('answer') or plan.get('plan', '')}")
            continue
        if action == "none" or not (plan.get("files") or plan.get("details")):
            print(f"\nℹ️ {plan.get('plan', '无需修改。')}")
            continue

        plan_files = plan.get("files") or []
        print("\n📋 修改计划")
        print(f"   涉及文件: {', '.join(plan_files) if plan_files else '（无，可能全部为新建文件）'}")
        print(f"   总体思路: {plan.get('plan', '')}")
        if plan.get("details"):
            print(f"   改动细节: {plan['details']}")

        # 方案定了就确认，不必等 AI 生成完改动再问
        if not yes:
            try:
                ans = input("\n是否按此方案生成修改？(y/N) ").strip().lower()
            except EOFError:
                ans = "n"
            if ans not in ("y", "yes"):
                print("已跳过本轮修改。\n")
                continue

        try:
            edits = ai_edits(plan, root, req, key, base, model)
        except Exception as e:
            print(f"❌ 生成修改失败：{e}")
            continue

        if not edits:
            print("✅ AI 认为无需修改代码。")
            continue

        show_edits(edits)
        results = apply_edits(edits, root)
        ok = sum(1 for _, m in results if m.startswith("✓"))
        fail = len(results) - ok
        for fp, msg in results:
            print(f"   {msg} {fp}")
        if fail:
            print(f"   ⚠️ {fail} 处未成功（模型生成的 old 与文件不一致时常见），可换更具体的说法重试。")
        else:
            print("   ✅ 修改已应用。")

        new_tree = collect_tree(scan_py_files(root))
        try:
            jp, hp = refresh_mind(new_tree, root)
            print(f"   🧠 已更新 {os.path.basename(jp)} 与 {os.path.basename(hp)}（结构已同步）")
        except Exception as e:
            print(f"   ⚠️ 思维导图更新失败：{e}")
        print("")

def auto_enter():
    key, base, model = load_config()
    if not key:
        print("⚠️ 尚未配置 API Key，正在弹出配置窗口…")
        setup_launcher()
        key, base, model = load_config()
        if not key:
            print("配置未完成，进入功能菜单。")
            return menu_mode()
    print("🔑 已检测到 API Key，直接进入对话开发模式。")
    return run_chat(key, base, model, False)

def menu_mode():
    while True:
        print("")
        print("=" * 50)
        print("   Agent - 项目开发助手")
        print("=" * 50)
        print(f"   当前目录: {os.getcwd()}")
        print("")
        print("   [1] 分析项目结构（报告 + 思维导图）")
        print("   [2] 配置 API Key（AI 功能用）")
        print("   [3] 和 AI 对话开发（按需求分析并修改，形成项目）")
        print("   [0] 退出")
        print("")
        try:
            choice = input("   请选择 [0-3]: ").strip()
        except EOFError:
            print("   已退出。")
            return 0
        if choice == "0":
            print("   再见！")
            return 0
        elif choice == "1":
            root = input("  项目路径（回车默认当前）: ").strip() or "."
            run_analysis([root])
        elif choice == "2":
            setup_launcher()
        elif choice == "3":
            menu_chat()
        else:
            print("   ❌ 无效选择，请输入 0-3。")

def menu_chat():
    key, base, model = load_config()
    if not key:
        print("   ⚠️ 尚未配置 API Key，正在弹出配置窗口…")
        setup_launcher()
        key, base, model = load_config()
        if not key:
            print("   ❌ 未配置 API Key，已取消。")
            return 1
    return run_chat(key, base, model, False)

# ---------- 入口 ----------
def main():
    argv = sys.argv[1:]
    key = base = model = None
    yes = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--key", "--base", "--model") and i + 1 < len(argv):
            v = argv[i + 1]
            if a == "--key":
                key = v
            elif a == "--base":
                base = v
            else:
                model = v
            i += 2
        elif a == "--yes":
            yes = True
            i += 1
        else:
            break
    rest = argv[i:]

    if not rest:
        if sys.stdin.isatty():
            return auto_enter()
        return run_analysis([])

    if rest[0] == "--setup":
        return setup_launcher()

    if rest[0] in ("plan", "apply"):
        req = " ".join(rest[1:]).strip()
        if not key:
            key, base, model = load_config()
        return run_ai(rest[0], req, key, base, model, yes)

    if rest[0] == "chat":
        if not key:
            key, base, model = load_config()
        if not key:
            setup_launcher()
            key, base, model = load_config()
        if not key:
            print("❌ 未配置 API Key。")
            return 1
        return run_chat(key, base, model, yes)

    return run_analysis(rest)

def run_ai(mode, req, key, base, model, yes):
    if not req:
        print("❌ 请提供需求描述")
        return 1
    if not key:
        print("❌ 未配置 API Key。")
        return 1
    base = base or DEFAULT_BASE_URL
    model = model or DEFAULT_MODEL
    root = os.getcwd()
    py_files = scan_py_files(root)
    tree = collect_tree(py_files)
    if not tree:
        print("❌ 当前目录未找到可解析的 Python 文件。")
        return 1
    summary = structure_summary(tree)
    print(f"🔍 已扫描 {len(tree)} 个文件，正在让 AI 分析需求…")

    try:
        plan = call_llm_json([
            {"role": "system",
             "content": "你是资深 Python 开发助手，负责分析项目结构并制定修改计划。只输出 JSON，不要输出任何其他文字。"},
            {"role": "user",
             "content": f"项目结构：\n{summary}\n\n用户需求：{req}\n\n"
                        f"请输出 JSON（不要用代码块包裹）：\n"
                        f"{{\"files\": [\"需要修改或新建的文件相对路径列表\"], "
                        f"\"plan\": \"总体修改思路（中文，150字以内）\", "
                        f"\"details\": \"每个文件的具体改动点（中文，简洁）\"}}"},
        ], key, base, model)
    except Exception as e:
        print(f"❌ AI 分析失败：{e}")
        return 1

    plan_files = plan.get("files") or []
    print("\n📋 AI 修改计划")
    print(f"   涉及文件: {', '.join(plan_files) if plan_files else '（无）'}")
    print(f"   总体思路: {plan.get('plan', '')}")
    if plan.get("details"):
        print(f"   改动细节: {plan['details']}")

    if mode == "plan":
        print("\n✅ 分析完成。如需自动修改代码，运行：python agent.py apply \"你的需求\"")
        return 0

    print("\n🛠️  正在读取相关文件并生成具体修改…")
    file_bundle = []
    for fp in plan_files:
        path = os.path.join(root, fp)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n...(内容过长已截断)"
        file_bundle.append(f"### 文件 {fp}\n```python\n{content}\n```")
    context = "\n".join(file_bundle) if file_bundle else "（方案涉及的文件都不存在或无法读取）"
    if len(context) > LLM_MAX_CONTEXT_CHARS:
        context = context[:LLM_MAX_CONTEXT_CHARS] + "\n...(上下文过长已截断)"

    try:
        edits = call_llm_json([
            {"role": "system",
             "content": "你是资深 Python 工程师，负责生成精确、可直接应用的代码修改。只输出 JSON，不要输出任何其他文字。"},
            {"role": "user",
             "content": f"用户需求：{req}\n\n修改方案：\n{plan.get('plan', '')}\n{plan.get('details', '')}\n\n"
                        f"相关文件内容：\n{context}\n\n"
                        f"请输出 JSON（不要用代码块包裹）：\n"
                        f"{{\"edits\": [{{\"file\": \"相对路径\", \"old\": \"与现有文件逐字一致的原文片段\", "
                        f"\"new\": \"替换后的新文本\"}}]}}\n"
                        f"要求：\n"
                        f"1. old 必须逐字复制文件中的现有内容（包括缩进和空行），绝不能捏造或改写\n"
                        f"2. 新建文件用 {{\"file\": \"路径\", \"action\": \"create\", \"content\": \"完整新文件内容\"}}\n"
                        f"3. 只包含确实需要修改的文件，修改要小而精确\n"
                        f"4. 若方案中提到的文件内容没有提供，请基于修改方案直接给出修改"},
        ], key, base, model)
        edits = (edits or {}).get("edits", [])
    except Exception as e:
        print(f"❌ 生成修改失败：{e}")
        return 1

    if not edits:
        print("✅ AI 认为无需修改代码。")
        return 0

    print(f"\n📝 AI 共生成 {len(edits)} 处修改：")
    for e in edits:
        fp = e.get("file", "?")
        if e.get("action") == "create" or ("content" in e and "old" not in e):
            print(f"   ✚ 新建文件 {fp}（{len(e.get('content', ''))} 字符）")
        else:
            print(f"   ✎ 修改 {fp}：替换 {len(e.get('old', ''))} 字符 → {len(e.get('new', ''))} 字符")

    if not yes:
        try:
            ans = input("\n是否应用以上修改？(y/N) ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes"):
            print("已取消，未做任何修改。")
            return 0

    results = apply_edits(edits, root)
    ok = sum(1 for _, m in results if m.startswith("✓"))
    fail = len(results) - ok
    for fp, msg in results:
        print(f"   {msg} {fp}")
    if fail:
        print(f"\n⚠️ {fail} 处未成功（常见原因：模型生成的 old 与文件不完全一致）。")
        print("   可尝试：更具体地描述需求，或把需求拆小后重试。")
    else:
        print("\n✅ 全部修改已应用。可运行 `python agent.py .` 重新生成思维导图查看新结构。")
    return 0 if fail == 0 else 1

if __name__ == "__main__":
    sys.exit(main())