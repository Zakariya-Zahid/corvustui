"""Provider abstraction layer for LLM APIs."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator

import httpx
import tiktoken

from corvustui.config.settings import ProviderType, get_settings


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(str, Enum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"


@dataclass(slots=True)
class Message:
    """Chat message with role and content."""

    role: MessageRole
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"role": self.role.value}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


@dataclass(slots=True)
class ToolCall:
    """Tool call from the model."""

    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": json.dumps(self.arguments)},
        }


@dataclass(slots=True)
class ToolDefinition:
    """Tool/function definition for the model."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

    def to_openai_format(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(slots=True)
class CompletionChunk:
    """Streaming completion chunk."""

    content: str | None = None
    thinking: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class CompletionResponse:
    """Non-streaming completion response."""

    content: str | None
    tool_calls: list[ToolCall] | None
    finish_reason: FinishReason
    usage: Usage
    raw: dict[str, Any]
    model: str
    provider: ProviderType
    thinking: str | None = None


@dataclass(slots=True)
class Usage:
    """Token usage statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class ProviderError(Exception):
    """Base exception for provider errors."""

    def __init__(self, message: str, provider: ProviderType, status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class ProviderAuthError(ProviderError):
    """Authentication error."""


class ProviderRateLimitError(ProviderError):
    """Rate limit exceeded."""


class ProviderTimeoutError(ProviderError):
    """Request timeout."""


class ProviderUnavailableError(ProviderError):
    """Provider service is unreachable or not running."""


class ProviderModelNotFoundError(ProviderError):
    """The requested model does not exist on the provider."""


class BaseProvider(ABC):
    """Abstract base class for LLM providers."""

    provider_type: ProviderType

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        **kwargs,
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._encoder = self._get_encoder()

    @abstractmethod
    def _get_encoder(self) -> tiktoken.Encoding:
        """Get the tiktoken encoder for this provider's models."""
        pass

    @abstractmethod
    async def _make_request(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        stream: bool,
        temperature: float,
        max_tokens: int | None,
        **kwargs,
    ) -> AsyncIterator[CompletionChunk] | CompletionResponse:
        """Make the actual API request."""
        pass

    async def list_models(self) -> list[str]:
        """List models offered by this provider.

        Providers that cannot enumerate models must raise NotImplementedError
        so callers can show an explicit message instead of inventing a list.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support model listing")

    async def validate_model(self) -> None:
        """Validate that the configured model exists.

        Providers that cannot cheaply validate a model keep this as a no-op.
        """
        return None

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        *,
        stream: bool = False,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> AsyncIterator[CompletionChunk] | CompletionResponse:
        """Public completion interface."""
        return await self._make_request(
            messages=messages,
            tools=tools,
            stream=stream,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return len(self._encoder.encode(text))

    def count_message_tokens(self, message: Message) -> int:
        """Count tokens in a message."""
        count = self.count_tokens(message.role.value)
        if message.content:
            count += self.count_tokens(message.content)
        if message.tool_calls:
            for tc in message.tool_calls:
                count += self.count_tokens(tc.name)
                count += self.count_tokens(json.dumps(tc.arguments))
        return count + 3  # overhead per message

    def count_messages_tokens(self, messages: list[Message]) -> int:
        """Count tokens in a list of messages."""
        return sum(self.count_message_tokens(m) for m in messages) + 3  # final overhead

    async def _ensure_response_read(self, response: httpx.Response) -> None:
        """Buffer an unconsumed streaming HTTP response before inspecting its body."""
        if not response.is_stream_consumed:
            try:
                await response.aread()
            except httpx.TimeoutException as exc:
                raise ProviderTimeoutError(
                    f"Timed out reading error response from {self.provider_type.value}.",
                    self.provider_type,
                    response.status_code,
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(
                    f"Failed reading error response (HTTP {response.status_code}): {exc}",
                    self.provider_type,
                    response.status_code,
                ) from exc

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        """Extract a human-readable error message from a buffered HTTP response."""
        try:
            fallback = response.text
        except httpx.ResponseNotRead:
            return f"HTTP {response.status_code}"

        try:
            data = response.json()
        except Exception:
            return fallback

        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or fallback)
            if isinstance(err, str) and err:
                return err
            if isinstance(data.get("message"), str) and data["message"]:
                return data["message"]
        return fallback

    def _raise_for_status(self, status_code: int | None, message: str) -> None:
        """Map an HTTP status code and error message to a typed ProviderError."""
        if status_code == 401:
            raise ProviderAuthError(message, self.provider_type, status_code)
        if status_code == 404:
            raise ProviderModelNotFoundError(message, self.provider_type, status_code)
        if status_code == 429:
            raise ProviderRateLimitError(message, self.provider_type, status_code)
        if status_code is not None and status_code >= 500:
            raise ProviderError(f"Server error: {message}", self.provider_type, status_code)
        raise ProviderError(message, self.provider_type, status_code)

    def _raise_from_stream_error(self, err: Any, *, default_status: int | None = 500) -> None:
        """Raise a typed ProviderError from a mid-stream JSON error payload."""
        if isinstance(err, dict):
            message = str(err.get("message") or err)
            code = err.get("code")
            if isinstance(code, int) and code >= 400:
                status_code: int | None = code
            elif err.get("type") == "rate_limit_error":
                status_code = 429
            else:
                status_code = default_status
        else:
            message = str(err)
            status_code = default_status
        self._raise_for_status(status_code, message)

    async def _handle_error(self, response: httpx.Response) -> None:
        """Validate HTTP status, buffering streaming error bodies before inspection."""
        if response.is_success:
            return
        await self._ensure_response_read(response)
        message = self._extract_error_message(response)
        self._raise_for_status(response.status_code, message)

    async def __aenter__(self) -> BaseProvider:
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


class ProviderFactory:
    """Factory for creating provider instances."""

    _providers: dict[ProviderType, type[BaseProvider]] = {}

    @classmethod
    def register(cls, provider_type: ProviderType, provider_class: type[BaseProvider]) -> None:
        cls._providers[provider_type] = provider_class

    @classmethod
    def create(
        cls,
        provider_type: ProviderType,
        model: str | None = None,
        **kwargs,
    ) -> BaseProvider:
        if provider_type not in cls._providers:
            raise ValueError(f"Unknown provider: {provider_type}")

        settings = get_settings()
        provider_class = cls._providers[provider_type]

        # Use provider-specific defaults if no model was supplied, so a
        # local Ollama tag is never sent to a cloud API.
        if model is None:
            model = {
                ProviderType.OLLAMA: settings.default_model,
                ProviderType.OPENAI: settings.openai_default_model,
                ProviderType.ANTHROPIC: settings.anthropic_default_model,
                ProviderType.GROQ: settings.groq_default_model,
                ProviderType.GEMINI: settings.gemini_default_model,
                ProviderType.OPENROUTER: settings.openrouter_default_model,
                ProviderType.GROK: settings.grok_default_model,
            }.get(provider_type, settings.default_model)

        # Provider-specific defaults
        if provider_type == ProviderType.OLLAMA:
            kwargs.setdefault("base_url", settings.ollama_base_url)
            kwargs.setdefault("timeout", settings.ollama_timeout)
        elif provider_type == ProviderType.OPENAI:
            kwargs.setdefault("api_key", settings.openai_api_key)
            kwargs.setdefault("base_url", settings.openai_base_url)
        elif provider_type == ProviderType.ANTHROPIC:
            kwargs.setdefault("api_key", settings.anthropic_api_key)
            kwargs.setdefault("base_url", settings.anthropic_base_url)
        elif provider_type == ProviderType.GROQ:
            kwargs.setdefault("api_key", settings.groq_api_key)
            kwargs.setdefault("base_url", settings.groq_base_url)
        elif provider_type == ProviderType.GEMINI:
            kwargs.setdefault("api_key", settings.gemini_api_key)
            kwargs.setdefault("base_url", settings.gemini_base_url)
        elif provider_type == ProviderType.OPENROUTER:
            kwargs.setdefault("api_key", settings.openrouter_api_key)
            kwargs.setdefault("base_url", settings.openrouter_base_url)
        elif provider_type == ProviderType.GROK:
            kwargs.setdefault("api_key", settings.grok_api_key)
            kwargs.setdefault("base_url", settings.grok_base_url)

        return provider_class(model=model, **kwargs)

    @classmethod
    def create_from_string(cls, provider_str: str, model: str | None = None, **kwargs) -> BaseProvider:
        """Create provider from string (e.g., 'ollama', 'openai')."""
        return cls.create(ProviderType(provider_str.lower()), model=model, **kwargs)