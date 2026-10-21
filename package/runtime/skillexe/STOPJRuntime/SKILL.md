---
name: STOPJRuntime
description: STOPJ Runtime闸门技能 - 完整多肽库。包含全部原子、多肽、蛋白三层结构。支持技能安装与备份。使用命令: /ST /SR
metadata:
  cowagent:
    always: true
---

# STOPJRuntime - 完整技能多肽库 v2.5

## 概述

STOPJRuntime是STOPJ Runtime闸门的完整技能库，包含三层注册结构：
- **Atom层**: 37 个原子
- **Polypeptide层**: 26 个多肽
- **Protein层**: 17 个蛋白

## 快捷命令

```bash
/ST          # STOP运行时别名（推荐）
/SR          # STOPJRuntime快捷别名（新）
/S           # 启动闸门
/T           # 停止检查
/O           # 循环执行
/P           # 规划工作流
/J           # 位点跳转
/HM          # HERB记忆管理
/ID          # 技能文档整合
/HMID        # HERB记忆管理与整合
/REM         # 记忆合并快捷命令
```

### 别名说明

| 命令 | 别名 | 功能 | Token |
|------|------|------|-------|
| `/ST` | STOP运行时 | 完整闸门工作流 (1030 tokens) |
| `/STOP` | STOP运行时 | 完整闸门工作流 |
| `/SR` | STOPJRuntime | 技能库快捷别名（新） |
| `/HM` | HERB记忆管理 | HERB API Key管理 |
| `/ID` | 技能文档整合 | 整合多日记忆 |
| `/HMID` | 记忆整合链 | 完整工作流程 |
| `/REM` | rememory | 记忆快捷命令 |

## 闸门序列

```
S → T → O → P → J
│     │     │     │
▼     ▼     ▼     ▼
执行  检查  循环  规划→跳转
```

## 使用示例

### 基本操作
```bash
/ST           # 执行完整STOPJ工作流
/SR           # STOPJRuntime快捷命令
/ST S         # 启动闸门
/ST T         # 停止检查
/ST O         # 循环执行
/ST P         # 规划工作流
/ST J         # 位点跳转
/HM           # HERB记忆管理
/ID           # 技能文档整合
/HMID         # 完整整合链
/REM          # rememory别名
```

### 组合操作
```bash
/ST ST        # 启动→停止检查 (2000 tokens)
/ST TO        # 停止→循环 (2000 tokens)
/ST OP        # 循环→规划 (550 tokens)
/ST PJ        # 规划→跳转 (580 tokens)
/HMID         # HERB管理+整合 (1400 tokens)
/REM          # rememory快捷命令
```

## 版本信息

- **版本**: 2.5
- **创建时间**: 2026-08-23
- **更新时间**: 2026-08-24
- **总Token**: 1030 (STOPJ) / 1400 (HMID) / 500 (REM) / 动态计算 (组合)
- **快捷别名**: /ST, /SR, /REM
