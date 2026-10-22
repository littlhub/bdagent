# 前端设计完整手册（中文）

生成具有辨识度的生产级前端界面的完整方法论。动手写代码前必读。英文版为 `references/playbook.md`，
两个版本的方法论、基准数值与检查表完全一致，仅语言不同。

---

## 1. 设计思考协议

在写第一行代码前，先用文字回答四个问题：

| 问题 | 产出 |
|---|---|
| **目的 Purpose** | 一句话说清：这个界面解决什么问题、给谁用 |
| **调性 Tone** | 从下方清单选一个极端方向，绝不混搭 |
| **约束 Constraints** | 技术栈、性能预算、可访问性目标、浏览器支持 |
| **记忆点 Differentiation** | 访客离开后唯一记得的那个东西 |

**调性菜单** —— 只能选一个：

| 方向 | 标志性手法 | 常见翻车点 |
|---|---|---|
| 极致极简 Brutally minimal | 大留白、单一强调色、精确间距 | 做成了空，而不是克制 |
| 极繁混搭 Maximalist chaos | 层叠、混排、撞色、高密度 | 做成了噪音，而不是丰富 |
| 复古未来 Retro-futuristic | CRT 扫描线、霓虹、等宽字体、网格地平线 | 噱头盖过可用性 |
| 自然有机 Organic / natural | 柔和曲线、大地色、纸质纹理、植物形态 | 看起来像没做完 |
| 轻奢雅致 Luxury / refined | 衬线大标题、克制的金属色、大边距 | 滑向普通商务风 |
| 杂志编辑 Editorial | 多栏网格、规则线、首字下沉、引言块 | 小屏直接崩版 |
| 粗野原始 Brutalist / raw | 裸露边框、粗黑线、原生控件感 | 对比度与可读性翻车 |
| 玩趣玩具 Playful | 圆润造型、明快色块、弹性动效 | B 端场景显得幼稚 |
| 工业实用 Industrial | 仪表盘密度、等宽数字、发丝边框 | 高密度中丢失层级 |

**铁律**：一个清晰的概念方向 + 精确执行，胜过任何数量的装饰堆砌。极繁与极简都能成立 ——
变量是"是否经过思考"，不是"强度大小"。

---

## 2. 字体系统

字体是见效最快的差异化手段。换一套字体带来的品质感提升，超过其他任何单一决策。

### 展示字禁用清单

Inter、Roboto、Arial、Helvetica、system-ui、Open Sans、Lato、Montserrat、Poppins、Space Grotesk。
这些字体是"通用输出"的正确选择 —— 而这恰恰是问题所在。

### 配对模式

一套独特的**展示字** + 一套内敛的**正文字**。绝不用两套都张扬的字体。

| 风格方向 | 展示字 | 正文字 |
|---|---|---|
| 轻奢 / 杂志 | Playfair Display、Bodoni Moda、Cormorant Garamond | Source Serif 4、Lora |
| 粗野 / 工业 | Archivo Black、Anton、Bebas Neue | IBM Plex Sans、Public Sans |
| 复古未来 | Orbitron、Chakra Petch、Monoton | IBM Plex Mono、JetBrains Mono |
| 自然有机 | Fraunces、Bricolage Grotesque、Newsreader | Karla、Work Sans |
| 玩趣 | Fredoka、Baloo 2、Rubik Bubbles | Nunito、Quicksand |
| 技术 / 后台 | Space Mono、JetBrains Mono、IBM Plex Mono | Inter Tight、Söhne |

### 执行规则

- 按需加载：`preconnect` + `font-display: swap`，只引入真正用到的字重。
- 展示字号：首屏标题 ≥ 48px，桌面端建议 64–120px；字距 −0.02em 至 −0.04em。
- 正文行长 45–75 字符（拉丁文），行高 1.5–1.7；大标题行高 0.95–1.1。
- 用模块化字阶（如 1.25 比例）写入 CSS 变量，禁止临时手写数值。
- 数据密集界面使用等宽数字 `font-variant-numeric: tabular-nums`。
- 中文页面：中文字体（思源宋体 / 思源黑体 / 霞鹜文楷）与拉丁展示字搭配；
  正文行高 ≥ 1.7，字距 0.02em。

---

## 3. 色彩与主题

| 规则 | 细节 |
|---|---|
| 主题先行 | 明色或暗色必须主动选择，不能默认白底 |
| 主色 + 强调色 | 一个主导表面色 + 一个锐利强调色，按 60 / 30 / 10 分配 |
| 只用变量 | 所有颜色写成 CSS 变量，组件内禁止硬编码色值 |
| 对比度 | 正文 ≥ 4.5:1，大字 ≥ 3:1，界面控件 ≥ 3:1 |
| 语义别名 | `--color-bg`、`--color-surface`、`--color-text`、`--color-muted`、`--color-accent` |
| 暗色模式 | 通过 `[data-theme="dark"]` 覆盖语义令牌，组件代码不动 |
| 强调色纪律 | 单屏最多一个强调色，只留给关键动作 |

