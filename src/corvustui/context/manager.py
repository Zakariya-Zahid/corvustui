"""Context window management with token counting, sliding window, and summarization."""

from __future__ import annotations

from dataclasses import dataclass

from corvustui.config.settings import get_settings
from corvustui.providers.base import BaseProvider, Message, MessageRole


@dataclass(slots=True)
class ContextStats:
    """Statistics about current context."""

    total_tokens: int
    max_tokens: int
    reserved_tokens: int
    available_tokens: int
    usage_ratio: float
    message_count: int

    @property
    def is_near_limit(self) -> bool:
        return self.usage_ratio >= get_settings().summarization_threshold

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.available_tokens)


class ContextManager:
    """
    Manages conversation context window with:
    - Token counting
    - Sliding window (keeps recent messages)
    - Automatic summarization when near limit
    """

    def __init__(
        self,
        provider: BaseProvider,
        max_tokens: int | None = None,
        reserved_tokens: int | None = None,
        summarization_model: str | None = None,
    ):
        self.provider = provider
        self.max_tokens = max_tokens or get_settings().max_context_tokens
        self.reserved_tokens = reserved_tokens or get_settings().reserved_tokens
        self.summarization_model = summarization_model or get_settings().summarization_model
        self.available_tokens = self.max_tokens - self.reserved_tokens

        self._messages: list[Message] = []
        self._system_message: Message | None = None
        self._summary: str | None = None

    @property
    def messages(self) -> list[Message]:
        return self._messages.copy()

    @property
    def system_message(self) -> Message | None:
        return self._system_message

    @property
    def summary(self) -> str | None:
        return self._summary

    def add_system(self, content: str) -> None:
        """Add or update system message."""
        self._system_message = Message(role=MessageRole.SYSTEM, content=content)

    def add_message(self, message: Message) -> None:
        """Add a message to context."""
        self._messages.append(message)

    def add_user(self, content: str) -> None:
        self.add_message(Message(role=MessageRole.USER, content=content))

    def add_assistant(self, content: str, tool_calls: list | None = None) -> None:
        self.add_message(Message(role=MessageRole.ASSISTANT, content=content, tool_calls=tool_calls))

    def add_tool(self, name: str, content: str, tool_call_id: str) -> None:
        self.add_message(Message(
            role=MessageRole.TOOL,
            content=content,
            name=name,
            tool_call_id=tool_call_id,
        ))

    def get_all_messages(self) -> list[Message]:
        """Get all messages including system and summary."""
        messages = []
        if self._system_message:
            messages.append(self._system_message)
        if self._summary:
            messages.append(Message(
                role=MessageRole.SYSTEM,
                content=f"Previous conversation summary:\n{self._summary}",
            ))
        messages.extend(self._messages)
        return messages

    def get_history(self) -> list[Message]:
        """Get replayable conversation history.

        The caller owns the main system prompt, so it is excluded here to
        avoid sending it twice.  The rolling summary is included because it
        is the only record of messages that were pruned.
        """
        messages: list[Message] = []
        if self._summary:
            messages.append(Message(
                role=MessageRole.SYSTEM,
                content=f"Previous conversation summary:\n{self._summary}",
            ))
        messages.extend(self._messages)
        return messages

    def count_tokens(self) -> int:
        """Count total tokens in current context."""
        return self.provider.count_messages_tokens(self.get_all_messages())

    def get_stats(self) -> ContextStats:
        """Get context statistics."""
        total = self.count_tokens()
        return ContextStats(
            total_tokens=total,
            max_tokens=self.max_tokens,
            reserved_tokens=self.reserved_tokens,
            available_tokens=self.available_tokens,
            usage_ratio=total / self.available_tokens if self.available_tokens > 0 else 1.0,
            message_count=len(self._messages) + (1 if self._system_message else 0),
        )

    def needs_summarization(self) -> bool:
        """Check if context needs summarization."""
        return self.get_stats().is_near_limit

    async def summarize_if_needed(self, summarizer_provider: BaseProvider | None = None) -> bool:
        """Summarize old messages if near token limit."""
        if not self.needs_summarization():
            return False

        provider = summarizer_provider or self.provider
        return await self._summarize_old_messages(provider)

    async def _summarize_old_messages(self, provider: BaseProvider) -> bool:
        """Summarize older messages, keeping recent ones."""
        if len(self._messages) <= 4:  # Keep at least 2 exchanges
            return False

        # Keep system + summary + last 4 messages
        keep_count = 4
        to_summarize = self._messages[:-keep_count]
        recent = self._messages[-keep_count:]

        if not to_summarize:
            return False

        # Build summarization prompt
        conversation = "\n".join([
            f"{m.role.value}: {m.content or ''}" for m in to_summarize
        ])

        summary_prompt = f"""Summarize the following conversation concisely, preserving key facts, decisions, and outcomes:

{conversation}

Summary:"""

        try:
            response = await provider.complete(
                messages=[Message(role=MessageRole.USER, content=summary_prompt)],
                stream=False,
                temperature=0.3,
                max_tokens=500,
            )

            new_summary = response.content or ""
            if self._summary:
                self._summary = f"{self._summary}\n\n---\n\n{new_summary}"
            else:
                self._summary = new_summary

            # Replace old messages with summary reference
            self._messages = recent
            return True
        except Exception:
            # If summarization fails, fall back to sliding window
            self._slide_window(keep_count)
            return False

    def _slide_window(self, keep_recent: int) -> None:
        """Simple sliding window - keep only recent messages."""
        if len(self._messages) > keep_recent:
            self._messages = self._messages[-keep_recent:]

    def force_slide_window(self, keep_recent: int) -> None:
        """Force sliding window to specified size."""
        self._slide_window(keep_recent)

    def clear(self, keep_system: bool = True) -> None:
        """Clear context."""
        self._messages.clear()
        self._summary = None
        if not keep_system:
            self._system_message = None