"""内核行为断言，覆盖权限解析、上下文预算与压缩、检查点回滚。

运行：python3 -m codecore.test_codecore
"""
from __future__ import annotations

import asyncio
import os
import tempfile

from . import shell_parser as sp
from . import safe_commands as sc
from .checkpoint import CheckpointManager, fingerprint_file
from .context import (
    ContextCompactor,
    Cursor,
    ModelMetadata,
    adjust_split_index,
    calibrated,
    effective_input_budget,
    estimate_message,
    output_reserve_tokens,
    preserve_recent_tokens,
    units,
)
from .messages import AgentMode, AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from .permission import (
    ToolPermissionPolicyEngine,
    Verdict,
    catastrophic_reason_for,
    normalize_path,
)
from .versions import FileVersionRegistry


def test_shell_parser() -> None:
    cases = [
        ("ls -la", True, [["ls", "-la"]]),
        ("git status -s", True, [["git", "status", "-s"]]),
        ("cat a && echo b", True, [["cat", "a"], ["echo", "b"]]),
        ("cat a | grep b", True, [["cat", "a"], ["grep", "b"]]),
        ("cat 'a && b'", True, [["cat", "a && b"]]),
        ('echo "hi $(whoami)"', False, None),
        ("echo `id`", False, None),
        ("(cd x; ls)", False, None),
        ("echo hi > /etc/passwd", False, None),
        ("echo hi > local.txt", True, None),
        ("cat a > /tmp/x", False, None),
        ("echo 'unclosed", False, None),
    ]
    for command, analyzable, segments in cases:
        analysis = sp.analyze(command)
        assert analysis.analyzable == analyzable, (command, analysis.analyzable)
        if segments is not None:
            assert analysis.segments == segments, (command, analysis.segments)

    assert sp.effective_tokens(["FOO=bar", "git", "pull"]) == ["git", "pull"]
    assert sp.rememberable_prefix(["FOO=1", "git", "pull", "origin"]) == "git pull"
    assert sp.rememberable_prefix(["cat", "x"]) == "cat"
    assert sp.rememberable_prefix(["git", "--version"]) == "git"
    assert sp.rememberable_prefix(["rm", "-rf", "x"]) is None
    assert sp.rememberable_prefix(["rm", "file.txt"]) == "rm file.txt"


def test_safe_commands() -> None:
    assert sc.is_safe(["ls", "-la"])
    assert sc.is_safe(["git", "status"])
    assert not sc.is_safe(["git", "push"])
    assert not sc.is_safe(["find", ".", "-delete"])
    assert not sc.is_safe(["env", "rm", "-rf", "/"])
    assert not sc.is_safe(["sed", "-i", "s/a/b/", "x"])
    assert not sc.is_safe(["curl", "http://x"])


def test_permission_paths() -> None:
    assert normalize_path("~/workspace/../workspace/x") == "/root/workspace/x"
    assert normalize_path("/tmp//a/./b") == "/tmp/a/b"
    assert normalize_path("workspace/./a/../b") == "workspace/b"

    # /root 是受保护系统目录，仅 /root/workspace 子树例外
    blocked = ["/", "/*", "*", "..", "~", "$HOME", "${HOME}",
               "/etc", "/usr/bin", "/tmp", "workspace", "/root/workspace", "/root/data"]
    for path in blocked:
        assert catastrophic_reason_for(path), path
    for path in ["/root/workspace/build", "workspace/dist", "./out"]:
        assert catastrophic_reason_for(path) is None, path


