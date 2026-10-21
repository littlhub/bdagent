"""沙箱工具集：CLI 工具 + 沙箱系统。

目录已扁平化（原 tools/sandbox/sandbox/ 的内容上移一级）：

    toolset.py                  CLI 工具集（read/write/edit/bash...）
    core/                       沙箱内部 core 兼容层（context / logging / path_utils）
    guard.py                    命令守卫：危险删除、目录越界、密钥外泄
    persistent_shell.py         持久 shell（复用进程，降低启动开销）
    execution_coordinator.py    命令执行协调
    shell_timeout_policy.py     按命令类型决定超时
    terminal_sanitizer.py       终端输出清洗
    seccomp_fallback_policy.py  seccomp 不可用时的回退策略
    kernel.py                   PRoot 路径解析
    console_auth.py             控制台密码闸门
    mounted_folder_coordinator.py 挂载目录协调
    native_offload.py           原生卸载服务
    pty_bridge.py               PTY 桥接

原始代码来自 Android 端 DBAgent 应用（Kotlin 移植）。
"""
