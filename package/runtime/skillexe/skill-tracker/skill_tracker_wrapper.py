#!/usr/bin/env python3
"""
Skill Tracker Wrapper - 自动调用入口
每次回答结束时调用此脚本记录技能使用
"""

import subprocess
import sys
from pathlib import Path

# 跟随脚本自身位置，不再硬编码手机端路径
TRACKER_DIR = Path(__file__).resolve().parent
AUTO_RECORD = TRACKER_DIR / "scripts" / "auto_record.py"

def record(skills, round_type="intermediate", tokens=0, duration=0):
    """记录技能调用"""
    cmd = [
        sys.executable, str(AUTO_RECORD),
        "--skills", skills,
        "--round-type", round_type,
        "--tokens", str(tokens),
        "--duration", str(duration)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: skill_tracker_wrapper.py <skill1,skill2> [round_type] [tokens] [duration]")
        sys.exit(1)
    
    skills = sys.argv[1]
    round_type = sys.argv[2] if len(sys.argv) > 2 else "intermediate"
    tokens = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    duration = float(sys.argv[4]) if len(sys.argv) > 4 else 0
    
    if record(skills, round_type, tokens, duration):
        print(f"✅ 已记录: {skills}")
    else:
        print(f"❌ 记录失败")
