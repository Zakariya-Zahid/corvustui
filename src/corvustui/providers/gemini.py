"""Google Gemini provider implementation."""

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


class GeminiProvider(BaseProvider):
    """Google Gemini API provider."""

    provider_type = ProviderType.GEMINI

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        **kwargs,
    ):
        super().__init__(model=model, base_url=base_url, api_key=api_key, timeout=timeout, **kwargs)
        self._params = {"key": api_key} if api_key else {}

    def _get_encoder(self) -> tiktoken.Encoding:
        return tiktoken.get_encoding("cl100k_base")

    @staticmethod
    def _merge_consecutive(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge adjacent turns with the same role (Gemini is strict about turns)."""
        merged: list[dict[str, Any]] = []
        for message in messages:
            if merged and merged[-1]["role"] == message["role"]:
                merged[-1]["parts"].extend(message["parts"])
            else:
                merged.append(message)
        return merged

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        gemini_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                continue
            if msg.role == MessageRole.TOOL:
                gemini_messages.append({
                    "role": "function",
                    "parts": [{
                        "function_response": {
                            "name": msg.name or "unknown",
                            "response": {"result": msg.content or ""},
                        }
                    }],
                })
            elif msg.tool_calls:
                parts: list[dict[str, Any]] = []
                if msg.content:
                    parts.append({"text": msg.content})
                for tc in msg.tool_calls:
                    parts.append({
                        "function_call": {
                            "name": tc.name,
                            "args": tc.arguments,
                        }
                    })
                gemini_messages.append({"role": "model", "parts": parts})
            else:
                gemini_messages.append({
                    "role": "user" if msg.role == MessageRole.USER else "model",
                    "parts": [{"text": msg.content or ""}],
                })
        return self._merge_consecutive(gemini_messages)

    def _extract_system(self, messages: list[Message]) -> str | None:
        parts = [msg.content for msg in messages if msg.role == MessageRole.SYSTEM and msg.content]
        return "\n\n".join(parts) if parts else None

    def _convert_tools(self, tools: list[ToolDefinition] | None) -> list[dict] | None:
        if not tools:
            return None
        return [{
            "function_declarations": [{
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            } for t in tools],
        }]

    async def validate_model(self) -> None:
        if not self.api_key:
            raise ProviderAuthError(
                "No Gemini API key configured. Set CORVUS_GEMINI_API_KEY in .env.",
                self.provider_type,
            )

    async def list_models(self) -> list[str]:
        """Fetch models from Google's Generative Language API."""
        if not self._client:
            raise RuntimeError("Provider not initialized. Use async context manager.")
        if not self.api_key:
            raise ProviderAuthError(
                "No Gemini API key configured. Set CORVUS_GEMINI_API_KEY in .env.",
                self.provider_type,
            )

        try:
            response = await self._client.get(
                f"{self.base_url.rstrip('/')}/models",
                params=self._params,
            )
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                "Could not reach Google Gemini.",
                self.provider_type,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "Timed out contacting Google Gemini.",
                self.provider_type,
            ) from exc

        await self._handle_error(response)
        data = response.json()
        models = []
        for item in data.get("models", []):
            name = item.get("name", "")
            methods = item.get("supportedGenerationMethods") or []
            if "generateContent" in methods:
                models.append(name.removeprefix("models/"))
        return models

    async def _post(self, url: str, payload: dict) -> httpx.Response:
        try:
            return await self._client.post(url, json=payload, params=self._params)
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                "Could not reach Google Gemini.",
                self.provider_type,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "Timed out contacting Google Gemini.",
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

        gemini_messages = self._convert_messages(messages)
        system = self._extract_system(messages)
        gemini_tools = self._convert_tools(tools)

        payload = {
            "contents": gemini_messages,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens or 8192,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if gemini_tools:
            payload["tools"] = gemini_tools

        suffix = "streamGenerateContent" if stream else "generateContent"
        url = f"{self.base_url.rstrip('/')}/models/{self.model}:{suffix}"

        if stream:
            return self._stream_request(url, payload)
        return await self._single_request(url, payload)

    async def _single_request(self, url: str, payload: dict) -> CompletionResponse:
        response = await self._post(url, payload)
        await self._handle_error(response)
        data = response.json()

        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError(
                "Gemini returned no candidates.",
                self.provider_type,
                response.status_code,
            )
        candidate = candidates[0]
        content_parts = []
        tool_calls = []

        for index, part in enumerate(candidate.get("content", {}).get("parts", [])):
            if "text" in part:
                content_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(ToolCall(
                    id=f"call_{index}",
                    name=fc.get("name", ""),
                    arguments=dict(fc.get("args") or {}),
                ))

        return CompletionResponse(
            content="\n".join(content_parts) if content_parts else None,
            tool_calls=tool_calls or None,
            finish_reason=self._parse_finish_reason(candidate.get("finishReason")),
            usage=Usage(
                prompt_tokens=data.get("usageMetadata", {}).get("promptTokenCount", 0),
                completion_tokens=data.get("usageMetadata", {}).get("candidatesTokenCount", 0),
                total_tokens=data.get("usageMetadata", {}).get("totalTokenCount", 0),
            ),
            raw=data,
            model=self.model,
            provider=ProviderType.GEMINI,
        )

    async def _stream_request(self, url: str, payload: dict) -> AsyncIterator[CompletionChunk]:
        params = dict(self._params)
        params["alt"] = "sse"
        try:
            stream_ctx = self._client.stream("POST", url, json=payload, params=params)
            response = await stream_ctx.__aenter__()
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                "Could not reach Google Gemini.",
                self.provider_type,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "Timed out contacting Google Gemini.",
                self.provider_type,
            ) from exc

        try:
            await self._handle_error(response)
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                for candidate in data.get("candidates", []):
                    for index, part in enumerate(candidate.get("content", {}).get("parts", [])):
                        if "text" in part:
                            yield CompletionChunk(content=part["text"])
                        elif "functionCall" in part:
                            fc = part["functionCall"]
                            yield CompletionChunk(tool_calls=[ToolCall(
                                id=f"call_{index}",
                                name=fc.get("name", ""),
                                arguments=dict(fc.get("args") or {}),
                            )])

                    if candidate.get("finishReason"):
                        usage = data.get("usageMetadata") or {}
                        yield CompletionChunk(
                            finish_reason=self._parse_finish_reason(candidate["finishReason"]),
                            usage=Usage(
                                prompt_tokens=usage.get("promptTokenCount", 0),
                                completion_tokens=usage.get("candidatesTokenCount", 0),
                                total_tokens=usage.get("totalTokenCount", 0),
                            ),
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
            "STOP": FinishReason.STOP,
            "MAX_TOKENS": FinishReason.LENGTH,
            "FUNCTION_CALL": FinishReason.TOOL_CALLS,
        }
        return mapping.get(reason, FinishReason.STOP)