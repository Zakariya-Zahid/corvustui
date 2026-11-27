"""Anthropic provider implementation."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx
import tiktoken

from corvustui.config.settings import get_settings
from corvustui.providers.base import (
    BaseProvider,
    CompletionChunk,
    CompletionResponse,
    FinishReason,
    Message,
    MessageRole,
    ProviderAuthError,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderType,
    ProviderUnavailableError,
    ToolCall,
    ToolDefinition,
    Usage,
)


class AnthropicProvider(BaseProvider):
    """Anthropic API provider."""

    provider_type = ProviderType.ANTHROPIC

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        **kwargs,
    ):
        super().__init__(model=model, base_url=base_url, api_key=api_key, timeout=timeout, **kwargs)
        self.anthropic_version = kwargs.get("anthropic_version", get_settings().anthropic_version)
        self._headers = {
            "x-api-key": api_key or "",
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }

    def _get_encoder(self) -> tiktoken.Encoding:
        return tiktoken.get_encoding("cl100k_base")

    @staticmethod
    def _merge_consecutive(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Anthropic requires alternating roles; merge adjacent same-role turns."""
        merged: list[dict[str, Any]] = []
        for message in messages:
            if merged and merged[-1]["role"] == message["role"]:
                previous = merged[-1]["content"]
                current = message["content"]
                if isinstance(previous, str) and isinstance(current, str):
                    merged[-1]["content"] = f"{previous}\n\n{current}".strip()
                else:
                    prev_blocks = previous if isinstance(previous, list) else [{"type": "text", "text": previous}]
                    cur_blocks = current if isinstance(current, list) else [{"type": "text", "text": current}]
                    merged[-1]["content"] = prev_blocks + cur_blocks
            else:
                merged.append(message)
        return merged

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        anthropic_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                # System messages are sent through the top-level "system" field.
                continue
            if msg.role == MessageRole.TOOL:
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id or "call_0",
                        "content": msg.content or "",
                    }],
                })
            elif msg.tool_calls:
                content = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                anthropic_messages.append({"role": "assistant", "content": content})
            else:
                anthropic_messages.append({
                    "role": msg.role.value,
                    "content": msg.content or "",
                })
        return self._merge_consecutive(anthropic_messages)

    def _extract_system(self, messages: list[Message]) -> str | None:
        parts = [msg.content for msg in messages if msg.role == MessageRole.SYSTEM and msg.content]
        return "\n\n".join(parts) if parts else None

    def _convert_tools(self, tools: list[ToolDefinition] | None) -> list[dict] | None:
        if not tools:
            return None
        return [{
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
        } for t in tools]

    async def validate_model(self) -> None:
        if not self.api_key:
            raise ProviderAuthError(
                "No Anthropic API key configured. Set CORVUS_ANTHROPIC_API_KEY in .env.",
                self.provider_type,
            )

    async def list_models(self) -> list[str]:
        """Fetch models from Anthropic's /v1/models endpoint."""
        if not self._client:
            raise RuntimeError("Provider not initialized. Use async context manager.")
        if not self.api_key:
            raise ProviderAuthError(
                "No Anthropic API key configured. Set CORVUS_ANTHROPIC_API_KEY in .env.",
                self.provider_type,
            )

        try:
            response = await self._client.get(
                f"{self.base_url.rstrip('/')}/v1/models",
                headers=self._headers,
                params={"limit": 1000},
            )
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Could not reach Anthropic at {self.base_url}.",
                self.provider_type,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "Timed out contacting Anthropic.",
                self.provider_type,
            ) from exc

        await self._handle_error(response)
        data = response.json()
        return [item["id"] for item in data.get("data", []) if item.get("id")]

    async def _post(self, url: str, payload: dict) -> httpx.Response:
        try:
            return await self._client.post(url, json=payload, headers=self._headers)
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Could not reach Anthropic at {self.base_url}.",
                self.provider_type,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "Timed out contacting Anthropic.",
                self.provider_type,
            ) from exc

    async def _make_request(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        stream: bool,
        temperature: float,
        max_tokens: int | None,
        **kwargs,
    ) -> AsyncIterator[CompletionChunk] | CompletionResponse:
        if not self._client:
            raise RuntimeError("Provider not initialized. Use async context manager.")

        anthropic_messages = self._convert_messages(messages)
        system = self._extract_system(messages)
        anthropic_tools = self._convert_tools(tools)

        payload = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        if anthropic_tools:
            payload["tools"] = anthropic_tools

        url = f"{self.base_url.rstrip('/')}/v1/messages"

        if stream:
            return self._stream_request(url, payload)
        return await self._single_request(url, payload)

    async def _single_request(self, url: str, payload: dict) -> CompletionResponse:
        response = await self._post(url, payload)
        await self._handle_error(response)
        data = response.json()

        content_parts = []
        thinking_parts = []
        tool_calls = []
        for block in data.get("content", []):
            block_type = block.get("type")
            if block_type == "text":
                content_parts.append(block.get("text", ""))
            elif block_type == "thinking":
                thinking_parts.append(block.get("thinking", ""))
            elif block_type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", "call_0"),
                    name=block.get("name", ""),
                    arguments=block.get("input") or {},
                ))

        return CompletionResponse(
            content="\n".join(content_parts) if content_parts else None,
            thinking="\n".join(thinking_parts) if thinking_parts else None,
            tool_calls=tool_calls or None,
            finish_reason=self._parse_finish_reason(data.get("stop_reason")),
            usage=Usage(
                prompt_tokens=(data.get("usage") or {}).get("input_tokens", 0),
                completion_tokens=(data.get("usage") or {}).get("output_tokens", 0),
                total_tokens=(data.get("usage") or {}).get("input_tokens", 0)
                    + (data.get("usage") or {}).get("output_tokens", 0),
            ),
            raw=data,
            model=self.model,
            provider=ProviderType.ANTHROPIC,
        )

    async def _stream_request(self, url: str, payload: dict) -> AsyncIterator[CompletionChunk]:
        try:
            stream_ctx = self._client.stream("POST", url, json=payload, headers=self._headers)
            response = await stream_ctx.__aenter__()
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Could not reach Anthropic at {self.base_url}.",
                self.provider_type,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "Timed out contacting Anthropic.",
                self.provider_type,
            ) from exc

        try:
            await self._handle_error(response)
            current_tool_use: dict | None = None
            usage = Usage()

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event = data.get("type")
                if event == "message_start":
                    raw_usage = (data.get("message") or {}).get("usage") or {}
                    usage = Usage(
                        prompt_tokens=raw_usage.get("input_tokens", 0),
                        completion_tokens=raw_usage.get("output_tokens", 0),
                        total_tokens=raw_usage.get("input_tokens", 0)
                        + raw_usage.get("output_tokens", 0),
                    )
                elif event == "content_block_start":
                    block = data.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        current_tool_use = {
                            "id": block.get("id", "call_0"),
                            "name": block.get("name", ""),
                            "input": "",
                        }

                elif event == "content_block_delta":
                    delta = data.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        yield CompletionChunk(content=delta.get("text", ""))
                    elif delta.get("type") == "thinking_delta":
                        yield CompletionChunk(thinking=delta.get("thinking", ""))
                    elif delta.get("type") == "input_json_delta":
                        if current_tool_use:
                            current_tool_use["input"] += delta.get("partial_json", "")

                elif event == "content_block_stop":
                    if current_tool_use:
                        try:
                            args = json.loads(current_tool_use["input"]) if current_tool_use["input"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield CompletionChunk(tool_calls=[ToolCall(
                            id=current_tool_use["id"],
                            name=current_tool_use["name"],
                            arguments=args,
                        )])
                        current_tool_use = None

                elif event == "message_delta":
                    raw_usage = data.get("usage") or {}
                    usage.completion_tokens = raw_usage.get("output_tokens", usage.completion_tokens)
                    usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
                    if (data.get("delta") or {}).get("stop_reason"):
                        yield CompletionChunk(
                            finish_reason=self._parse_finish_reason(data["delta"]["stop_reason"]),
                            usage=usage,
                        )
        finally:
            await stream_ctx.__aexit__(None, None, None)

    async def _handle_error(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        # Streaming responses haven't consumed the body yet; read it first
        # so that .json() / .text are available for error inspection.
        if not response.is_stream_consumed:
            await response.aread()
        try:
            error_data = response.json()
            message = error_data.get("error", {}).get("message", response.text)
        except Exception:
            message = response.text

        if response.status_code == 401:
            raise ProviderAuthError(message, self.provider_type, response.status_code)
        elif response.status_code == 404:
            raise ProviderModelNotFoundError(message, self.provider_type, response.status_code)
        elif response.status_code == 429:
            raise ProviderRateLimitError(message, self.provider_type, response.status_code)
        elif response.status_code >= 500:
            raise ProviderError(f"Server error: {message}", self.provider_type, response.status_code)
        else:
            raise ProviderError(message, self.provider_type, response.status_code)

    def _parse_finish_reason(self, reason: str | None) -> FinishReason:
        mapping = {
            "end_turn": FinishReason.STOP,
            "max_tokens": FinishReason.LENGTH,
            "tool_use": FinishReason.TOOL_CALLS,
        }
        return mapping.get(reason, FinishReason.STOP)