def test_permission_engine() -> None:
    engine = ToolPermissionPolicyEngine()
    denied = engine.evaluate("Bash", {"command": "rm -rf /"}, AgentMode.AUTO, {"EXECUTE_COMMANDS"})
    assert denied.verdict is Verdict.DENY

    elevated = engine.evaluate(
        "Bash", {"command": "rm -rf /", "elevate": True}, AgentMode.AUTO, {"EXECUTE_COMMANDS"}
    )
    assert elevated.verdict is Verdict.ASK and elevated.rememberable_patterns == []

    readonly = engine.evaluate("Bash", {"command": "ls -la"}, AgentMode.BUILD, {"EXECUTE_COMMANDS"})
    assert readonly.verdict is Verdict.ALLOW

    ask = engine.evaluate("writeFile", {"path": "a"}, AgentMode.BUILD, {"WRITE_WORKSPACE"})
    assert ask.verdict is Verdict.ASK and ask.rememberable_patterns == ["*"]

    config = engine.evaluate("manageMcp", {}, AgentMode.BUILD, {"MODIFY_AGENT_CONFIG"})
    assert config.verdict is Verdict.ASK and config.rememberable_patterns == []

    plan_read = engine.evaluate("list", {}, AgentMode.PLAN, {"READ_WORKSPACE"})
    # PLAN 下只读工具不被拦截，但仍走通用规则评估（弹窗）
    assert plan_read.verdict is Verdict.ASK, plan_read

    plan_write = engine.evaluate("Bash", {"command": "ls"}, AgentMode.PLAN, {"EXECUTE_COMMANDS"})
    assert plan_write.verdict is Verdict.DENY

    shizuku = engine.evaluate("Shizuku", {"command": "ls"}, AgentMode.AUTO, {"EXECUTE_COMMANDS"})
    assert shizuku.verdict is Verdict.ASK and shizuku.rememberable_patterns == []


def test_context_budget() -> None:
    metadata = ModelMetadata(context_tokens=128_000)
    assert output_reserve_tokens(metadata) == 8192
    assert effective_input_budget(metadata) == 128_000 - 8192
    assert output_reserve_tokens(ModelMetadata(context_tokens=4_000)) == 1000
    assert preserve_recent_tokens(1_000_000) == 125_000
    assert preserve_recent_tokens(8_000) == 2_000

    assert estimate_message(UserMessage(content="abcd")) == 1 + 12
    assert estimate_message(UserMessage(content="中文")) == 2 + 12
    assert calibrated(100, 80, 200) == 220
    assert calibrated(100, 80, 0) == 100


def test_compaction_split() -> None:
    messages = [
        UserMessage(content="u1"),
        AssistantMessage(content="", tool_calls=[ToolCall("c1", "x", {})]),
        ToolResultMessage(id="c1", tool_name="x", result="r"),
        UserMessage(content="u2"),
    ]
    assert adjust_split_index(messages, 2) == 1
    assert len(units(messages)) == 3

    cursor = Cursor(["a" * 100, "b" * 100])
    first = cursor.next(50)
    assert "[history-unit 1" in first and not cursor.finished
    while not cursor.finished:
        cursor.next(50)


def test_compaction_effect() -> None:
    compactor = ContextCompactor(summarize=lambda _: "s", threshold_percent=lambda: 80)
    big = ModelMetadata(context_tokens=128_000)
    assert not compactor.compact_if_needed([UserMessage(content="hi")], big, "", [], 0).compacted

    history = [UserMessage(content=f"轮{i}:" + "内容" * 500) for i in range(30)]
    small = ModelMetadata(context_tokens=16_000, output_tokens=2_000)
    result = compactor.compact_if_needed(history, small, "", [], 0)
    assert result.compacted
    assert result.messages[0].content == "What did we do so far?"
    assert isinstance(result.messages[1], AssistantMessage)
    assert result.messages[-1] is history[-1]


