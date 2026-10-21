#!/bin/bash
# skill-manager encrypt - 加密技能配置
# 依赖: CRYPTO

INPUT_FILE="${1:?用法: encrypt <input_file>}"
KEY="${2:-}"
ALGORITHM="${3:-Fernet}"

echo "🔐 开始加密: $INPUT_FILE"
echo "📊 算法: $ALGORITHM"

# 检查文件是否存在
if [ ! -f "$INPUT_FILE" ]; then
    echo "❌ 文件不存在: $INPUT_FILE"
    exit 1
fi

# 调用CRYPTO加密
if command -v python3 &> /dev/null; then
    # 使用CRYPTO脚本
    CRYPTO_SCRIPT="skills/CRYPTO/scripts/c4.sh"
    if [ -f "$CRYPTO_SCRIPT" ]; then
        bash "$CRYPTO_SCRIPT" encrypt "$INPUT_FILE" ${KEY:+--key "$KEY"} --algorithm "$ALGORITHM"
        echo "✅ 加密成功"
    else
        echo "⚠️ CRYPTO技能未安装，使用Python标准库加密"
        #  fallback: 使用Python加密
        python3 -c "
from cryptography.fernet import Fernet
import base64

key = '$KEY' if '$KEY' else Fernet.generate_key().decode()
if '$KEY' == '':
    print(f'生成新密钥: {key}')
    
f = Fernet(key.encode() if isinstance(key, str) else key)
with open('$INPUT_FILE', 'rb') as f_in:
    data = f_in.read()
encrypted = f.encrypt(data)
output_file = '$INPUT_FILE'.replace('.', '_enc.')
with open(output_file, 'wb') as f_out:
    f_out.write(encrypted)
print(f'✅ 加密成功: {output_file}')
print(f'🔑 密钥: {key}')
"
    fi
else
    echo "❌ 需要Python3和cryptography库"
    exit 1
fi
