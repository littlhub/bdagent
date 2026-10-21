"""技能运行时 —— 规划注入 / 执行 / 收尾三个阶段。

定位
----
技能运行时是「技能库和工具调用之前」的一层，负责三件事：

    规划注入  before_run()   规划阶段把技能链、预算、约束注入运行时上下文
    执行观察  record_step()  每个步骤结束后记录用时与结果
    结束收尾  finish()       汇总统计、写回追踪记录、输出执行报告

它本身不执行技能——执行仍由 skill_exec 完成。这一层让规划的结果
可以被带进执行、执行的过程可以被记录、执行完可以收尾报告，
对应 skill-planner / skill-runner / skill-tracker 三个技能原本的意图，
但改为在进程内直接调用，不再 spawn 子进程，也不再依赖手机端路径。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RuntimeContext:
    """规划阶段产出的执行上下文，注入给执行器。"""

    requirement: str = ""
    # 规划给出的技能链（按顺序执行）
    skill_chain: list[str] = field(default_factory=list)
    # 每步的调用参数：{技能名: [参数...]}
    args: dict[str, list[str]] = field(default_factory=dict)
    # token 预算（-1 表示未知）
    budget: int = -1
    # 规划来源：local（规则库）/ llm（模型）/ stopj（直接表达式）
    source: str = ""
    # 表达式（若规划已给出）
    expression: str = ""
    # 约束与提示，注入到系统提示或执行日志
    constraints: list[str] = field(default_factory=list)

    def describe(self) -> str:
        parts = [f"来源={self.source or '未指定'}"]
        if self.skill_chain:
            parts.append("链=" + " → ".join(self.skill_chain))
        if self.budget >= 0:
            parts.append(f"预算≈{self.budget} tokens")
        return " | ".join(parts)


@dataclass
class StepRecord:
    """单个执行步骤的记录。"""

    name: str
    ok: bool
    elapsed: float
    detail: str = ""


class SkillRuntime:
    """技能运行时：持有一次执行的上下文与记录。"""

    def __init__(self, tracker_dir: str | None = None):
        self.context: RuntimeContext | None = None
        self.steps: list[StepRecord] = []
        self.started_at: float = 0.0
        self.tracker_dir = tracker_dir or self._default_tracker_dir()

    @staticmethod
    def _default_tracker_dir() -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "..", "runtime", "skillexe", "skill-tracker")

    # ---------- 阶段一：规划注入 ----------
    def before_run(self, context: RuntimeContext) -> RuntimeContext:
        """接收规划结果，做入口校验，返回规整后的上下文。"""
        self.context = context
        self.steps = []
        self.started_at = time.time()

        # 校验技能名，剔除不存在的项（避免执行到一半才失败）
        context.constraints = list(context.constraints or [])
        return context

    # ---------- 阶段二：执行观察 ----------
    def record_step(self, name: str, ok: bool, elapsed: float, detail: str = "") -> None:
        self.steps.append(StepRecord(name=name, ok=ok, elapsed=elapsed,
                                     detail=(detail or "")[:200]))

    # ---------- 阶段三：结束收尾 ----------
    def finish(self, write_tracker: bool = True) -> dict:
        """汇总本次执行，可选写回 skill-tracker 的历史记录。"""
        total = round(time.time() - self.started_at, 2) if self.started_at else 0.0
        ok_count = sum(1 for s in self.steps if s.ok)
        summary = {
            "requirement": (self.context.requirement if self.context else "")[:80],
            "source": self.context.source if self.context else "",
            "steps": len(self.steps),
            "ok": ok_count,
            "failed": len(self.steps) - ok_count,
            "elapsed": total,
        }

        # 写回追踪记录：直接追加到 tracker 的历史文件，不 spawn 子进程
        if write_tracker and self.steps:
            try:
                self._append_history(summary)
            except Exception:
                pass

        self._report(summary)
        return summary

    def _append_history(self, summary: dict) -> None:
        data_dir = os.path.join(self.tracker_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, "history.json")
        data = {"history": []}
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded.get("history"), list):
                    data = loaded
            except (OSError, ValueError):
                pass
        names = [s.name for s in self.steps]
        data["history"].append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "skills": names,
            "ok": summary["failed"] == 0,
            "duration": summary["elapsed"],
            "tokens": 0,
            "source": summary["source"],
        })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _report(self, summary: dict) -> None:
        if not self.steps:
            return
        mark = "✅" if summary["failed"] == 0 else "⚠️"
        print(f"\n{mark} 执行收尾：{summary['steps']} 步，"
              f"成功 {summary['ok']}，失败 {summary['failed']}，"
              f"共 {summary['elapsed']}s（规划来源: {summary['source'] or '未知'}）")
        for s in self.steps:
            flag = "✓" if s.ok else "✗"
            print(f"   {flag} {s.name:<24} {s.elapsed:>6.2f}s")


__all__ = ["RuntimeContext", "StepRecord", "SkillRuntime"]
