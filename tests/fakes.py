"""Shared test doubles for CorvusTUI tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from corvustui.providers.base import (
    CompletionChunk,
    CompletionResponse,
    FinishReason,
    Message,
    ProviderType,
    ToolCall,
    Usage,
)


@dataclass
class FakeUsage:
    prompt_tokens: int = 1
    completion_tokens: int = 1
    total_tokens: int = 2


@dataclass
class FakeResponse:
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: FinishReason = FinishReason.STOP
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] = field(default_factory=dict)
    model: str = "fake"
    provider: ProviderType = ProviderType.OLLAMA


def text_response(text: str) -> CompletionResponse:
    return CompletionResponse(
        content=text,
        tool_calls=None,
        finish_reason=FinishReason.STOP,
        usage=Usage(total_tokens=2),
        raw={},
        model="fake",
        provider=ProviderType.OLLAMA,
    )


def tool_response(*calls: tuple[str, dict[str, Any]], content: str | None = None) -> CompletionResponse:
    tool_calls = [
        ToolCall(id=f"call_{index}", name=name, arguments=arguments)
        for index, (name, arguments) in enumerate(calls)
    ]
    return CompletionResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=FinishReason.TOOL_CALLS,
        usage=Usage(total_tokens=2),
        raw={},
        model="fake",
        provider=ProviderType.OLLAMA,
    )


class FakeProvider:
    """Provider double that replays a queue of responses."""

    provider_type = ProviderType.OLLAMA

    def __init__(
        self,
        responses: list[CompletionResponse],
        model: str = "fake-model",
        fail_with: Exception | None = None,
    ):
        self.responses = list(responses)
        self.model = model
        self.calls: list[list[Message]] = []
        self.entered = 0
        self.exited = 0
        self.validated = 0
        self.fail_with = fail_with
        self.validation_error: Exception | None = None

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *args):
        self.exited += 1

    async def validate_model(self):
        self.validated += 1
        if self.validation_error is not None:
            raise self.validation_error

    async def complete(self, messages, tools=None, stream=False, temperature=0.7,
                       max_tokens=None, **kwargs):
        self.calls.append(list(messages))
        if self.fail_with is not None:
            raise self.fail_with
        if not self.responses:
            response = text_response("done")
        else:
            response = self.responses.pop(0)

        if not stream:
            return response

        async def chunks():
            text = response.content or ""
            for index in range(0, len(text), 4):
                yield CompletionChunk(content=text[index:index + 4])
            for call in response.tool_calls or []:
                yield CompletionChunk(tool_calls=[call])
            yield CompletionChunk(
                finish_reason=response.finish_reason,
                usage=response.usage,
            )

        return chunks()

    def count_message_tokens(self, message: Message) -> int:
        return len(message.content or "") // 4 + 1

    def count_messages_tokens(self, messages: list[Message]) -> int:
        return sum(self.count_message_tokens(message) for message in messages)


class FakeToolRegistryTool:
    """Minimal tool used to observe loop execution."""

    def __init__(self, name: str = "echo", result: str = "ok"):
        from corvustui.tools.base import ToolResult

        self.name = name
        self.description = f"Fake {name} tool"
        self.parameters = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": [],
        }
        self._result = ToolResult

        async def execute(**kwargs):
            return self._result.ok(result)

        self.execute = execute
