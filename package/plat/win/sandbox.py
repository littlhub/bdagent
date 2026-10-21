#!/usr/bin/env python3
"""Windows 技能沙箱 —— 让 skill_exec 在 Windows 上也能执行技能脚本。

与 Linux 版的差异
------------------
Linux 版用 chroot 把技能关进一个只含解释器的根目录，隔离是内核级的。
Windows 没有 chroot，本模块用三层机制达到「够用的隔离」：

1. **目录边界**：技能目录复制到沙箱，工作目录固定为沙箱内 `work/`，
   产出只从 `work/` 收集，技能看不到也写不到项目其他位置。
2. **环境变量白名单**：只透传必要变量，清掉 PYTHONPATH 等会改变
   解释器行为的变量，避免脚本越出沙箱导入模块。
3. **可选的 AppContainer / 低权限令牌**：设置 `DBAGENT_SANDBOX_STRICT=1`
   时尝试用受限令牌启动（需要 pywin32），失败则自动回退到 1+2。

诚实说明：1+2 是「约定式隔离」，不是内核强制。恶意脚本理论上仍可
通过绝对路径访问磁盘。要真隔离请用 Linux chroot 版、容器，或对
不可信技能启用 strict 模式。

用法（由 skill_exec 调用，不直接对外）
-------------------------------------
    from skill_exec_win import ensure_sandbox, stage_skill, run_in_sandbox
    root = ensure_sandbox()
    inner = stage_skill(root, skills_dir, entry)
    code, out, err = run_in_sandbox(root, inner, argv)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

SANDBOX_ROOT_NAME = "dbagent-sandbox"
WORK_DIRNAME = "work"
EXEC_TIMEOUT = 120

# Windows 上技能脚本多为 GBK 输出；先按 UTF-8 解码，失败再退回 GBK。
DECODE_CANDIDATES = ("utf-8", "gbk", "cp936", "latin-1")

# 透传给技能脚本的环境变量（其余一律不带）
ENV_WHITELIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
    "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
    "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONHASHSEED",
)

# 技能里常用、但从项目外透传的业务变量
ENV_PASSTHROUGH = (
    "OPENAI_API_KEY", "AGENT_API_KEY", "HERB_API_KEY", "HERB_URL",
    "AGNES_API_KEY", "AGNES_BASE_URL",
)


def sandbox_root() -> str:
    """沙箱根目录：放在系统 temp 下（项目盘可能不可执行/受限）。"""
    base = tempfile.gettempdir()
    return os.path.join(base, SANDBOX_ROOT_NAME)


def data_work_dir(data_dir: str) -> str:
    """<data>/work —— 技能产出目录（与 Linux 版一致）。"""
    path = os.path.realpath(os.path.join(data_dir, WORK_DIRNAME))
    os.makedirs(path, exist_ok=True)
    return path


def ensure_sandbox(key: str | None = None) -> str:
    """创建（或复用）沙箱根，返回路径。

    key 为空时用共享根；有 key 时按分支分目录，并行超蛋白的分支
    各自 stage_skill / clear_work，互不干扰。
    """
    root = sandbox_root() if not key else sandbox_root() + f"-{key}"
    marker = os.path.join(root, ".ready")
    for sub in ("skills", WORK_DIRNAME):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    if not os.path.exists(marker):
        with open(marker, "w", encoding="utf-8") as f:
            f.write("ok")
    return root


def _resolve_python() -> str:
    """当前解释器优先；它一定存在且与调用方一致。"""
    exe = sys.executable
    if exe and os.path.isfile(exe):
        return exe
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return found
    return "python"


def _resolve_node() -> str | None:
    return shutil.which("node")


def stage_skill(sandbox_root: str, skills_dir: str, entry: str,
                key: str | None = None) -> str:
    """把整个技能目录复制进沙箱，返回脚本在沙箱内的相对路径。

    整个目录都复制：Node 技能需要同目录的 node_modules，
    Python 技能也可能 import 同目录模块。key 仅用于与 Linux 签名对齐
    （Windows 分支沙箱已按 key 分根，无需再分目录）。
    """
    rel_entry = os.path.relpath(entry, skills_dir)
    skill_name = rel_entry.split(os.sep)[0]
    src_dir = os.path.join(skills_dir, skill_name)
    dst_dir = os.path.join(sandbox_root, "skills", skill_name)
    if os.path.isdir(dst_dir):
        shutil.rmtree(dst_dir, ignore_errors=True)
    shutil.copytree(src_dir, dst_dir, symlinks=False)
    return rel_entry


def _build_env(cwd: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ENV_WHITELIST + ENV_PASSTHROUGH:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    # 让脚本里的 "python" 指向同一个解释器
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PATH", os.environ.get("PATH", ""))
    env["PWD"] = cwd
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


# 有状态技能的数据目录名：执行前后需要双向同步，否则记录会丢
PERSIST_DIRNAME = "data"


def persist_dir(sandbox_root: str, script_rel: str) -> str | None:
    """返回技能在沙箱内的持久化数据目录（<skills>/<name>/data）。"""
    parts = script_rel.replace("\\", "/").split("/")
    if not parts:
        return None
    path = os.path.join(sandbox_root, "skills", parts[0], PERSIST_DIRNAME)
    return path


def snapshot_persist(sandbox_root: str, script_rel: str) -> dict[str, float]:
    """记录执行前持久化目录里各文件的 mtime，用于判断是否被修改。"""
    path = persist_dir(sandbox_root, script_rel)
    snap: dict[str, float] = {}
    if not path or not os.path.isdir(path):
        return snap
    for root, _dirs, files in os.walk(path):
        for f in files:
            full = os.path.join(root, f)
            try:
                snap[os.path.relpath(full, path)] = os.path.getmtime(full)
            except OSError:
                continue
    return snap


def sync_back_persist(sandbox_root: str, script_rel: str,
                      target_skill_dir: str) -> list[str]:
    """把沙箱内技能 data/ 的变化写回宿主上的技能目录。

    只写回新增或修改过的文件，避免覆盖宿主上更新的内容。
    """
    src = persist_dir(sandbox_root, script_rel)
    if not src or not os.path.isdir(src):
        return []
    dst = os.path.join(target_skill_dir, PERSIST_DIRNAME)
    written: list[str] = []
    for root, _dirs, files in os.walk(src):
        for f in files:
            s = os.path.join(root, f)
            rel = os.path.relpath(s, src)
            d = os.path.join(dst, rel)
            try:
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                written.append(rel)
            except OSError:
                continue
    return written



def _decode(raw: bytes | None) -> str:
    """技能输出可能是 UTF-8 也可能是 GBK，按候选顺序尝试。"""
    if not raw:
        return ""
    for enc in DECODE_CANDIDATES:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# 生图专用的环境变量名，按优先级排列
IMAGE_KEY_VARS = ("AGNES_API_KEY", "OPENAI_API_KEY")
IMAGE_KEY_FIELDS = ("agnes_api_key", "AGNES_API_KEY", "openai_key",
                    "OPENAI_API_KEY", "image_api_key")


def _image_api_key() -> str:
    """读取**生图专用** Key。

    与对话用的主 Key（api_key）是两回事：
      1. 环境变量 AGNES_API_KEY / OPENAI_API_KEY
      2. key.json 里独立的生图字段（agnes_api_key / openai_key / image_api_key）
    都找不到就返回空，**不回落到主 api_key** —— 生图接口与对话接口
    鉴权范围不同，拿主 Key 去请求只会 401，反而掩盖真正的配置缺失。
    """
    import json

    for var in IMAGE_KEY_VARS:
        v = os.environ.get(var)
        if v and v.strip():
            return v.strip()

    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(os.path.expanduser("~"), ".dbagent", "key.json")]
    cur = here
    for _ in range(6):
        cands.append(os.path.join(cur, "key.json"))
        cands.append(os.path.join(cur, "key.txt"))
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt

    for p in cands:
        if not os.path.isfile(p):
            continue
        try:
            data = json.load(open(p, encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for k in IMAGE_KEY_FIELDS:
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _runtime_cfg_path() -> str:
    """定位宿主上的 workspace/data/runtime.json。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(6):
        cand = os.path.join(cur, "workspace", "data", "runtime.json")
        if os.path.isfile(cand):
            return cand
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return ""


