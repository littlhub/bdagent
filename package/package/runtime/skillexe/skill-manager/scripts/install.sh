#!/bin/bash
# skill-manager install - 安装技能
# 依赖: SECURE_INSTALL

SKILL_PATH="${1:?用法: install <skill_path|url>}"
VERIFY="${2:-false}"

echo "📥 开始安装技能: $SKILL_PATH"

# 调用SECURE_INSTALL安装
if command -v cow &> /dev/null; then
    cow install "$SKILL_PATH"
    INSTALL_STATUS=$?
else
    echo "⚠️ cow命令不可用，尝试手动安装"
    # 检查本地路径或URL
    if [ -d "$SKILL_PATH" ]; then
        DEST="skills/$(basename "$SKILL_PATH")"
        cp -r "$SKILL_PATH" "$DEST"
        echo "✅ 已复制到: $DEST"
        INSTALL_STATUS=0
    elif echo "$SKILL_PATH" | grep -qE '^https?://'; then
        echo "⚠️ URL安装需要curl/wget支持"
        INSTALL_STATUS=1
    else
        echo "❌ 无效路径: $SKILL_PATH"
        INSTALL_STATUS=1
    fi
fi

if [ $INSTALL_STATUS -eq 0 ]; then
    echo "✅ 安装成功"
    
    # 验证安装
    if [ "$VERIFY" = "true" ]; then
        echo "🔍 验证安装..."
        if [ -f "skills/$(basename "$SKILL_PATH")/manifest.json" ]; then
            echo "✅ 验证通过"
        else
            echo "⚠️ 验证失败: manifest.json不存在"
        fi
    fi
else
    echo "❌ 安装失败"
    exit 1
fi
