---
name: SECURE_INSTALL
description: 技能安装与备份多肽。使用cow install命令安装技能，使用STOPJRuntime技能进行多肽备份。支持批量安装、版本管理、备份恢复。使用命令: /SI
metadata:
  cowagent:
    always: true
---

# SECURE_INSTALL - 技能安装与备份多肽

## 概述

SECURE_INSTALL是技能安装与管理工具，支持：
- **安装技能**: 使用`cow install`命令安装技能
- **备份多肽**: 调用STOPJRuntime技能备份原子、多肽、蛋白
- **版本管理**: 支持版本标签和回滚
- **批量操作**: 批量安装/备份多个技能

## 快捷命令

```bash
/SI          # SECURE_INSTALL技能安装与备份（主命令）
/SR          # STOPJRuntime快捷别名（已废弃的C2别名）
/SI install  # 安装技能
/SI backup   # 备份多肽
/SI restore  # 恢复备份
/SI status   # 查看技能状态
```

## 使用示例

### 安装技能
```bash
/SI install CRYPTO              # 安装CRYPTO技能
/SI install ./skill-package # 从本地路径安装
/SI install https://...     # 从URL安装
```

### 备份多肽
```bash
/SI backup CRYPTO               # 备份CRYPTO技能的多肽
/SI backup --all                # 备份所有技能的多肽
/SI backup CRYPTO v1.1          # 带版本标签备份
```

### 恢复备份
```bash
/SI restore crypto_skill_*.tar.gz  # 从备份文件恢复
/SI restore latest                 # 恢复最新备份
```

## 依赖关系

| 依赖 | 说明 | 状态 |
|------|------|------|
| STOPJRuntime | 多肽备份引擎 | ✅ 必需 |
| skill-capsule-lib | 技能胶囊库 | ✅ 可选 |

## 执行流程

```
/SI install <skill>
  ↓
cow install <skill>    # 调用cow命令安装
  ↓
验证安装结果
  ↓
记录到日志

/SI backup <skill>
  ↓
调用STOPJRuntime backup_peptides
  ↓
生成备份文件
  ↓
更新latest软链接
```

## 版本信息

- **版本**: 2.0
- **创建时间**: 2026-08-23
- **重命名时间**: 2026-08-24
- **原名称**: C3
- **依赖**: STOPJRuntime (多肽备份)