### 禁止清单

- 白底紫渐变（或蓝渐变）。
- 没有主色的、平均分配的胆小粉色系配色。
- 纯 `#000` 文字压在纯 `#fff` 上 —— 改用暖调近黑与近白。
- 仅用颜色表达状态、错误或含义。

### 令牌骨架

```css
:root {
  --color-bg: #0e0d0c;
  --color-surface: #171614;
  --color-text: #f5f1ea;
  --color-muted: #a09a90;
  --color-accent: #ff6b2c;
  --color-border: rgba(245, 241, 234, 0.12);
  --radius-sm: 4px; --radius-md: 10px; --radius-lg: 20px;
  --space-1: 4px; --space-2: 8px; --space-3: 16px; --space-4: 24px;
  --space-5: 40px; --space-6: 64px; --space-7: 96px;
  --shadow-1: 0 2px 8px rgba(0,0,0,.24);
  --shadow-2: 0 8px 28px rgba(0,0,0,.32);
  --shadow-3: 0 24px 64px rgba(0,0,0,.42);
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --dur-fast: 160ms; --dur-base: 320ms; --dur-slow: 640ms;
}
```

---

## 4. 动效设计

| 原则 | 做法 |
|---|---|
| CSS 优先 | HTML/CSS 交付物只用 CSS 实现动效，不依赖框架 |
| React 项目 | 有 Motion 库时优先使用；否则用 CSS 过渡与 Web Animations API |
| 一个编排时刻 | 一次编排好的页面入场序列（错峰 `animation-delay`），胜过一堆零散微交互 |
| 滚动触发 | 用 `IntersectionObserver` 触发，只动 `opacity` 与 `translateY` |
| 悬停反馈 | 要惊喜：缩放、字距变化、下划线扫过、背景擦除，而不只是变亮 |
| 性能 | 只动 `transform` 与 `opacity`，避开触发布局的属性 |
| 降级 | 所有动效包在 `@media (prefers-reduced-motion: no-preference)` 内，或在 `reduce` 下关闭 |

### 时长基准

| 类型 | 时长 | 缓动 |
|---|---|---|
| 微交互（悬停、按压） | 150–200ms | `ease-out` |
| 常规过渡（菜单、面板） | 300–400ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| 场景过渡（页面、弹层） | 600–800ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| 入场错峰 | 组间隔 60–120ms，共 3–6 组 | — |

### 入场序列骨架

```css
.reveal { opacity: 0; transform: translateY(24px); }
.reveal.in { opacity: 1; transform: none;
  transition: opacity var(--dur-base) var(--ease-out),
              transform var(--dur-base) var(--ease-out); }
.reveal:nth-child(2) { transition-delay: 80ms; }
.reveal:nth-child(3) { transition-delay: 160ms; }
.reveal:nth-child(4) { transition-delay: 240ms; }
@media (prefers-reduced-motion: reduce) {
  .reveal { opacity: 1; transform: none; transition: none; }
}
```

---

## 5. 空间构成

- **非对称优于居中**。首屏偏移、打破网格、让某个元素冲出画布边缘。
- **重叠**。文字压图、徽标压卡片边缘、数字压住区块分界。
- **对角线流动**。旋转跑马灯、倾斜分割线、卡片纵向错落。
- **破格**。每屏至少有一个元素故意逃出栅格系统。
- **留白充足或密度可控** —— 二选一；失败模式是"什么都中等"。
- **纵向节奏**。间距用变量梯度（4 / 8 / 16 / 24 / 40 / 64 / 96px）。
- **响应式意图**。构成决策必须有移动端答案，而不只是"自动堆叠"。

---

## 6. 背景与视觉细节

用氛围替代纯色平铺。按风格方向挑 2–3 种技法：

| 技法 | 配方 |
|---|---|
| 渐变网格 | 2–3 个大尺寸径向渐变 + 模糊 + 低透明度，铺在背景层 |
| 噪点 / 颗粒 | SVG `feTurbulence` 生成 data-URI 叠加层，透明度 3–6%，`mix-blend-mode: overlay` |
| 几何图案 | 低透明度的重复 `linear-gradient` 或 `conic-gradient` |
| 层叠透明 | 在纹理底上叠加 `backdrop-filter: blur()` 面板 |
| 戏剧阴影 | 三级高度体系（Y 偏移 2 / 8 / 24px），阴影带环境色而非纯黑 |
| 装饰边框 | 发丝线、四角括号、双描边、虚线分隔 |
| 自定义光标 | 带混合模式的小跟随点；纯触屏场景不要加 |
| 跑马灯 | 无限滚动条带，用于 Logo 墙、关键词或行情数据 |

