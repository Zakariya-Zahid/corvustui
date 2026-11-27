"""Provider detection from model names.

Kept outside the CLI so both the REPL and the Textual UI can use the same
rules without importing each other.
"""

from __future__ import annotations

from corvustui.providers.base import ProviderType


def detect_provider_from_model(model: str) -> ProviderType:
    """Detect provider type from a model name.

    Ollama tags use a ``name:tag`` shape.  This check must run before the
    Groq name heuristics, otherwise local tags such as ``gemma2:2b`` or
    ``mixtral:8x7b`` are misrouted to Groq.
    """
    model_lower = model.lower()

    # Ollama Hub references are local even though they contain a slash.
    if model_lower.startswith("hf.co/"):
        return ProviderType.OLLAMA

    # OpenRouter models normally have a provider prefix.
    if "/" in model:
        return ProviderType.OPENROUTER

    if ":" in model:
        return ProviderType.OLLAMA

    if model_lower.startswith("grok"):
        return ProviderType.GROK

    if model_lower.startswith(("gpt-", "o1", "o3", "o4")):
        return ProviderType.OPENAI

    if model_lower.startswith("claude"):
        return ProviderType.ANTHROPIC

    # Groq-hosted model names carry distinctive suffixes; require one of
    # them instead of matching bare families that also exist in Ollama.
    if any(x in model_lower for x in ["-instant", "-versatile", "-32768"]):
        return ProviderType.GROQ

    if model_lower.startswith("gemini"):
        return ProviderType.GEMINI

    return ProviderType.OLLAMA
