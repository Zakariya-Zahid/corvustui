"""OpenAI-compatible provider (OpenAI, Groq, etc.)."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx
import tiktoken

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


class OpenAICompatibleProvider(BaseProvider):
    """Base class for OpenAI-compatible APIs."""

    def __init__(self, model: str, base_url: str, api_key: str | None = None, **kwargs):
        super().__init__(model=model, base_url=base_url, api_key=api_key, **kwargs)
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _get_encoder(self) -> tiktoken.Encoding:
        try:
            return tiktoken.encoding_for_model(self.model)
        except KeyError:
            return tiktoken.get_encoding("cl100k_base")

    async def validate_model(self) -> None:
        """Fail fast when the required API key is missing."""
        if not self.api_key:
            raise ProviderAuthError(
                f"No API key configured for {self.provider_type.value}. "
                f"Set CORVUS_{self.provider_type.value.upper()}_API_KEY in .env.",
                self.provider_type,
            )

    async def list_models(self) -> list[str]:
        """Fetch available models from the API.

        Raises a specific error instead of returning a fabricated list.
        """
        if not self._client:
            raise RuntimeError("Provider not initialized. Use async context manager.")
        if not self.api_key:
            raise ProviderAuthError(
                f"No API key configured for {self.provider_type.value}. "
                f"Set CORVUS_{self.provider_type.value.upper()}_API_KEY in .env.",
                self.provider_type,
            )

        try:
            response = await self._client.get(
                f"{self.base_url.rstrip('/')}/models",
                headers=self._headers,
            )
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Could not reach {self.provider_type.value} at {self.base_url}.",
                self.provider_type,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Timed out contacting {self.provider_type.value}.",
                self.provider_type,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Could not contact {self.provider_type.value}: {exc}",
                self.provider_type,
            ) from exc

        await self._handle_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"{self.provider_type.value} returned an invalid model list.",
                self.provider_type,
                response.status_code,
            ) from exc

        return [model["id"] for model in data.get("data", []) if model.get("id")]

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        openai_messages = []
        for msg in messages:
            if msg.role == MessageRole.TOOL:
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content or "",
                })
            elif msg.tool_calls:
                openai_messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [tc.to_dict() for tc in msg.tool_calls],
                })
            else:
                openai_messages.append({"role": msg.role.value, "content": msg.content or ""})
        return openai_messages

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

        openai_messages = self._convert_messages(messages)
        openai_tools = [t.to_openai_format() for t in tools] if tools else None

        payload = {
            "model": self.model,
            "messages": openai_messages,
            "stream": stream,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if openai_tools:
            payload["tools"] = openai_tools
            payload["tool_choice"] = "auto"

        url = f"{self.base_url.rstrip('/')}/chat/completions"

        if stream:
            return self._stream_request(url, payload)
        return await self._single_request(url, payload)

    async def _post(self, url: str, payload: dict) -> httpx.Response:
        try:
            return await self._client.post(url, json=payload, headers=self._headers)
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Could not reach {self.provider_type.value} at {self.base_url}.",
                self.provider_type,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Timeout talking to {self.provider_type.value}.",
                self.provider_type,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Could not contact {self.provider_type.value}: {exc}",
                self.provider_type,
            ) from exc

    async def _single_request(self, url: str, payload: dict) -> CompletionResponse:
        response = await self._post(url, payload)
        await self._handle_error(response)
        data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(
                f"{self.provider_type.value} returned no choices.",
                self.provider_type,
                response.status_code,
            )
        choice = choices[0]
        msg = choice.get("message", {})
        usage = data.get("usage") or {}

        return CompletionResponse(
            content=msg.get("content"),
            tool_calls=self._parse_tool_calls(msg.get("tool_calls") or []),
            finish_reason=self._parse_finish_reason(choice.get("finish_reason")),
            usage=Usage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
            raw=data,
            model=self.model,
            provider=self.provider_type,
        )

    @staticmethod
    def _flush_pending_calls(pending: dict[int, dict[str, Any]]) -> list[ToolCall]:
        """Turn accumulated streamed tool-call fragments into ToolCalls."""
        calls: list[ToolCall] = []
        for _, slot in sorted(pending.items()):
            args = slot["arguments"]
            try:
                parsed = json.loads(args) if args else {}
            except json.JSONDecodeError:
                parsed = OpenAICompatibleProvider._repair_json(args)
            calls.append(ToolCall(
                id=slot["id"] or f"call_{len(calls)}",
                name=slot["name"],
                arguments=parsed if isinstance(parsed, dict) else {},
            ))
        pending.clear()
        return calls

    async def _stream_request(self, url: str, payload: dict) -> AsyncIterator[CompletionChunk]:
        pending: dict[int, dict[str, Any]] = {}
        last_usage: Usage | None = None
        usage_yielded = False
        try:
            stream_ctx = self._client.stream("POST", url, json=payload, headers=self._headers)
            response = await stream_ctx.__aenter__()
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Could not reach {self.provider_type.value} at {self.base_url}.",
                self.provider_type,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Timeout talking to {self.provider_type.value}.",
                self.provider_type,
            ) from exc

        try:
            await self._handle_error(response)
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    for call in self._flush_pending_calls(pending):
                        yield CompletionChunk(tool_calls=[call])
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # Some APIs send usage in a final chunk with no choices.
                if data.get("usage"):
                    raw_usage = data["usage"]
                    last_usage = Usage(
                        prompt_tokens=raw_usage.get("prompt_tokens", 0),
                        completion_tokens=raw_usage.get("completion_tokens", 0),
                        total_tokens=raw_usage.get("total_tokens", 0),
                    )

                choices = data.get("choices") or []
                if not choices:
                    if last_usage is not None and not usage_yielded:
                        usage_yielded = True
                        yield CompletionChunk(usage=last_usage)
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})

                if content := delta.get("content"):
                    yield CompletionChunk(content=content)

                for tc in delta.get("tool_calls") or []:
                    index = tc.get("index", 0)
                    slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    func = tc.get("function", {})
                    if func.get("name"):
                        slot["name"] = func["name"]
                    if func.get("arguments"):
                        slot["arguments"] += func["arguments"]

                if choice.get("finish_reason"):
                    for call in self._flush_pending_calls(pending):
                        yield CompletionChunk(tool_calls=[call])
                    yield CompletionChunk(
                        finish_reason=self._parse_finish_reason(choice["finish_reason"]),
                        usage=None if usage_yielded else last_usage,
                    )

            # Streams that end without an explicit finish_reason still must
            # deliver any fully accumulated tool calls.
            for call in self._flush_pending_calls(pending):
                yield CompletionChunk(tool_calls=[call])
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

    @staticmethod
    def _repair_json(raw: str) -> dict[str, Any] | None:
        """Best-effort repair for malformed tool-call JSON."""
        import re

        text = raw.strip()
        text = re.sub(r"'([^']*)':", r'"\1":', text)
        text = re.sub(r":\s*'([^']*)'", r': "\1"', text)
        text = re.sub(r",\s*}", "}", text)
        text = re.sub(r",\s*]", "]", text)
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def _parse_tool_calls(self, tool_calls: list[dict]) -> list[ToolCall] | None:
        if not tool_calls:
            return None

        result: list[ToolCall] = []
        for index, tc in enumerate(tool_calls):
            func = tc.get("function", {})
            raw_args = func.get("arguments", {})
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    args = self._repair_json(raw_args) or {}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}
            result.append(ToolCall(
                id=tc.get("id", f"call_{index}"),
                name=func.get("name", ""),
                arguments=args,
            ))
        return result

    def _parse_finish_reason(self, reason: str | None) -> FinishReason:
        mapping = {
            "stop": FinishReason.STOP,
            "length": FinishReason.LENGTH,
            "tool_calls": FinishReason.TOOL_CALLS,
            "content_filter": FinishReason.CONTENT_FILTER,
        }
        return mapping.get(reason, FinishReason.STOP)


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI API provider."""
    provider_type = ProviderType.OPENAI


class GroqProvider(OpenAICompatibleProvider):
    """Groq API provider (OpenAI-compatible)."""
    provider_type = ProviderType.GROQ


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter API provider (OpenAI-compatible)."""
    provider_type = ProviderType.OPENROUTER


class GrokProvider(OpenAICompatibleProvider):
    """Grok (xAI) API provider (OpenAI-compatible)."""
    provider_type = ProviderType.GROK