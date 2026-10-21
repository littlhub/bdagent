---
name: skill-tracker
description: 技能调用追踪。记录每次技能执行的时间、耗时、token 与成败，支持查看历史记录和统计摘要。使用命令: /ST
metadata:
  emoji: "📊"
---

# skill-tracker - 技能追踪

记录技能调用的历史与统计，数据落盘在技能自身目录的 `data/` 下。

## 用法

```bash
python scripts/tracker.py record --skill pptx --ok --duration 1.2 --tokens 500
python scripts/tracker.py show            # 最近 20 条
python scripts/tracker.py show --limit 50
python scripts/tracker.py stats           # 按技能汇总
python scripts/tracker.py clear           # 清空历史
```

## 参数

| 参数 | 说明 |
|------|------|
| `--skill` | 技能名（必填，多个用逗号分隔） |
| `--ok` / `--fail` | 执行结果，默认 ok |
| `--duration` | 耗时（秒） |
| `--tokens` | token 消耗 |
| `--limit` | show 时显示条数，默认 20 |

## 数据文件

- `data/history.json` — 调用历史（追加写）
- 可用环境变量 `DBAGENT_DATA_DIR` 覆盖数据目录

## 输出示例

```
📊 技能统计
总调用 42 次，成功 40 次（95.2%）
  1. pptx           12 次  平均 0.85s
  2. docx            8 次  平均 1.20s
```
