#!/usr/bin/env python3
"""技能执行器 —— 把技能名解析为脚本，交给对应平台的沙箱执行。

本模块只做「与平台无关」的事：
  - 读取技能库（atoms/polypeptides/proteins 的 index.json）
  - 解析技能入口脚本（.py / .js）
  - 清洗模型传入的参数
  - 执行 STOPJ 工作流、调度工具

真正的隔离执行按平台分派：
  - Linux : platform/linux/sandbox.py  —— chroot 隔离（内核级）
  - Windows: platform/win/sandbox.py   —— 目录边界 + 环境变量白名单

新增平台只需在 platform/<name>/ 下实现同名接口：
  data_work_dir / ensure_sandbox / stage_skill / collect_outputs / run_in_sandbox
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import sys
import time

# ==================== 平台分派 ====================
IS_WINDOWS = os.name == "nt"
IS_TERMUX = ("com.termux" in (os.environ.get("PREFIX") or "")
             or bool(os.environ.get("TERMUX_VERSION")))
_PLATFORM_NAME = "win" if IS_WINDOWS else ("termux" if IS_TERMUX else "linux")


def _load_platform_sandbox():
    """按文件路径加载 plat/<os>/sandbox.py。

    不依赖包名与 sys.path 顺序：无论 skill_exec 是被 import 还是被
    runpy/exec 直接加载，都能定位到同目录下的平台实现。
    另用独立模块名注册，避免与标准库 platform 冲突。
    """
    import importlib.util

    here = os.path.dirname(os.path.abspath(__file__))
    # 本文件可能在 package/ 下，也可能在 package/skills/ 下：
    # 先看同级 plat/，再退到上一级 package/plat/
    candidates = [
        os.path.join(here, "plat", _PLATFORM_NAME, "sandbox.py"),
        os.path.join(os.path.dirname(here), "plat", _PLATFORM_NAME, "sandbox.py"),
    ]
    path = next((p for p in candidates if os.path.isfile(p)), candidates[0])
    if not os.path.isfile(path):
        raise ImportError(
            f"找不到平台沙箱实现: {path}"
            f"（当前平台: {os.name}）。请补齐 package/plat/{_PLATFORM_NAME}/sandbox.py。"
        )
    name = f"dbagent_plat_{_PLATFORM_NAME}_sandbox"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载平台沙箱: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


_sandbox = _load_platform_sandbox()

WORK_DIRNAME = "work"
# 默认单技能预算。抓取类技能受网络影响可能跑两三分钟
# （web-summarizer 实测 150~180s），120s 会被误杀。
# 个别更长的技能可在 skill.json 里用 "timeout": 600 单独声明。
EXEC_TIMEOUT = 300
MAX_OUTPUT_CHARS = 4000


def _declared_timeout(skill_dir, default=EXEC_TIMEOUT):
    """读取技能在 skill.json 里声明的超时；没有就用默认值。"""
    if not skill_dir:
        return default
    path = os.path.join(skill_dir, "skill.json")
    try:
        # utf-8-sig：Windows 下手工编辑的 json 常带 BOM，
        # 用 utf-8 读会 ValueError，导致声明的 timeout 被忽略
        with open(path, encoding="utf-8-sig") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return default
    if not isinstance(meta, dict):
        return default
    for key in ("timeout", "timeout_seconds", "exec_timeout"):
        v = meta.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return default


# ==================== 平台无关的薄封装 ====================
def data_work_dir(data_dir: str) -> str:
    """<data>/work —— 技能产出目录。"""
    return _sandbox.data_work_dir(data_dir)


def ensure_sandbox(key=None) -> str:
    """构建（或复用）沙箱根，返回沙箱根路径。

    key 指定时按分支分实例（并行超蛋白），为空用共享根。
    """
    return _sandbox.ensure_sandbox(key)


def stage_skill(sandbox_root: str, skills_dir: str, entry: str, key=None) -> str:
    """把技能目录复制进沙箱，返回脚本在沙箱内的路径（key 用于分支隔离）。"""
    return _sandbox.stage_skill(sandbox_root, skills_dir, entry, key=key)


def collect_outputs(sandbox_root: str, target_dir: str, key=None):
    """把沙箱分支 work 里的文件同步到 <data>/work。"""
    return _sandbox.collect_outputs(sandbox_root, target_dir, key=key)


def list_work_files(sandbox_root: str, since=None, key=None):
    """列出本分支 work 里本次新增的文件（相对路径），供 fan-out 展开用。"""
    return _sandbox.list_work_files(sandbox_root, since=since, key=key)


def sync_persist(sandbox_root: str, script_rel: str, skill_dir: str):
    """把沙箱内技能的 data/ 变更写回宿主技能目录。

    有状态技能（tracker 之类）需要跨次保留数据，否则每次执行都从零开始。
    平台实现未提供时直接跳过。
    """
    fn = getattr(_sandbox, "sync_back_persist", None)
    if fn is None:
        return []
    try:
        return fn(sandbox_root, script_rel, skill_dir)
    except Exception:
        return []


def clear_work(sandbox_root: str, key=None) -> None:
    """清空沙箱分支 work，避免上一次的产出混进本次结果。

    平台实现若未提供 clear_work，则退化为逐个删除沙箱 work/ 下的条目。
    """
    fn = getattr(_sandbox, "clear_work", None)
    if fn is not None:
        fn(sandbox_root)
        return
    work = os.path.join(sandbox_root, WORK_DIRNAME)
    if not os.path.isdir(work):
        return
    import shutil
    for name in os.listdir(work):
        p = os.path.join(work, name)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
        except OSError:
            pass


def run_in_sandbox(sandbox_root: str, script_path: str, argv, timeout: int = EXEC_TIMEOUT,
                   on_line=None, key=None, api_key=None):
    """在沙箱内执行脚本，返回 (returncode, stdout, stderr)。

    api_key 指定时用它作为本步的 API Key（Key 池轮转由调用方决定）。
    """
    return _sandbox.run_in_sandbox(sandbox_root, script_path, argv, timeout=timeout,
                                   on_line=on_line, key=key, api_key=api_key)


# ==================== 技能库 ====================
class SkillLibrary:
    """技能运行时：统一管理分散在多个根目录下的技能。

    skills_dirs 可以是单个目录，也可以是目录列表。查找技能时按顺序
    取第一个命中者，因此 workspace/skills 优先，runtime/skillexe 作为
    补充来源（原先这 6 个技能只在 skillexe 里，运行时看不到）。
    """

    def __init__(self, data_dir, skills_dirs):
        self.data_dir = data_dir
        if isinstance(skills_dirs, (str, os.PathLike)):
            skills_dirs = [skills_dirs]
        # 去重并保留顺序
        seen = set()
        roots = []
        for d in skills_dirs:
            if not d:
                continue
            real = os.path.realpath(str(d))
            if real in seen:
                continue
            seen.add(real)
            roots.append(real)
        self.skills_dirs = roots
        # 兼容旧代码：skills_dir 指向主目录
        self.skills_dir = roots[0] if roots else ""

        self.atoms = self._load_index("atoms")
        self.polypeptides = self._load_index("polypeptides")
        self.proteins = self._load_index("proteins")
        self._by_skill = {a.get("skill"): a for a in self.atoms if a.get("skill")}
        self._dir_of = self._scan_skill_dirs()

    def _scan_skill_dirs(self):
        """扫描各根目录，建立 技能名 -> 实际目录 的映射。"""
        mapping = {}
        for root in self.skills_dirs:
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                path = os.path.join(root, name)
                if not os.path.isdir(path):
                    continue
                if name not in mapping:  # 先到先得
                    mapping[name] = path
        return mapping

    def _load_index(self, layer):
        path = os.path.join(self.data_dir, layer, "index.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f).get(layer) or []

    def skill_names(self):
        """返回可用技能名：索引登记的 ∪ 磁盘上真实存在的目录。"""
        return sorted(set(self._by_skill) | set(self._dir_of))

    def skill_dir(self, skill):
        """技能的实际目录（跨根目录查找）。"""
        return self._dir_of.get(skill)

    def atom_of(self, skill):
        return self._by_skill.get(skill)

    def describe(self, skill):
        atom = self.atom_of(skill)
        if not atom:
            # 未登记在 atoms 索引，但磁盘上有目录，也给出基本信息
            path = self.skill_dir(skill)
            if path:
                return {"skill": skill, "name": skill, "type": None,
                        "description": self._read_description(path)}
            return None
        return {
            "skill": skill,
            "name": atom.get("name_cn") or atom.get("name_en") or skill,
            "type": atom.get("type"),
            "description": atom.get("description"),
        }

    @staticmethod
    def _read_description(skill_dir_path):
        """从 SKILL.md 的 front-matter 里取 description。"""
        md = os.path.join(skill_dir_path, "SKILL.md")
        if not os.path.isfile(md):
            return None
        try:
            with open(md, encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("description:"):
                        return line.split(":", 1)[1].strip().strip("'\"")
        except OSError:
            pass
        return None

    def usage_hint(self, skill):
        """从 SKILL.md 的快捷命令段提取用法提示，供模型构造参数。"""
        skill_dir = self.skill_dir(skill) or os.path.join(self.skills_dir, skill)
        path = os.path.join(skill_dir, "SKILL.md")
        if not os.path.exists(path):
            return ""
        lines = []
        pending = ""
        in_block = False
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("```"):
                        in_block = not in_block
                        continue
                    if not in_block:
                        continue
                    # 反斜杠续行要拼成完整命令，否则模型只能看到半条
                    if pending:
                        stripped = pending + " " + stripped
                        pending = ""
                    if stripped.endswith("\\"):
                        pending = stripped[:-1].rstrip()
                        continue
                    if stripped.startswith(("python3 ", "python ", "node ", "/")):
                        lines.append("      " + stripped)
        except OSError:
            return ""
        if not lines:
            return ""
        return "    用法：\n" + "\n".join(lines[:8])


# ==================== 入口解析 ====================
# 辅助模块标记：函数/常量名命中这些的脚本，视为被主脚本 import 的库，
# 不当成入口候选（如 person-image-search/readtxt.py 提供 search_images()）。
_HELPER_MARKERS = ("def crawl", "def search_images", "def fetch(", "def get(",
                   "class Paginator")


def _looks_like_helper(path):
    """粗判脚本是不是辅助模块（没有 main 入口、只有函数定义）。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            src = f.read(8000)
    except OSError:
        return False
    # 有 __main__ 就是可独立运行的入口
    if "__main__" in src:
        return False
    return any(m in src for m in _HELPER_MARKERS)


