# -*- coding: utf-8 -*-
"""任务队列与后台执行器。

解决的问题：run_code_agent 是同步阻塞的（24 轮 × 每次 LLM 数十秒），
执行期间主线程停在函数栈里，input() 根本不在，用户的新需求既不能
打断也不能排队。

这里的做法：
  · 每个需求变成一个 Task，进队列
  · 单独一个 worker 线程按序取出执行
  · 主线程始终回到 input()，随时收新需求
  · 支持 cancel（打断当前）与 queue（排到后面）

线程安全：所有对任务状态的读写都过锁；输出走 emit 回调，
由调用方决定怎么串行化打印。
"""

from __future__ import annotations

import threading
import time
import traceback

# 任务状态
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


class Task:
    """一个待执行需求。"""

    _seq = 0
    _seq_lock = threading.Lock()

    def __init__(self, req, mode="code", kind="code"):
        with Task._seq_lock:
            Task._seq += 1
            self.id = Task._seq
        self.req = req
        self.mode = mode
        self.kind = kind          # code / plan / goal / workflow
        self.status = QUEUED
        self.created = time.time()
        self.started = None
        self.finished = None
        self.error = None
        self.summary = None

    def __repr__(self):
        return f"<Task#{self.id} {self.status} {self.req[:20]!r}>"


class TaskRunner:
    """单 worker 的后台任务执行器。

    runner = TaskRunner(executor, on_event)
      executor(task) -> str|None   实际执行，返回可选摘要
      on_event(text)               输出回调（已在 worker 线程中调用）

    runner.submit(req, mode)  入队
    runner.cancel_current()   请求打断当前任务
    runner.clear_queue()      清空未执行的任务
    runner.status()           查看快照
    """

    def __init__(self, executor, on_event=None):
        self._executor = executor
        self._on_event = on_event or (lambda text: None)
        self._lock = threading.RLock()
        self._queue = []
        self._current = None
        self._history = []
        self._cancel_flag = threading.Event()
        self._worker = None
        self._stop = False

    # ---------- 对外接口 ----------

    def submit(self, req, mode="code", kind="code"):
        """入队一个需求，返回 Task。重复需求会被跳过。"""
        req = (req or "").strip()
        if not req:
            return None
        with self._lock:
            # 正在跑或排队中的相同需求都不重复排
            if self._current is not None and self._current.req == req:
                return self._current
            for t in self._queue:
                if t.req == req and t.status == QUEUED:
                    return t
            task = Task(req, mode=mode, kind=kind)
            ahead = len([t for t in self._queue if t.status == QUEUED])
            self._queue.append(task)
            self._ensure_worker()
        if ahead:
            self._emit(f"\n📥 已排队 #{task.id}：{req[:40]}（前面还有 {ahead} 个）")
        else:
            self._emit(f"\n📥 已排队 #{task.id}：{req[:40]}")
        return task

    def cancel_current(self):
        """打断当前正在跑的任务。返回是否真的打断了。"""
        with self._lock:
            cur = self._current
        if cur is None or cur.status != RUNNING:
            return False
        self._cancel_flag.set()
        self._emit(f"\n⏹️ 已请求打断 #{cur.id}，将在本轮结束后停止")
        return True

    def clear_queue(self):
        """清空所有还在排队的任务，保留正在跑的。"""
        with self._lock:
            pending = [t for t in self._queue if t.status == QUEUED]
            for t in pending:
                t.status = CANCELLED
                t.finished = time.time()
            self._queue = []
        if pending:
            self._emit(f"\n🗑️ 已清空 {len(pending)} 个排队任务")
        return len(pending)

    def status(self):
        """返回 (当前任务, 排队列表, 最近完成列表) 的快照。"""
        with self._lock:
            cur = self._current
            queued = [t for t in self._queue if t.status == QUEUED]
            done = self._history[-5:]
        return cur, queued, done

    def is_busy(self):
        with self._lock:
            return self._current is not None and self._current.status == RUNNING

    def wait_idle(self, timeout=None):
        """等所有任务跑完。用于退出前收尾。"""
        deadline = None if timeout is None else time.time() + timeout
        while True:
            with self._lock:
                busy = self._current is not None or bool(self._queue)
            if not busy:
                return True
            if deadline is not None and time.time() > deadline:
                return False
            time.sleep(0.2)

    def stopped(self):
        """当前任务是否已被请求打断（供 executor 轮询）。"""
        return self._cancel_flag.is_set()

    # ---------- 内部 ----------

    def _emit(self, text):
        try:
            self._on_event(text)
        except Exception:
            pass

    def _ensure_worker(self):
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            if self._stop:
                return
            self._worker = threading.Thread(
                target=self._run, name="dbagent-worker", daemon=True)
            self._worker.start()

    def _run(self):
        while True:
            with self._lock:
                if self._stop:
                    return
                task = None
                for t in self._queue:
                    if t.status == QUEUED:
                        task = t
                        break
                if task is None:
                    # 队列空了，worker 退出；下次 submit 会重新拉起
                    self._worker = None
                    return
                self._queue.remove(task)
                task.status = RUNNING
                task.started = time.time()
                self._current = task
                self._cancel_flag.clear()

            try:
                summary = self._executor(task)
                with self._lock:
                    if self._cancel_flag.is_set():
                        task.status = CANCELLED
                    else:
                        task.status = DONE
                    task.summary = summary
            except Exception as e:
                with self._lock:
                    task.status = FAILED
                    task.error = f"{type(e).__name__}: {e}"
                self._emit(f"\n❌ #{task.id} 执行失败：{task.error}")
                traceback.print_exc()
            finally:
                with self._lock:
                    task.finished = time.time()
                    self._history.append(task)
                    self._current = None
                    self._cancel_flag.clear()

            took = (task.finished - task.started) if task.started else 0
            if task.status == CANCELLED:
                self._emit(f"\n⏹️ #{task.id} 已打断（{took:.1f}s）")
            elif task.status == DONE:
                self._emit(f"\n✅ #{task.id} 完成（{took:.1f}s）")

    def shutdown(self):
        with self._lock:
            self._stop = True
            self._cancel_flag.set()
