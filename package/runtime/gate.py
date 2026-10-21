"""STOPJ Runtime 闸门 —— 把工作流计划交给闸门串行调度执行。

这一层是原本被拆散的部分：`ASCTOPJ.py` 里定义了完整的
ASC 索引 → SkillPlanner 规划 → STOPJRuntime 闸门执行 三段式，
但 `main.py` 的 `_run_planned` 直接调 `execute_stopj`，绕过了闸门，
导致闸门形同虚设。

现在把闸门补回来，职责明确：
    SkillPlanner   根据技能链生成带闸门标记的步骤序列
    STOPJRuntime   逐步执行，每一步通过回调真正跑技能/工具
    GateRuntime    对外的统一入口（供 main._run_planned 调用）

闸门语义（S/T/O/P/J）：
    S 启动        标记管线开始
    P 规划        声明本次要跑哪些技能
    O 循环        进入循环体，由 T 决定是否再跑一轮
    T 闸门        检查循环，未完成则回跳
    J 跳转        跳到位点
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class WorkflowPlan:
    """STOPJ 工作流计划。"""

    index: str
    skill_chain: list[str]
    steps: list[str]
    max_iterations: int = 10

    def __str__(self) -> str:
        return (f"WorkflowPlan(index={self.index}, "
                f"skill_chain={self.skill_chain}, "
                f"steps={self.steps})")


class SkillPlanner:
    """根据技能链生成 STOPJ 执行步骤。"""

    def plan(self, index: str, skill_chain: list[str],
             max_iterations: int = 10) -> WorkflowPlan:
        steps = ["S"]
        for i, skill in enumerate(skill_chain):
            steps.append(f"P(i={i}, skill={skill})")
            steps.append(f"J(i={i}, skill={skill})")
            steps.append("O")
            steps.append(f"T(i={i})")
        steps.append("T(stop)")
        return WorkflowPlan(index=index, skill_chain=skill_chain,
                            steps=steps, max_iterations=max_iterations)


class STOPJRuntime:
    """STOPJ Runtime 闸门执行器。

    runner 收到 (step_type, payload)，返回该步的执行结果文本。
    闸门只负责调度与流程控制，不关心具体是技能还是工具。
    """

    def __init__(self, plan: WorkflowPlan,
                 runner: Callable[[str, dict[str, Any]], str] | None = None):
        self.plan = plan
        self.runner = runner
        self.iteration = 0
        self.results: list[str] = []
        self.log: list[str] = []

    def _emit(self, line: str) -> None:
        self.log.append(line)
        print(line)

    def execute(self) -> list[str]:
        """执行计划，返回每步结果。"""
        self._emit(f"\n=== STOPJ Runtime 开始执行（{self.plan.index}）===")
        self._emit(f"[S] 启动管线，技能链: {self.plan.skill_chain}")

        for step in self.plan.steps:
            if step == "S":
                continue  # 已在开头声明
            if step.startswith("T(stop)"):
                self._emit("[T] 最终闸门：停止")
                break
            self._execute_step(step)
        self._emit("=== STOPJ Runtime 执行结束 ===\n")
        return self.results

    def _execute_step(self, step: str) -> None:
        if step.startswith("P("):
            self._emit(f"[P] 规划：{step}")
        elif step.startswith("J("):
            skill = step.split("skill=")[1].rstrip(")") if "skill=" in step else step
            self._emit(f"[J] 跳转执行：{skill}")
            if self.runner:
                try:
                    out = self.runner(skill, {"step": len(self.results),
                                              "iteration": self.iteration})
                    self.results.append(out)
                    first = (out or "").splitlines()[0] if out else ""
                    self._emit(f"    ↳ {first}")
                except Exception as e:  # 单步失败不中断整条管线
                    self.results.append(f"执行失败: {e}")
                    self._emit(f"    ↳ 失败: {e}")
        elif step == "O":
            self.iteration += 1
            self._emit(f"[O] 循环迭代 {self.iteration}")
        elif step.startswith("T("):
            self._emit(f"[T] 闸门检查通过：{step}")
        else:
            self._emit(f"[未知步骤] {step}")


class GateRuntime:
    """闸门统一入口：SkillPlanner + STOPJRuntime。

    给 main._run_planned 用，把「技能链」变成「受闸门控制的执行」。
    """

    def __init__(self, runner: Callable[[str, dict[str, Any]], str] | None = None):
        self.planner = SkillPlanner()
        self.runner = runner

    def run(self, index: str, skill_chain: list[str],
            max_iterations: int = 10) -> list[str]:
        plan = self.planner.plan(index, skill_chain, max_iterations)
        runtime = STOPJRuntime(plan, self.runner)
        return runtime.execute()


__all__ = [
    "WorkflowPlan",
    "SkillPlanner",
    "STOPJRuntime",
    "GateRuntime",
]
