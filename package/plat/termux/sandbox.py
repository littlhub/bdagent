# -*- coding: utf-8 -*-
"""Termux 技能沙箱 —— 目录隔离（无 root、无 chroot）。

Termux 普通用户态没有 chroot 权限，chroot 版沙箱不可用；
这里用与 Windows 版同思路的「目录边界 + 环境变量白名单」：
  - 技能目录复制到 temp 下的沙箱，工作目录锁定在 work-<key>；
  - 子进程只带白名单环境变量，Key 由主程序按池注入；
  - 产出通过 collect_outputs 同步回 <data>/work，重名自动改名。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

WORK_DIRNAME = "work"
SANDBOX_ROOT_NAME = "dbagent-sandbox"
EXEC_TIMEOUT = 120

ENV_WHITELIST = (
    "PATH", "HOME", "TMPDIR", "PREFIX", "LANG", "LC_ALL", "TERM",
    "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONHASHSEED",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
)


def _package_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _project_dir() -> str:
    return os.path.dirname(_package_dir())


def sandbox_root() -> str:
    return os.path.join(tempfile.gettempdir(), SANDBOX_ROOT_NAME)


def data_work_dir(data_dir: str) -> str:
    """<data>/work —— 技能产出目录。

    动态数据在 data/dynamic/，产出固定落在 data/work/。
    """
    real = os.path.realpath(data_dir)
    if os.path.basename(real) == WORK_DIRNAME:
        path = real
    else:
        path = os.path.join(os.path.dirname(real), WORK_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def _work_name(key: str | None = None) -> str:
    return WORK_DIRNAME if not key else f"{WORK_DIRNAME}-{key}"


def _skills_name(key: str | None = None) -> str:
    return "skills" if not key else f"skills-{key}"


def ensure_sandbox(key: str | None = None) -> str:
    root = sandbox_root() if not key else sandbox_root() + f"-{key}"
    for sub in (_skills_name(key), _work_name(key)):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return root


def stage_skill(sandbox_root: str, skills_dir: str, entry: str,
                key: str | None = None) -> str:
    """复制整个技能目录到沙箱，返回脚本在沙箱内的相对路径。"""
    rel_entry = os.path.relpath(entry, skills_dir)
    skill_name = rel_entry.split(os.sep)[0]
    src_dir = os.path.join(skills_dir, skill_name)
    dst_dir = os.path.join(sandbox_root, _skills_name(key), skill_name)
    if os.path.isdir(dst_dir):
        shutil.rmtree(dst_dir, ignore_errors=True)
    os.makedirs(os.path.dirname(dst_dir), exist_ok=True)
    shutil.copytree(src_dir, dst_dir, symlinks=False)
    return f"{_skills_name(key)}/{rel_entry.replace(os.sep, '/')}"


def collect_outputs(sandbox_root: str, target_dir: str, key: str | None = None):
    """把沙箱 work 同步到 <data>/work，返回 [(沙箱相对, 宿主相对)]；重名改名。"""
    src = os.path.join(sandbox_root, _work_name(key))
    pairs = []
    if not os.path.isdir(src):
        return pairs
    os.makedirs(target_dir, exist_ok=True)

    def _unique(dst):
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
            pairs.append((name, os.path.basename(d)))
    return pairs


def list_work_files(sandbox_root: str, since: float | None = None,
                    key: str | None = None) -> list[str]:
    work = os.path.join(sandbox_root, _work_name(key))
    files = []
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


def sync_back_persist(sandbox_root: str, script_rel: str, skill_dir: str):
    """有状态技能：把沙箱内技能 data/ 写回宿主技能目录。"""
    parts = script_rel.replace("\\", "/").split("/")
    if len(parts) < 2 or not parts[0].startswith("skills"):
        return []
    skill_name = parts[1]
    src = os.path.join(sandbox_root, parts[0], skill_name, "data")
    if not os.path.isdir(src):
        return []
    dst = os.path.join(skill_dir, "data")
    written = []
    for root_dir, _dirs, files in os.walk(src):
        for f in files:
            s = os.path.join(root_dir, f)
            rel = os.path.relpath(s, src)
            d = os.path.join(dst, rel)
            try:
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                written.append(rel)
            except OSError:
                continue
    return written


def clear_work(sandbox_root: str, key: str | None = None) -> None:
    work = os.path.join(sandbox_root, _work_name(key))
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


def _project_api_key() -> str:
    """env > ~/.dbagent/key.json > ~/.dbagent/agent_coder.json > 项目 key.txt。"""
    import json

    for var in ("OPENAI_API_KEY", "AGNES_API_KEY", "AGENT_API_KEY", "DBAGENT_CHAT_KEY"):
        v = os.environ.get(var)
        if v and v.strip():
            return v.strip()

    cands = [
        os.path.join(os.path.expanduser("~"), ".dbagent", "key.json"),
        os.path.join(os.path.expanduser("~"), ".dbagent", "agent_coder.json"),
        os.path.join(_project_dir(), "key.txt"),
        os.path.join(_project_dir(), "key.json"),
    ]
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
            if isinstance(data, dict):
                for k in ("api_key", "key", "token"):
                    v = data.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
        else:
            first = raw.splitlines()[0].strip()
            if first:
                return first
    return ""


def _runtime_cfg_path() -> str:
    path = os.path.join(_project_dir(), "workspace", "data", "runtime.json")
    return path if os.path.isfile(path) else ""


def _build_env(extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in ENV_WHITELIST if k in os.environ}
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["DBAGENT_ROOT"] = _project_dir()
    cfg = _runtime_cfg_path()
    if cfg:
        env["DBAGENT_RUNTIME_CFG"] = cfg
    env.update(extra or {})
    return env


def run_in_sandbox(sandbox_root: str, script_path: str, argv,
                   timeout: int = EXEC_TIMEOUT, on_line=None,
                   key: str | None = None, api_key: str | None = None):
    """直接用宿主 python 执行沙箱内脚本（目录隔离，无 chroot）。"""
    import threading

    script = os.path.join(sandbox_root, script_path)
    workdir = os.path.join(sandbox_root, _work_name(key))
    os.makedirs(workdir, exist_ok=True)
    if not os.path.isfile(script):
        return 127, "", f"沙箱内找不到脚本: {script_path}"

    py = sys.executable or "python3"
    cmd = [py, "-I", script, *[str(a) for a in argv]]

    extra = {}
    chosen = (api_key or "").strip() or _project_api_key()
    if chosen:
        extra["AGNES_API_KEY"] = chosen
        extra["OPENAI_API_KEY"] = chosen
        extra["DBAGENT_CHAT_KEY"] = chosen
        extra["AGENT_API_KEY"] = chosen
    env = _build_env(extra)

    try:
        proc = subprocess.Popen(cmd, cwd=workdir, env=env,
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, errors="replace")
    except FileNotFoundError as e:
        return 127, "", f"找不到解释器: {e}"
    except OSError as e:
        return 126, "", f"启动失败: {e}"

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
    t_out = threading.Thread(target=_pump, args=(proc.stdout, "out", out_buf), daemon=True)
    t_err = threading.Thread(target=_pump, args=(proc.stderr, "err", err_buf), daemon=True)
    t_out.start()
    t_err.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        return 124, "".join(out_buf), "".join(err_buf) + f"\n执行超时（>{timeout}s）"
    t_out.join()
    t_err.join()
    return code, "".join(out_buf), "".join(err_buf)
