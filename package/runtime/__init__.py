"""runtime —— DBAgent 运行时核心。

一套可运行的 agent 系统由四部分组成，都在本包内：

    runtime.core.prompt     系统提示词（工具声明 + 行为准则 + 项目上下文）
    runtime.core.session    会话记录（JSONL，追加写）
    runtime.core.settings   全局/项目两级配置（base_url、model 等）
    runtime.core.truncate   文本截断

    runtime.gate            STOPJ 闸门（SkillPlanner + STOPJRuntime）
    runtime.STOPJ           表达式解析 + ASC 索引 + 早期执行器（兼容保留）
    runtime.ASCTOPJ         完整三件套：BioDatabase / ASCIndexer / STOPJRuntime

调用关系：

    main._run_planned()
        └── skill_exec.execute_stopj()   真正跑技能/工具
        └── runtime.gate.GateRuntime()   闸门收口，登记流程

    main.run_task()
        └── skill_exec.SkillLibrary      技能库
        └── runtime.STOPJ.parse_stopj_expression  表达式分词

说明：本文件是显式包入口。此前 runtime/ 缺少 __init__.py，只能靠
Python 3 的隐式命名空间包工作；显式化后不受 sys.path 顺序干扰。
"""

from . import core  # noqa: F401

__all__ = ["core", "gate", "STOPJ", "ASCTOPJ"]
