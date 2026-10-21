#!/usr/bin/env python3
"""
Skill Tracker Auto - 自动追踪插件
在每轮对话结束时自动记录技能调用
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 数据目录跟随脚本自身位置，可用 DBAGENT_DATA_DIR 覆盖
_SKILL_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DBAGENT_DATA_DIR") or (_SKILL_ROOT / "data"))
SKILLS_FILE = DATA_DIR / "skills.json"

def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

def load_skills_data():
    if SKILLS_FILE.exists():
        with open(SKILLS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"history": [], "stats": {}}

def save_skills_data(data):
    with open(SKILLS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def record_skill(skill_name, input_tokens=0, output_tokens=0, total_tokens=0, duration=0, round_type="intermediate"):
    """记录技能调用"""
    ensure_data_dir()
    
    data = load_skills_data()
    
    entry = {
        "timestamp": datetime.now().isoformat(),
        "skill": skill_name,
        "round_type": round_type,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "duration": duration,
        "tokens_per_second": round(total_tokens / duration, 2) if duration > 0 else 0
    }
    
    data["history"].append(entry)
    
    # 更新统计
    if skill_name not in data["stats"]:
        data["stats"][skill_name] = {"count": 0, "total_tokens": 0, "total_duration": 0}
    
    data["stats"][skill_name]["count"] += 1
    data["stats"][skill_name]["total_tokens"] += total_tokens
    data["stats"][skill_name]["total_duration"] += duration
    
    save_skills_data(data)
    return entry

def auto_record(skills_str, round_type="intermediate", tokens=0, duration=0):
    """自动记录多个技能"""
    skills = [s.strip() for s in skills_str.split(",")]
    per_skill_tokens = max(tokens // len(skills), 100) if skills and tokens else 100
    
    results = []
    for skill in skills:
        entry = record_skill(
            skill_name=skill,
            input_tokens=per_skill_tokens // 2,
            output_tokens=per_skill_tokens // 2,
            total_tokens=per_skill_tokens,
            duration=duration / len(skills) if skills else duration,
            round_type=round_type
        )
        results.append(entry)
    
    return results

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills", required=True, help="技能名称列表（逗号分隔）")
    parser.add_argument("--round-type", choices=["first", "intermediate", "last"], default="intermediate")
    parser.add_argument("--tokens", type=int, default=0, help="总tokens")
    parser.add_argument("--duration", type=float, default=0, help="持续时间(秒)")
    
    args = parser.parse_args()
    
    results = auto_record(args.skills, args.round_type, args.tokens, args.duration)
    print(f"已记录 {len(results)} 个技能调用")
    for r in results:
        print(f"  - {r['skill']}: {r['total_tokens']} tokens")
