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
- 详见 [`Termux安装.md`](Termux安装.md)

---

## 架构

```
dbagent/
├── package/                    内核
│   ├── main.py                 入口、规划、路由、工作流
│   ├── agent_loop.py           ReAct 式读写循环
│   ├── skill_exec.py           技能解析与执行
│   ├── stopj.py                STOPJ 工作流运行时（S/P/O/T 闸门）
│   ├── task_queue.py           任务队列（执行中可继续排队新需求）
│   ├── keypool.py              API Key 池
│   ├── codetree.py             项目结构树
│   ├── treemind.py             思维导图生成
│   ├── plat/                   平台沙箱实现（win / linux / termux）
│   ├── tools/                  内置工具（ls / read / write / edit / search / fetch…）
│   └── dbagent_sys/            上下文、日志、持久 shell
│
└── workspace/
    ├── skills/                 技能库（每个技能一个目录 + skill.json）
    └── data/                   配置与数据
        ├── proteins/index.json       多步链（蛋白）
        ├── atoms/index.json          单技能（原子）
        ├── superproteins/index.json  超蛋白层（4 碱基组合）
        ├── runtime.json              语言规则
        └── work/                     运行产出（不入库）
```

### 规划分层

请求进来后按成本从低到高逐层匹配，命中即止：

```
1. 蛋白库规则   proteins/index.json 的 rules + priority   0 次 LLM
2. 蛋白关键词   keywords 精确/弱匹配                       0 次 LLM
3. 原子触发词   atoms/index.json 的 triggers               0 次 LLM
4. LLM 分类     兜底，交给模型判断意图                     1 次 LLM
```

### 配置化设计

内核不认识具体技能。以下概念全部由配置声明：

**蛋白（多步链）** — `workspace/data/proteins/index.json`

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

**原子（单技能）** — `workspace/data/atoms/index.json`

```json
{
  "skill": "person-image-search",
  "triggers": ["找图", "搜图", "找照片"],
  "strip_words": ["找图", "复刻", "图片"],
  "not_person_name": ["恋情", "复刻", "生成"]
}
```

**语言规则** — `workspace/data/runtime.json`

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

技能统一由 `skill_exec.py` 在沙箱内执行，产出同步回 `workspace/data/work/`。

执行遵循 STOPJ 工作流语法：`S` 启动 → `P{n}` 规划 → `O{n}` 循环 → `T` 闸门 → `J{n}` 跳转。

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

- [`项目架构.md`](项目架构.md) — 模块职责与数据流
- [`PROTEIN_TREE.md`](PROTEIN_TREE.md) — 蛋白树结构
- [`Termux安装.md`](Termux安装.md) — Android 部署细节
- [`问题修复.md`](问题修复.md) — 已知问题与修复记录
