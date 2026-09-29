"""Resolve MaiBot replies and walk MaiBot/OneBot forward message bodies."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


class SourceError(Exception):
    """A user-facing error that contains no private message or RPC details."""


def message_body(message: Any) -> Any:
    if not isinstance(message, dict):
        return message
    # Prefer structured content over a raw CQ string or processed preview.
    for key in ("raw_message", "message", "content", "messages"):
        if isinstance(message.get(key), list):
            return message[key]
    for key in ("raw_message", "message", "content", "processed_plain_text"):
        if isinstance(message.get(key), str) and message[key]:
            return message[key]
    return None


def find_reply(message: dict) -> tuple[str, str]:
    body = message_body(message)
    if isinstance(body, list):
        for segment in body:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data", {})
            if isinstance(data, dict):
                target = str(data.get("target_message_id") or data.get("id") or "")
                preview = data.get("target_message_content")
                if target and target != "0":
                    return target, preview if isinstance(preview, str) else ""
    target = str(message.get("reply_to") or "")
    return (target, "") if target != "0" else ("", "")


@dataclass
class MessageText:
    texts: list[str] = field(default_factory=list)
    warnings: set[str] = field(default_factory=set)


class MessageResolver:
    def __init__(self, ctx: Any, *, stream_id: str, platform: str, adapter_api_prefix: str):
        self.ctx = ctx
        self.stream_id = stream_id
        self.adapter_api_prefix = adapter_api_prefix if platform.lower() == "qq" else ""
        self.result = MessageText()
        self._visited_forwards: set[str] = set()
        self._nodes = 0

    async def _adapter_call(self, method: str, **kwargs: Any) -> Any:
        if not self.adapter_api_prefix:
            return None
        try:
            return await asyncio.wait_for(
                self.ctx.api.call(f"{self.adapter_api_prefix}.{method}", version="1", **kwargs), 5
            )
        except Exception as exc:
            self.ctx.logger.warning("适配器消息查询失败 (%s)", type(exc).__name__)
            return None

    async def resolve_reply(self, message: dict) -> MessageText:
        target, preview = find_reply(message)
        if not target:
            raise SourceError("请引用一条包含磁力链接的消息，再发送“给我下”。")
        detail = None
        try:
            detail = await asyncio.wait_for(self.ctx.message.get_by_id(target, stream_id=self.stream_id), 5)
        except Exception as exc:
            self.ctx.logger.warning("历史消息查询失败 (%s)", type(exc).__name__)

        body = message_body(detail)
        # A forward/reply preview alone cannot recover its nested content.
        if body is None or (isinstance(body, str) and "[forward]" in body):
            remote = await self._adapter_call("get_msg", message_id=target)
            body = message_body(remote) or body
        if body is None:
            if not preview or "[forward]" in preview:
                raise SourceError("无法读取被引用的消息；请检查适配器消息查询接口，或重新发送原文后引用。")
            body = preview
            self.result.warnings.add("未取到完整原消息，仅处理了引用预览中的磁链。")
        if isinstance(body, str) and "[forward]" in body:
            self.result.warnings.add("仅取得转发预览，无法读取其中的完整内容。")
        await self._walk(body, 0)
        return self.result

    async def _walk(self, value: Any, depth: int) -> None:
        self._nodes += 1
        if depth > 20 or self._nodes > 5000:
            self.result.warnings.add("转发内容过大或嵌套过深，部分内容未处理。")
            return
        if isinstance(value, str):
            self.result.texts.append(value)
            return
        if isinstance(value, list):
            for item in value:
                await self._walk(item, depth + 1)
                if self._nodes > 5000:
                    break
            return
        if not isinstance(value, dict):
            return
        kind = value.get("type")
        data = value.get("data", value.get("content"))
        if kind == "text":
            text = data.get("text", "") if isinstance(data, dict) else data
            if isinstance(text, str):
                self.result.texts.append(text)
        elif kind == "forward":
            if isinstance(data, list):
                await self._walk(data, depth + 1)
            elif isinstance(data, dict):
                inline = message_body(data)
                if inline is not None:
                    await self._walk(inline, depth + 1)
                else:
                    await self._fetch_forward(str(data.get("id") or ""), depth)
        elif kind == "node":
            # OneBot inline nodes: data.content; referenced nodes: data.id.
            body = message_body(data)
            if body is None and isinstance(data, dict) and data.get("id"):
                body = message_body(await self._adapter_call("get_msg", message_id=str(data["id"])))
            if body is None:
                self.result.warnings.add("部分转发节点无法读取。")
            else:
                await self._walk(body, depth + 1)
        elif kind == "reply":
            # Do not follow arbitrary reply chains beyond the explicitly quoted message.
            if isinstance(data, dict) and isinstance(data.get("target_message_content"), str):
                self.result.texts.append(data["target_message_content"])
        elif kind is None:
            body = message_body(value)
            if body is not None:
                await self._walk(body, depth + 1)
        # Image URLs, nicknames and unrelated metadata are deliberately not message text.

    async def _fetch_forward(self, forward_id: str, depth: int) -> None:
        if forward_id in self._visited_forwards:
            return
        if not forward_id or len(self._visited_forwards) >= 32:
            self.result.warnings.add("部分转发内容缺少 ID 或超过查询上限。")
            return
        self._visited_forwards.add(forward_id)
        detail = await self._adapter_call("get_forward_msg", id=forward_id)
        body = message_body(detail)
        if body is None:
            self.result.warnings.add("部分合并转发无法读取，请检查 SnowLuma/NapCat 适配器。")
        else:
            await self._walk(body, depth + 1)
