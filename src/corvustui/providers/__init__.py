"""Provider package - registers all available providers."""

from corvustui.providers.anthropic import AnthropicProvider
from corvustui.providers.base import (
    BaseProvider,
    CompletionChunk,
    CompletionResponse,
    FinishReason,
    Message,
    MessageRole,
    ProviderAuthError,
    ProviderError,
    ProviderFactory,
    ProviderModelNotFoundError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderType,
    ProviderUnavailableError,
    ToolCall,
    ToolDefinition,
    Usage,
)
from corvustui.providers.gemini import GeminiProvider
from corvustui.providers.ollama import OllamaProvider
from corvustui.providers.openai_compat import (
    GrokProvider,
    GroqProvider,
    OpenAIProvider,
    OpenRouterProvider,
)

# Register all providers
ProviderFactory.register(ProviderType.OLLAMA, OllamaProvider)
ProviderFactory.register(ProviderType.OPENAI, OpenAIProvider)
ProviderFactory.register(ProviderType.GROQ, GroqProvider)
ProviderFactory.register(ProviderType.OPENROUTER, OpenRouterProvider)
ProviderFactory.register(ProviderType.GROK, GrokProvider)
ProviderFactory.register(ProviderType.ANTHROPIC, AnthropicProvider)
ProviderFactory.register(ProviderType.GEMINI, GeminiProvider)

__all__ = [
    "ProviderFactory",
    "ProviderType",
    "BaseProvider",
    "Message",
    "MessageRole",
    "ToolDefinition",
    "ToolCall",
    "CompletionChunk",
    "CompletionResponse",
    "FinishReason",
    "Usage",
    "ProviderError",
    "ProviderAuthError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderModelNotFoundError",
    "OllamaProvider",
    "OpenAIProvider",
    "GroqProvider",
    "OpenRouterProvider",
    "GrokProvider",
    "AnthropicProvider",
    "GeminiProvider",
]