#!/bin/bash
# skill-manager status - 查看技能状态

SKILL_NAME="${1:-}"

if [ -z "$SKILL_NAME" ]; then
    echo "📊 所有技能状态:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # 检查skills_config.json
    if [ -f "skills_config.json" ]; then
        python3 -c "
import json, os
with open('skills_config.json', 'r') as f:
    config = json.load(f)
for skill in config.get('skills', []):
    name = skill.get('name', 'unknown')
    installed = os.path.isdir(f'skills/{name}')
    status = '✅' if installed else '❌'
    print(f'  {status} {name}')
"
    fi
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
    echo "📊 技能状态: $SKILL_NAME"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    SKILL_PATH="skills/$SKILL_NAME"
    
    if [ -d "$SKILL_PATH" ]; then
        echo "✅ 已安装: $SKILL_PATH"
        
        # 检查manifest.json
        if [ -f "$SKILL_PATH/manifest.json" ]; then
            echo "📄 manifest.json: 存在"
            python3 -c "
import json
with open('$SKILL_PATH/manifest.json', 'r') as f:
    m = json.load(f)
print(f'  版本: {m.get(\"version\", \"?\")}')
print(f'  描述: {m.get(\"description\", \"?\")}')
print(f'  命令: {\", \".join(m.get(\"commands\", []))}')
"
        fi
        
        # 检查原子
        ATOM_COUNT=$(find "$SKILL_PATH/atoms" -name "*.md" 2>/dev/null | wc -l)
        echo "🧬 原子数量: $ATOM_COUNT"
        
        # 检查脚本
        SCRIPT_COUNT=$(find "$SKILL_PATH/scripts" -name "*.sh" 2>/dev/null | wc -l)
        echo "📜 脚本数量: $SCRIPT_COUNT"
        
    else
        echo "❌ 未安装: $SKILL_PATH"
    fi
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi
