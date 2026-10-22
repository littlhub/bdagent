"""容器路径与宿主路径的双向映射，对应 feature/workspace/domain/WorkspacePathMapper.kt。

AI 只看到容器路径，工具层负责映射到宿主真实文件。三条确定映射：
- `~/workspace[/…]` → 宿主工作区目录（容器内 bind mount，写它即写宿主）；
- `/root/.aicode[/…]` → 宿主 AI 配置目录（独立于 rootfs，升级不丢）；
- 其它容器绝对路径 `/etc/…` → 当前 profile rootfs 下的对应文件；
- 相对路径 `src/Main.kt` → 挂到工作区根下。

`/root/.aicode` 必须先于通用 `/`→rootfs 规则匹配，否则会落到 rootfs 内的临时副本。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

CONTAINER_ROOT = "~/workspace"
AICODE_ROOT = "/root/.aicode"


def expand_home(path: str, home: str) -> str:
    """展开 `~` 或 `~/` 前缀为当前环境的 home；其它路径原样返回。"""
    if path == "~":
        return home
    if path.startswith("~/"):
        return home.rstrip("/") + path[1:]
    return path


@dataclass
class WorkspacePathMapper:
    """容器路径 ↔ 宿主路径。

    workspace_root: 宿主上的工作区根目录
    rootfs_root: 宿主上的容器 rootfs 目录（容器内 `/` 即此目录）
    aicode_dir: 宿主上的 AI 配置目录（容器内 `/root/.aicode`）
    home: 容器内的家目录，用于展开 `~`
    extra_bindings: 额外挂载，形如 ["宿主源:容器目标"]
    """

    workspace_root: str
    rootfs_root: str
    aicode_dir: str
    home: str = "/root"
    extra_bindings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.workspace_root = os.path.abspath(self.workspace_root)
        self.rootfs_root = os.path.abspath(self.rootfs_root)
        self.aicode_dir = os.path.abspath(self.aicode_dir)

    # ── 容器 → 宿主 ──────────────────────────────────────────────────────

    @property
    def _container_root(self) -> str:
        return self.home.rstrip("/") + "/workspace"

    def _extra_mounts(self) -> List[Tuple[str, str]]:
        """解析额外挂载，按容器目标长度降序（最长前缀优先，避免嵌套挂载歧义）。"""
        mounts: List[Tuple[str, str]] = []
        for binding in self.extra_bindings:
            index = binding.find(":")
            if index >= 0:
                src, dst_raw = binding[:index].strip(), binding[index + 1:].strip()
            else:
                src, dst_raw = binding.strip(), binding.strip()
            dst = expand_home(dst_raw, self.home).rstrip("/")
            if src and dst:
                mounts.append((src, dst))
        return sorted(mounts, key=lambda pair: len(pair[1]), reverse=True)

    def _mounted_host_file(self, path: str) -> Optional[str]:
        for src, dst in self._extra_mounts():
            if path == dst or path == dst + "/":
                return src
            if path.startswith(dst + "/"):
                return os.path.join(src, path[len(dst) + 1:])
        return None

    def to_host_path(self, path: str) -> str:
        """把 AI 提供的路径解析为宿主真实路径。"""
        ws_root = self._container_root
        expanded = expand_home(path.strip(), self.home)

        if expanded in (ws_root, ws_root + "/", CONTAINER_ROOT, CONTAINER_ROOT + "/"):
            return self.workspace_root
        if expanded.startswith(ws_root + "/"):
            return os.path.join(self.workspace_root, expanded[len(ws_root) + 1:])
        if expanded in (AICODE_ROOT, AICODE_ROOT + "/"):
            return self.aicode_dir
        if expanded.startswith(AICODE_ROOT + "/"):
            return os.path.join(self.aicode_dir, expanded[len(AICODE_ROOT) + 1:])

        mounted = self._mounted_host_file(expanded)
        if mounted is not None:
            return mounted
        if expanded.startswith("/"):
            return os.path.join(self.rootfs_root, expanded.lstrip("/"))
        return os.path.join(self.workspace_root, expanded)

    # ── 宿主 → 容器 ──────────────────────────────────────────────────────

    def _mounted_container_path(self, absolute: str) -> Optional[str]:
        for src, dst in self._extra_mounts():
            src_abs = os.path.abspath(src)
            if absolute == src_abs:
                return dst
            if absolute.startswith(src_abs + "/"):
                return dst + "/" + absolute[len(src_abs) + 1:]
        return None

    def to_container_path(self, host_path: str) -> str:
        """把宿主路径还原为容器路径，用于回显给 AI。"""
        root_path = self.workspace_root.replace("\\", "/")
        aicode_path = self.aicode_dir.replace("\\", "/")
        rootfs_path = self.rootfs_root.replace("\\", "/")
        absolute = os.path.abspath(host_path).replace("\\", "/")
        raw = host_path.strip().replace("\\", "/")
        resolved_ws = self._container_root.replace("\\", "/")

        if absolute == root_path:
            return CONTAINER_ROOT
        if absolute.startswith(root_path + "/"):
            return CONTAINER_ROOT + "/" + absolute[len(root_path) + 1:]
        if raw in (resolved_ws, resolved_ws + "/") or absolute == resolved_ws:
            return CONTAINER_ROOT
        if raw.startswith(resolved_ws + "/"):
            return CONTAINER_ROOT + "/" + raw[len(resolved_ws) + 1:]
        if absolute.startswith(resolved_ws + "/"):
            return CONTAINER_ROOT + "/" + absolute[len(resolved_ws) + 1:]
        if absolute == aicode_path:
            return AICODE_ROOT
        if absolute.startswith(aicode_path + "/"):
            return AICODE_ROOT + "/" + absolute[len(aicode_path) + 1:]
        if absolute == rootfs_path:
            return "/"
        if absolute.startswith(rootfs_path + "/"):
            return "/" + absolute[len(rootfs_path) + 1:]

        mounted = self._mounted_container_path(absolute)
        return mounted if mounted is not None else host_path

    # ── 工作区边界 ────────────────────────────────────────────────────────

    def is_inside_workspace(self, host_path: str) -> bool:
        """宿主路径是否落在工作区内（解析符号链接后判断，防止 ../ 与软链逃逸）。"""
        root = os.path.realpath(self.workspace_root)
        target = os.path.realpath(host_path)
        return target == root or target.startswith(root + os.sep)