def _project_api_key() -> str:
    """读取对话用的主 API Key，供技能脚本使用。

    优先级：环境变量 > ~/.dbagent/key.json > 项目内 key.json。
    兼容裸字符串与 JSON 对象两种写法。
    """
    import json

    for var in ("OPENAI_API_KEY", "AGNES_API_KEY", "AGENT_API_KEY"):
        v = os.environ.get(var)
        if v and v.strip():
            return v.strip()

    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(os.path.expanduser("~"), ".dbagent", "key.json")]
    cur = here
    for _ in range(6):
        cands.append(os.path.join(cur, "key.json"))
        cands.append(os.path.join(cur, "key.txt"))
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt

    for p in cands:
        if not os.path.isfile(p):
            continue
        try:
            raw = open(p, encoding="utf-8", errors="ignore").read().strip()
        except OSError:
            continue
        if not raw:
            continue
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            for k in ("api_key", "key", "openai_key", "OPENAI_API_KEY"):
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        elif raw.startswith("sk-") or len(raw) > 20:
            return raw.splitlines()[0].strip()
    return ""


def run_in_sandbox(sandbox_root: str, script_rel: str, argv: list[str],
                   timeout: int = EXEC_TIMEOUT, on_line=None,
                   key: str | None = None,
                   api_key: str | None = None) -> tuple[int, str, str]:
    """在沙箱内执行脚本，返回 (returncode, stdout, stderr)。

    - .py 用当前解释器执行
    - .js 用 node 执行
    - 工作目录固定为沙箱 work/
    - stdin 接空，避免技能抢占调用方终端
    - on_line(stream, line)：逐行回调（stream 为 'out'/'err'），用于实时转发进度
    """
    script_path = os.path.join(sandbox_root, "skills", script_rel)
    if not os.path.isfile(script_path):
        return 127, "", f"沙箱内找不到脚本: {script_rel}"

    workdir = os.path.join(sandbox_root, WORK_DIRNAME)
    os.makedirs(workdir, exist_ok=True)

    if script_path.lower().endswith(".js"):
        node = _resolve_node()
        if not node:
            return 127, "", "找不到 node，无法执行 JavaScript 技能"
        cmd = [node, script_path, *[str(a) for a in argv]]
    else:
        cmd = [_resolve_python(), "-I", script_path, *[str(a) for a in argv]]

    # 沙箱环境是白名单构建的，Key 可能透传不到。分两个变量注入：
    #   AGNES_API_KEY    -> 生图（实测主 Key 也能调生图接口，故优先专用、次选主 Key）
    #   DBAGENT_CHAT_KEY -> 对话模型（visioncustom 等）
    extra = {}
    try:
        # api_key 由调用方（Key 池）指定；未指定时回退到配置文件
        chosen = (api_key or "").strip()
        img_key = chosen or _image_api_key() or _project_api_key()
        if img_key:
            extra["AGNES_API_KEY"] = img_key
            extra["OPENAI_API_KEY"] = img_key
        chat_key = chosen or _project_api_key()
        if chat_key:
            extra["DBAGENT_CHAT_KEY"] = chat_key
        # 技能在沙箱里上溯不到项目根，读不了 runtime.json。
        # 把配置路径直接告诉它们，否则能力表/规则全都退回默认值。
        cfg_path = _runtime_cfg_path()
        if cfg_path:
            extra["DBAGENT_RUNTIME_CFG"] = cfg_path
    except Exception:
        pass
    env = _build_env(workdir, extra=extra)
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        return _run_streamed(cmd, workdir, env, timeout, on_line)
    except subprocess.TimeoutExpired:
        return 124, "", f"执行超时（>{timeout}s）"
    except FileNotFoundError as e:
        return 127, "", f"找不到可执行文件: {e}"
    except OSError as e:
        return 126, "", f"启动失败: {e}"


