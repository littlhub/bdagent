# DBAgent

蛋白/原子驱动的 AI Agent 运行时。

## 结构

- \package/\ — 内核（路由、规划、沙箱、技能执行）
- \workspace/skills/\ — 技能库
- \workspace/data/\ — 蛋白库、原子库、运行时配置

## 启动

Windows: \dbagent.batLinux/Termux: \./run.sh
## 设计

内核只负责机制，领域知识全部在配置里：

- \workspace/data/proteins/index.json\ — 多步链（rules / priority / consumes / defaults）
- \workspace/data/atoms/index.json\ — 单技能（triggers / strip_words / not_person_name）
- \workspace/data/runtime.json\ — 语言规则（尺寸说法、动词表、噪声前缀）
