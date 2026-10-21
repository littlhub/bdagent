#!/usr/bin/env python3
"""
蛋白 agent - 生物技能工作流系统
支持：JSON存储、ASC索引、STOPJ工作流（含无空格解析）
"""

import json
import os
import re
import math
import hashlib
from typing import List, Dict, Any, Optional, Tuple

# ==================== 预设技能（23个） ====================
PRESET_SKILLS = [f"skill_{i:02d}" for i in range(1, 24)]

# ==================== JSON 存储管理 ====================
def _default_data_dir() -> str:
    """默认数据目录固定在 dbagent 内的 package/data。

    原先是相对路径 "data"，会随启动时的工作目录漂移，
    甚至写到 dbagent 之外（如 workspase\\data），这里改为绝对路径。
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


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

# ==================== STOPJ 表达式解析器（支持无空格） ====================
# 按顺序逐个位置匹配：位点定义 → 跳转 → 带参指令 → 单字指令 → 技能名
# 技能名允许连字符（如 plan-filter），并且优先匹配已知技能名
TOKEN_PATTERNS = (
    re.compile(r"\d+J"),
    re.compile(r"J\d+"),
    re.compile(r"[SPTO]\d+"),
    re.compile(r"[SPTO]"),
    re.compile(r"[A-Za-z_][A-Za-z0-9_-]*"),
)

def tokenize_compact(expr: str, known_skills: Optional[List[str]] = None) -> List[str]:
    """拆分紧凑表达式。

    传入 known_skills 时，每个位置优先尝试匹配已知名字（最长优先），
    因此 readeditbash 会被拆成 read + edit + bash。
    """
    skills = sorted(known_skills or [], key=len, reverse=True)
    tokens = []
    i = 0
    n = len(expr)
    while i < n:
        matched = next((s for s in skills if s and expr.startswith(s, i)), None)
        if matched:
            tokens.append(matched)
            i += len(matched)
            continue
        for pattern in TOKEN_PATTERNS:
            m = pattern.match(expr, i)
            if m:
                tokens.append(m.group(0))
                i = m.end()
                break
        else:
            i += 1  # 无法识别的字符（空白、标点）直接跳过
    return tokens

def parse_stopj_expression(expr: str, known_skills: Optional[List[str]] = None
                           ) -> Tuple[List[Dict[str, Any]], Dict[int, int]]:
    # 去掉 STOPJ 前缀（如果有）
    expr = expr.replace("STOPJ", "").strip()
    # 有空白时按空白切分（无歧义）；紧凑写法才需要扫描
    tokens = expr.split() if any(ch.isspace() for ch in expr) else tokenize_compact(expr, known_skills)
    steps = []
    sites = {}
    for tok in tokens:
        # 位点定义
        m = re.fullmatch(r'(\d+)J', tok)
        if m:
            site_id = int(m.group(1))
            step_idx = len(steps)
            steps.append({"type": "DEF_SITE", "site": site_id})
            sites[site_id] = step_idx
            continue
        # 跳转
        m = re.fullmatch(r'J(\d+)', tok)
        if m:
            site_id = int(m.group(1))
            steps.append({"type": "JUMP", "site": site_id})
            continue
        # 标准指令 S/P/T/O，可带数字参数
        m = re.fullmatch(r'([SPTO])(\d*)', tok)
        if m:
            steps.append({"type": m.group(1), "value": int(m.group(2)) if m.group(2) else 0})
        else:
            steps.append({"type": "SKILL", "skill": tok})
    return steps, sites

# ==================== ASC 索引器 ====================
class ASCIndexer:
    def __init__(self, store: JsonStore):
        self.store = store
        self.atoms = store.load("atoms.json")
        self.peptides = store.load("peptides.json")
        self.proteins = store.load("proteins.json")

    def get_skill_chain(self, index: str) -> List[str]:
        protein = self.proteins.get(index)
        if protein:
            # peptide_index 可能存于蛋白顶层，也可能嵌在 fold_tree 里
            peptide_index = protein.get("peptide_index") \
                or (protein.get("fold_tree") or {}).get("peptide_index")
            if peptide_index and peptide_index in self.peptides:
                return self.peptides[peptide_index]["skill_chain"]
            if index in self.peptides:
                return self.peptides[index]["skill_chain"]
        if index in self.peptides:
            return self.peptides[index]["skill_chain"]
        atoms = [v for k, v in self.atoms.items() if k == index]
        if atoms:
            return [atoms[0]["skill"]]
        return []

# ==================== 工作流执行器 ====================
def execute_workflow(
    expression: str,
    indexer: ASCIndexer,
    max_steps: int = 100,
    max_loop_iterations: int = 100,
    llm_callback=None
):
    steps, sites = parse_stopj_expression(expression)
    ip = 0
    count = 0
    loop_stack = []
    plan_context = {"budget": 0, "skills": []}
    output_log = []
    print(f"\n=== 执行工作流: {expression} ===")
    while ip < len(steps) and count < max_steps:
        step = steps[ip]
        stype = step["type"]
        if stype == "S":
            print("[S] 启动")
        elif stype == "P":
            val = step.get("value", 0)
            if val > 0:
                plan_context["skills"] = indexer.get_skill_chain("P1")[:val]
                plan_context["budget"] = val
                print(f"[P] 规划 {val} 步: {plan_context['skills']}")
            else:
                print("[P] 规划")
        elif stype == "O":
            loop_count = step.get("value", 0)
            if loop_count > 0:
                if loop_count > max_loop_iterations:
                    print(f"[O] 循环次数 {loop_count} 超过上限 {max_loop_iterations}，已截断")
                    loop_count = max_loop_iterations
                loop_stack.append({"start_ip": ip + 1, "remaining": loop_count})
                print(f"[O] 进入循环，剩余 {loop_count} 次")
            else:
                print("[O] 跳过无效循环（次数为0）")
        elif stype == "T":
            if loop_stack:
                loop_stack[-1]["remaining"] -= 1
                if loop_stack[-1]["remaining"] > 0:
                    ip = loop_stack[-1]["start_ip"] - 1
                    print(f"[T] 闸门通过，剩余 {loop_stack[-1]['remaining']} 次")
                else:
                    loop_stack.pop()
                    print("[T] 闸门关闭，循环结束")
            else:
                print("[T] 闸门检查（无活动循环）")
        elif stype == "DEF_SITE":
            print(f"[定义位点 {step['site']}]")
        elif stype == "JUMP":
            site_id = step["site"]
            if site_id in sites:
                target = sites[site_id] + 1
                print(f"[J] 跳转到位点 {site_id}，目标步骤 {target}")
                ip = target
                continue
            else:
                print(f"[错误] 未定义位点 {site_id}，停止")
                break
        elif stype == "SKILL":
            skill = step["skill"]
            if llm_callback:
                context = {
                    "step": count,
                    "skill": skill,
                    "plan_budget": plan_context.get("budget", 0),
                    "loop_depth": len(loop_stack),
                    "recent_outputs": output_log[-5:],
                }
                try:
                    response = llm_callback(skill, context)
                    output_log.append(response)
                    print(f"[技能] {skill} → 建议: {response[:80]}")
                except Exception as e:
                    print(f"[技能] {skill} → 回调失败: {e}")
            else:
                print(f"[技能] {skill} → 未提供执行回调，仅解析到该步骤")
        else:
            print(f"[未知] {step}")
        ip += 1
        count += 1
    if count >= max_steps:
        print("达到最大步骤限制，强制停止")
    if loop_stack:
        print(f"工作流结束但仍有未完成的循环（剩余 {loop_stack[-1]['remaining']} 次）")
    print("=== 执行结束 ===\n")
    return output_log

# ==================== 初始化数据 ====================
def initialize_data(store: JsonStore):
    if os.path.exists(os.path.join(store.base_dir, "atoms.json")):
        print("数据已存在，跳过初始化。")
        return
    print("初始化默认数据...")
    # 原子
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

    # 多肽
    coder = DynamicATCGCoder(PRESET_SKILLS)
    peptides = {
        "P1": {"skill_chain": ["skill_01", "skill_02", "skill_03"], "gene_chain": "1-2-3"},
        "P2": {"skill_chain": ["skill_04", "skill_05", "skill_06"], "gene_chain": "4-5-6"}
    }
    for pep in peptides.values():
        atcg = coder.encode(pep["skill_chain"])
        pep["atcg_gene_chain"] = atcg
        pep["hash"] = hashlib.md5(atcg.encode()).hexdigest()
    store.save("peptides.json", peptides)

    # 蛋白
    protein_expr = "Start[2,3[1,5],Stop]"
    fold_tree = parse_protein_chain(protein_expr)
    fold_tree["peptide_index"] = "P1"
    path = tree_to_path(fold_tree)
    proteins = {
        "Prot1": {"fold_tree": fold_tree, "peptide_gene_path": path}
    }
    store.save("proteins.json", proteins)
    print("初始化完成。")

# ==================== 交互式命令行 ====================
def interactive_cli():
    store = JsonStore()
    initialize_data(store)
    indexer = ASCIndexer(store)

    print("欢迎使用蛋白 agent！输入 'help' 查看命令。")
    while True:
        try:
            cmd_line = input("\n> ").strip()
            if not cmd_line:
                continue
            parts = cmd_line.split()
            cmd = parts[0].lower()
            args = parts[1:]

            if cmd in ("exit", "quit"):
                print("再见！")
                break
            elif cmd == "help":
                print("""
