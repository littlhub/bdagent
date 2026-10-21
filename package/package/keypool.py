# -*- coding: utf-8 -*-
"""API Key 池 —— 多 Key 轮转 + 失败冷却，供并行分支分摊限流。

来源（按顺序去重合并）：
    · 环境变量 DBAGENT_KEYS（逗号/空白分隔）
    · <project>/key.txt（每行一个 token，也接受 JSON 对象）
    · ~/.dbagent/key.json（token 或 {"api_key": ...}）
    · ~/.dbagent/agent_coder.json（{"api_key": ...}）

并行分支与 fan-out 的子任务各自 pick() 一个 Key，天然错开；
遇到 429/5xx/超时 report_failure() 后进入冷却，后续选择自动跳过。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

DEFAULT_COOLDOWN = 60          # 限流/临时失败的冷却秒数
INVALID_KEY_COOLDOWN = 600     # 401/403 这类无效 Key 的冷却秒数

_lock = threading.Lock()
_pool: "KeyPool | None" = None


def _extract_keys(text):
    """从一段文本里抽取 Key：JSON 对象、裸 token、多行列表都支持。"""
    text = (text or "").strip()
    if not text:
        return []

    def _from_obj(data):
        out = []
        if isinstance(data, dict):
            for field in ("api_key", "key", "token"):
                v = data.get(field)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
                    break
        elif isinstance(data, str) and data.strip():
            out.append(data.strip())
        return out

    def _valid(s):
        return len(s) >= 16 and re.fullmatch(r"[A-Za-z0-9._\-]+", s) is not None

    # 整段就是一个 JSON 对象（多行 pretty print 也能直接解析）
    if text.startswith("{"):
        try:
            return [k for k in _from_obj(json.loads(text)) if _valid(k)]
        except ValueError:
            pass

    keys = []
    for line in text.splitlines():
        s = line.strip().strip(",")
        if not s or s.startswith("#"):
            continue
        if s.startswith("{"):
            try:
                keys.extend(k for k in _from_obj(json.loads(s)) if _valid(k))
            except ValueError:
                pass
            continue
        # 多行 JSON 的片段行（"api_key": "..."）不是 key，直接跳过
        if '"' in s or ":" in s:
            continue
        for part in re.split(r"[,\s]+", s):
            part = part.strip().strip('"').strip("'")
            if _valid(part):
                keys.append(part)
    return keys


def _candidate_paths():
    here = os.path.dirname(os.path.abspath(__file__))      # package/
    project = os.path.dirname(here)                        # dbagent/
    home = os.path.expanduser("~")
    return [
        os.path.join(project, "key.txt"),
        os.path.join(home, ".dbagent", "key.json"),
        os.path.join(home, ".dbagent", "agent_coder.json"),
    ]


class KeyPool:
    def __init__(self, keys=None):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._keys = []
        self._idx = 0
        self._cooldown = {}
        self._inflight = {}   # key -> 当前占用数（单 key 并发=1）
        for k in keys or []:
            self.add(k)

    def __len__(self):
        return len(self._keys)

    @property
    def keys(self):
        return list(self._keys)

    def add(self, key):
        k = str(key or "").strip()
        if k and k not in self._keys:
            self._keys.append(k)

    def acquire(self, timeout=None):
        """等待并占用一个空闲 Key（被占用或冷却中的跳过）。

        子任务开始前调用，用完必须 release()。单 key 并发=1，
        多个 key 时总并发 = key 数，任务完成快的先拿到空出来的 key。
        """
        deadline = time.time() + timeout if timeout else None
        with self._cond:
            while True:
                now = time.time()
                for k in self._keys:
                    if self._cooldown.get(k, 0) <= now and not self._inflight.get(k):
                        self._inflight[k] = 1
                        return k
                if deadline and time.time() >= deadline:
                    return ""
                self._cond.wait(timeout=1.0 if not deadline else max(0.1, deadline - time.time()))

    def release(self, key):
        with self._cond:
            if key:
                self._inflight.pop(str(key), None)
            self._cond.notify_all()

    def pick(self, exclude=None):
        """轮转取一个可用 Key；全部处于冷却时取最早解冻的那个。"""
        exclude = set(exclude or ())
        with self._lock:
            if not self._keys:
                return ""
            now = time.time()
            usable = [k for k in self._keys
                      if self._cooldown.get(k, 0) <= now and k not in exclude]
            if not usable:
                fallback = [k for k in self._keys if k not in exclude] or list(self._keys)
                if not fallback:
                    return ""
                return min(fallback, key=lambda k: self._cooldown.get(k, 0))
            key = usable[self._idx % len(usable)]
            self._idx += 1
            return key

    def report_failure(self, key, cooldown=DEFAULT_COOLDOWN):
        with self._lock:
            if key:
                self._cooldown[str(key)] = time.time() + cooldown

    def report_success(self, key):
        with self._lock:
            self._cooldown.pop(str(key), None)

    def status(self):
        """返回 [(key_masked, 剩余冷却秒数)]，供调试/展示。"""
        now = time.time()
        with self._lock:
            return [(k[:6] + "***", max(0, int(self._cooldown.get(k, 0) - now)))
                    for k in self._keys]


def load_pool(paths=None):
    keys = []
    env = os.environ.get("DBAGENT_KEYS") or ""
    if env.strip():
        keys.extend(_extract_keys(env))
    for path in (paths or _candidate_paths()):
        try:
            with open(path, encoding="utf-8-sig") as f:
                keys.extend(_extract_keys(f.read()))
        except OSError:
            continue
    return KeyPool(keys)


def get_pool():
    """进程级单例（多线程共享轮转状态）。"""
    global _pool
    with _lock:
        if _pool is None:
            _pool = load_pool()
        return _pool


def reload_pool():
    global _pool
    with _lock:
        _pool = load_pool()
        return _pool
