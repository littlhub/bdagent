#!/bin/sh
# herb-encrypt.sh - 加密存储 HERB API 配置
# 用法: /SM herb-encrypt <密码>
# 说明: 使用 512 位密钥 (PBKDF2-HMAC-SHA512) 加密 HERB 配置文件

PASSWORD="${1:?用法: herb-encrypt <密码>}"
ENCRYPTED="skills/CRYPTO/config_herb.encrypted"

echo "🔐 加密 HERB 配置..."
python3 skills/CRYPTO/scripts/herb_encrypt.py encrypt \
  --password "$PASSWORD" \
  --input /tmp/herb_config.json \
  --output "$ENCRYPTED"

echo ""
echo "✅ HERB 配置已加密存储到: $ENCRYPTED"
echo "   请妥善保管密码，丢失无法恢复！"
