# DBAgent

蛋白/原子驱动的 AI Agent 运行时。内核只负责机制，领域知识（技能链、匹配规则、语言规则）全部存在配置里，改配置即可扩展能力，不必修改代码。

支持 **Windows**、**Linux/容器**、**Termux（Android）** 三个平台，各自有独立的沙箱实现。

---

## 快速开始

| 平台 | 启动 | 配置密钥 |
|---|---|---|
| Windows | 双击 `dbagent.bat` | 首次运行按提示，或 `/config` |
| Linux / 容器 | `./run.sh` | 同上 |
| Termux (Android) | `bash install-termux.sh` 后 `./run.sh` | 同上 |

### 依赖

```bash
pip install -r requirements.txt
```

核心依赖：`requests`、`Pillow`、`python-docx`、`openpyxl`、`python-pptx`、`pypdf`、`reportlab`。

### 命令

```bash
./run.sh                  # 菜单 / 对话
./run.sh --setup          # 配置 API Key
./run.sh plan  "需求"      # 只做规划，不改代码
./run.sh apply "需求"      # 规划 + 生成改动，确认后应用
./run.sh chat             # AI 对话开发模式
./run.sh <项目路径>        # 分析项目结构（报告 + 思维导图）
```

Windows 的 `dbagent.bat` 行为与 `run.sh` 完全一致（`run.sh` 是参考实现）。

也可以直接跑：`python package/main.py`（`agent.py` / `AgesP.py` 是兼容包装）。

---

## 平台支持

三套沙箱实现位于 `package/plat/`，按平台自动选择。

| 平台 | 目录 | 隔离方式 |
|---|---|---|
| **Windows** | `plat/win/sandbox.py` | 目录边界 + 环境变量白名单（无 chroot）。设 `DBAGENT_SANDBOX_STRICT=1` 时尝试受限令牌启动，需要 pywin32，失败自动回退 |
| **Linux** | `plat/linux/sandbox.py` | chroot 隔离。chroot 根只含 Python 运行时与技能脚本副本，工作目录固定 `/work` |
| **Termux** | `plat/termux/sandbox.py` | 目录边界 + 环境变量白名单。Termux 普通用户态无 chroot 权限，故不用 chroot |

每个实现需提供同一组接口：`data_work_dir`、`ensure_sandbox`、`stage_skill`、`collect_outputs`、`run_in_sandbox`。

### Termux 安装

```bash
bash install-termux.sh
```

脚本会：
- 用 `pkg` 安装 Python 与编译依赖
- 安装 `requirements.txt` 里的核心包
- 可选包（`lxml`、`pdfplumber`、`pdf2image`）按 best-effort 处理，编译失败不影响主流程

Termux 注意事项：
- 沙箱根放在 `$PREFIX/tmp` 下，不是 `data/work`（Android 部分分区不保留可执行位）
- 无 root 时不做 chroot，隔离强度弱于 Linux，靠目录边界保证产出只落 `work/`
- 详见 [`Termux安装.md`](memory/docs/Termux安装.md)

---

## 网络与超时

### 优先 IPv4

`package/main.py` 与 `package/tools/sandbox/net.py` 在导入时会包装
`socket.getaddrinfo`，**只返回 IPv4 地址**（没有 IPv4 时才回退 IPv6）。

原因：Windows 上 IPv6 路由常不通，而系统按解析顺序先试 IPv6，
每次连接要干等 20s 才回落。实测同一接口：

| 方式 | 耗时 |
|---|---|
| IPv4 直连 | 0.26s |
| IPv6 直连 | 20.00s（TimeoutError） |
| 默认连接（先试 IPv6） | 42.34s |

修复后模型调用从 45~105s 降到 1.7~2.1s，14 个新闻源抓取从 65.8s 降到 1.0s。

设 `DBAGENT_IPV4_FIRST=0` 可关闭；纯 IPv6 环境无需设置。

### 技能超时

技能默认单次执行预算 300 秒（`skills/exec.py` 与 `plat/win/sandbox.py` 的
`EXEC_TIMEOUT`）。耗时更长的技能可在自己的 `skill.json` 里声明：

