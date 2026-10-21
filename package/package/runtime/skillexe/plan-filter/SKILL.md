---
name: plan-filter
description: 计划过滤器 - 防止向大模型发送完整密码和密钥。扫描输入文本，自动脱敏敏感信息（API Key、密码、Token等）。使用命令: /PF
metadata:
  cowagent:
    always: true
  requires:
    python: ["re", "sys", "argparse"]
---

# PLAN-FILTER - 计划过滤器

## 概述

PLAN-FILTER是一个安全过滤技能，用于在将文本发送给大模型之前，自动检测并脱敏敏感信息，防止密码、API Key、Token等泄露。

### 支持的敏感信息类型

| 类型 | 匹配模式 | 示例 |
|------|----------|------|
| **API Key** | `mv_`前缀, `api_key:`前缀, `Bearer token` | `mv_0b5d7cd3...ace45ec` |
| **密码** | `password:`/`passwd:`后跟8+字符 | `password:mysecr...1234` |
| **密钥** | `private_key:`/`secret_key:`后跟长字符串 | `secret_key:ABCD...WXYZ` |
| **加密密钥** | `encryption_key:`后跟Base64字符串 | `encryption_key:AAAA...====` |

### 脱敏规则

- 保留前6位 + 后4位
- 中间用 `...` 替代
- 示例: `[示例密钥]` → `[脱敏]`

## 快捷命令

```bash
/PF                 # PLAN过滤器主入口
/PF filter <text>   # 过滤文本
/PF filter --file <file>  # 过滤文件
/PF filter --details  # 显示脱敏详情
```

## 使用示例

### 1. 基本过滤
```bash
/PF filter "HERB配置: api_key=[示例密钥]"
```
**输出**:
```
HERB配置: api_key=mv_0b5d...e45ec
```

### 2. 文件过滤
```bash
/PF filter --file config.txt --output filtered.txt
```

### 3. 显示详情
```bash
/PF filter --details "密码: mypassword123, Key: abcdefghijklmnopqrstuvwxyz"
```
**输出**:
```
密码: mypa...zwyzt, Key: abcde...wxyz
[INFO] 检测到 2 处敏感信息已脱敏
```

### 4. 管道使用
```bash
echo "API Key: mv_1234567890abcdef1234567890abcdef" | /PF filter
```

## 集成方式

### 在PLAN中自动调用
当用户请求包含以下关键词时，自动触发过滤：
- "发送" + "密码/Key/Token"
- "配置" + "api_key/password"
- "查询" + "密钥"

### 在技能间传递前过滤
所有技能在执行前，使用 `/PF filter` 预处理输入文本，确保不泄露敏感信息。

## 文件结构

```
skills/plan-filter/
├── SKILL.md              # 技能文档
├── manifest.json         # 元数据
└── scripts/
    └── filter.py         # 核心过滤脚本
```

## 安全规范

### ✅ 正确做法
1. 所有涉及敏感信息的输出必须经过过滤
2. 使用脱敏版本展示给用户
3. 原始敏感信息存储在加密文件中

### ❌ 禁止行为
1. 在对话中明文输出完整密码/Key
2. 将未过滤的敏感信息写入日志
3. 在记忆文件中明文存储密钥

## 版本信息

- **版本**: 1.0
- **创建时间**: 2026-08-24
- **依赖**: Python 3 + 标准库
