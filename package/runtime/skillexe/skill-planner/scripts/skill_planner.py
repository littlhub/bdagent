#!/usr/bin/env python3
"""
Skill Planner v3 - 生物隐喻架构
原子(A/T/C/G) → 多肽(基因链) → 蛋白(折叠树)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# 配置路径：优先环境变量，其次脚本自身目录，最后旧的固定路径
_SCRIPT_DIR = Path(__file__).resolve().parent
_CANDIDATE_DIRS = [
    Path(os.environ["SKILL_DB_DIR"]) if os.environ.get("SKILL_DB_DIR") else None,
    _SCRIPT_DIR.parent,
    Path("/tmp/storage/sda1/skill"),
]
SKILL_DB_DIR = next((d for d in _CANDIDATE_DIRS if d and (d / "gene_db.json").exists()),
                    _SCRIPT_DIR.parent)
ATOMS_DIR = SKILL_DB_DIR / "atoms"
POLYPEPTIDES_DIR = SKILL_DB_DIR / "polypeptides"
PROTEINS_DIR = SKILL_DB_DIR / "proteins"
GENE_DB = SKILL_DB_DIR / "gene_db.json"
POLY_DB = SKILL_DB_DIR / "polypeptide_db.json"
PROTEIN_DB = SKILL_DB_DIR / "protein_db.json"
MAPPINGS_FILE = SKILL_DB_DIR / "skill_mappings.json"

def load_json(filepath):
    """加载JSON文件"""
    if filepath.exists():
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def analyze_intent(user_query, mappings):
    """分析用户意图，匹配技能链"""
    query_lower = user_query.lower()
    
    intent = {
        "type": "general",
        "match_type": "none",
        "match_key": None,
        "gene_chain": "",
        "atoms": [],
        "tokens": 0
    }
    
    # 检查快捷命令（不区分大小写）
    if query_lower.startswith("/"):
        cmd = query_lower.strip().lower()
        
        # 蛋白级命令（数据键名：protein_aliases）
        protein_cmds = mappings.get("protein_aliases", {})
        for key, value in protein_cmds.items():
            if key.lower() == cmd:
                intent["type"] = "protein_workflow"
                intent["match_type"] = "protein"
                intent["match_key"] = key
                if isinstance(value, dict):
                    intent["gene_chain"] = value.get("chain") or value.get("target", "")
                else:
                    match = re.search(r'\(([^)]+)\)', value)
                    if match:
                        intent["gene_chain"] = match.group(1)
                intent["tokens"] = 1500
                return intent
        
        # 多肽级命令（数据键名：polypeptide_commands）
        poly_cmds = mappings.get("polypeptide_commands", {})
        for key, value in poly_cmds.items():
            if key.lower() == cmd:
                intent["type"] = "polypeptide_chain"
                intent["match_type"] = "polypeptide"
                intent["match_key"] = key
                match = re.search(r'\(([^)]+)\)', value)
                if match:
                    intent["gene_chain"] = match.group(1)
                intent["tokens"] = 1000
                return intent
        
        # 原子级命令（数据键名：atom_commands，值为字典）
        atom_cmds = mappings.get("atom_commands", {})
        for key, value in atom_cmds.items():
            if key.lower() == cmd:
                intent["type"] = "atom"
                intent["match_type"] = "atom"
                intent["match_key"] = key
                gene = value.get("gene") if isinstance(value, dict) else None
                if gene:
                    intent["gene_chain"] = gene
                intent["tokens"] = 500
                return intent
        
        # 别名命令
        aliases = mappings.get("aliases", {})
        if not aliases:
            aliases = {k.lstrip("/"): v.get("target", "") for k, v in
                       mappings.get("protein_aliases", {}).items() if isinstance(v, dict)}
        # 尝试匹配带/和不带/两种形式
        if cmd in aliases:
            protein_name = aliases[cmd]
            intent["type"] = "alias_match"
            intent["match_type"] = "protein"
            intent["match_key"] = f"/{protein_name[-1]}"
            intent["tokens"] = 1500
            return intent
        # 去掉/前缀再尝试匹配
        if cmd.startswith("/") and cmd[1:] in aliases:
            protein_name = aliases[cmd[1:]]
            intent["type"] = "alias_match"
            intent["match_type"] = "protein"
            intent["match_key"] = f"/{protein_name[-1]}"
            intent["tokens"] = 1500
            return intent
    
    # 自然语言模式（数据键名：natural_language_patterns）
    for name, rule in (mappings.get("natural_language_patterns") or {}).items():
        if not isinstance(rule, dict):
            continue
        keywords = rule.get("keywords") or []
        if keywords and all(kw.lower() in query_lower for kw in keywords):
            intent["type"] = name
            intent["match_type"] = "natural_language"
            intent["match_key"] = rule.get("target")
            intent["gene_chain"] = rule.get("target", "").lstrip("/")
            intent["tokens"] = 500
            return intent

    # 关键词映射（数据键名：keyword_mappings）
    for kw, rule in (mappings.get("keyword_mappings") or {}).items():
        if kw.lower() in query_lower:
            intent["type"] = f"match_{kw}"
            intent["match_type"] = "keyword"
            intent["match_key"] = rule.get("target") if isinstance(rule, dict) else rule
            return intent

    # 关键词匹配蛋白
    protein_keywords = {
        "丝袜": "C",
        "写真": "C",
        "美甲": "D",
        "游戏": "A",
        "游泳": "A",
        "文档": "B",
        "MIG全链路": "E"
    }
    
    for kw, protein in protein_keywords.items():
        if kw in query_lower:
            intent["type"] = f"match_{kw}"
            intent["match_type"] = "protein"
            intent["match_key"] = f"/{protein}"
            return intent
    
    return intent

def build_skill_chain(intent, gene_db, poly_db, protein_db):
    """构建技能执行链"""
    chain = []
    
    if intent["match_type"] == "protein" and intent["match_key"]:
        protein_idx = intent["match_key"].replace("/", "")
        protein = protein_db.get("proteins", {}).get(protein_idx)
        if protein:
            chain.append({
                "type": "protein",
                "name": protein.get("name_cn", ""),
                "gene_chain": protein.get("gene_chain", ""),
                "atoms": protein.get("atom_indices", []),
                "tree": protein.get("polypeptide_tree", {})
            })
    
    elif intent["match_type"] == "polypeptide" and intent["match_key"]:
        # 解析多肽索引
        poly_match = intent["match_key"].replace("_", "").replace("-", "")
        for idx in poly_db.get("polypeptides", {}):
            if idx in poly_match:
                poly = poly_db["polypeptides"][idx]
                chain.append({
                    "type": "polypeptide",
                    "name": poly.get("name_cn", ""),
                    "gene_chain": poly.get("gene_chain", ""),
                    "atoms": poly.get("atom_indices", [])
                })
                break
    
    elif intent["match_type"] == "atom" and intent["match_key"]:
        # 解析原子索引
        atom_match = intent["match_key"].replace("_", "").replace("-", "")
        for idx in gene_db.get("unified_index", {}).get("atoms", {}):
            if idx in atom_match:
                atom = gene_db["unified_index"]["atoms"][idx]
                chain.append({
                    "type": "atom",
                    "name": atom.get("name", ""),
                    "gene_chain": atom.get("gene", ""),
                    "atoms": [int(idx)]
                })
                break
    
    return chain

def create_ascii_flow(user_query, intent, chain):
    """创建ASCII流程图"""
    now = datetime.now().strftime("%H:%M:%S")
    
    lines = []
    lines.append("=" * 60)
    lines.append(f"🧬 技能规划器 v3 (生物隐喻) | {now}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"📋 用户问题: \"{user_query[:50]}{'...' if len(user_query) > 50 else ''}\"")
    lines.append("")
    lines.append("🔍 意图识别:")
    lines.append(f"   类型: {intent['type']}")
    lines.append(f"   匹配: {intent['match_type']} - {intent['match_key'] or '无'}")
    lines.append("")
    
    if chain:
        lines.append("🔗 技能链:")
        lines.append("   " + "─" * 40)
        
        for step in chain:
            gene = step.get("gene_chain", "")
            atoms = step.get("atoms", [])
            lines.append(f"   [{step['type']}] {step['name']}")
            lines.append(f"       基因链: {gene}")
            lines.append(f"       原子索引: {atoms}")
            lines.append("")
        
        lines.append("   " + "─" * 40)
        lines.append("   ↓")
        lines.append("   📤 执行完成")
    else:
        lines.append("ℹ️  无需额外技能，直接回答")
    
    lines.append("")
    lines.append(f"💰 Token预估: ~{intent.get('tokens', 0)}")
    lines.append("")
    
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Skill Planner v3 - 生物隐喻架构")
    parser.add_argument("query", help="用户问题或需求")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")
    args = parser.parse_args()
    
    # 加载数据库
    gene_db = load_json(GENE_DB)
    poly_db = load_json(POLY_DB)
    protein_db = load_json(PROTEIN_DB)
    mappings = load_json(MAPPINGS_FILE)
    
    # 分析意图
    intent = analyze_intent(args.query, mappings)
    
    # 构建技能链
    chain = build_skill_chain(intent, gene_db, poly_db, protein_db)
    
    # 创建流程图
    flow = create_ascii_flow(args.query, intent, chain)
    
    print(flow)
    
    # 详细输出
    if args.verbose:
        result = {
            "intent": intent,
            "chain": chain,
            "gene_bases": gene_db.get("gene_map", {}),
            "codon_table": gene_db.get("codon_table", {})
        }
        print("\n📊 详细结果:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
