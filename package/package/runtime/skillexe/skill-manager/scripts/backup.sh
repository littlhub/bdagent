#!/bin/bash
# skill-manager backup - 备份技能
# 依赖: STOPJRuntime

SKILL_NAME="${1:-all}"
OUTPUT_DIR="${2:-/tmp/storage/sda1/记忆skill}"
VERSION="${3:-$(date +%Y%m%d_%H%M%S)}"

echo "🔄 开始备份技能: $SKILL_NAME"
echo "📁 输出目录: $OUTPUT_DIR"
echo "📌 版本标签: $VERSION"

# 调用STOPJRuntime备份
if command -v cow &> /dev/null; then
    cow backup "$SKILL_NAME" --output "$OUTPUT_DIR" --version "$VERSION"
    echo "✅ 备份成功 (通过cow命令)"
else
    # 直接打包技能目录
    SKILL_PATH="skills/$SKILL_NAME"
    if [ -d "$SKILL_PATH" ]; then
        mkdir -p "$OUTPUT_DIR"
        tar -czf "$OUTPUT_DIR/${SKILL_NAME}_v${VERSION}.tar.gz" -C skills "$SKILL_NAME"
        echo "✅ 备份成功: $OUTPUT_DIR/${SKILL_NAME}_v${VERSION}.tar.gz"
    else
        echo "❌ 技能不存在: $SKILL_PATH"
        exit 1
    fi
fi

# 更新latest软链接
if [ "$SKILL_NAME" != "all" ]; then
    ln -sf "$OUTPUT_DIR/${SKILL_NAME}_v${VERSION}.tar.gz" "$OUTPUT_DIR/${SKILL_NAME}_latest.tar.gz"
    echo "🔗 latest软链接已更新"
fi

echo "📊 备份完成"
