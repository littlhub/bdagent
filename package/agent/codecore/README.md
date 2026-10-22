# codecore — AiCode 编程内核的 Python 移植

从 [jieapi/AiCode](https://github.com/jieapi/AiCode) 的 Kotlin 实现移植，保留原有的
MVI 状态机、上下文预算与压缩、授权策略与检查点回滚语义，去掉 Android 依赖
（Room、Hilt、OkHttp、SystemClock、FileLogger）。

## 文件映射

| Kotlin 源文件 | Python 模块 |
|---|---|
| `domain/model/AgentMessage.kt` | `messages.py` |
| `domain/workflow/StatefulAgentWorkflow.kt` | `workflow.py` |
| `domain/workflow/ContextTokenEstimator.kt` | `context.py` |
| `domain/workflow/ContextCompactor.kt` | `context.py` |
| `settings/domain/model/ModelContextPolicy.kt` | `context.py` |
| `domain/permission/ShellCommandParser.kt` | `shell_parser.py` |
| `domain/permission/BuiltInSafeCommands.kt` | `safe_commands.py` |
| `domain/permission/ToolPermissionPolicyEngine.kt` | `permission.py` |
| `domain/checkpoint/CheckpointManager.kt` | `checkpoint.py` |
| `domain/tool/FileVersionRegistry.kt` | `versions.py` |
| `prompt/SystemPromptProvider.kt`（ProjectRuleSource） | `rules.py` |
| `prompt/PromptFragmentCatalog.kt` + `PromptFragmentResolver.kt` + `SystemPromptProvider.kt` | `prompt.py` |
| `workspace/domain/WorkspacePathMapper.kt` | `paths.py` |
| `workspace/domain/LocalFileAccess.kt` | `file_access.py` |
| `agent/domain/tool/file/FileTools.kt` | `tools.py` |
| `agent/domain/notification/AgentNotificationCenter.kt` | `notifications.py` |
| `agent/domain/notification/AgentNotificationFormatter.kt` + `AgentEventInjector.kt` | `notification_format.py` |
| `agent/domain/provider/OpenAIAdapter.kt`（最小实现） | `providers.py` |

## 项目规则（CLAUDE.md）

项目规则是**兜底文件**，不是普通文档：两处来源（全局 `~/.aicode` 与工作区根目录）各自优先
`AGENTS.md`，不存在时回退 `CLAUDE.md`，正文经 `{{AICODE_PROJECT_RULES_GLOBAL}}` /
`{{AICODE_PROJECT_RULES_PROJECT}}` 注入 `99-runtime-context.md`，全局在前、工作区在后。

```python
from codecore.rules import load_project_rules
rules = load_project_rules("~/.aicode", "/root/workspace")
```

## 路径约束

AI 只使用容器路径，工具层经 `WorkspacePathMapper` 映射到宿主真实文件：

| 容器路径 | 宿主路径 |
|---|---|
| `~/workspace/…` | 工作区根目录 |
| `/root/.aicode/…` | AI 配置目录（先于通用 `/` 规则匹配） |
| `/etc/…` | 当前 rootfs 下对应文件 |
| 相对路径 | 工作区根下 |

写入走原子替换：同目录临时文件 → 写入 → fsync → 长度校验 → `os.replace`，
不留半截文件、不残留 `.aicode-tmp-*.part`。

## 用户插入与排队

`AgentNotificationCenter` 管会话级待送通知，支持五类：后台任务、子代理、代理间消息、
模式切换、**用户插话**。送达两条路径：

- **搭车**（首选）：每批工具执行完后 peek 出通知，注入本批最后一条工具结果的 `notifications` 字段，
  本轮内即送达；结果落库后才 ack，中途取消则留在队列。
- **兜底**：整轮没调用任何工具时，peek 后作为用户消息（`notification_message` 事件）触发新一轮。

用户插话与系统事件的关键区别：前者按用户消息处理（纯用户插话时不加 `[后台任务通知]` 前缀），
后者明确告知模型「不是来自用户的消息，不要视为确认或回答」。

## 架构

状态不可变，动作驱动纯函数 `reduce_state`，产出的副作用由主循环执行：

```
AgentAction --reduce_state--> (AgentSessionState, [AgentSideEffect])
                                      |
                                      v
                     CallLlm / PersistUser / RequestPermission
                     ExecuteToolBatch / CancelToolBatch
```

一轮完整链路：`InitRequest` → 估算与压缩 → `CallLlm` → 流式累计 →
`LlmResponse` → 逐个 `RequestPermission` → 全部批准后 `ExecuteToolBatch` →
按原始顺序组装 tool 结果 → `ToolBatchFinished` → 再 `CallLlm`。

## 用法

```python
import asyncio
from codecore import (
    AgentContext, AgentMode, ContextCompactor, ModelMetadata,
    StatefulAgentWorkflow, ToolPermissionPolicyEngine,
)

workflow = StatefulAgentWorkflow(
    provider=my_provider,                 # 实现 complete_stream() 的异步生成器
    tools=[...],                          # 实现 name/description/execute/to_json_schema
    policy_engine=ToolPermissionPolicyEngine(),
    compactor=ContextCompactor(summarize=my_summarizer),
    metadata=ModelMetadata(context_tokens=128_000),
    permission_resolver=ask_user,         # async (ToolCall, EvalResult) -> bool
)

async def main():
    async for event in workflow.run("帮我看下这个项目", AgentContext(project_root=".")):
        print(event)

asyncio.run(main())
```

## 与 App 实现的差异

- 提示词构建（`SystemPromptProvider`）、Provider 适配器（OpenAI/Anthropic/Gemini）、
  子代理、技能、MCP、Room 持久化未移植，通过 `provider` / 回调注入。
- 压缩不落库，只在内存替换消息；持久化由调用方通过 `persist_user` 回调处理。
- 检查点元数据用 `manifest.json` 替代 Room 表。
- 退出 PLAN 的审批（`plan_approval_resolver`）保留接口，当前主循环未挂载该流程。

## 验证

```bash
python3 -m codecore.demo              # 11 个端到端场景
python3 -m codecore.test_codecore     # 权限、预算、压缩、检查点、路径、通知断言
python3 -m codecore.task_calculator   # 真实模型端到端：生成计算器页面
```
