# -*- coding: utf-8 -*-
"""agent.py —— 兼容包装，实际实现已合并到 main.py。

保留本文件是为了不破坏既有调用方式（旧版 agent.bat、外部脚本、文档里的
`python agent.py ...` 命令）。新代码请直接使用 main.py。

注意：这里不能用 `from main import main` 或 `import main`。
直接执行 `python agent.py` 时，main.py 会作为 __main__ 运行，
模块名 main 并不存在于 sys.modules 中，`from main import main`
实际会拿不到符号（甚至在某些路径下解析到自己）。
因此改为按文件路径显式加载 main.py，并复用其中的 main 函数。
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _load_main_module():
    """按文件路径加载 main.py，返回模块对象。"""
    path = os.path.join(HERE, "main.py")
    spec = importlib.util.spec_from_file_location("_dbagent_main", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_dbagent_main"] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    sys.exit(_load_main_module().main())