```json
{
  "main": "scripts/xxx.py",
  "timeout": 420
}
```

解析优先级：调用方显式传参 > `skill.json` 的 `timeout` > 全局默认值。

### 提示

- 读 JSON 配置用 `encoding="utf-8-sig"`：Windows 下手工编辑的文件常带 BOM，
  用 `utf-8` 读会抛 `ValueError`，表现为「配置写了却不生效」
- 技能用 `[GA]/[GC]/[GG]` 这类方括号标签标记阶段，主程序会实时转发这些行
  及其缩进子行，用户能看到中间过程而不是干等

---

## 架构

```
dbagent/
├── package/                      内核
│   ├── main.py                   入口、规划、路由、工作流
│   ├── agent/                    ReAct 式读写循环
│   ├── skills/                   技能解析、执行、STOPJ 运行时
│   ├── tools/                    内置工具（ls / read / write / edit / search / fetch…）
│   ├── runtime/                  运行时（AiCode 内核、skillexe、结构分析）
│   ├── prompts/                  提示词模板
│   ├── infra/                    日志、缓存等基础设施
│   ├── plat/                     平台沙箱（win / linux / termux）
│   ├── dbagent_sys/              上下文、日志、持久 shell
│   ├── data/                     内核自带数据
│   ├── agent_loop.py             兼容层，re-export agent/
│   └── skill_exec.py             兼容层，re-export skills/
│
├── memory/docs/                  用户文档（架构、Termux 安装、蛋白树）
│
└── workspace/
    ├── skills/                   技能库（每个技能一个目录 + skill.json）
    └── data/
        ├── dynamic/              配置与数据（可整体替换的「基因库」）
        │   ├── proteins/index.json       多步链（蛋白）
        │   ├── polypeptides/index.json   密码子组合（多肽）
        │   ├── atoms/index.json          单技能（原子）
        │   ├── superproteins/index.json  超蛋白层（4 碱基组合）
        │   ├── words/                    词表（问候语、泛词、别名等）
        │   └── runtime.json              语言规则
        │
        └── work/                 运行产出（不入库）
```

> `data/dynamic/` 是数据与代码分离的关键：整目录可替换、可分发，
> 内核只按约定读，不硬编码任何技能名。
> 产出单独落在同级 `data/work/`，替换 `dynamic/` 不会丢产物。

### 仓库里没有 `workspace/`

技能库（`workspace/skills/`）与基因库（`workspace/data/`）**不随代码发布**：
它们体积大、含各人本地产出，而且可以独立替换。拿到代码后需要自备：

- 技能：放进 `workspace/skills/<名称>/`，每个技能一个 `skill.json`
- 基因库：放进 `workspace/data/dynamic/`（结构见上）
- 产物目录 `workspace/data/work/` 会自动创建

### 规划分层

请求进来后按成本从低到高逐层匹配，命中即止：

```
1. 蛋白库规则   dynamic/proteins/index.json 的 rules + priority   0 次 LLM
2. 蛋白关键词   keywords 精确/弱匹配                       0 次 LLM
3. 原子触发词   dynamic/atoms/index.json 的 triggers               0 次 LLM
4. LLM 分类     兜底，交给模型判断意图                     1 次 LLM
```

### 配置化设计

内核不认识具体技能。以下概念全部由配置声明：

**蛋白（多步链）** — `workspace/data/dynamic/proteins/index.json`

```json
{
  "gene_chain": "APV",
  "rules": ["复刻", "参考(?:这|该)?(?:张)?(?:图|照片)"],
  "priority": 10,
  "skill_chain": ["person-image-search", "visioncustom", "agnes-image"],
  "polypeptide_tree": {
    "nodes": [
      {"skill": "person-image-search", "consumes": []},
      {"skill": "visioncustom", "consumes": ["prev_file"],
       "defaults": {"-p": "描述人物外貌"}, "produces_text": true},
      {"skill": "agnes-image", "consumes": ["prev_text"],
       "defaults": {"prefix": "根据以下外貌描述绘制："}}
    ]
  }
}
```

