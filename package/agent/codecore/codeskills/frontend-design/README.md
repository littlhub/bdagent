# 前端设计 / Frontend Design

> **一句话定位**：拒绝千篇一律的 AI 味界面，一次交付有记忆点的生产级前端代码。
> **One-liner**: Refuse generic AI slop interfaces — ship production-grade frontend code with a
> memorable point of view.

---

## 它能解决什么 / What It Solves

| 场景 | 你会遇到的问题 | 交付物 |
|---|---|---|
| 落地页 / 官网首页 | 做出来像模板，换个 Logo 就是别家 | 带定调说明的成品页面代码 |
| 后台 / 仪表盘 | 灰白卡片堆砌，毫无气质 | 暗色令牌体系 + 高密度信息布局 |
| 组件设计 | 圆角卡片 + 居中图标，千篇一律 | 独特排版与八态覆盖的组件代码 |
| 作品集 / 个人站 | 字体配色全默认，看不出个性 | 展示字配对 + 编排动效的个人站 |
| 去 AI 味改造 | 一眼被看出是 AI 生成 | 12 项反 AI 味清单逐项整改 |
| 视觉风格定调 | 颜色字号越加越乱 | 单一方向 + 60/30/10 配色令牌 |
| 动效打磨 | 全部元素同样淡入上浮 | 一次编排好的入场序列 + 滚动触发 |
| 活动 / 营销页 | 视觉不够炸，转化上不去 | 极繁方向：多层氛围 + 跑马灯 + 强动效 |

---

## 快速开始 / Quick Start

中文：

```
帮我做一个 AI 编程工具的落地页，要有辨识度，别用紫渐变和 Inter 字体，直接给能跑的代码
```

```
这个后台太像模板了，重新定一套暗色视觉方向和排版系统
```

```
做一个数据卡片组件，要有悬停惊喜和加载、空态、错误态
```

```
给作品集加一套编排动效：入场序列 + 滚动触发，要支持 prefers-reduced-motion
```

English:

```
Design a landing page for a developer tool — distinctive, no purple gradient, no Inter,
ship working code.
```

```
Our dashboard looks like a template. Define a dark visual direction and type system for it.
```

```
Build a data card component with a surprising hover state plus loading, empty and error states.
```

```
Add an orchestrated load sequence and scroll reveals to this portfolio, respecting
prefers-reduced-motion.
```

---

## 核心方法 / Core Method

1. **先定调再写代码**：目的 → 调性（选一个极端方向）→ 约束 → 记忆点。
2. **四个美学支柱**：独特排版、主色 + 锐利强调色、一次编排的动效、非对称构成。
3. **氛围替代平铺**：渐变网格、噪点颗粒、戏剧阴影、装饰边框，按方向挑 2–3 种。
4. **反 AI 味清单**：12 项硬检查，字体、配色、布局、文案、动效、状态全覆盖。
5. **复杂度匹配方向**：极繁要堆到位，极简要克制到精准，两者都靠执行而非数量。

---

## 关键基准 / Key Benchmarks

| 项目 | 基准值 |
|---|---|
| 首屏展示字 | ≥ 48px（桌面建议 64–120px），字距 −0.02em ~ −0.04em |
| 正文行长 / 行高 | 45–75 字符 / 1.5–1.7（中文 28–40 字，行高 ≥1.7） |
| 配色比例 | 主色 60% / 辅助 30% / 强调 10%，单屏一个强调色 |
| 对比度 | 正文 ≥ 4.5:1，大字 ≥ 3:1，控件 ≥ 3:1 |
| 入场序列 | 3–6 组，间隔 60–120ms |
| 动效时长 | 微交互 150–200ms / 常规 300–400ms / 场景 600–800ms |
| 缓动 | `cubic-bezier(0.16, 1, 0.3, 1)` |
| 阴影层级 | 3 级，Y 偏移 2 / 8 / 24px |
| 点击目标 | ≥ 44×44px |
| 焦点样式 | 2px 实线 + 2px 偏移 |
| 禁用清单 | Inter / Roboto / Arial / system-ui / Space Grotesk 做展示字；白底紫渐变 |
| 状态覆盖 | 默认 / 悬停 / 按下 / 聚焦可见 / 禁用 / 加载 / 错误 / 空 |

---

## 文件结构 / Structure

```
frontend-design/
├── SKILL.md                 # 平台规范 frontmatter + 英文核心方法论 + 语言路由
├── references/
│   ├── playbook.md          # 英文完整手册：设计协议、字体配对、色彩、动效、构成、氛围、检查表
│   └── zh-CN.md             # 完整中文手册（结构与英文版一一对应）
├── README.md                # 本文件
├── _meta.json               # 发布元信息
└── _icon.png                # 技能图标
```

---

## 双语支持 / Bilingual

- 中文提问 → 自动加载 `references/zh-CN.md`，全程中文输出，注释与文档用中文。
- English or any other language → uses the English body and `references/playbook.md`.
- 两种语言的方法论、风格清单、基准数值、检查表完全一致，仅语言不同。

---

## 作者与版本 / Author & Version

- **作者 / Author**: zxh
- **版本 / Version**: 1.0.0
- **分类 / Category**: design
- **运行时 / Runtime**: generic
