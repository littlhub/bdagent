#!/bin/sh
# herb-status.sh - 查看 HERB 加密配置状态
# 用法: /SM herb-status

ENCRYPTED="skills/CRYPTO/config_herb.encrypted"

echo "📊 HERB 配置状态"
echo "================"

if [ -f "$ENCRYPTED" ]; then
  SIZE=$(wc -c < "$ENCRYPTED" | tr -d ' ')
  MODIFIED=$(stat -c %y "$ENCRYPTED" 2>/dev/null || stat -f %Sm "$ENCRYPTED" 2>/dev/null)
  echo "✅ 加密文件存在: $ENCRYPTED"
  echo "   文件大小: $SIZE 字节"
  echo "   最后修改: $MODIFIED"
  echo "   算法: AES-256-GCM + HMAC-SHA256"
  echo "   密钥派生: PBKDF2-HMAC-SHA512 (100,000 次迭代)"
  echo "   有效密钥长度: 512 位"
else
  echo "❌ 加密文件不存在: $ENCRYPTED"
  echo "   请先使用 /SM herb-encrypt <密码> 加密存储"
fi