- `rules` / `priority` — 匹配规则与优先级
- `consumes` — 该步吃上一步的**文件**（`prev_file`）还是**文字**（`prev_text`）
- `produces_text` — 该步输出可作为后续提示词
- `defaults` — 必填参数缺省值

**原子（单技能）** — `workspace/data/dynamic/atoms/index.json`

```json
{
  "skill": "person-image-search",
  "triggers": ["找图", "搜图", "找照片"],
  "strip_words": ["找图", "复刻", "图片"],
  "not_person_name": ["恋情", "复刻", "生成"]
}
```

**语言规则** — `workspace/data/dynamic/runtime.json`

```json
{
  "size": {"ratio_pattern": "(?<!\\d)(\\d{1,4})\\s*[:：]\\s*(\\d{1,4})(?!\\d)",
           "words": {"竖图": "9:16", "横屏": "16:9"}},
  "person_name": {"verb_pattern": "写|改|做|生成|查|找", "min_length": 2, "max_length": 5},
  "web": {"hints": ["新闻", "联网", "百度"], "weak_news": ["新闻"]},
  "image_providers": {
    "agnes": {"mode": "enum", "allowed_ratios": ["1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"]}
  }
}
```

所有配置文件按 mtime 缓存，改动后自动重载，无需重启。

### 跨步骤数据流

链上每一步的输入由 `consumes` 决定，另有显式占位符：

- `{prev}` — 上一步产出的文件路径
- `{prev_text}` — 上一步的文字结果

例如「形象复刻」链：搜图产出的图片喂给视觉识别，识别出的文字描述再喂给生图。

### 生图比例适配

不同生图 API 支持的比例各不相同。`image_providers` 声明每个提供商的能力（`enum` 固定比例 / `size` 只认像素 / `free` 任意），超出能力时按最接近的合法比例生成后**裁剪**到目标比例，因此 `658:1250` 这类任意比例也能得到正确成品。

---

## 技能

技能放在 `workspace/skills/<名称>/`，必须有 `skill.json` 声明入口：

```json
{"main": "scripts/run.py"}
```

技能统一由 `skills/exec.py` 在沙箱内执行，产出同步回 `workspace/data/work/`。

执行遵循 STOPJ 工作流语法：`S` 启动 → `P{n}` 规划 → `O{n}` 循环 → `T` 闸门 → `J{n}` 跳转。

---

## 蛋白库自动登记

库里没有的链，模型现场规划并**执行成功后**会自动固化进蛋白库，
下次同样的需求直接本地命中，不再花 token。

```
💾 已登记蛋白 GAA：图片加字 → person-image-search → agnes-image
```

只在「规划来自 LLM」且「整条链执行成功」且「至少两步」时才登记，
失败的链不会入库——否则后续同样需求会命中错误流程。

对话里也可以手动管理：

```
/protein list                              列出所有蛋白
/protein new 名称 --skills a,b,c            登记新链（--keywords / --rules 可选）
/protein del 基因码                         删除
```

登记时会校验每个技能是否真实存在，并自动分配未被占用的密码子。

---

## API Key

配置优先级：环境变量 > `~/.dbagent/key.json` > 项目内 `key.json`。

```json
{
  "api_key": "sk-...",
  "base_url": "https://apihub.agnes-ai.com/v1",
  "model": "agnes-2.5-flash",
  "agnes_api_key": "sk-..."
}
```

- `api_key` — 对话模型（规划、问答、视觉识别）
- `agnes_api_key` — 生图专用，未配置时回退 `api_key`

对话中可用 `/config` 配置主 Key，`/imagekey` 单独配置生图 Key。

---

## 文档

- [`项目架构.md`](memory/docs/项目架构.md) — 模块职责与数据流
- [`PROTEIN_TREE.md`](memory/docs/PROTEIN_TREE.md) — 蛋白树结构
- [`Termux安装.md`](memory/docs/Termux安装.md) — Android 部署细节
