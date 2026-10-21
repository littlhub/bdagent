#!/usr/bin/env python3
"""
backup-zip.sh - 备份所有技能为 .zip 文件
用法: backup-zip.sh [输出目录]
"""

import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "/tmp/storage/sda1/记忆skill"
SKILLS_DIR = Path("skills").resolve()
WORKSPACE = Path.cwd().resolve()
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP_NAME = f"skills_backup_{TIMESTAMP}"

print(f"🔄 开始备份所有技能")
print(f"📁 输出目录: {OUTPUT_DIR}")
print(f"📌 备份名称: {BACKUP_NAME}")
print("")

# 创建输出目录
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 要排除的目录/文件模式
EXCLUDE_PATTERNS = [".git", "__pycache__", "*.pyc", "*.pyo"]

def should_exclude(path_str):
    """检查路径是否应被排除"""
    for pattern in EXCLUDE_PATTERNS:
        if pattern in path_str:
            return True
    return False

# 统计
count = 0
total_files = 0
all_sizes = []

# 遍历所有技能目录
for skill_path in sorted(SKILLS_DIR.iterdir()):
    if not skill_path.is_dir():
        continue
    
    skill_name = skill_path.name
    
    # 跳过配置目录
    if skill_name.startswith("skills_config"):
        continue
    
    zip_file = Path(OUTPUT_DIR) / f"{skill_name}_v{TIMESTAMP}.zip"
    
    print(f"⏳ 备份: {skill_name}")
    
    try:
        with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            files_count = 0
            for root, dirs, files in os.walk(skill_path):
                # 排除目录
                dirs[:] = [d for d in dirs if not should_exclude(d)]
                
                for file in files:
                    file_path = Path(root) / file
                    if should_exclude(file):
                        continue
                    
                    # 计算相对路径（相对于工作空间）
                    try:
                        arcname = str(file_path.relative_to(WORKSPACE))
                    except ValueError:
                        # 如果不在工作空间内，使用 skill 目录下的相对路径
                        arcname = str(file_path.relative_to(skill_path))
                        arcname = f"skills/{skill_name}/{arcname}"
                    
                    zf.write(file_path, arcname)
                    files_count += 1
            
            count += 1
            total_files += files_count
            size_mb = zip_file.stat().st_size / 1024 / 1024
            all_sizes.append((skill_name, size_mb))
            print(f"   ✅ {files_count} 个文件, {size_mb:.2f} MB")
            
    except Exception as e:
        print(f"   ❌ 失败: {e}")

# 创建总压缩包
print("")
print("📦 打包所有技能为总压缩包...")
total_zip = Path(OUTPUT_DIR) / f"{BACKUP_NAME}.zip"

try:
    with zipfile.ZipFile(total_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for skill_path in sorted(SKILLS_DIR.iterdir()):
            if not skill_path.is_dir() or skill_path.name.startswith("skills_config"):
                continue
            for root, dirs, files in os.walk(skill_path):
                dirs[:] = [d for d in dirs if not should_exclude(d)]
                for file in files:
                    file_path = Path(root) / file
                    if should_exclude(file):
                        continue
                    try:
                        arcname = str(file_path.relative_to(WORKSPACE))
                    except ValueError:
                        arcname = f"skills/{skill_path.name}/{file_path.relative_to(skill_path)}"
                    zf.write(file_path, arcname)
    
    total_size = total_zip.stat().st_size / 1024 / 1024
    print(f"   ✅ 总包: {total_size:.2f} MB, {total_files} 个文件")
except Exception as e:
    print(f"   ⚠️  总包创建失败: {e}")

print("")
print(f"📊 备份完成! (共 {count} 个技能, {total_files} 个文件)")
print(f"📁 备份位置: {OUTPUT_DIR}")
print("")

# 显示备份文件列表
for name, size in sorted(all_sizes, key=lambda x: -x[1])[:10]:
    print(f"   {name}: {size:.2f} MB")

total_mb = sum(s for _, s in all_sizes)
print(f"   {'---':>20s}: {total_mb:.2f} MB (总计)")

# 更新 latest 软链接
try:
    latest_link = Path(OUTPUT_DIR) / "latest.zip"
    if latest_link.exists() or latest_link.is_symlink():
        latest_link.unlink()
    latest_link.symlink_to(total_zip.name)
    print("")
    print("🔗 latest.zip 软链接已更新")
except Exception as e:
    print(f"   ⚠️  软链接创建失败: {e}")
