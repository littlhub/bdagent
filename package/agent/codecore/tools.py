"""文件与命令工具，对应 domain/tool/file/FileTools.kt 与 container/ 的基础工具。

所有文件操作都经 LocalFileAccess：路径由 WorkspacePathMapper 映射，
AI 只看到容器路径，写入走原子替换。
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .file_access import FileAccessError, LocalFileAccess, NoSuchFile
from .workflow import error, success

MAX_READ_CHARS = 200_000


@dataclass
class ReadFileTool:
    access: LocalFileAccess
    name: str = "readFile"
    description: str = "读取文件内容（工作区路径用 ~/workspace/... 或相对路径）。"
    capabilities: frozenset = frozenset({"READ_WORKSPACE"})

    def to_json_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "start_line": {"type": "integer", "description": "起始行号（从 1 计）"},
                "end_line": {"type": "integer", "description": "结束行号"},
            },
            "required": ["path"],
        }

    def execute(self, args: Dict[str, Any]) -> dict:
        path = str(args.get("path", ""))
        if not path:
            return error("缺少必需参数：path", "MISSING_PARAM")
        try:
            lines = self.access.read_lines(path)
        except NoSuchFile:
            return error(f"文件不存在：{path}", "FILE_NOT_FOUND")
        except (OSError, FileAccessError) as exc:
            return error(f"读取失败：{exc}", "READ_FAILED")

        start = int(args.get("start_line") or 1)
        end = int(args.get("end_line") or len(lines))
        start = max(1, start)
        end = min(len(lines), max(start, end))
        body = "".join(f"{i:>5}\t{lines[i - 1]}" for i in range(start, end + 1))
        if len(body) > MAX_READ_CHARS:
            body = body[:MAX_READ_CHARS] + "\n\n[内容超长，已截断]"
        return success(body)


@dataclass
class WriteFileTool:
    access: LocalFileAccess
    name: str = "writeFile"
    description: str = "写入完整文件内容，文件不存在时自动创建。"
    capabilities: frozenset = frozenset({"WRITE_WORKSPACE"})

    def to_json_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        }

    def execute(self, args: Dict[str, Any]) -> dict:
        path = str(args.get("path", ""))
        if not path:
            return error("缺少必需参数：path", "MISSING_PARAM")
        if "content" not in args:
            return error("缺少必需参数：content", "MISSING_PARAM")
        content = args.get("content")
        if not isinstance(content, str):
            content = str(content)
        try:
            self.access.write_file(path, content)
        except (OSError, FileAccessError) as exc:
            return error(f"写入失败：{exc}", "WRITE_FAILED")
        display = self.access.to_display_path(path)
        return success(f"已写入 {display}（{len(content)} 字符）")


@dataclass
class EditFileTool:
    access: LocalFileAccess
    name: str = "editFile"
    description: str = "按精确字符串匹配局部修改已有文件，old_string 必须唯一匹配。"
    capabilities: frozenset = frozenset({"WRITE_WORKSPACE"})

    def to_json_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_string": {"type": "string", "description": "要被替换的原文，需唯一匹配"},
                "new_string": {"type": "string", "description": "替换后的新内容"},
            },
            "required": ["path", "old_string", "new_string"],
        }

    def execute(self, args: Dict[str, Any]) -> dict:
        path = str(args.get("path", ""))
        if not path:
            return error("缺少必需参数：path", "MISSING_PARAM")
        for key in ("old_string", "new_string"):
            if key not in args:
                return error(f"缺少必需参数：{key}", "MISSING_PARAM")
        old = str(args.get("old_string", ""))
        new = str(args.get("new_string", ""))
        if not old:
            return error("old_string 不能为空", "INVALID_PARAM")

        try:
            text = self.access.read_file(path)
        except NoSuchFile:
            return error(f"文件不存在：{path}", "FILE_NOT_FOUND")
        except (OSError, FileAccessError) as exc:
            return error(f"读取失败：{exc}", "READ_FAILED")

        count = text.count(old)
        if count != 1:
            return error(f"old_string 匹配到 {count} 处，要求唯一匹配", "EDIT_AMBIGUOUS")
        try:
            self.access.write_file(path, text.replace(old, new))
        except (OSError, FileAccessError) as exc:
            return error(f"写入失败：{exc}", "WRITE_FAILED")
        return success(f"已修改 {self.access.to_display_path(path)}")


@dataclass
class ListTool:
    access: LocalFileAccess
    name: str = "list"
    description: str = "按 ls 风格列出文件和目录。"
    capabilities: frozenset = frozenset({"READ_WORKSPACE"})

    def to_json_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目录路径"}},
            "required": ["path"],
        }

    def execute(self, args: Dict[str, Any]) -> dict:
        path = str(args.get("path") or ".")
        try:
            entries = self.access.list_files(path)
        except (OSError, FileAccessError) as exc:
            return error(f"列出失败：{exc}", "LIST_FAILED")
        if not entries:
            return success("（空目录）")
        lines = [
            f"{entry.name}{'/' if entry.is_directory else ''}\t{entry.size}"
            for entry in entries
        ]
        return success("\n".join(lines))


@dataclass
class BashTool:
    access: LocalFileAccess
    name: str = "Bash"
    description: str = "在工作区执行一次性 shell 命令。"
    capabilities: frozenset = frozenset({"EXECUTE_COMMANDS"})
    timeout: int = 120

    def to_json_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "要执行的命令"}},
            "required": ["command"],
        }

    def execute(self, args: Dict[str, Any]) -> dict:
        command = str(args.get("command", ""))
        if not command:
            return error("缺少必需参数：command", "MISSING_PARAM")
        try:
            completed = subprocess.run(
                command, shell=True, cwd=self.access.mapper.workspace_root,
                capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return error(f"命令超时（{self.timeout} 秒）", "TIMEOUT")
        except OSError as exc:
            return error(f"执行失败：{exc}", "EXEC_FAILED")
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0:
            return error(output.strip() or f"退出码 {completed.returncode}", "NONZERO_EXIT")
        return success(output.strip() or "（无输出）")


def default_tools(access: LocalFileAccess) -> List[Any]:
    return [
        ReadFileTool(access),
        WriteFileTool(access),
        EditFileTool(access),
        ListTool(access),
        BashTool(access),
    ]