def test_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        registry = FileVersionRegistry()
        manager = CheckpointManager(os.path.join(tmp, "checkpoints"), registry)
        target = os.path.join(tmp, "a.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("original")

        checkpoint_id = manager.create_checkpoint("s1", "m1", "改文件")
        manager.before_file_modified("s1", target)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("modified by agent")
        registry.record("s1", target, fingerprint_file(target))

        assert manager.restore("s1", checkpoint_id).restored == 1
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "original"

        checkpoint_id2 = manager.create_checkpoint("s1", "m2", "再改")
        manager.before_file_modified("s1", target)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("changed by someone else")
        result = manager.restore("s1", checkpoint_id2)
        assert result.skipped == [target]
        with open(target, encoding="utf-8") as handle:
            assert handle.read() == "changed by someone else"



def test_path_mapper() -> None:
    from .paths import WorkspacePathMapper

    mapper = WorkspacePathMapper(
        workspace_root="/srv/ws",
        rootfs_root="/srv/rootfs",
        aicode_dir="/srv/aicode",
        home="/root",
    )
    # 工作区
    assert mapper.to_host_path("~/workspace") == "/srv/ws"
    assert mapper.to_host_path("~/workspace/a.txt") == "/srv/ws/a.txt"
    assert mapper.to_host_path("a.txt") == "/srv/ws/a.txt"
    assert mapper.to_host_path("/root/workspace/a.txt") == "/srv/ws/a.txt"
    # AI 配置目录必须先于通用 / 规则
    assert mapper.to_host_path("/root/.aicode/mcp.json") == "/srv/aicode/mcp.json"
    # 其它容器绝对路径落到 rootfs
    assert mapper.to_host_path("/etc/hosts") == "/srv/rootfs/etc/hosts"
    # 反向还原
    assert mapper.to_container_path("/srv/ws/a.txt") == "~/workspace/a.txt"
    assert mapper.to_container_path("/srv/ws") == "~/workspace"
    assert mapper.to_container_path("/srv/aicode/mcp.json") == "/root/.aicode/mcp.json"
    assert mapper.to_container_path("/srv/rootfs/etc/hosts") == "/etc/hosts"
    assert mapper.to_container_path("/srv/rootfs") == "/"


def test_workspace_boundary() -> None:
    from .paths import WorkspacePathMapper

    mapper = WorkspacePathMapper(
        workspace_root="/srv/ws",
        rootfs_root="/srv/rootfs",
        aicode_dir="/srv/aicode",
    )
    assert mapper.is_inside_workspace("/srv/ws/a.txt")
    assert mapper.is_inside_workspace("/srv/ws/sub/deep.txt")
    assert not mapper.is_inside_workspace("/srv/wsx/a.txt")
    assert not mapper.is_inside_workspace("/srv/rootfs/etc/hosts")


def test_local_file_access() -> None:
    import tempfile
    from .file_access import FileAlreadyExists, LocalFileAccess, NoSuchFile
    from .paths import WorkspacePathMapper

    with tempfile.TemporaryDirectory() as tmp:
        ws = os.path.join(tmp, "ws")
        os.makedirs(ws)
        access = LocalFileAccess(WorkspacePathMapper(
            workspace_root=ws,
            rootfs_root=os.path.join(tmp, "rootfs"),
            aicode_dir=os.path.join(tmp, "aicode"),
        ))

        access.write_file("a.txt", "hello")
        assert access.read_file("a.txt") == "hello"
        # 相对路径与容器路径指向同一文件
        assert access.read_file("~/workspace/a.txt") == "hello"
        assert access.exists("/root/workspace/a.txt")

        # 原子写不留临时文件
        leftovers = [n for n in os.listdir(ws) if n.startswith(".aicode-tmp-")]
        assert leftovers == [], leftovers

        # 覆盖保护
        try:
            access.write_file("a.txt", "x", overwrite=False)
            raise AssertionError("应当抛 FileAlreadyExists")
        except FileAlreadyExists:
            pass

        # 指纹随内容变化
        fp1 = access.fingerprint("a.txt")
        access.write_file("a.txt", "hello2")
        assert access.fingerprint("a.txt") != fp1

        # 回显为容器路径
        assert access.to_display_path("a.txt") == "~/workspace/a.txt"

        # 不存在的文件
        try:
            access.read_file("nope.txt")
            raise AssertionError("应当抛 NoSuchFile")
        except NoSuchFile:
            pass


def test_tools_path_isolation() -> None:
    import tempfile
    from .file_access import LocalFileAccess
    from .paths import WorkspacePathMapper
    from .tools import WriteFileTool

    with tempfile.TemporaryDirectory() as tmp:
        ws = os.path.join(tmp, "ws")
        rootfs = os.path.join(tmp, "rootfs")
        os.makedirs(ws)
        os.makedirs(rootfs)
        access = LocalFileAccess(WorkspacePathMapper(
            workspace_root=ws, rootfs_root=rootfs, aicode_dir=os.path.join(tmp, "aicode"),
        ))
        tool = WriteFileTool(access)

        result = tool.execute({"path": "calc.html", "content": "<html></html>"})
        assert result["status"] == "success", result
        assert os.path.isfile(os.path.join(ws, "calc.html"))

        # 容器绝对路径同样落到工作区，不会写到宿主真实 /root
        tool.execute({"path": "/root/workspace/calc2.html", "content": "x"})
        assert os.path.isfile(os.path.join(ws, "calc2.html"))

        # 缺失参数要报错而不是静默写空文件
        bad = tool.execute({"path": "empty.txt"})
        assert bad["status"] == "error" and bad["code"] == "MISSING_PARAM", bad
        assert not os.path.exists(os.path.join(ws, "empty.txt"))



def test_notification_queue() -> None:
    from .notifications import AgentNotificationCenter, AgentNotificationKind, PendingNotification

    center = AgentNotificationCenter()
    seq1 = center.enqueue("s1", PendingNotification(
        kind=AgentNotificationKind.USER_MESSAGE, message="先改颜色"))
    seq2 = center.enqueue("s1", PendingNotification(
        kind=AgentNotificationKind.BACKGROUND_TASK, source_id="tab-1", title="构建", exit_code=0))

    assert center.pending_count("s1") == 2
    peeked = center.peek("s1")
    assert [i.seq for i in peeked] == [seq1, seq2]
    # peek 不移除
    assert center.pending_count("s1") == 2

    # 只 ack 一条，另一条仍在
    center.ack("s1", [seq1])
    assert center.pending_count("s1") == 1
    assert center.peek("s1")[0].seq == seq2

    # peek 之后新入队的不受此前 ack 影响
    seq3 = center.enqueue("s1", PendingNotification(
        kind=AgentNotificationKind.USER_MESSAGE, message="再改字体"))
    center.ack("s1", [seq2])
    assert [i.seq for i in center.peek("s1")] == [seq3]

    assert [i.seq for i in center.drain("s1")] == [seq3]
    assert center.pending_count("s1") == 0


def test_notification_format() -> None:
    from .notification_format import build_message, inject
    from .notifications import AgentNotificationKind, NotificationOutcome, PendingNotification
    from .messages import AgentMode

    # 纯用户插话：不加系统通知前缀，原文返回
    only_user = [PendingNotification(kind=AgentNotificationKind.USER_MESSAGE, message="顺便加个深色模式")]
    assert build_message(only_user) == "顺便加个深色模式"

    # 多条用户插话编号
    two_user = [
        PendingNotification(kind=AgentNotificationKind.USER_MESSAGE, message="A"),
        PendingNotification(kind=AgentNotificationKind.USER_MESSAGE, message="B"),
    ]
    assert build_message(two_user) == "1. A\n2. B"

    # 系统事件：带前缀与 XML
    task = PendingNotification(
        kind=AgentNotificationKind.BACKGROUND_TASK, source_id="tab-1",
        title="构建", exit_code=0, tail_output="ok", outcome=NotificationOutcome.COMPLETED,
    )
    text = build_message([task])
    assert "后台任务通知" in text
    assert "<task-notification>" in text
    assert "<exit-code>0</exit-code>" in text
    assert "不是来自用户的消息" in text

    # 模式切换
    mode = PendingNotification(kind=AgentNotificationKind.MODE_CHANGE, new_mode=AgentMode.PLAN)
    mode_text = build_message([mode])
    assert "<new-mode>PLAN</new-mode>" in mode_text
    assert "<status>changed</status>" in mode_text

    # 搭车注入：仍是合法 JSON，且带 notifications 字段
    import json
    raw = json.dumps({"status": "success", "data": "ok"})
    injected = inject(raw, [only_user[0]])
    payload = json.loads(injected)
    assert payload["status"] == "success"
    assert payload["notifications"][0]["kind"] == "user_message"
    assert "用户本人" in payload["notifications"][0]["notice"]

    # 非 JSON 结果退化为文本追加
    plain = inject("plain text", [task])
    assert plain.startswith("plain text") and "<task-notification>" in plain


def test_notification_riding_batch() -> None:
    """通知搭车：注入到本批最后一条工具结果，并只 ack 已送达的。"""
    import asyncio
    from .notification_format import build_message
    from .notifications import AgentNotificationCenter, AgentNotificationKind, PendingNotification
    from .messages import AgentContext
    from .permission import ToolPermissionPolicyEngine
    from .workflow import AIResponse, Final, StatefulAgentWorkflow, TextDelta

    class Provider:
        provider_id = "t"
        model = "t"
        max_output_tokens = 4096
        def __init__(self):
            self.script = [
                AIResponse(content="", tool_calls=[ToolCall("c1", "echo", {"text": "x"})]),
                AIResponse(content="done", input_tokens=10),
            ]
            self.seen = []
        async def complete_stream(self, system_prompt, messages, tools, reasoning_effort=None):
            self.seen.append([type(m).__name__ for m in messages])
            response = self.script.pop(0) if self.script else AIResponse(content="done")
            if response.content:
                yield TextDelta(response.content)
            yield Final(response)

    class Echo:
        name = "echo"
        description = "d"
        capabilities = frozenset()
        def to_json_schema(self):
            return {"type": "object", "properties": {"text": {"type": "string"}}}
        def execute(self, args):
            return {"status": "success", "data": "echo"}

    async def run():
        center = AgentNotificationCenter()
        provider = Provider()
        workflow = StatefulAgentWorkflow(
            provider=provider, tools=[Echo()],
            policy_engine=ToolPermissionPolicyEngine(),
            compactor=ContextCompactor(summarize=lambda m: "s", threshold_percent=lambda: 90),
            permission_resolver=lambda call, ev: asyncio.sleep(0, result=True),
            notification_center=center,
        )
        context = AgentContext(project_root="/tmp", session_id="s1", mode=AgentMode.AUTO)
        # 用户在工作期间插入消息
        center.enqueue("s1", PendingNotification(
            kind=AgentNotificationKind.USER_MESSAGE, message="顺便加个标题"))
        events = [e async for e in workflow.run("开始", context)]
        return center, events

    center, events = asyncio.run(run())
    delivered = [e for e in events if e["type"] == "notifications_delivered"]
    assert delivered and delivered[0]["kinds"] == ["USER_MESSAGE"], events
    finished = [e for e in events if e["type"] == "tool_call_finished"]
    assert "notifications" in finished[-1]["result"], finished[-1]["result"]
    # 已送达即 ack
    assert center.pending_count("s1") == 0


def main() -> None:
    test_shell_parser()
    test_safe_commands()
    test_permission_paths()
    test_permission_engine()
    test_context_budget()
    test_compaction_split()
    test_compaction_effect()
    test_checkpoint()
    test_path_mapper()
    test_workspace_boundary()
    test_local_file_access()
    test_tools_path_isolation()
    test_notification_queue()
    test_notification_format()
    test_notification_riding_batch()
    print("codecore 全部断言通过")


if __name__ == "__main__":
    main()
