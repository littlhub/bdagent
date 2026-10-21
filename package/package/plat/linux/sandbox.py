#!/usr/bin/env python3
"""Linux 技能沙箱 —— chroot 隔离。

隔离边界
--------
- chroot 根只含 Python 解释器运行库与被调用的技能脚本副本，看不到宿主文件系统；
- 脚本工作目录固定为 /work，映射到宿主 <data>/work，产出只落在这里；
- 不经过 shell，参数以列表传入，避免命令注入。

沙箱根放在 /tmp 而不是 data/work：项目所在文件系统不保留可执行位，
chroot 内的解释器无法执行。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess

SANDBOX_ROOT_NAME = "dbagent-sandbox"
WORK_DIRNAME = "work"
EXEC_TIMEOUT = 120

RUNTIME_FILES = [
    ("/usr/bin/python3.12", "usr/bin/python3"),
    ("/usr/lib/libpython3.12.so.1.0", "usr/lib/libpython3.12.so.1.0"),
    ("/lib/ld-musl-aarch64.so.1", "lib/ld-musl-aarch64.so.1"),
    ("/bin/bash", "bin/bash"),
    ("/bin/busybox", "bin/busybox"),
    ("/usr/lib/libreadline.so.8", "usr/lib/libreadline.so.8"),
    ("/usr/lib/libncursesw.so.6", "usr/lib/libncursesw.so.6"),
]
RUNTIME_TREES = [
    ("/usr/lib/python3.12", "usr/lib/python3.12"),
]
# busybox 内置命令，以链接形式挂到 /bin 下
BUSYBOX_APPLETS = [
    "sh", "grep", "sed", "awk", "find", "cat", "ls", "rm", "mkdir",
    "head", "tail", "wc", "sort", "tr", "cut", "xargs", "env", "test",
    "cp", "mv", "touch", "diff", "uniq", "realpath", "dirname", "basename",
]


def data_work_dir(data_dir: str) -> str:
    """<data>/work —— 技能产出目录。"""
    path = os.path.realpath(os.path.join(data_dir, WORK_DIRNAME))
    os.makedirs(path, exist_ok=True)
    return path


def _copy_runtime(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)


def _node_runtime():
    """收集 node 可执行文件及其依赖库，返回 [(源路径, 沙箱内相对路径)]。"""
    node = shutil.which("node")
    if not node:
        return []
    items = [(node, "usr/bin/node")]
    try:
        out = subprocess.run(["ldd", node], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return items
    for line in out.splitlines():
        parts = line.split("=>")
        if len(parts) != 2:
            continue
        target = parts[1].strip().split(" ")[0]
        if target.startswith("/") and os.path.exists(target):
            items.append((target, target.lstrip("/")))
    return items


def _package_dir() -> str:
    """本文件位于 <package>/plat/linux/，反推 package 根。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ensure_sandbox(key: str | None = None) -> str:
    """构建（或复用）chroot 根，返回沙箱根路径。

    chroot 根始终共享：分支隔离不复制 143MB 运行时，而是靠不同的
    work-<key> / skills-<key> 子目录（见 _work_name / _skills_name）。
    """
    root = os.path.join("/tmp", SANDBOX_ROOT_NAME)
    marker = os.path.join(root, ".ready")
    if os.path.exists(marker):
        _ensure_extras(root)
        return root

    for path in ("usr/bin", "usr/lib", "lib", WORK_DIRNAME):
        os.makedirs(os.path.join(root, path), exist_ok=True)

    for src, rel in RUNTIME_FILES:
        if os.path.exists(src):
            _copy_runtime(src, os.path.join(root, rel))
    for src, rel in RUNTIME_TREES:
        if os.path.isdir(src) and not os.path.exists(os.path.join(root, rel)):
            shutil.copytree(src, os.path.join(root, rel), symlinks=True)
    for src, rel in _node_runtime():
        _copy_runtime(src, os.path.join(root, rel))

    # busybox 多调用二进制：把 applet 链接到 /bin 下
    busybox = os.path.join(root, "bin", "busybox")
    if os.path.exists(busybox):
        for applet in BUSYBOX_APPLETS:
            link = os.path.join(root, "bin", applet)
            if not os.path.lexists(link):
                try:
                    os.symlink("busybox", link)
                except OSError:
                    shutil.copy2(busybox, link)
                    os.chmod(link, 0o755)

    _ensure_extras(root)

    with open(marker, "w", encoding="utf-8") as f:
        f.write("ok")
    return root