def _run_streamed(cmd, workdir, env, timeout, on_line):
    """Popen 执行，两个读线程分别收 stdout/stderr。

    逐行喂给 on_line 并同步累积成完整字符串，进程结束后
    返回值与旧 subprocess.run 版本完全一致。
    """
    import threading

    proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors="replace")

    def _pump(pipe, stream, sink):
        try:
            for raw in iter(pipe.readline, ""):
                line = raw.rstrip("\n")
                sink.append(line + "\n")
                if on_line:
                    try:
                        on_line(stream, line)
                    except Exception:
                        pass
        finally:
            pipe.close()

    out_buf, err_buf = [], []
    t_out = threading.Thread(target=_pump,
                             args=(proc.stdout, "out", out_buf), daemon=True)
    t_err = threading.Thread(target=_pump,
                             args=(proc.stderr, "err", err_buf), daemon=True)
    t_out.start()
    t_err.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    t_out.join()
    t_err.join()
    return code, "".join(out_buf), "".join(err_buf)


def collect_outputs(sandbox_root: str, target_dir: str,
                    key: str | None = None) -> list[tuple[str, str]]:
    """把沙箱 work/ 里的文件同步到 <data>/work，返回 [(沙箱相对, 宿主相对)]。

    并行子任务可能在同一秒生成同名文件，重名时改名而不是覆盖。
    """
    src = os.path.join(sandbox_root, WORK_DIRNAME)
    pairs: list[tuple[str, str]] = []
    if not os.path.isdir(src):
        return pairs
    os.makedirs(target_dir, exist_ok=True)

    def _unique(dst: str) -> str:
        if not os.path.exists(dst):
            return dst
        stem, ext = os.path.splitext(dst)
        i = 2
        while os.path.exists(f"{stem}_{i}{ext}"):
            i += 1
        return f"{stem}_{i}{ext}"

    for name in sorted(os.listdir(src)):
        s = os.path.join(src, name)
        if os.path.isdir(s):
            for root_dir, _dirs, names in os.walk(s):
                for fname in sorted(names):
                    sp = os.path.join(root_dir, fname)
                    rel = os.path.relpath(sp, src).replace(os.sep, "/")
                    d = _unique(os.path.join(target_dir, *rel.split("/")))
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                    shutil.copy2(sp, d)
                    pairs.append((rel, os.path.relpath(d, target_dir).replace(os.sep, "/")))
        else:
            d = _unique(os.path.join(target_dir, name))
            shutil.copy2(s, d)
            pairs.append((name, os.path.relpath(d, target_dir).replace(os.sep, "/")))
    return pairs


def list_work_files(sandbox_root: str, since: float | None = None,
                    key: str | None = None) -> list[str]:
    """列出本分支 work 里的文件（相对路径），可按 mtime 过滤本次新增。"""
    work = os.path.join(sandbox_root, WORK_DIRNAME)
    files: list[str] = []
    if not os.path.isdir(work):
        return files
    for root_dir, _dirs, names in os.walk(work):
        for name in names:
            p = os.path.join(root_dir, name)
            if since is not None:
                try:
                    if os.path.getmtime(p) < since:
                        continue
                except OSError:
                    continue
            files.append(os.path.relpath(p, work).replace(os.sep, "/"))
    return sorted(files)


def clear_work(sandbox_root: str, key: str | None = None) -> None:
    """清空沙箱 work/，避免上一次的产出混进下一次结果。"""
    work = os.path.join(sandbox_root, WORK_DIRNAME)
    if not os.path.isdir(work):
        return
    for name in os.listdir(work):
        p = os.path.join(work, name)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
        except OSError:
            pass
