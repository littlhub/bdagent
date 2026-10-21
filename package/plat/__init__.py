"""平台相关实现（按操作系统的沙箱策略分目录）。

目录名用 plat 而不是 platform，避免与 Python 标准库的 platform 模块重名。

    plat/win/sandbox.py    Windows: 目录边界 + 环境变量白名单
    plat/linux/sandbox.py  Linux  : chroot 隔离

每个实现需提供：
    data_work_dir / ensure_sandbox / stage_skill / collect_outputs / run_in_sandbox
"""
