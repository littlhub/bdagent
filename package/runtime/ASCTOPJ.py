"""
完整系统：生物数据库 + ASC 索引 + STOPJ Runtime 闸门工作流

功能：
1. 初始化数据库（原子、多肽、蛋白记录）
2. 实现 ASC 索引规则（蛋白→多肽→自组织技能→Skill-Creater）
3. 提供大模型解析模拟接口（当本地数据库无记录时使用）
4. 生成并执行 STOPJ 工作流（S→P→J→O→T）
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Union
import hashlib
import random


# ==================== 数据模型 ====================

@dataclass
class AtomRecord:
    index: str
    skill: str
    name: str
    nickname: str
    abbreviation: str


@dataclass
class PeptideRecord:
    index: str
    skill_chain: List[str]          # skill链
    gene_chain: str                 # 基因链 1-2-3
    atcg_gene_chain: str            # ATCG基因链
    hash: str                       # 指纹


@dataclass
class ProteinRecord:
    index: str
    fold_tree: Dict[str, Any]       # 多肽折叠图 tree（简化为字典）
    peptide_gene_path: str          # 多肽基因链路径，如 "start-2-3-1-5-stop"


class BioDatabase:
    """内存数据库，存储三层记录"""
    def __init__(self):
        self.atoms: Dict[str, AtomRecord] = {}
        self.peptides: Dict[str, PeptideRecord] = {}
        self.proteins: Dict[str, ProteinRecord] = {}

    def add_atom(self, atom: AtomRecord):
        self.atoms[atom.index] = atom

    def add_peptide(self, pep: PeptideRecord):
        self.peptides[pep.index] = pep

    def add_protein(self, prot: ProteinRecord):
        self.proteins[prot.index] = prot


# ==================== 大模型解析模拟 ====================

class LLMResolver:
    """
    模拟大模型解析器，用于本地数据库无记录时生成技能链。
    实际使用时，可替换为真实大模型 API 调用。
    """
    def __init__(self):
        # 模拟一些预定义技能
        self.skill_pool = ["skill_X", "skill_Y", "skill_Z", "skill_W"]

    def generate_skill_chain(self, query: str, context: Optional[Dict] = None) -> List[str]:
        """根据查询生成技能链（模拟）"""
        print(f"[LLM] 调用大模型解析，查询: {query}")
        # 简单模拟：从技能池中随机选几个技能
        n = random.randint(2, 4)
        chain = random.sample(self.skill_pool, n)
        print(f"[LLM] 大模型返回技能链: {chain}")
        return chain

    def create_skill(self, name: str, description: str = "") -> str:
        """模拟大模型创建新技能"""
        new_skill = f"LLM_Created_{name}"
        print(f"[LLM] 创建新技能: {new_skill}")
        return new_skill


# ==================== ASC 索引器（本地规则 + 大模型回退） ====================

class ASCIndexer:
    """
    ASC/RULE.md 查找规则：
    1. 蛋白记录 → 提取多肽 → 获取技能链
    2. 多肽记录 → 获取技能链
    3. 自组织技能（使用原子记录）
    4. Skill-Creater（大模型创建）
    如果以上均失败，调用大模型解析器直接生成技能链
    """
    def __init__(self, db: BioDatabase, llm: LLMResolver):
        self.db = db
        self.llm = llm

    def get_skill_chain(self, index: str) -> List[str]:
        """按优先级获取技能链，必要时使用大模型"""
        # 第一优先：蛋白记录
        protein = self.db.proteins.get(index)
        if protein:
            print(f"[ASC] 找到蛋白记录 {index}")
            peptide_index = protein.fold_tree.get("peptide_index")
            if peptide_index and peptide_index in self.db.peptides:
                pep = self.db.peptides[peptide_index]
                print(f"[ASC] 蛋白关联多肽 {peptide_index}，获取技能链")
                return pep.skill_chain
            # 如果 fold_tree 中没有 peptide_index，尝试用 index 直接查多肽
            if index in self.db.peptides:
                pep = self.db.peptides[index]
                print(f"[ASC] 蛋白索引同时是多肽索引，获取技能链")
                return pep.skill_chain
            print("[ASC] 蛋白记录未能解析出多肽，进入下一层")

        # 第二优先：多肽记录
        peptide = self.db.peptides.get(index)
        if peptide:
            print(f"[ASC] 找到多肽记录 {index}，获取技能链")
            return peptide.skill_chain

        # 第三优先：自组织技能（使用原子记录）
        atoms = [a for a in self.db.atoms.values() if a.index == index]
        if atoms:
            # 简单自组织：取第一个原子的 skill
            skill = atoms[0].skill
            print(f"[ASC] 通过原子记录自组织技能: {skill}")
            return [skill]

        # 第四优先：Skill-Creater（调用大模型创建）
        print("[ASC] 本地无记录，调用 Skill-Creater（大模型）创建新技能")
        new_skill = self.llm.create_skill(index)
        # 实际中会将新技能登记回数据库，这里仅返回
        return [new_skill]

    def get_skill_chain_with_llm_fallback(self, index: str) -> List[str]:
        """增强版：如果本地完全无法解析，则直接调用大模型生成技能链"""
        try:
            chain = self.get_skill_chain(index)
            if chain:
                return chain
        except Exception as e:
            print(f"[ASC] 本地解析失败: {e}")
        # 本地解析失败，使用大模型直接生成
        return self.llm.generate_skill_chain(index)


# ==================== 规划器（P 阶段） ====================

@dataclass
class WorkflowPlan:
    """STOPJ 工作流计划"""
    index: str
    skill_chain: List[str]
    steps: List[str]
    max_iterations: int = 10

    def __str__(self):
        return (f"WorkflowPlan(index={self.index}, "
                f"skill_chain={self.skill_chain}, "
                f"steps={self.steps})")


class SkillPlanner:
    """根据技能链生成 STOPJ 执行步骤"""
    def __init__(self):
        pass

    def plan(self, index: str, skill_chain: List[str], max_iterations: int = 10) -> WorkflowPlan:
        steps = ["S"]  # 启动
        for i, skill in enumerate(skill_chain):
            steps.append(f"P(i={i}, skill={skill})")  # 规划
            steps.append(f"J(i={i}, skill={skill})")  # 跳转执行
            steps.append("O")                        # 循环
            steps.append(f"T(i={i})")                # 闸门检查
        steps.append("T(stop)")  # 最终停止
        return WorkflowPlan(index=index, skill_chain=skill_chain, steps=steps, max_iterations=max_iterations)


# ==================== STOPJ Runtime 执行器 ====================

class STOPJRuntime:
    """
    STOPJ Runtime 闸门技能执行器。
    解释并执行 WorkflowPlan 中的步骤。
    """
    def __init__(self, plan: WorkflowPlan):
        self.plan = plan
        self.current_step = 0
        self.iteration = 0

    def execute(self):
        """执行计划"""
        print("\n=== STOPJ Runtime 开始执行 ===")
        for step in self.plan.steps:
            self._execute_step(step)
            if step.startswith("T(stop)"):
                break
        print("=== STOPJ Runtime 执行结束 ===\n")

    def _execute_step(self, step: str):
        """解释并执行单个步骤"""
        if step == "S":
            print(f"[S] 启动管线，技能链: {self.plan.skill_chain}")
        elif step.startswith("P("):
            # P(i=0, skill=skill_A) 形式
            print(f"[P] 规划：{step}")
        elif step.startswith("J("):
            # J(i=0, skill=skill_A) 形式
            print(f"[J] 跳转执行：{step}")
            # 实际执行技能（这里模拟）
            skill = step.split("skill=")[1].rstrip(")")
            print(f"    执行技能: {skill}")
        elif step == "O":
            self.iteration += 1
            print(f"[O] 循环迭代 {self.iteration}")
        elif step.startswith("T("):
            # T(i=0) 或 T(stop)
            if "stop" in step:
                print("[T] 最终闸门：停止")
            else:
                print(f"[T] 闸门检查：{step}")
                # 这里可以增加返工逻辑，当前简化直接通过
        else:
            print(f"未知步骤: {step}")


# ==================== 主函数：初始化数据库并演示 ====================

def main():
    # 1. 初始化数据库
    db = BioDatabase()

    # 添加原子记录
    db.add_atom(AtomRecord("A1", "skill_A", "技能A", "A", "ASC_A"))
    db.add_atom(AtomRecord("A2", "skill_B", "技能B", "B", "ASC_B"))
    db.add_atom(AtomRecord("A3", "skill_C", "技能C", "C", "ASC_C"))

    # 添加多肽记录
    db.add_peptide(PeptideRecord(
        index="P1",
        skill_chain=["skill_A", "skill_B", "skill_C"],
        gene_chain="1-2-3",
        atcg_gene_chain="ATCGATCG",
        hash=hashlib.md5(b"P1").hexdigest()
    ))

    # 添加蛋白记录
    db.add_protein(ProteinRecord(
        index="Prot1",
        fold_tree={"peptide_index": "P1"},  # 关联到多肽 P1
        peptide_gene_path="start-2-3-1-5-stop"
    ))

    # 2. 初始化大模型解析器
    llm = LLMResolver()

    # 3. 创建 ASC 索引器
    indexer = ASCIndexer(db, llm)

    # 4. 创建规划器
    planner = SkillPlanner()

    # 5. 演示本地数据库解码（蛋白记录存在）
    print("=" * 50)
    print("场景1：本地数据库有蛋白记录 Prot1")
    print("=" * 50)
    skill_chain = indexer.get_skill_chain("Prot1")
    print(f"获取到的技能链: {skill_chain}\n")
    plan = planner.plan("Prot1", skill_chain)
    print("生成计划:")
    print(plan)
    runtime = STOPJRuntime(plan)
    runtime.execute()

    # 6. 演示大模型解析（本地数据库无记录）
    print("=" * 50)
    print("场景2：本地数据库无记录，使用大模型解析")
    print("=" * 50)
    unknown_index = "UnknownX"
    # 使用带大模型回退的方法
    skill_chain = indexer.get_skill_chain_with_llm_fallback(unknown_index)
    print(f"大模型返回的技能链: {skill_chain}\n")
    plan = planner.plan(unknown_index, skill_chain)
    print("生成计划:")
    print(plan)
    runtime = STOPJRuntime(plan)
    runtime.execute()


if __name__ == "__main__":
    main()