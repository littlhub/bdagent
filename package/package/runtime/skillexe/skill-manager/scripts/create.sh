#!/bin/bash
# skill-manager create - 创建新技能

SKILL_NAME="${1:?用法: create <skill_name>}"
TEMPLATE="${2:-basic}"

echo "🛠️ 开始创建技能: $SKILL_NAME"
echo "📋 模板: $TEMPLATE"

# 创建技能目录结构
SKILL_PATH="skills/$SKILL_NAME"
mkdir -p "$SKILL_PATH"/{atoms,scripts}

# 根据模板创建文件
case "$TEMPLATE" in
    basic)
        cat > "$SKILL_PATH/SKILL.md" << 'EOF'
---
name: SKILL_NAME
description: 技能描述
metadata:
  cowagent:
    always: true
---

# SKILL_NAME

## 概述
[技能描述]

## 快捷命令
\`\`\`bash
/COMMAND    # 功能描述
\`\`\`

## 使用示例
[示例]

## 版本信息
- **版本**: 1.0
- **创建时间**: DATE
EOF
        sed -i "s/SKILL_NAME/$SKILL_NAME/g" "$SKILL_PATH/SKILL.md"
        sed -i "s/DATE/$(date +%Y-%m-%d)/" "$SKILL_PATH/SKILL.md"
        ;;
    empty)
        touch "$SKILL_PATH/SKILL.md"
        ;;
    *)
        echo "⚠️ 未知模板: $TEMPLATE，使用basic模板"
        cat > "$SKILL_PATH/SKILL.md" << EOF
---
name: $SKILL_NAME
description: 技能描述
metadata:
  cowagent:
    always: true
---

# $SKILL_NAME
EOF
        ;;
esac

# 创建manifest.json
cat > "$SKILL_PATH/manifest.json" << EOF
{
  "name": "$SKILL_NAME",
  "version": "1.0",
  "description": "技能描述",
  "created": "$(date +%Y-%m-%d)",
  "metadata": {
    "cowagent": {
      "always": true
    }
  }
}
EOF

echo "✅ 技能已创建: $SKILL_PATH"
echo "📁 目录结构:"
echo "   $SKILL_NAME/"
echo "   ├── SKILL.md"
echo "   ├── manifest.json"
echo "   ├── atoms/"
echo "   └── scripts/"