def _ensure_extras(root: str) -> None:
    """增量补齐运行时依赖（对已建好的旧沙箱也生效）。

    · 整个 package → /dbagent/package：技能（如 web-summarizer）会
      按 DBAGENT_ROOT/package 找共享工具模块，甚至 import main 调
      load_config()；chroot 里技能被单独摆在 /skills/，相对上溯
      永远找不到，索性把包整体搬进来。
    · /etc/ssl → /etc/ssl：urllib 用系统证书路径校验 HTTPS，
      requests 自带 certifi 不受影响，但标准库没有这棵树就全挂。
    """
    pkg_dst = os.path.join(root, "dbagent", "package")
    if not os.path.isfile(os.path.join(pkg_dst, "main.py")):
        shutil.rmtree(pkg_dst, ignore_errors=True)
        shutil.copytree(_package_dir(), pkg_dst,
                        ignore=shutil.ignore_patterns("__pycache__"))
    ssl_dst = os.path.join(root, "etc", "ssl")
    if not os.path.isdir(ssl_dst) and os.path.isdir("/etc/ssl"):
        shutil.copytree("/etc/ssl", ssl_dst, symlinks=True)


def _work_name(key: str | None = None) -> str:
    """分支工作目录名：共享时为 work，分支为 work-<key>。"""
    return WORK_DIRNAME if not key else f"{WORK_DIRNAME}-{key}"


def _skills_name(key: str | None = None) -> str:
    """分支技能目录名：共享时为 skills，分支为 skills-<key>。"""
    return "skills" if not key else f"skills-{key}"


def stage_skill(sandbox_root: str, skills_dir: str, entry: str,
                key: str | None = None) -> str:
    """把技能目录复制进沙箱，返回脚本在沙箱内的路径。

    整个技能目录都要复制：Node 技能依赖同目录的 node_modules，
    Python 技能也可能 import 同目录模块。key 用于并行分支隔离。
    """
    skills_name = _skills_name(key)
    skill_dir = os.path.basename(os.path.dirname(os.path.dirname(entry)))
    src_dir = os.path.join(skills_dir, skill_dir)
    dst_dir = os.path.join(sandbox_root, skills_name, skill_dir)
    if os.path.isdir(dst_dir):
        shutil.rmtree(dst_dir, ignore_errors=True)
    os.makedirs(os.path.dirname(dst_dir), exist_ok=True)
    shutil.copytree(src_dir, dst_dir, symlinks=False)
    rel = os.path.relpath(entry, skills_dir).replace(os.sep, "/")
    return f"/{skills_name}/" + rel


def _bootstrap(script_path: str, argv, work_name: str = WORK_DIRNAME) -> str:
    return (
        "import os,sys,runpy\n"
        f"os.chdir({work_name!r})\n"
        f"sys.argv={[script_path] + list(argv)!r}\n"
        f"runpy.run_path({script_path!r}, run_name='__main__')\n"
    )


def collect_outputs(sandbox_root: str, target_dir: str, key: str | None = None):
    """把沙箱分支 work 里的文件同步到 <data>/work，保留沙箱内文件供后续编辑。

    返回 [(沙箱内相对路径, 宿主相对路径)]。并行子任务可能在同一秒
    生成同名文件（如 20261020_211829.png），重名时改名而不是覆盖。
    """
    src = os.path.join(sandbox_root, _work_name(key))
    pairs = []
    if not os.path.isdir(src):
        return pairs

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
                    if fname.startswith(".l2s."):
                        continue
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
    """列出本分支 work 里的文件（相对路径），可按 mtime 过滤本次新增。

    分支沙箱隔离后，这里是「本步真的产出了什么」的准确来源；
    共享宿主 work 目录在并行时会互相串，不能用来做 fan-out 展开。
    """
    work = os.path.join(sandbox_root, _work_name(key))
    files = []
    if not os.path.isdir(work):
        return files
    for root_dir, _dirs, names in os.walk(work):
        for name in names:
            if name.startswith(".l2s."):
                continue
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
    """把沙箱内技能 data/ 的变更写回宿主技能目录（有状态技能用）。"""
    parts = script_rel.replace("\\", "/").lstrip("/").split("/")
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
    """清空沙箱本分支 work 目录，避免上一次的产出混进本次结果。"""
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
    """读取对话主 Key，供技能脚本使用。

    chroot 内看不到宿主 home，Key 只能靠环境变量透传进技能进程。
    优先级：环境变量 > ~/.dbagent/key.json > ~/.dbagent/agent_coder.json
    > 项目内 key.json。兼容 JSON 对象与裸 token 两种写法。
    """
    import json

    for var in ("OPENAI_API_KEY", "AGNES_API_KEY", "AGENT_API_KEY",
                "DBAGENT_CHAT_KEY"):
        v = os.environ.get(var)
        if v and v.strip():
            return v.strip()

    cands = [
        os.path.join(os.path.expanduser("~"), ".dbagent", "key.json"),
        os.path.join(os.path.expanduser("~"), ".dbagent", "agent_coder.json"),
    ]
    here = os.path.dirname(os.path.abspath(__file__))
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
            if isinstance(data, dict):
                for k in ("api_key", "key"):
                    v = data.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
        elif raw.startswith("sk-") or len(raw) > 20:
            return raw.splitlines()[0].strip()
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


