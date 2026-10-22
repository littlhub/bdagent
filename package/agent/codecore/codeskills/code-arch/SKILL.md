---
name: code-arch
description: 代码结构与架构分析器。本地仓库走 结构和架构.py（codearch，AST+正则）输出 JSON/Mermaid/PNG；GitHub 仓库走 GitDiagram 抓取已生成的交互架构图，并离线渲染 PNG。使用命令: /CA
metadata:
  cowagent:
    always: true
  emoji: "🗺️"
  requires:
    bins: ["python3"]
---

# code-arch - 代码结构与架构分析器

## 概述

code-arch 是一个**双模式**代码结构/架构分析技能：

- **本地模式**：调用 [`结构和架构.py`](../../结构和架构.py)（codearch）做 Python AST + 通用语言正则解析，输出
  - `codearch.json` — 文件级结构（函数、类、导入、SQL 表、外部 API、配置键）
  - `codearch.mmd` — 模块/调用图 Mermaid
  - `codearch.png` — 通过 kroki.io 或本地 mmdc 渲染

- **GitHub 模式**：访问 `https://gitdiagram.com/<owner>/<repo>`，抓取 GitDiagram 服务端已生成的**交互架构图**，离线提取
  - `codearch.mmd` — GitDiagram 的 Mermaid（含 subgraph + click 跳转）
  - `codearch.json` — 结构化 diagram-state（groups/nodes/edges/AI 解释）
  - `codearch.png` — kroki.io 渲染
  - 若该仓库 GitDiagram 尚未生成，则自动 `git clone --depth 1` 到临时目录走本地 codearch（需 `GITHUB_TOKEN` 走私有仓库）

## 快捷命令

```bash
/CA                          # 主入口
/CA local <repo_path>        # 分析本地仓库
/CA local <repo_path> --max-files 200
/CA github <owner/repo>      # 抓取 GitHub 仓库的 GitDiagram 架构图
```

## 使用示例

```bash
# 本地：分析当前工作区 codex-main
/CA local G:/deep工作区/codex-main --max-files 200

# 本地：只输出到指定路径
/CA local myproj --json out.json --mmd out.mmd --png out.png

# GitHub：OpenAI/codex
/CA github OpenAI/codex

# GitHub：任意仓库（需已生成或可 clone）
/CA github vercel/next.js
```

## 输出结构

```
<默认输出目录>/
├── codearch.json          # 本地: 全量分析; GitHub: GitDiagram 结构化 state
├── codearch.mmd           # Mermaid 源码
└── codearch.png           # 渲染后的 PNG（kroki.io / mmdc）
```

## 本地 vs GitHub 对比

| 维度 | 本地 | GitHub |
|------|------|--------|
| 数据源 | 本地仓库文件 | GitDiagram 已生成图；缺失则浅克隆本地分析 |
| 解析 | Python AST + 通用正则 | GitDiagram AI 生成 |
| 输出 | JSON + Mermaid + PNG | 同左 |
| 网络 | 仅渲染时可选 | 必需（gitdiagram.com / kroki.io） |
| 速度 | 秒级 ~ 分钟级 | 取决于 GitDiagram 是否已缓存 |
| 鉴权 | 无 | `GITHUB_TOKEN`（仅私有仓库） |

## 依赖

| 用途 | 说明 |
|------|------|
| `python3` | 必需 |
| `结构和架构.py` (codearch) | 本地模式必需，通过 `CODEARCH` 环境变量或脚本同目录寻找 |
| `kroki.io` 或 `mmdc` | PNG 渲染（本地 mmdc 优先） |
| `git` + `GITHUB_TOKEN` | GitHub 私有仓库 |
| `urllib` | GitHub 模式抓 GitDiagram 页面 |

## 执行流程

### 本地模式
```
/CA local <repo_path>
  ↓
_run_codearch() (调用 结构和架构.py)
  ↓
扫描 + Python AST + 通用正则 + 资源检测
  ↓
GraphBuilder 建图
  ↓
输出 JSON / Mermaid
  ↓
_render_mermaid_png (mmdc → kroki.io)
```

### GitHub 模式
```
/CA github <owner/repo>
  ↓
_http_get gitdiagram.com/<owner>/<repo> (跟随 308)
  ↓
_extract_mermaid (从 RSC 载荷抠 flowchart TD 段)
  ↓
_extract_state (解析 RSC 里的 groups/nodes/edges/explanation)
  ↓
输出 JSON / Mermaid
  ↓
_render_mermaid_png
  ↓
(若 GitDiagram 未生成 → git clone --depth 1 → 走本地 codearch)
```

## 输出示例

### 本地
```
✅ 完成。 JSON: ./codearch.json
          Mermaid: ./codearch.mmd
          PNG: ./codearch.png
```

### GitHub
```
🌐 GitDiagram: https://gitdiagram.com/OpenAI/codex
   fetched: https://gitdiagram.com/openai/codex (49192 bytes)
💾 Mermaid 已写入 ./codearch.mmd
💾 结构化 state 已写入 ./codearch.json
   生成时间: 2026-09-19T22:22:01Z  分组: 5  节点: 21  边: 20
🖼️  PNG (kroki.io): ./codearch.png (168805 bytes)

✅ GitHub (OpenAI/codex) 完成。 JSON: ./codearch.json
                  Mermaid: ./codearch.mmd
                  PNG: ./codearch.png
```

## 注意事项

- **本地模式**：`结构和架构.py` 仅 Python AST 精确，其他语言用正则（粗略），建议对 Rust/Go/Java 仓库用 `--max-files` 限流。
- **GitHub 模式**：GitDiagram 对未生成过图的仓库会回退到浅克隆 + 本地 codearch，首次跑大仓库可能较慢。
- **渲染**：未装 `mmdc` 时自动用 `kroki.io`，需外网。
- **Mermaid**：本地 codearch 默认 `max_nodes=80`，大仓库图会很密，可改 `build_mermaid` 的 `max_nodes`。

## 版本信息

- **版本**: 1.0
- **创建时间**: 2026-09-20
- **依赖**: `结构和架构.py` (本地), `kroki.io` (渲染), `gitdiagram.com` (GitHub 模式)
- **快捷命令**: /CA
