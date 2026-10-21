---
name: skill-manager
description: 技能管理多肽 - 完整技能生命周期管理。支持备份、安装、创建、删除技能，加密解密配置。依赖原有技能可组合使用。使用命令: /SM
metadata:
  cowagent:
    always: true
  requires:
    skills: ["STOPJRuntime", "SECURE_INSTALL", "CRYPTO"]
---

# skill-manager - 技能管理多肽

## 概述

skill_manager是完整的技能生命周期管理工具，整合了备份、安装、创建、删除、加密解密功能。

## 快捷命令

```bash
/SM                    # skill-manager主入口
/SM backup             # 备份技能（依赖STOPJRuntime）
/SM install            # 安装技能（依赖SECURE_INSTALL）
/SM create             # 创建新技能
/SM delete             # 删除技能
/SM encrypt            # 加密技能配置（依赖CRYPTO）
/SM decrypt            # 解密技能配置（依赖CRYPTO）
/SM list               # 列出所有技能
/SM status             # 查看技能状态
/SM migrate            # 技能迁移
/SM herb-encrypt       # 加密存储HERB配置（512位密钥+密码验证）
/SM herb-decrypt       # 解密HERB配置（需输入密码）
/SM herb-status        # 查看HERB加密状态
```

## 依赖关系

| 依赖技能 | 功能 | 说明 |
|----------|------|------|
| **STOPJRuntime** | 多肽备份 | 备份原子、多肽、蛋白三层结构 |
| **SECURE_INSTALL** | 安装管理 | cow install命令封装 |
| **CRYPTO** | 加密解密 | Fernet/AES加密配置 |

## 加密算法

| 用途 | 算法 | 密钥长度 | 派生方式 |
|------|------|----------|----------|
| 通用配置加密 | Fernet (AES-128-CBC+HMAC) | 32字节 | 随机生成 |
| HERB配置加密 | AES-256-GCM + HMAC-SHA256 | 512位 (64字节) | PBKDF2-HMAC-SHA512, 100,000迭代 |

## 使用示例

### 1. 备份技能
```bash
/SM backup CRYPTO              # 备份单个技能
/SM backup --all               # 备份所有技能
/SM backup CRYPTO v1.0         # 带版本标签备份
/SM backup --output ~/backup/  # 指定输出目录
```

### 2. 安装技能
```bash
/SM install CRYPTO             # 安装技能
/SM install ./my-skill.zip     # 从本地安装
/SM install https://...        # 从URL安装
/SM install --verify           # 安装后验证
```

### 3. 创建技能
```bash
/SM create my-skill            # 创建基础技能
/SM create my-skill --atoms a,b,c  # 指定原子
/SM create my-skill --template basic   # 使用模板
```

### 4. 删除技能
```bash
/SM delete CRYPTO              # 删除技能
/SM delete CRYPTO --backup     # 删除前备份
/SM delete --all --force       # 强制删除所有
```

### 5. 加密解密配置
```bash
/SM encrypt config.json        # 加密配置文件
/SM decrypt config.encrypted   # 解密配置文件
/SM encrypt --key generate     # 生成新密钥
/SM encrypt --algorithm AES    # 指定算法
```

### 6. HERB 配置加密（512位密钥）
```bash
/SM herb-encrypt <密码>        # 加密存储HERB配置（首次）
/SM herb-decrypt <密码>        # 解密并显示明文配置
/SM herb-status                # 查看加密状态
```

## 执行流程

```
/SM backup <skill>
  ↓
STOPJRuntime.peptides_backup(skill)
  ↓
生成tar.gz备份文件
  ↓
更新latest软链接

/SM install <skill>
  ↓
SECURE_INSTALL.install(skill)
  ↓
cow install验证
  ↓
注册到skills_config.json

/SM encrypt <file>
  ↓
CRYPTO.encrypt(file, key)
  ↓
生成.encrypted文件

/SM decrypt <file>
  ↓
CRYPTO.decrypt(file, key)
  ↓
还原明文配置

/SM herb-encrypt <password>
  ↓
PBKDF2-HMAC-SHA512(密码, salt, 100000轮) → 512位密钥
  ↓
AES-256-GCM加密配置文件
  ↓
写入 config_herb.encrypted
  ↓
输出密钥哈希供验证

/SM herb-decrypt <password>
  ↓
PBKDF2-HMAC-SHA512(密码, salt, 100000轮) → 512位密钥
  ↓
AES-256-GCM解密
  ↓
输出明文JSON配置
```

## 输出格式

### 备份结果
```
✅ 备份成功
文件: /tmp/storage/sda1/记忆skill/CRYPTO_v1.0.tar.gz
大小: 7.3 KB
时间: 2026-08-24 05:30:00
包含: 3 atoms, 1 polypeptide
```

### 安装结果
```
✅ 安装成功
技能: CRYPTO
路径: skills/CRYPTO/
版本: 2.0
命令: /CR
```

### 加密结果
```
✅ 加密成功
输入: config.json
输出: config.encrypted
算法: Fernet
密钥: [REDACTED_SK_KEY]
```

### HERB 加密结果
```
✅ 加密成功
输入: /tmp/herb_config.json
输出: skills/CRYPTO/config_herb.encrypted
算法: AES-256-GCM + HMAC-SHA256
密钥派生: PBKDF2-HMAC-SHA512 (100,000 次迭代)
有效密钥长度: 512 位 (64 字节)

🔑 密钥哈希 (用于验证): QNNzXW8mX/ywD8InnSuEnJAck5RCvdQJjA1zRkRgwUc=
```

### HERB 解密结果
```json
{
  "base_url": "http://10.168.2.202:5173",
  "api_key": "<从加密配置读取>"
}
```

## 版本信息

- **版本**: 1.1
- **创建时间**: 2026-08-24
- **更新时间**: 2026-08-24
- **依赖**: STOPJRuntime, SECURE_INSTALL, CRYPTO
- **快捷命令**: /SM