```css
.atmosphere::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(60rem 40rem at 12% -10%, rgba(255,107,44,.28), transparent 60%),
    radial-gradient(50rem 40rem at 100% 0%, rgba(94,92,230,.22), transparent 55%);
  filter: blur(0.5px);
}
.grain::after {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 1; opacity: .05;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.8'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}
```

---

## 7. 反 AI 味检查表

每次交付前逐项检查。命中项必须修复，或明确说明理由。

| # | 检查项 | 不合格信号 |
|---|---|---|
| 1 | 展示字体 | 用 Inter / Roboto / Arial / system-ui / Space Grotesk 做展示字 |
| 2 | 配色 | 白底紫（蓝）渐变；无主色的平均分配粉色系 |
| 3 | 布局 | 首屏 = 居中标题 + 副标题 + 两个按钮 + 三张特性卡 |
| 4 | 卡片 | 一模一样的圆角卡片，统一阴影，居中图标 |
| 5 | 图标 | 通用线性图标集纯装饰、无信息含义 |
| 6 | 文案 | "无缝赋能""新一代""释放……的潜力" |
| 7 | 动效 | 所有元素同样 300ms 淡入上浮，或者干脆不动 |
| 8 | 层次 | 纯色平背景，零纹理零氛围 |
| 9 | 人格 | 换个 Logo 就变成别家产品 |
| 10 | 状态 | 缺悬停、聚焦、禁用、空态、错误态或加载态 |
| 11 | 记忆点 | 找不到一个访客能凭印象描述出来的元素 |
| 12 | 可访问性 | 对比度低于 4.5:1、焦点圈不可见、无降级动效方案 |

---

## 8. 复杂度匹配

| 视觉方向 | 代码必须包含的内容 |
|---|---|
| 极繁 | 多层背景、多种字体处理、滚动编排、自定义光标、跑马灯、完整状态覆盖 |
| 极简克制 | 元素少但间距精准、一套完美字体配对、克制的过渡、无可挑剔的对齐 |
| 杂志编辑 | 多栏网格、规则线、首字下沉、引言块、图文锚定布局 |
| 工业实用 | 高密度信息、等宽数字、发丝边框、状态色体系 |

优雅来自"把方向执行到位"，而不是"效果堆得多"。克制是一种能力。在极简方向上过度装饰，
与在极繁方向上敷衍交付，同样是错的。

---

## 9. 技术基准

| 领域 | 要求 |
|---|---|
| 语义化 | 真实地标与标题层级（`header` / `main` / `section` / `footer`，唯一 `h1`） |
| 键盘可达 | 全部功能可键盘操作；`:focus-visible` 可见描边（2px + 2px 偏移） |
| 对比度 | 正文 ≥ 4.5:1，大字 ≥ 3:1，界面控件 ≥ 3:1 |
| 点击目标 | 交互区域 ≥ 44×44px |
| 响应式 | 移动优先；构成决策有明确的移动端答案 |
| 降级动效 | `prefers-reduced-motion: reduce` 下关闭全部动效 |
| 性能 | 只动 `transform` / `opacity`；`font-display: swap`；非关键媒体懒加载 |
| 状态覆盖 | 默认、悬停、按下、聚焦可见、禁用、加载、错误、空 |
| 代码质量 | 无占位符、无 TODO、无死代码；注释解释设计意图 |

---

## 10. 交付结构

```
<project>/
├── index.html           # 结构、语义地标、内容
├── styles/
│   ├── tokens.css       # CSS 变量：颜色、字阶、间距、圆角、阴影、动效
│   ├── base.css         # 重置、排版、焦点与降级动效规则
│   └── components.css   # 组件与构成样式
├── scripts/
│   └── main.js          # IntersectionObserver 入场、跑马灯、微交互
└── README.md            # 定调说明、运行方式、令牌映射、自检结果
```

React / Vue 项目沿用同样的拆分：令牌层、基础组件、组合区块、动效 hooks。

---

## 11. 交付自检表

| 关卡 | 通过条件 |
|---|---|
| 定调说明写清楚 | 一段话说清受众、方向、字体与配色决策、记忆点 |
| 展示字 + 正文字已确定并加载 | 两套都在，且不在禁用清单内 |
| 对比度实测而非目测 | 每一组文字/底色都校验过 |
| 存在一次编排好的入场序列 | 3–6 组，间隔 60–120ms |
| 降级动效已验证 | 是 |
| 存在氛围层 | 至少 1 项纹理 / 渐变 / 阴影体系 |
| 组件状态覆盖完整 | 适用处覆盖 8 种状态 |
| 375 / 768 / 1440px 三档检查 | 无溢出、无构成崩坏 |
| 反 AI 味检查表已跑 | 0 项未修复命中 |
| 交付代码可直接运行 | 打开即用，无缺失资源 |

交付必须是可运行的代码 + 一段简短定调说明。绝不能只交付"可以怎么做"的描述。