可用命令：
  help                    显示帮助
  list                    列出所有记录
  query <index>           查询技能链（ASC规则）
  run <expression>        运行STOPJ工作流表达式（可无空格）
  encode <skill1,skill2>  动态ATCG编码技能链
  decode <atcg_seq>       解码ATCG序列
  parse <表达式>          解析蛋白链表达式
  init                    重新初始化数据（谨慎）
  exit/quit               退出
""")
            elif cmd == "list":
                print("--- 原子记录 ---")
                atoms = store.load("atoms.json")
                for k, v in atoms.items():
                    print(f"  {k}: {v['skill']} ({v['name']})")
                print("--- 多肽记录 ---")
                peptides = store.load("peptides.json")
                for k, v in peptides.items():
                    print(f"  {k}: {v['skill_chain']}")
                print("--- 蛋白记录 ---")
                proteins = store.load("proteins.json")
                for k, v in proteins.items():
                    print(f"  {k}: path={v['peptide_gene_path']}")
            elif cmd == "query":
                if len(args) != 1:
                    print("用法: query <index>")
                    continue
                idx = args[0]
                chain = indexer.get_skill_chain(idx)
                print(f"索引 {idx} 的技能链: {chain}")
            elif cmd == "run":
                if len(args) < 1:
                    print("用法: run <expression>")
                    continue
                expr = "".join(args)  # 去掉空格，支持无空格
                execute_workflow(expr, indexer)
            elif cmd == "encode":
                if len(args) != 1:
                    print("用法: encode <skill1,skill2,...>")
                    continue
                skills = [s.strip() for s in args[0].split(',')]
                coder = DynamicATCGCoder(PRESET_SKILLS)
                try:
                    encoded = coder.encode(skills)
                    print(f"编码结果: {encoded}")
                except KeyError as e:
                    print(f"错误：技能 {e} 不在预设列表中")
            elif cmd == "decode":
                if len(args) != 1:
                    print("用法: decode <atcg_seq>")
                    continue
                coder = DynamicATCGCoder(PRESET_SKILLS)
                try:
                    decoded = coder.decode(args[0])
                    print(f"解码结果: {decoded}")
                except ValueError as e:
                    print(f"错误: {e}")
            elif cmd == "parse":
                if len(args) != 1:
                    print("用法: parse <表达式>")
                    continue
                tree = parse_protein_chain(args[0])
                path = tree_to_path(tree)
                print(f"解析树: {tree}")
                print(f"路径: {path}")
            elif cmd == "init":
                import shutil
                shutil.rmtree(store.base_dir)
                initialize_data(store)
            else:
                # 尝试将整个输入作为 STOPJ 表达式运行
                if cmd.startswith("s") or cmd.startswith("stopj"):
                    expr = "".join(parts)
                    execute_workflow(expr, indexer)
                else:
                    print(f"未知命令: {cmd}")
        except Exception as e:
            print(f"错误: {e}")

if __name__ == "__main__":
    interactive_cli()