def _stage_input_file(work: str, host_path: str, work_name: str = WORK_DIRNAME) -> str:
    """把宿主输入文件复制进沙箱分支 work 目录，返回沙箱内绝对路径。

    相对结构按路径里最后一段 work/ 之后的尾部保留（技能产出常用
    图片/<名字>/xx.jpg 这种子目录）；不在 work/ 下的文件取文件名平铺。
    copy2 保留 mtime，被搬进来的输入不会因时间戳更新而混进本步产出清单。
    """
    p = str(host_path).replace("\\", "/")
    if "/work/" in p:
        rel = p.rsplit("/work/", 1)[1].strip("/")
    else:
        rel = os.path.basename(p)
    dst = os.path.join(work, *rel.split("/")) if rel else os.path.join(work, os.path.basename(p))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.realpath(dst) != os.path.realpath(host_path):
        shutil.copy2(host_path, dst)
    return "/" + work_name + "/" + (rel or os.path.basename(p))


def translate_inputs(sandbox_root: str, script_path: str, argv,
                     key: str | None = None):
    """把 argv 里宿主上存在的文件搬进沙箱分支 work，路径改写为沙箱内路径。

    chroot 看不到宿主文件系统；上一步技能的产出在宿主 work/ 里，
    而 clear_work 已清空沙箱 work，不搬进来就无法作为本步输入。
    URL、选项、提示词等非文件参数原样保留。
    """
    work_name = _work_name(key)
    work = os.path.join(sandbox_root, work_name)
    out = []
    changed = False
    for a in argv:
        s = str(a)
        if (s.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", s)) and os.path.isfile(s):
            try:
                out.append(_stage_input_file(work, s, work_name))
                changed = True
                continue
            except OSError:
                pass
        out.append(a)
    return (out, changed) if changed else (list(argv), False)


def run_in_sandbox(sandbox_root: str, script_path: str, argv,
                   timeout: int = EXEC_TIMEOUT, on_line=None,
                   key: str | None = None, api_key: str | None = None):
    """chroot 执行脚本，返回 (returncode, stdout, stderr)。

    子进程接空的 stdin：技能脚本在无参数时可能读 stdin，不能让它抢占调用方的终端。
    on_line(stream, line)：逐行回调（stream 为 'out'/'err'），
    用于实时转发技能的阶段日志；不传则等进程结束后一次性返回。
    key：并行分支隔离，对应 work-<key> / skills-<key>。
    """
    work_name = _work_name(key)
    os.makedirs(os.path.join(sandbox_root, work_name), exist_ok=True)
    argv, _staged = translate_inputs(sandbox_root, script_path, argv, key)
    if script_path.endswith(".js"):
        # 用 sh -c 切到工作目录，否则脚本会把产出写到沙箱根目录
        cmd = ["chroot", sandbox_root, "/bin/sh", "-c",
               f"cd /{work_name} && exec /usr/bin/node {script_path} " +
               " ".join(shlex.quote(a) for a in argv)]
    else:
        cmd = ["chroot", sandbox_root, "/usr/bin/python3", "-I", "-c",
               _bootstrap(script_path, argv, work_name)]

    # 技能在沙箱内读不到宿主的 key 文件与 runtime.json，
    # 按白名单注入（同 win/sandbox.py）：
    #   AGNES_API_KEY/OPENAI_API_KEY -> 生图（主 Key 实测可调生图接口）
    #   DBAGENT_CHAT_KEY             -> 对话模型（visioncustom 等）
    #   DBAGENT_RUNTIME_CFG          -> 能力表路径（agnes-image 比例映射）
    env = dict(os.environ)
    try:
        # api_key 由调用方（Key 池）指定；未指定时回退到配置文件
        api = (api_key or "").strip() or _project_api_key()
        if api:
            env.setdefault("AGNES_API_KEY", api)
            env.setdefault("OPENAI_API_KEY", api)
            env.setdefault("DBAGENT_CHAT_KEY", api)
            env.setdefault("AGENT_API_KEY", api)
        cfg_path = _runtime_cfg_path()
        if cfg_path:
            env["DBAGENT_RUNTIME_CFG"] = cfg_path
        env.setdefault("PYTHONUTF8", "1")
        # print 默认全缓冲（非 tty），不开这个阶段日志就攒到最后才出来
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["DBAGENT_ROOT"] = "/dbagent"
    except Exception:
        pass

    try:
        return _run_streamed(cmd, env, timeout, on_line)
    except subprocess.TimeoutExpired:
        return 124, "", f"执行超时（>{timeout}s）"
    except FileNotFoundError as e:
        return 127, "", f"找不到可执行文件: {e}"


def _run_streamed(cmd, env, timeout, on_line):
    """Popen 执行，两个读线程分别收 stdout/stderr。

    逐行喂给 on_line 并同步累积成完整字符串，进程结束后
    返回值与旧 subprocess.run 版本完全一致。
    """
    import threading

    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL,
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
