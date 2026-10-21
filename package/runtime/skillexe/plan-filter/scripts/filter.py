#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
计划过滤器 - 防止向大模型发送完整密码和密钥
扫描输入文本，自动脱敏敏感信息
"""

import re
import sys

# 敏感信息模式匹配规则
SENSITIVE_PATTERNS = {
    # API Key 模式
    'api_key': [
        r'(?i)(api[_-]?key|apikey)\s*[:=]\s*[A-Za-z0-9_\-]{20,}',
        r'(?i)mv_[a-f0-9]{32,}',
        r'(?i)Bearer\s+[A-Za-z0-9_\-\.]{20,}',
        r'(?i)token\s*[:=]\s*[A-Za-z0-9_\-\.]{20,}',
    ],
    # 密码模式
    'password': [
        r'(?i)(password|passwd|pwd)\s*[:=]\s*(\S+)',
        r'(?i)secret\s*[:=]\s*[A-Za-z0-9_\-]{4,}',
    ],
    # 通用密钥模式
    'key': [
        r'(?i)(private[_-]?key|secret[_-]?key)\s*[:=]\s*[A-Za-z0-9/+=]{32,}',
        r'-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----',
    ],
    # 加密密钥
    'encryption_key': [
        r'(?i)(encryption[_-]?key|decrypt[_-]?key)\s*[:=]\s*[A-Za-z0-9+/=]{32,}',
    ],
}

def mask_sensitive(text: str) -> str:
    """脱敏敏感信息"""
    masked = text
    
    for category, patterns in SENSITIVE_PATTERNS.items():
        for pattern in patterns:
            def replacer(match, cat=category):
                original = match.group(0)
                
                # 提取值部分（等号或冒号后面的内容）
                value = original
                if ':' in original or '=' in original:
                    parts = re.split(r'[:=]', original, maxsplit=1)
                    if len(parts) > 1:
                        value = parts[-1].strip()
                
                # 密码脱敏：根据值的长度决定策略
                if cat == 'password':
                    if len(value) < 6:
                        # 短密码：<6位用 ****
                        return original.replace(value, '****')
                    elif len(value) <= 12:
                        # 中等密码：6-12位用前4位+****
                        masked_value = value[:4] + '****'
                        return original.replace(value, masked_value)
                    else:
                        # 长密码：>12位用前6位+...+后4位
                        masked_value = value[:6] + '...' + value[-4:]
                        return original.replace(value, masked_value)
                
                # 其他类型：根据整体长度脱敏
                if len(original) > 12:
                    return original[:6] + '...' + original[-4:]
                elif len(original) >= 6:
                    return original[:4] + '****'
                else:
                    return '********'
            
            masked = re.sub(pattern, replacer, masked)
    
    return masked

def filter_text(input_text: str, show_details: bool = False) -> dict:
    """过滤敏感信息"""
    original = input_text
    filtered = mask_sensitive(input_text)
    
    result = {
        'original_length': len(original),
        'filtered_length': len(filtered),
        'is_masked': original != filtered,
        'filtered_text': filtered,
    }
    
    if show_details and result['is_masked']:
        mask_count = 0
        for category, patterns in SENSITIVE_PATTERNS.items():
            for pattern in patterns:
                matches = re.findall(pattern, original)
                mask_count += len(matches)
        result['mask_count'] = mask_count
        result['categories'] = list(SENSITIVE_PATTERNS.keys())
    
    return result

def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='计划过滤器 - 脱敏敏感信息')
    parser.add_argument('input', nargs='?', help='输入文本（从stdin读取）')
    parser.add_argument('--file', '-f', help='从文件读取输入')
    parser.add_argument('--details', '-d', action='store_true', help='显示脱敏详情')
    parser.add_argument('--output', '-o', help='输出到文件')
    
    args = parser.parse_args()
    
    # 读取输入
    if args.file:
        with open(args.file, 'r', encoding='utf-8') as f:
            input_text = f.read()
    elif args.input:
        input_text = args.input
    else:
        input_text = sys.stdin.read()
    
    # 执行过滤
    result = filter_text(input_text, args.details)
    
    # 输出结果
    output = result['filtered_text']
    
    if args.details and result['is_masked']:
        print(f"[INFO] 检测到 {result.get('mask_count', 0)} 处敏感信息已脱敏", file=sys.stderr)
        print(f"[INFO] 原文长度: {result['original_length']}, 过滤后: {result['filtered_length']}", file=sys.stderr)
    
    # 输出过滤后的文本
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"[INFO] 过滤结果已保存到: {args.output}", file=sys.stderr)
    else:
        print(output)

if __name__ == '__main__':
    main()
