#!/bin/bash
# skill-manager list - 列出所有技能

echo "📋 已注册的技能列表:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 读取skills_config.json
if [ -f "skills_config.json" ]; then
    python3 -c "
import json
with open('skills_config.json', 'r') as f:
    config = json.load(f)
for skill in config.get('skills', []):
    name = skill.get('name', 'unknown')
    version = skill.get('version', '?')
    commands = ', '.join(skill.get('commands', []))
    print(f'  {name:20} v{version:5}  [{commands}]')
print(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
print(f'总计: {len(config.get(\"skills\", []))} 个技能')
"
else
    echo "⚠️ skills_config.json不存在"
    # 列出skills目录
    if [ -d "skills" ]; then
        echo ""
        echo "📁 skills目录内容:"
        ls -1 skills/
    fi
fi
