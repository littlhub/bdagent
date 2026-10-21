#!/usr/bin/env python3
"""
生物技能工作流系统 - 初始化与运行
支持：
- JSON 持久化存储（atoms.json, peptides.json, proteins.json）
- ASC 索引规则（蛋白→多肽→自组织→Skill-Creater）
- STOPJ 工作流表达式解析（支持 nJ 定义位点、Jn 跳转）
- 动态 ATCG 编码/解码
- 简单蛋白链解析
"""

import json
import os
import re
import math
import hashlib
from typing import List, Dict, Any, Optional, Tuple

# ==================== 预设技能（23个） ====================
PRESET_SKILLS = [
    "skill_01", "skill_02", "skill_03", "skill_04", "skill_05",
    "skill_06", "skill_07", "skill_08", "skill_09", "skill_10",
    "skill_11", "skill_12", "skill_13", "skill_14", "skill_15",
    "skill_16", "skill_17", "skill_18", "skill_19", "skill_20",
    "skill_21", "skill_22", "skill_23"
]

# ==================== JSON 存储管理 ====================
def _default_data_dir() -> str:
    """默认数据目录固定在 package/data，不随工作目录漂移。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "data")


class JsonStore:
    def __init__(self, base_dir: str | None = None):
        self.base_dir = os.path.abspath(base_dir) if base_dir else _default_data_dir()
        os.makedirs(self.base_dir, exist_ok=True)

    def load(self, filename: str) -> Any:
        path = os.path.join(self.base_dir, filename)
        if not os.path.exists(path):
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save(self, filename: str, data: Any):
        path = os.path.join(self.base_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

# ==================== 动态 ATCG 编码器 ====================
class DynamicATCGCoder:
    BASES = ['A', 'T', 'C', 'G']
    def __init__(self, skill_names: List[str]):
        self.skill_names = list(skill_names)
        self.num_skills = len(self.skill_names)
        self.code_length = max(1, math.ceil(math.log(self.num_skills, 4)))
        self.skill_to_code, self.code_to_skill = self._build_mapping()

    def _build_mapping(self):
        skill_to_code = {}
        code_to_skill = {}
        def gen_codes(length):
            if length == 0:
                yield ''
            else:
                for base in self.BASES:
                    for suffix in gen_codes(length - 1):
                        yield base + suffix
        gen = gen_codes(self.code_length)
        for skill in self.skill_names:
            code = next(gen)
            skill_to_code[skill] = code
            code_to_skill[code] = skill
        return skill_to_code, code_to_skill

    def encode(self, skill_chain: List[str]) -> str:
        return ''.join(self.skill_to_code[skill] for skill in skill_chain)

    def decode(self, atcg_seq: str) -> List[str]:
        if len(atcg_seq) % self.code_length != 0:
            raise ValueError(f"ATCG 序列长度必须是 {self.code_length} 的倍数")
        return [self.code_to_skill[atcg_seq[i:i+self.code_length]]
                for i in range(0, len(atcg_seq), self.code_length)]

# ==================== 蛋白链解析 ====================
def parse_protein_chain(expr: str) -> Dict[str, Any]:
    expr = expr.replace(" ", "")
    pos = 0
    length = len(expr)
    def parse_node():
        nonlocal pos
        match = re.match(r'[A-Za-z0-9]+', expr[pos:])
        if not match:
            raise ValueError(f"无法解析节点，位置 {pos}")
        name = match.group(0)
        pos += len(name)
        node = {"name": name, "children": []}
        if pos < length and expr[pos] == '[':
            pos += 1
            while pos < length and expr[pos] != ']':
                child = parse_node()
                node["children"].append(child)
                if pos < length and expr[pos] == ',':
                    pos += 1
            if pos >= length or expr[pos] != ']':
                raise ValueError("缺少右括号 ]")
            pos += 1
        return node
    tree = parse_node()
    if pos != length:
        raise ValueError(f"表达式解析后仍有剩余字符: {expr[pos:]}")
    return tree

def tree_to_path(tree: Dict[str, Any]) -> str:
    paths = []
    def dfs(node, path):
        current = path + [node["name"]]
        if not node["children"]:
            paths.append("-".join(current))
        else:
            for child in node["children"]:
                dfs(child, current)
    dfs(tree, [])
    return ";".join(paths)

# ==================== STOPJ 工作流解析器 ====================
def parse_stopj_expression(expr: str) -> Tuple[List[Dict[str, Any]], Dict[int, int]]:
    """
    解析工作流表达式，返回 (步骤列表, 结合位点映射)
    步骤类型: S, P, O, T, J, DEF_SITE, SKILL, JUMP
    """
    expr = expr.replace(" ", "")
    token_pattern = re.compile(r'\d+J|J\d+|[A-Z](?![a-z])|[A-Za-z0-9_\u4e00-\u9fff]+')
    tokens = token_pattern.findall(expr)
    steps = []
    binding_sites = {}  # {site_id: step_index}
    for tok in tokens:
        # 定义位点: 数字J
        m = re.fullmatch(r'(\d+)J', tok)
        if m:
            site_id = int(m.group(1))
            step_idx = len(steps)
            steps.append({"type": "DEF_SITE", "site": site_id})
            binding_sites[site_id] = step_idx
            continue
        # 跳转: J数字
        m = re.fullmatch(r'J(\d+)', tok)
        if m:
            site_id = int(m.group(1))
            steps.append({"type": "JUMP", "site": site_id})
            continue
        # 标准指令
        if tok == 'S':
            steps.append({"type": "S"})
        elif tok == 'P':
            steps.append({"type": "P"})
        elif tok == 'O':
            steps.append({"type": "O"})
        elif tok == 'T':
            steps.append({"type": "T"})
        else:
            # 视为技能名
            steps.append({"type": "SKILL", "skill": tok})
    return steps, binding_sites

# ==================== ASC 索引器 ====================
class ASCIndexer:
    def __init__(self, store: JsonStore):
        self.store = store
        self.atoms = store.load("atoms.json")
        self.peptides = store.load("peptides.json")
        self.proteins = store.load("proteins.json")

    def get_skill_chain(self, index: str) -> List[str]:
        # 1. 蛋白记录
        protein = self.proteins.get(index)
        if protein:
            peptide_index = protein.get("peptide_index")
            if peptide_index and peptide_index in self.peptides:
                return self.peptides[peptide_index]["skill_chain"]
            if index in self.peptides:
                return self.peptides[index]["skill_chain"]
        # 2. 多肽记录
        if index in self.peptides:
            return self.peptides[index]["skill_chain"]
        # 3. 自组织技能（原子）
        atoms = [v for k, v in self.atoms.items() if k == index]
        if atoms:
            return [atoms[0]["skill"]]
        # 4. 默认返回空，由上层处理
        return []

# ==================== 工作流执行器 ====================
def execute_workflow(expression: str, indexer: ASCIndexer, max_steps: int = 100):
    steps, sites = parse_stopj_expression(expression)
    ip = 0
    count = 0
    print(f"=== 执行工作流: {expression} ===")
    while ip < len(steps) and count < max_steps:
        step = steps[ip]
        stype = step["type"]
        if stype == "S":
            print("[S] 启动")
        elif stype == "P":
            print("[P] 规划")
        elif stype == "O":
            print("[O] 循环")
        elif stype == "T":
            print("[T] 闸门检查")
            # 简化：如果下一步是停止位点或越界则停止，否则继续
            if ip + 1 >= len(steps):
                print("    到达末尾，停止")
                break
        elif stype == "DEF_SITE":
            print(f"[定义位点 {step['site']}]")
        elif stype == "JUMP":
            site_id = step["site"]
            if site_id in sites:
                target = sites[site_id] + 1  # 跳到位点定义之后
                print(f"[J] 跳转到位点 {site_id}，目标步骤 {target}")
                ip = target
                continue
            else:
                print(f"[错误] 未定义位点 {site_id}，停止")
                break
        elif stype == "SKILL":
            skill = step["skill"]
            print(f"[技能] 执行 {skill}")
            # 可选：检查该技能是否在技能库中
        else:
            print(f"[未知] {step}")
        ip += 1
        count += 1
    if count >= max_steps:
        print("达到最大步骤限制，强制停止")
    print("=== 执行结束 ===\n")

# ==================== 初始化数据 ====================
def initialize_data(store: JsonStore):
    """如果 JSON 文件不存在，则创建默认数据"""
    # 检查是否已初始化
    if os.path.exists(os.path.join(store.base_dir, "atoms.json")):
        print("数据已存在，跳过初始化。")
        return

    print("初始化默认数据...")

    # 原子记录（为每个预设技能创建一个原子）
    atoms = {}
    for i, skill in enumerate(PRESET_SKILLS, 1):
        index = f"A{i}"
        atoms[index] = {
            "skill": skill,
            "name": f"技能{i}",
            "nickname": f"S{i}",
            "abbreviation": f"SK{i}"
        }
    store.save("atoms.json", atoms)

    # 多肽记录（示例）
    peptides = {
        "P1": {
            "skill_chain": ["skill_01", "skill_02", "skill_03"],
            "gene_chain": "1-2-3",
            "atcg_gene_chain": "",  # 稍后填充
            "hash": ""
        },
        "P2": {
            "skill_chain": ["skill_04", "skill_05", "skill_06"],
            "gene_chain": "4-5-6",
            "atcg_gene_chain": "",
            "hash": ""
        }
    }
    # 使用动态编码填充 ATCG
    coder = DynamicATCGCoder(PRESET_SKILLS)
    for pep in peptides.values():
        atcg = coder.encode(pep["skill_chain"])
        pep["atcg_gene_chain"] = atcg
        pep["hash"] = hashlib.md5(atcg.encode()).hexdigest()
    store.save("peptides.json", peptides)

    # 蛋白记录（示例）
    protein_expr = "Start[2,3[1,5],Stop]"
    fold_tree = parse_protein_chain(protein_expr)
    fold_tree["peptide_index"] = "P1"
    path = tree_to_path(fold_tree)
    proteins = {
        "Prot1": {
            "fold_tree": fold_tree,
            "peptide_gene_path": path
        }
    }
    store.save("proteins.json", proteins)

    print("初始化完成。")

# ==================== 主函数 ====================
def main():
    store = JsonStore()
    initialize_data(store)

    # 创建索引器
    indexer = ASCIndexer(store)

    # 示例工作流表达式（演示 nJ 定义和 Jn 跳转）
    # 该表达式定义位点1，执行技能，跳到位点2，定义位点2，闸门检查，循环，跳回位点1
    expression = "S1JPskill_01J2T OJ1"

    # 运行工作流
    execute_workflow(expression, indexer, max_steps=20)

    # 另一个示例：使用简单蛋白链查询技能链
    print("查询 Prot1 的技能链：")
    chain = indexer.get_skill_chain("Prot1")
    print(f"  {chain}")

if __name__ == "__main__":
    main()