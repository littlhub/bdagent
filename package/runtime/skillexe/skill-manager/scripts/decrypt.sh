#!/bin/bash
# skill-manager decrypt - 解密技能配置
# 依赖: CRYPTO

INPUT_FILE="${1:?用法: decrypt <input_file>}"
KEY="${2:?用法: decrypt <input_file> <key>}"

echo "🔓 开始解密: $INPUT_FILE"

# 检查文件是否存在
if [ ! -f "$INPUT_FILE" ]; then
    echo "❌ 文件不存在: $INPUT_FILE"
    exit 1
fi

# 调用CRYPTO解密
if command -v python3 &> /dev/null; then
    python3 -c "
from cryptography.fernet import Fernet

f = Fernet('$KEY'.encode() if isinstance('$KEY', str) else '$KEY')
with open('$INPUT_FILE', 'rb') as f_in:
    encrypted = f_in.read()
decrypted = f.decrypt(encrypted)
output_file = '$INPUT_FILE'.replace('_enc.', '.')
with open(output_file, 'wb') as f_out:
    f_out.write(decrypted)
print(f'✅ 解密成功: {output_file}')
"
else
    echo "❌ 需要Python3和cryptography库"
    exit 1
fi