def resolve_entry(skills_dir, skill):
    """按 skill.json 的 main，或 scripts/ 下同名/唯一脚本确定入口。

    优先级：
      1. skill.json 的 main / entry / scripts.<name> 字段（显式声明最可靠）
      2. scripts/ 下与技能同名的脚本
      3. 排除辅助模块后只剩一个脚本
      4. 仍然多个时，选带 __main__ 的那个（有 CLI 入口的才算主脚本）
    """
    skill_dir = os.path.join(skills_dir, skill)
    if not os.path.isdir(skill_dir):
        return None, None

    # ---- 1) skill.json 显式声明 ----
    meta_path = os.path.join(skill_dir, "skill.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = {}
        for key in ("main", "entry"):
            rel = meta.get(key)
            if isinstance(rel, str) and rel:
                candidate = os.path.join(skill_dir, rel)
                if os.path.isfile(candidate):
                    return candidate, None
        scripts = meta.get("scripts")
        if isinstance(scripts, str) and scripts:
            candidate = os.path.join(skill_dir, scripts)
            if os.path.isfile(candidate):
                return candidate, None
        if isinstance(scripts, dict) and scripts:
            # {"name": "scripts/x.py"} —— 取第一个可用的
            for rel in scripts.values():
                if not isinstance(rel, str):
                    continue
                candidate = os.path.join(skill_dir, rel)
                if os.path.isfile(candidate):
                    return candidate, None

    # 兼容两种目录名：多数技能用 scripts/，pdr-timer 用 script/
    scripts_dir = os.path.join(skill_dir, "scripts")
    if not os.path.isdir(scripts_dir):
        scripts_dir = os.path.join(skill_dir, "script")
    if not os.path.isdir(scripts_dir):
        return None, None

    # 同时认 .py 与 .js：pptx 这类技能入口是 node 脚本
    entries = sorted(
        name for name in os.listdir(scripts_dir)
        if name.endswith((".py", ".js")) and name != "__init__.py" and not name.startswith("_")
    )
    if not entries:
        return None, None

    # ---- 2) 与技能同名的脚本 ----
    for name in entries:
        if os.path.splitext(name)[0] == skill:
            return os.path.join(scripts_dir, name), None

    # ---- 3) 排除辅助模块后唯一 ----
    mains = [n for n in entries
             if not _looks_like_helper(os.path.join(scripts_dir, n))]
    if len(mains) == 1:
        return os.path.join(scripts_dir, mains[0]), None

    # ---- 4) 仍有多个：优先带 __main__ 的 ----
    runnable = []
    for n in mains or entries:
        p = os.path.join(scripts_dir, n)
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                if "__main__" in f.read(8000):
                    runnable.append(n)
        except OSError:
            continue
    pool = runnable or mains or entries
    if len(pool) == 1:
        return os.path.join(scripts_dir, pool[0]), None
    return None, pool


def _strip_interpreter(entry, argv):
    """去掉模型误加的解释器和脚本路径，只留真正参数。"""
    argv = list(argv or [])
    script_name = os.path.basename(entry)
    while argv:
        head = str(argv[0])
        if head in ("python", "python3", "node", "bash", "sh"):
            argv.pop(0)
            continue
        if os.path.basename(head) == script_name:
            argv.pop(0)
            continue
        break
    return argv


def execute_skill(library, skill, args=None, timeout=None, on_line=None,
                  sandbox_key=None, api_key=None):
    """在沙箱内执行一个技能，返回结果字典。

    timeout=None 时按 skill.json 的 "timeout" 取值，缺省用 EXEC_TIMEOUT。
    """
    skill_dir = library.skill_dir(skill) if hasattr(library, "skill_dir") else None
    if skill_dir is None and library.atom_of(skill) is None:
        return {"ok": False, "skill": skill,
                "error": f"技能 {skill} 不在技能库中",
                "available": library.skill_names()}
    if timeout is None:
        timeout = _declared_timeout(skill_dir)

    entry, ambiguous = resolve_entry(os.path.dirname(skill_dir), skill) \
        if skill_dir else resolve_entry(library.skills_dir, skill)
    if entry is None:
        if ambiguous:
            return {"ok": False, "skill": skill,
                    "error": f"技能 {skill} 有多个脚本，无法确定入口: {', '.join(ambiguous)}"}
        return {"ok": False, "skill": skill,
                "error": f"技能 {skill} 没有可执行脚本"}

    root = ensure_sandbox(sandbox_key)
    # 执行前清空本分支 work：否则历次产出会累积，outputs 无法反映本次结果
    clear_work(root, sandbox_key)
    skills_root = os.path.dirname(skill_dir)
    inner = stage_skill(root, skills_root, entry, key=sandbox_key)
    argv = _strip_interpreter(entry, args)
    start = time.time()
    code, out, err = run_in_sandbox(root, inner, argv, timeout=timeout,
                                    on_line=on_line, key=sandbox_key, api_key=api_key)
    pairs = collect_outputs(root, data_work_dir(library.data_dir), key=sandbox_key)
    try:
        new_src = set(list_work_files(root, since=start, key=sandbox_key))
    except Exception:
        new_src = set()
    files = [dst for src_rel, dst in pairs if not new_src or src_rel in new_src]
    outputs = sorted({dst.split("/")[0] for _src, dst in pairs})
    # 有状态技能：把沙箱内 data/ 的变更写回技能自身的 data/，否则记录会丢
    persisted = []
    try:
        persisted = sync_persist(root, inner, skill_dir)
    except Exception:
        persisted = []
    return {
        "ok": code == 0,
        "skill": skill,
        "entry": os.path.relpath(entry, skills_root),
        "exit_code": code,
        "elapsed": round(time.time() - start, 2),
        "stdout": out[-MAX_OUTPUT_CHARS:],
        "stderr": err[-MAX_OUTPUT_CHARS:],
        "outputs": outputs,
        "files": files,
        "persisted": persisted,
        "sandbox": data_work_dir(library.data_dir),
    }


# ==================== 工作流执行 ====================
def _stopj():
    from . import stopj
    return stopj


def stopj_parse(expr, known_skills=None):
    """复用 stopj 的表达式解析器。"""
    return _stopj().parse_stopj_expression(expr, known_skills)


def execute_stopj(expression, library, max_steps=200, max_loop_iterations=100,
                  skill_callback=None, extra_names=None, loop_hook=None):
    """执行 STOPJ 工作流；每个步骤通过 skill_callback 真正执行。

    extra_names 用于告知分词器除技能名之外还可识别的名字（如工具名），
    否则 readeditbash 会被当成一个完整名字。
    loop_hook(ctx) 在 O（迭代入口）与 T 回跳时调用，返回 dict 即注入上下文，
    后续步骤通过 skill_callback 的 context["injected"] 读到。

    返回每个步骤的结果文本列表。
    """
    names = list(library.skill_names()) + list(extra_names or [])
    steps, sites = stopj_parse(expression, names)
    ip = 0
    count = 0
    loop_stack = []
    results = []
    runtime_ctx = {}

    def _fire_loop_hook(phase, indent):
        """迭代边界：允许规划层注入/编辑上下文（如改写后的提示词）。"""
        if not loop_hook:
            return
        try:
            out = loop_hook({"phase": phase, "results": list(results),
                             "context": dict(runtime_ctx)})
        except Exception:
            out = None
        if isinstance(out, dict) and out:
            runtime_ctx.update(out)
            note = str(out.get("note") or "")
            if note:
                print(f"{indent}[O] {note}")

    print(f"\n=== 执行工作流: {expression} ===")
    while ip < len(steps) and count < max_steps:
        step = steps[ip]
        stype = step["type"]
        indent = "  " * len(loop_stack)

        if stype == "S":
            print(f"{indent}[S] 启动")
        elif stype == "P":
            budget = step.get("value", 0)
            print(f"{indent}[P] 规划 {budget} 步" if budget else f"{indent}[P] 规划")
        elif stype == "O":
            rounds = step.get("value", 0)
            if rounds > 0:
                if rounds > max_loop_iterations:
                    print(f"{indent}[O] 循环 {rounds} 次超过上限，截断为 {max_loop_iterations}")
                    rounds = max_loop_iterations
                loop_stack.append({"start_ip": ip + 1, "remaining": rounds})
                print(f"{indent}[O] 进入循环，剩余 {rounds} 次")
                _fire_loop_hook("loop_start", indent)
            else:
                print(f"{indent}[O] 跳过无效循环（次数为0）")
        elif stype == "T":
            if loop_stack:
                loop_stack[-1]["remaining"] -= 1
                if loop_stack[-1]["remaining"] > 0:
                    ip = loop_stack[-1]["start_ip"] - 1
                    print(f"{indent}[T] 继续循环，剩余 {loop_stack[-1]['remaining']} 次")
                    _fire_loop_hook("iterate", indent)
                else:
                    loop_stack.pop()
                    print(f"{indent}[T] 循环结束")
            else:
                print(f"{indent}[T] 闸门检查（无活动循环）")
        elif stype == "DEF_SITE":
            print(f"{indent}[位点 {step['site']}]")
        elif stype == "JUMP":
            site_id = step["site"]
            if site_id in sites:
                ip = sites[site_id] + 1
                print(f"{indent}[J] 跳转到位点 {site_id}")
                continue
            print(f"{indent}[错误] 未定义位点 {site_id}，停止")
            break
        elif stype == "SKILL":
            skill = step["skill"]
            if skill_callback:
                # prev 传上一步的完整结果，供「上一步产出作为下一步输入」
                # 这类链使用（如 找图 → 给找到的图加字）
                prev = results[-1] if results else ""
                result = skill_callback(skill, {
                    "step": count,
                    "loop_depth": len(loop_stack),
                    "prev": prev,
                    "injected": dict(runtime_ctx),
                })
                results.append(result)
                print(f"{indent}[技能] {skill} → {result.splitlines()[0] if result else ''}")
            else:
                print(f"{indent}[技能] {skill} → 未提供执行回调，跳过")
        else:
            print(f"{indent}[未知] {step}")

        ip += 1
        count += 1

    if count >= max_steps:
        print(f"⚠️ 达到最大步数 {max_steps}，强制停止")
    if loop_stack:
        print(f"⚠️ 仍有未完成循环（剩余 {loop_stack[-1]['remaining']} 次）")
    print("=== 执行结束 ===\n")
    return results


# ==================== 工具集调度 ====================
TOOL_COMMANDS = ("read", "write", "edit", "ls", "grep", "find", "bash")


def _toolset():
    """加载 tools/sandbox/toolset.py。"""
    here = os.path.dirname(os.path.abspath(__file__))
    tool_dir = os.path.join(here, "tools", "sandbox")
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    import toolset
    return toolset


def execute_tool(command, argv, workdir):
    """执行一个工具子命令，捕获其 JSON 输出并返回结果字典。

    失败时必须带回可读原因：argparse 的报错走 stderr，
    早期版本没捕获它，模型只看到「[失败] 未知错误」，
    不知道是参数问题，于是反复重试同一调用直至被防重复拦截。
    """
    toolset = _toolset()
    buffer = io.StringIO()
    err_buffer = io.StringIO()
    start = time.time()
    code = 1
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(err_buffer):
            code = toolset.main(["--workdir", workdir, command] + list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception as e:  # 工具内部异常也要回传，不让整条工作流中断
        return {"ok": False, "tool": command, "args": list(argv),
                "elapsed": round(time.time() - start, 2),
                "error": f"{type(e).__name__}: {e}"}
    raw = buffer.getvalue().strip()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {"raw": raw}
    payload.setdefault("ok", code == 0)
    payload["tool"] = command
    payload["args"] = list(argv)
    payload["elapsed"] = round(time.time() - start, 2)

    # 失败且没有原因时，把 stderr 里的 usage/报错带出去
    stderr_text = err_buffer.getvalue().strip()
    if not payload.get("ok") and not payload.get("error") and stderr_text:
        # 只保留最后两行（argparse 的 usage 很长，末行才是真正原因）
        lines = [ln.strip() for ln in stderr_text.splitlines() if ln.strip()]
        payload["error"] = " / ".join(lines[-2:])[:300]
    return payload
