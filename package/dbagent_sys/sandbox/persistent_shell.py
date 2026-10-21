"""dbagent_sys.sandbox.persistent_shell 转发到 tools.sandbox.persistent_shell。"""

from __future__ import annotations

import os
import sys

_package_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _package_dir not in sys.path:
    sys.path.insert(0, _package_dir)

from tools.sandbox.persistent_shell import *  # noqa: F401,F403
from tools.sandbox import persistent_shell as _impl  # noqa: F401

# 逐个转发公开符号，保证 `from dbagent_sys.sandbox.persistent_shell import X` 可用
for _name in dir(_impl):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_impl, _name)
