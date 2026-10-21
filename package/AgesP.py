# -*- coding: utf-8 -*-
"""AgesP.py —— 旧名兼容包装。

历史版本以此为入口，现实现已统一到 main.py。保留此文件仅为兼容
`python AgesP.py ...` 的既有调用。

与 agent.py 一致：按文件路径加载 main.py，
避免 `from main import main` 在直接执行时拿不到符号。
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
