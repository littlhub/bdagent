#!/usr/bin/env python3
"""
Skill Runner Plugin - 自动每轮调用技能
确保回答问题时最后一轮运行计费技能
"""

import argparse
import json
import os
import time
import sys
import subprocess
import re
from datetime import datetime

SKILLS_DIR = "/tmp/storage/sda1/.oray_app/cow/skills"
OUTPUT_DIR = "/tmp/storage/sda1/图片/"

# 计费技能列表（会产生token消耗的技能）
BILLING_SKILLS = [
    "visioncustom",           # 图片识别
    "agnes-image",            # 图片生成
    "memory-image-generation", # MIG生图
    "web-summarizer",         # 网页摘要
]

def run_skill(skill_name: str, args: list = None):
    """运行指定技能"""
    # 先尝试标准命名
    skill_script = os.path.join(SKILLS_DIR, skill_name, "scripts", f"{skill_name}.py")
    
    # 尝试多种脚本命名
    if not os.path.exists(skill_script):
        alt_scripts = {
            "visioncustom": "image_recognition.py",
            "memory-image-generation": "memory_image.py",
            "agnes-image": "image_generation_v4.py",
        }
        if skill_name in alt_scripts:
            skill_script = os.path.join(SKILLS_DIR, skill_name, "scripts", alt_scripts[skill_name])
    
    if not os.path.exists(skill_script):
        raise FileNotFoundError(f"未找到技能脚本: {skill_name}")
    
    # 构建命令
    cmd = ["python3", skill_script]
    
    # 特殊处理 visioncustom：图片路径是 positional arg，prompt 是 --prompt
    if skill_name == "visioncustom" and args:
        if len(args) == 1:
            cmd.extend(["--prompt", "请详细描述这张图片的内容", args[0]])
        else:
            cmd.extend([args[0], "--prompt", args[1] if len(args) > 1 else "请详细描述这张图片的内容"])
    elif args:
        cmd.extend(args)
    
    start_time = time.time()
    
    # 执行命令
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - start_time
    
    return result.stdout, result.stderr, elapsed

def parse_token_usage(output: str):
    """从输出中解析 token 使用量"""
    stats = {"total": 0, "input": 0, "output": 0}
    
    # 匹配如 "1278 tokens (输入) | 661 tokens (输出)" 的格式
    patterns = [
        r'(\d+)\s*tokens\s*\(输入\)\s*\|\s*(\d+)\s*tokens\s*\(输出\)',
        r'输入.*?(\d+).*?输出.*?(\d+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            stats["input"] = int(match.group(1))
            stats["output"] = int(match.group(2))
            stats["total"] = stats["input"] + stats["output"]
            break
    
    return stats

def main():
    parser = argparse.ArgumentParser(description="Skill Runner Plugin")
    parser.add_argument("--skill", required=True, help="技能名称")
    parser.add_argument("--args", nargs="...", help="技能参数")
    parser.add_argument("--output", default=OUTPUT_DIR, help="输出目录")
    parser.add_argument("--billing", action="store_true", help="是否为计费技能")
    
    args = parser.parse_args()
    
    # 确保输出目录存在
    os.makedirs(args.output, exist_ok=True)
    
    # 运行技能
    stdout, stderr, elapsed = run_skill(args.skill, args.args)
    
    # 解析 token 统计
    stats = parse_token_usage(stdout)
    
    # 输出结果
    billing_tag = " 💰计费" if args.billing or args.skill in BILLING_SKILLS else ""
    print(f"\n[调用技能：{args.skill}]{billing_tag}")
    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)
    
    # 打印 token 统计
    tps = stats["total"] / elapsed if elapsed > 0 else 0
    
    print(f"\n📊 Token 统计{billing_tag}：")
    print(f"- 总 tokens: {stats['total']}")
    print(f"- tokens/s: {tps:.2f}")
    print(f"- 输入 tokens: {stats['input']}")
    print(f"- 输出 tokens: {stats['output']}")
    
    # 自动发送生成的图片（适用于 agnes-image 和 memory-image-generation）
    if args.skill in ["agnes-image", "memory-image-generation"]:
        import glob
        output_dir = args.output if args.output else OUTPUT_DIR
        # 查找最新生成的图片
        png_files = glob.glob(os.path.join(output_dir, "20*.png"))
        if png_files:
            latest_png = max(png_files, key=os.path.getmtime)
            img_info = {
                "path": latest_png,
                "name": os.path.basename(latest_png),
                "size": os.path.getsize(latest_png)
            }
            print(f"\n🖼️  图片已生成:")
            print(f"   文件名: {img_info['name']}")
            print(f"   大小: {img_info['size']/1024:.1f} KB")
            print(f"   路径: {img_info['path']}")

if __name__ == "__main__":
    main()


def run_planner(question, user_id="", mappings=None):
    """运行技能规划器"""
    if mappings is None:
        mappings = DEFAULT_MAPPINGS
    
    planner_script = os.path.join(SKILLS_DIR, "skill-planner", "scripts", "skill_planner.py")
    if not os.path.exists(planner_script):
        return None
    
    cmd = ["python3", planner_script, question]
    if user_id:
        cmd.extend(["--user", user_id])
    for short, full in mappings.items():
        cmd.extend(["--map", f"{short}={full}"])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            # 提取JSON部分
            lines = result.stdout.strip().split('\n')
            for line in reversed(lines):
                if line.startswith('{'):
                    return json.loads(line)
    except Exception as e:
        print(f"⚠️ 规划器错误: {e}", file=sys.stderr)
    
    return None
