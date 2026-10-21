# -*- coding: utf-8 -*-
"""超蛋白编排 —— 串行 / 并行、同构 / 异构组合的调度器。

超蛋白 = 多个蛋白（或同一蛋白的多份实例）按运行时模式组合：
    SERIAL    分支顺序执行，后者可消费前者的产出
    PARALLEL  分支并发执行，结果合并
同构（homogeneous）= 同一蛋白重复多份（如多人复刻：APV×N）；
异构（heterogeneous）= 不同蛋白组合（如复刻 + 联网摘要）。

分支的执行细节由调用方通过 branch_runner 提供：
    branch_runner(branch, index, prev) -> (ok, detail)
调用方负责分支内的技能调度、参数推导与沙箱隔离，这一层只管编排。
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


@dataclass
class SuperproteinPlan:
    """一次超蛋白编排计划。"""

    name: str
    mode: str = "SERIAL"            # SERIAL / PARALLEL
    homogeneous: bool = False       # 同构：同一蛋白多份
    branches: list[dict] = field(default_factory=list)  # [{"chain": [...], "requirement": str}]
    match: str = ""

    def __str__(self) -> str:
        return (f"SuperproteinPlan(name={self.name}, mode={self.mode}, "
                f"homogeneous={self.homogeneous}, branches={len(self.branches)})")


def load_composites(data_dir: str) -> list[dict]:
    """读取显式声明的超蛋白组合（数据驱动，缺文件返回空）。"""
    path = os.path.join(data_dir, "superproteins", "composites.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("composites") or []
    except (OSError, ValueError):
        return []


def _load_protein_chains(data_dir: str) -> dict:
    """gene_chain -> skill_chain，供 composites 用蛋白代号声明。"""
    path = os.path.join(data_dir, "proteins", "index.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            prots = json.load(f).get("proteins") or []
    except (OSError, ValueError):
        return {}
    return {str(p.get("gene_chain")): list(p.get("skill_chain") or [])
            for p in prots if p.get("gene_chain") and p.get("skill_chain")}


def _split_requirements(req: str, count: int) -> list[str]:
    """按「并/然后/再/同时」拆需求，供异构串行的各分支各自推导参数。"""
    parts = [p.strip() for p in re.split(r"并|然后|再|同时|，", req or "") if p.strip()]
    if len(parts) >= count:
        return parts[:count]
    return [req] * count


def match_composite(req: str, data_dir: str) -> SuperproteinPlan | None:
    """按 keywords（正则）匹配显式声明的超蛋白。"""
    req = (req or "").strip()
    if not req:
        return None
    protein_chains = _load_protein_chains(data_dir)
    for comp in load_composites(data_dir):
        for kw in comp.get("keywords") or []:
            try:
                if not re.search(kw, req):
                    continue
            except re.error:
                continue
            chains = []
            for p in comp.get("proteins") or []:
                if isinstance(p, (list, tuple)):
                    chains.append(list(p))
                else:
                    chain = protein_chains.get(str(p))
                    if chain:
                        chains.append(chain)
            if not chains:
                continue
            reqs = _split_requirements(req, len(chains))
            return SuperproteinPlan(
                name=str(comp.get("name_cn") or comp.get("name") or "超蛋白"),
                mode=str(comp.get("mode") or "SERIAL").upper(),
                homogeneous=bool(comp.get("homogeneous")),
                branches=[{"chain": c, "requirement": r}
                          for c, r in zip(chains, reqs)],
                match=f"超蛋白:{comp.get('name_cn') or comp.get('name')}",
            )
    return None


class SuperproteinRuntime:
    """串行/并行执行超蛋白分支，并合并结果。"""

    def __init__(self, branch_runner, max_workers: int = 3):
        self.branch_runner = branch_runner
        self.max_workers = max_workers

    def run(self, plan: SuperproteinPlan):
        """返回 (全部成功, 合并后的可读结果)。"""
        kind = "同构" if plan.homogeneous else "异构"
        print(f"\n[SP] 超蛋白编排：{plan.name}（{kind}/{plan.mode}，{len(plan.branches)} 个分支）")
        results = self._run_parallel(plan) if plan.mode.upper() == "PARALLEL" \
            else self._run_serial(plan)
        return self._merge(plan, results)

    def _run_serial(self, plan):
        results = []
        prev = ""
        for i, br in enumerate(plan.branches, 1):
            print(f"[SP] 分支 {i}/{len(plan.branches)}（串行）：{' → '.join(br['chain'])}")
            try:
                out = self.branch_runner(br, i, prev)
                if len(out) == 3:
                    ok, detail, prev_text = out
                    prev = prev_text or detail
                else:
                    ok, detail = out
                    prev = detail
            except Exception as e:
                ok, detail = False, f"分支异常: {type(e).__name__}: {e}"
            results.append((i, ok, detail))
        return results

    def _run_parallel(self, plan):
        results = []
        workers = max(1, min(self.max_workers, len(plan.branches)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            for i, br in enumerate(plan.branches, 1):
                print(f"[SP] 并行分支 {i}/{len(plan.branches)}：{' → '.join(br['chain'])}")
                futures.append((i, pool.submit(self.branch_runner, br, i, "")))
            for i, fut in futures:
                try:
                    out = fut.result()
                    if len(out) == 3:
                        ok, detail, _prev_text = out
                    else:
                        ok, detail = out
                except Exception as e:
                    ok, detail = False, f"分支异常: {type(e).__name__}: {e}"
                results.append((i, ok, detail))
        return results

    def _merge(self, plan: SuperproteinPlan, results):
        ok_count = sum(1 for _, good, _ in results if good)
        print(f"[SP] 完成：{ok_count}/{len(results)} 个分支成功")
        parts = []
        for i, good, detail in sorted(results, key=lambda x: x[0]):
            mark = "✓" if good else "✗"
            parts.append(f"──── 分支 {i} {mark} ────\n{detail}")
        return ok_count == len(results), "\n\n".join(parts)


__all__ = [
    "SuperproteinPlan",
    "SuperproteinRuntime",
    "load_composites",
    "match_composite",
]
