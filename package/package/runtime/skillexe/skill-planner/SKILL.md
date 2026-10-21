---
name: skill-planner
description: 技能规划器 - 在回答开始前分析用户意图，规划技能执行流程，并展示技能链图谱。**已集成技能链数据库**，支持从 atoms/polypeptides/proteins 三层读取可用链。当用户提出问题时需要多步操作时触发。
metadata:
  requires:
    bins: ["python3"]
    env: ["HERB_API_KEY"]
  emoji: "🎯"
  auto_trigger: true
  auto_trigger_phase: "first"
---

# Skill Planner - 技能规划器

在每轮对话开始前，先用本技能规划技能执行流程。

## 核心功能

1. **意图分析** - 理解用户需求，识别需要的技能类型
2. **流程规划** - 生成技能执行流程图
3. **双库查询** - 同时查询记忆胶囊库和技能链数据库
4. **Token预估** - 预估本次操作消耗的token
5. **可视化输出** - 用ASCII艺术展示技能流程图

## 数据库路径

| 数据库 | 路径 | 说明 |
|--------|------|------|
| 胶囊库 | `/tmp/storage/sda1/.oray_app/cow/skills/skill-capsule-lib/capsule_db.json` | 旧版技能链 |
| **技能链库** | `/tmp/storage/sda1/skill/skill_chains.json` | 新版多肽结构数据库 ⭐ |
| 原子层 | `/tmp/storage/sda1/skill/atoms/` | 按index命名 |
| 多肽层 | `/tmp/storage/sda1/skill/polypeptides/` | 单技能封装 |
| 蛋白层 | `/tmp/storage/sda1/skill/proteins/` | 按字母命名工作流 |

## 使用方式

```bash
python scripts/skill_planner.py "用户问题或需求" --verbose
```

## 输出示例

```
🎯 技能规划器 - 分析用户需求

📋 意图识别：
   类型: 图片生成
   复杂度: 中等
   预估步骤: 3

🔗 技能流程：
   [用户问题]
        ↓
   ┌─────────────────────────────┐
   │  📝 技能链数据库查询         │
   │  - 匹配: chain_8b8d9efa      │
   │  - 来源: proteins/C.丝袜写真链│
   └─────────────────────────────┘
        ↓
   ┌─────────────────────────────┐
   │  🎨 memory-image-generation  │
   │  Token预估: ~1500           │
   └─────────────────────────────┘
        ↓
   ┌─────────────────────────────┐
   │  📤 发送结果                │
   └─────────────────────────────┘

💰 总计预估: ~1500 tokens
```

## 技能链数据库结构

```
skill_chains.json
├── structure          # 目录结构
├── skills_index       # 原子层索引（0-99）
├── peptide_index      # 多肽层索引（100-299）
├── protein_index      # 蛋白层索引（300-499）
├── user_mappings      # 用户快捷命令映射
└── usage_history      # 使用历史
```

## 执行流程

1. 接收用户问题
2. 分析意图（图片/视觉/搜索/文档/通用）
3. **优先查询技能链数据库**（新）
4. 备选查询胶囊库（旧）
5. 规划技能执行顺序
6. 预估token消耗
7. 输出流程图
8. 等待用户确认（可选）
