#!/bin/sh
# herb-decrypt.sh - 解密 HERB API 配置
# 用法: /SM herb-decrypt <密码>
# 说明: 使用 512 位密钥解密 HERB 配置文件
#       默认脱敏显示，如需完整内容请手动添加 --unmask 参数

PASSWORD="${1:?用法: herb-decrypt <密码>}"
ENCRYPTED="skills/CRYPTO/config_herb.encrypted"

if [ ! -f "$ENCRYPTED" ]; then
  echo "❌ 加密文件不存在: $ENCRYPTED"
  exit 1
fi

echo "🔓 解密 HERB 配置..."
python3 skills/CRYPTO/scripts/herb_encrypt.py decrypt \
  --password "$PASSWORD" \
  --input "$ENCRYPTED"

echo ""
echo "ℹ️  提示：敏感信息已脱敏显示"
echo "   如需查看完整配置（本地使用）："
echo "   python3 skills/CRYPTO/scripts/herb_encrypt.py decrypt \\"
echo "     --password \"$PASSWORD\" \\"
echo "     --input $ENCRYPTED \\"
echo "     --unmask"
