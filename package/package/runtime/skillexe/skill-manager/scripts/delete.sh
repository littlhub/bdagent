#!/bin/bash
# skill-manager delete - 删除技能

SKILL_NAME="${1:?用法: delete <skill_name>}"
BACKUP="${2:-false}"

echo "🗑️ 开始删除技能: $SKILL_NAME"

# 检查技能是否存在
SKILL_PATH="skills/$SKILL_NAME"
if [ ! -d "$SKILL_PATH" ]; then
    echo "❌ 技能不存在: $SKILL_PATH"
    exit 1
fi

# 删除前备份
if [ "$BACKUP" = "true" ]; then
    echo "💾 删除前备份..."
    BACKUP_DIR="/tmp/storage/sda1/记忆skill"
    mkdir -p "$BACKUP_DIR"
    tar -czf "$BACKUP_DIR/${SKILL_NAME}_pre_delete_$(date +%Y%m%d_%H%M%S).tar.gz" -C skills "$SKILL_NAME"
    echo "✅ 已备份到: $BACKUP_DIR"
fi

# 删除技能目录
rm -rf "$SKILL_PATH"
echo "✅ 技能已删除: $SKILL_PATH"

# 从skills_config.json中移除
if [ -f "skills_config.json" ]; then
    python3 -c "
import json
with open('skills_config.json', 'r') as f:
    config = json.load(f)
config['skills'] = [s for s in config['skills'] if s.get('name') != '$SKILL_NAME']
with open('skills_config.json', 'w') as f:
    json.dump(config, f, indent=2)
print('✅ 已从skills_config.json中移除')
"
fi

echo "📊 删除完成"
