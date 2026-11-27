"""Ollama provider implementation."""

from __future__ import annotations

import json
import logging
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
    ProviderError,
    ProviderModelNotFoundError,
    ProviderTimeoutError,
    ProviderType,
    ProviderUnavailableError,
    ToolCall,
    ToolDefinition,
    Usage,
)

logger = logging.getLogger(__name__)


class OllamaProvider(BaseProvider):
    """Ollama local LLM provider."""

    provider_type = ProviderType.OLLAMA

    def _get_encoder(self) -> tiktoken.Encoding:
        return tiktoken.get_encoding("cl100k_base")

    def _tags_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/tags"

    def _chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/chat"

    async def list_models(self) -> list[str]:
        """Fetch available models from Ollama.

        Raises ProviderUnavailableError when Ollama cannot be reached so the
        CLI can distinguish "service down" from "no models installed".
        """
        if not self._client:
            raise RuntimeError("Provider not initialized. Use async context manager.")

        try:
            response = await self._client.get(self._tags_url())
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Ollama is not reachable at {self.base_url}. "
                "Start it with 'ollama serve' and try again.",
                ProviderType.OLLAMA,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Timed out contacting Ollama at {self.base_url}.",
                ProviderType.OLLAMA,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Could not contact Ollama at {self.base_url}: {exc}",
                ProviderType.OLLAMA,
            ) from exc

        if not response.is_success:
            raise ProviderError(
                f"Ollama returned HTTP {response.status_code} while listing models.",
                ProviderType.OLLAMA,
                response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(
                "Ollama returned an invalid response while listing models.",
                ProviderType.OLLAMA,
                response.status_code,
            ) from exc

        return [model["name"] for model in data.get("models", []) if model.get("name")]

    async def validate_model(self) -> None:
        """Ensure the configured model is installed in Ollama."""
        models = await self.list_models()
        if not models:
            raise ProviderModelNotFoundError(
                f"Ollama is running but no models are installed. "
                f"Install the configured model with 'ollama pull {self.model}'.",
                ProviderType.OLLAMA,
            )
        # Ollama reports untagged models as "<name>:latest", while users
        # commonly type the bare name.  Accept either spelling.
        accepted = {self.model}
        if ":" not in self.model:
            accepted.add(f"{self.model}:latest")

        if not accepted.intersection(models):
            available = ", ".join(models)
            raise ProviderModelNotFoundError(
                f"Model '{self.model}' is not installed in Ollama. "
                f"Available models: {available}. "
                f"Run 'ollama pull {self.model}' to install it, or switch with 'model <name>'.",
                ProviderType.OLLAMA,
            )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert internal messages to Ollama's native chat format."""
        ollama_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == MessageRole.TOOL:
                item: dict[str, Any] = {
                    "role": "tool",
                    "content": msg.content or "",
                }
                if msg.name:
                    item["tool_name"] = msg.name
                ollama_messages.append(item)
            elif msg.tool_calls:
                item = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments,
                            }
                        }
                        for tc in msg.tool_calls
                    ],
                }
                ollama_messages.append(item)
            else:
                ollama_messages.append({
                    "role": msg.role.value,
                    "content": msg.content or "",
                })
        return ollama_messages

    @staticmethod
    def _convert_tools(tools: list[ToolDefinition] | None) -> list[dict[str, Any]]:
        """Convert tool definitions to Ollama's OpenAI-compatible format.

        Ollama requires the full ``{"type": "function", "function": {...}}``
        wrapper.  Sending only the inner function object makes Ollama unmarshal
        empty ``type``/``name``/``parameters`` fields and reject the whole
        request with HTTP 500 ("Unsupported tool type").  Definitions without
        a usable name or schema are dropped so a single bad tool cannot break
        every chat request.
        """
        if not tools:
            return []

        converted: list[dict[str, Any]] = []
        for tool in tools:
            name = (tool.name or "").strip()
            if not name:
                logger.warning("Skipping Ollama tool with an empty name")
                continue

            raw_parameters = tool.parameters if isinstance(tool.parameters, dict) else {}
            parameters = dict(raw_parameters)
            # Function parameters are always an object schema in the OpenAI
            # tool format.  Normalise missing/incorrect values instead of
            # letting Ollama reject the entire request.
            parameters["type"] = "object"
            if not isinstance(parameters.get("properties"), dict):
                parameters["properties"] = {}

            converted.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.description or "",
                    "parameters": parameters,
                },
            })
        return converted

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

        ollama_messages = self._convert_messages(messages)
        ollama_tools = self._convert_tools(tools)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": stream,
            "options": {"temperature": temperature, "num_predict": max_tokens or -1},
        }
        if ollama_tools:
            payload["tools"] = ollama_tools

        url = self._chat_url()
        if stream:
            return self._stream_request(url, payload)
        return await self._single_request(url, payload)

    async def _handle_response_error(self, response: httpx.Response) -> None:
        """Handle Ollama-specific error responses."""
        if response.is_success:
            return
        # Streaming responses haven't consumed the body yet; read it first
        # so that .json() / .text are available for error inspection.
        if not response.is_stream_consumed:
            await response.aread()

        if response.status_code == 404:
            try:
                error_data = response.json()
                error_msg = error_data.get("error", "")
            except ValueError:
                error_msg = ""

            if "not found" in error_msg.lower() or "model" in error_msg.lower():
                available = await self._get_available_models()
                models_str = ", ".join(available) if available else "none installed"
                raise ProviderModelNotFoundError(
                    f"Model '{self.model}' not found in Ollama. "
                    f"Available models: {models_str}. "
                    f"Run 'ollama pull {self.model}' to install it.",
                    ProviderType.OLLAMA,
                    response.status_code,
                )

        raise ProviderError(
            f"Ollama request failed (HTTP {response.status_code}): {response.text[:300]}",
            ProviderType.OLLAMA,
            response.status_code,
        )

    async def _get_available_models(self) -> list[str]:
        """Best-effort model list used only to enrich error messages."""
        try:
            if not self._client:
                return []
            resp = await self._client.get(self._tags_url())
            if resp.is_success:
                data = resp.json()
                return [m["name"] for m in data.get("models", []) if m.get("name")]
        except Exception:
            pass
        return []

    async def _post(self, url: str, payload: dict) -> httpx.Response:
        try:
            return await self._client.post(url, json=payload)
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Ollama is not reachable at {self.base_url}. "
                "Start it with 'ollama serve' and try again.",
                ProviderType.OLLAMA,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Ollama request timed out after {self.timeout}s.",
                ProviderType.OLLAMA,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Ollama request failed: {exc}",
                ProviderType.OLLAMA,
            ) from exc

    async def _single_request(self, url: str, payload: dict) -> CompletionResponse:
        response = await self._post(url, payload)
        await self._handle_response_error(response)
        data = response.json()

        msg = data.get("message", {})
        return CompletionResponse(
            content=msg.get("content"),
            thinking=msg.get("thinking"),
            tool_calls=self._parse_tool_calls(msg.get("tool_calls", [])),
            finish_reason=self._parse_finish_reason(data.get("done_reason", "stop")),
            usage=Usage(
                prompt_tokens=data.get("prompt_eval_count", 0),
                completion_tokens=data.get("eval_count", 0),
                total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            ),
            raw=data,
            model=self.model,
            provider=ProviderType.OLLAMA,
        )

    async def _stream_request(self, url: str, payload: dict) -> AsyncIterator[CompletionChunk]:
        try:
            stream_ctx = self._client.stream("POST", url, json=payload)
            response = await stream_ctx.__aenter__()
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Ollama is not reachable at {self.base_url}. "
                "Start it with 'ollama serve' and try again.",
                ProviderType.OLLAMA,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Ollama request timed out after {self.timeout}s.",
                ProviderType.OLLAMA,
            ) from exc

        try:
            await self._handle_response_error(response)
            tool_call_index = 0
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = data.get("message", {})
                if thinking_delta := msg.get("thinking"):
                    yield CompletionChunk(thinking=thinking_delta)
                if delta := msg.get("content"):
                    yield CompletionChunk(content=delta)

                if tool_calls := msg.get("tool_calls"):
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        yield CompletionChunk(tool_calls=[ToolCall(
                            id=tc.get("id") or f"call_{tool_call_index}",
                            name=func.get("name", ""),
                            arguments=func.get("arguments", {}),
                        )])
                        tool_call_index += 1

                if data.get("done"):
                    yield CompletionChunk(
                        finish_reason=self._parse_finish_reason(data.get("done_reason", "stop")),
                        usage=Usage(
                            prompt_tokens=data.get("prompt_eval_count", 0),
                            completion_tokens=data.get("eval_count", 0),
                            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
                        ),
                    )
        finally:
            await stream_ctx.__aexit__(None, None, None)

    def _parse_tool_calls(self, tool_calls: list[dict]) -> list[ToolCall] | None:
        if not tool_calls:
            return None
        result = []
        for tc in tool_calls:
            func = tc.get("function", {})
            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            result.append(ToolCall(
                id=tc.get("id", f"call_{len(result)}"),
                name=func.get("name", ""),
                arguments=args,
            ))
        return result

    def _parse_finish_reason(self, reason: str) -> FinishReason:
        mapping = {
            "stop": FinishReason.STOP,
            "length": FinishReason.LENGTH,
            "tool_calls": FinishReason.TOOL_CALLS,
        }
        return mapping.get(reason, FinishReason.STOP)
