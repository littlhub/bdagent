"""OpenAI 兼容 provider，对应 domain/provider/OpenAIAdapter.kt 的最小实现。

只覆盖移植验证所需：流式补全、工具调用、usage 统计。多 Key 轮换、重试策略、
思考签名回传等按 App 实现另行补齐。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from .messages import (
    AgentImage,
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from .workflow import (
    AIResponse,
    Final,
    ReasoningDelta,
    Retrying,
    TextDelta,
    ToolCallDeclared,
)


def _args_ok(raw: str) -> bool:
    """工具参数是否是完整、可解析的 JSON。"""
    if not raw:
        return False
    try:
        json.loads(raw)
        return True
    except json.JSONDecodeError:
        return False


def _load_arguments(raw: str) -> dict:
    """解析工具参数 JSON。

    解析失败不能静默变成 {}：那会让工具收到空参数，表现为「write {}」，
    既看不出原因也没法诊断。这里先试常见修法，仍失败就报出来。
    """
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 尾随逗号是模型最常犯的 JSON 错误
    fixed = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError as exc:
        # 参数不完整通常是模型输出被 max_tokens 截断（写大文件时常见）。
        # 把原因带回工具层，让模型知道要分段写，而不是默默收到空参数。
        print(f"[警告] 工具参数不完整：{exc}；长度 {len(raw)}；片段 {raw[-200:]!r}",
              file=sys.stderr)
        return {"__args_error__": (
            f"工具参数 JSON 不完整，本次约 {len(raw)} 字符，远超单次上限 8000。"
            f"请拆成至少 {(len(raw) // 7000) + 1} 次写入：\n"
            "  1) 第一次 write 只写文件开头（约 100 行）\n"
            "  2) 后续用 write + append=true 每次追加约 100 行\n"
            "不要一次写完整个大文件——输出会被截断，这一轮就白跑了。"
        )}


def _message_to_wire(message: Any) -> Dict[str, Any]:
    if isinstance(message, UserMessage):
        if message.images:
            parts: List[Dict[str, Any]] = [{"type": "text", "text": message.content}]
            for image in message.images:
                mime = image.mime_type or "image/png"
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{image.base64_data}"},
                })
            return {"role": "user", "content": parts}
        return {"role": "user", "content": message.content}

    if isinstance(message, AssistantMessage):
        payload: Dict[str, Any] = {"role": "assistant", "content": message.content or None}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        return payload

    return {
        "role": "tool",
        # 必须回原始 tool_call id：KT 的 ToolResultMessage.id 就是 call.id，
        # message_id 是持久化用的 tool_<id>，回传给模型会让 id 对不上。
        "tool_call_id": message.id,
        "content": message.model_result if message.model_result is not None else message.result,
    }


@dataclass
class OpenAIProvider:
    base_url: str
    api_key: str
    model: str
    provider_id: str = "openai-compatible"
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    timeout: float = 120.0
    extra_headers: Dict[str, str] = field(default_factory=dict)

    async def complete_stream(
        self,
        system_prompt: str,
        messages: Sequence[Any],
        tools: Sequence[Any],
        reasoning_effort: Optional[str] = None,
    ) -> AsyncIterator[Any]:
        """带重试的流式补全。

        移动网络下 [Errno 103]/[Errno -3] 很常见，一次中断不该让整轮作废。
        完全没有有效内容时重试（并通过 Retrying 通知上层清空已收增量）；
        已经拿到文本或完整工具调用时保留结果，不重试。
        """
        max_retries = 2
        last_exc: Optional[BaseException] = None
        for attempt in range(1, max_retries + 1):
            try:
                async for chunk in self._stream_once(system_prompt, messages, tools):
                    yield chunk
                return
            except Exception as exc:
                last_exc = exc
                if attempt >= max_retries:
                    break
                yield Retrying(attempt=attempt, max_retries=max_retries,
                               error=f"{type(exc).__name__}: {exc}")
        if last_exc is not None:
            raise last_exc

    async def _stream_once(
        self,
        system_prompt: str,
        messages: Sequence[Any],
        tools: Sequence[Any],
        reasoning_effort: Optional[str] = None,
    ) -> AsyncIterator[Any]:
        import urllib.error
        import urllib.request

        wire_messages: List[Dict[str, Any]] = []
        if system_prompt:
            wire_messages.append({"role": "system", "content": system_prompt})
        wire_messages.extend(_message_to_wire(message) for message in messages)

        body: Dict[str, Any] = {
            "model": self.model,
            "messages": wire_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.to_json_schema(),
                    },
                }
                for tool in tools
            ]
        if self.max_output_tokens:
            body["max_tokens"] = self.max_output_tokens
        if self.temperature is not None:
            body["temperature"] = self.temperature

        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                **self.extra_headers,
            },
            method="POST",
        )

        # 阻塞式 urllib 放到线程池，保持异步接口
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: urllib.request.urlopen(request, timeout=self.timeout))

        content_parts: List[str] = []
        tool_buffers: Dict[int, Dict[str, str]] = {}
        finish_reason: Optional[str] = None
        usage: Dict[str, Any] = {}
        interrupted: Optional[BaseException] = None

        try:
            for raw in response:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    # 思考过程：DeepSeek 用 reasoning_content，部分厂商用 reasoning
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield ReasoningDelta(reasoning)
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                        yield TextDelta(delta["content"])
                    for call in delta.get("tool_calls") or []:
                        index = call.get("index", 0)
                        slot = tool_buffers.setdefault(index, {"id": "", "name": "", "args": ""})
                        if call.get("id"):
                            slot["id"] = call["id"]
                        function = call.get("function") or {}
                        if function.get("name") and not slot["name"]:
                            slot["name"] = function["name"]
                            yield ToolCallDeclared(function["name"])
                        if function.get("arguments"):
                            slot["args"] += function["arguments"]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        except Exception as exc:
            # 移动网络下常见 [Errno 103] connection abort。不能直接抛出去让整轮作废：
            # 已收到的内容和工具调用可能已经够用，后续由工具层报参数不完整。
            print(f"[警告] 流式读取中断：{type(exc).__name__}: {exc}", file=sys.stderr)
            interrupted = exc
        finally:
            response.close()

        # 什么都没拿到（或只拿到半截工具参数）→ 抛出去让外层重试。
        # 已经拿到文本或完整工具调用时保留，避免白白重跑一整轮。
        if interrupted is not None:
            has_text = bool("".join(content_parts).strip())
            has_valid_call = any(_args_ok(s["args"]) for s in tool_buffers.values())
            if not has_text and not has_valid_call:
                raise interrupted

        tool_calls: List[ToolCall] = []
        for index in sorted(tool_buffers):
            slot = tool_buffers[index]
            arguments = _load_arguments(slot["args"])
            tool_calls.append(ToolCall(slot["id"] or f"call_{index}", slot["name"], arguments))
        if finish_reason == "length":
            print("[警告] 模型输出达到 max_tokens 上限，工具参数可能被截断",
                  file=sys.stderr)

        yield Final(AIResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            stop_reason=finish_reason,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cached_input_tokens=int(
                (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            ),
        ))
