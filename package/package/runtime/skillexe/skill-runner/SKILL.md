# SKILL.md - Skill Runner Plugin

**描述**：每轮调用技能并输出结果，确保回答问题时最后一轮运行技能。

## 功能

1. **每轮显示技能调用** - 每次回答前显示调用的技能名称
2. **最后一轮必执行技能** - 确保最后一步是运行某个技能
3. **Token 统计** - 记录并显示 tokens 使用情况

## 使用方法

```bash
# 执行技能并输出
python3 scripts/run_skill.py --skill <skill_name> --args "参数"

# 示例：运行图片识别
python3 scripts/run_skill.py --skill visioncustom --args "图片路径"
```

## 输出格式

```
[调用技能：visioncustom]
执行中...

📊 Token 统计：
- 总 tokens: xxx
- tokens/s: xx.x
- 输入 tokens: xxx
- 输出 tokens: xxx
```

## 配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--skill` | - | 技能名称 |
| `--args` | - | 技能参数 |
| `--output` | `/tmp/storage/sda1/图片/` | 输出目录 |