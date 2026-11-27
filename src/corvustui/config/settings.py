"""Configuration management for CorvusTUI using pydantic-settings."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderType(str, Enum):
    """Supported LLM providers."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    GROK = "grok"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """Global application settings loaded from environment and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="CORVUS_",
    )

    # General
    log_level: LogLevel = LogLevel.INFO
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".corvustui")
    config_dir: Path = Field(default_factory=lambda: Path.home() / ".config" / "corvustui")

    # Provider settings (can be overridden per-session)
    default_provider: ProviderType = ProviderType.OLLAMA
    default_model: str = "qwen3.5:0.8b"

    # Provider-specific defaults, used when --model is omitted.  These are
    # deliberately separate from default_model so a local Ollama tag is never
    # sent to a cloud API.
    openai_default_model: str = "gpt-4o-mini"
    anthropic_default_model: str = "claude-3-5-sonnet-latest"
    groq_default_model: str = "llama-3.1-8b-instant"
    gemini_default_model: str = "gemini-1.5-flash"
    openrouter_default_model: str = "openai/gpt-4o-mini"
    grok_default_model: str = "grok-2-latest"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout: float = 120.0

    # OpenAI
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_organization: str | None = None

    # Anthropic
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"

    # Groq
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Gemini
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # OpenRouter
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Grok (xAI)
    grok_api_key: str | None = None
    grok_base_url: str = "https://api.x.ai/v1"

    # Tavily Web Search
    tavily_api_key: str | None = None

    # Context management
    max_context_tokens: int = 8192
    reserved_tokens: int = 1024
    summarization_threshold: float = 0.8
    summarization_model: str = "qwen3.5:0.8b"

    # ReAct loop
    max_iterations: int = 30
    max_tool_retries: int = 3
    tool_timeout: float = 60.0

    # MCP
    mcp_enabled: bool = True
    mcp_servers_config: Path | None = None
    mcp_timeout: float = 30.0

    # Safety guardrails
    safety_enabled: bool = True
    safety_allowed_paths: list[Path] = Field(default_factory=list)
    safety_allow_outside_paths: bool = False
    safety_deny_sensitive_paths: bool = True
    safety_shell_mode: str = "deny_dangerous"
    safety_shell_allowlist: list[str] = Field(default_factory=list)
    safety_shell_denylist: list[str] = Field(default_factory=list)
    safety_network_enabled: bool = True
    safety_require_approval: bool = True
    safety_approval_tools: list[str] = Field(default_factory=list)
    safety_auto_approve: bool = False
    safety_redact_secrets: bool = True
    safety_audit_path: Path | None = None
    safety_audit_preview_chars: int = 2000

    # RAG / Memory
    chromadb_path: Path | None = None
    embedding_model: str = "all-MiniLM-L6-v2"
    rag_top_k: int = 5
    rag_similarity_threshold: float = 0.7

    @field_validator(
        "data_dir",
        "config_dir",
        "chromadb_path",
        "mcp_servers_config",
        "safety_audit_path",
        mode="before",
    )
    @classmethod
    def expand_path(cls, v: Any) -> Any:
        if isinstance(v, str):
            if not v.strip():
                return None
            return Path(v).expanduser().resolve()
        if isinstance(v, Path):
            return v.expanduser().resolve()
        return v

    @field_validator("safety_allowed_paths", mode="before")
    @classmethod
    def expand_path_list(cls, v: Any) -> Any:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
            except json.JSONDecodeError:
                parsed = [part.strip() for part in v.split(",") if part.strip()]
            v = parsed
        if isinstance(v, (list, tuple, set)):
            return [Path(item).expanduser().resolve() for item in v]
        return v

    @field_validator("safety_shell_mode")
    @classmethod
    def validate_shell_mode(cls, v: str) -> str:
        if v not in {"deny_dangerous", "allowlist", "unrestricted"}:
            raise ValueError(
                "safety_shell_mode must be deny_dangerous, allowlist, or unrestricted"
            )
        return v


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get the global settings instance."""
    return settings