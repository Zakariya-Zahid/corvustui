"""Provider connection management, multi-provider model discovery, and UI prefs."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corvustui.config.settings import ProviderType, get_settings
from corvustui.providers import ProviderFactory


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Metadata for configuring a provider in /connect."""

    provider_type: ProviderType
    display_name: str
    env_key: str | None
    base_url_env: str
    default_base_url: str
    requires_api_key: bool
    placeholder: str


PROVIDER_SPECS: list[ProviderSpec] = [
    ProviderSpec(
        provider_type=ProviderType.OLLAMA,
        display_name="Ollama (Local)",
        env_key=None,
        base_url_env="CORVUS_OLLAMA_BASE_URL",
        default_base_url="http://localhost:11434",
        requires_api_key=False,
        placeholder="http://localhost:11434",
    ),
    ProviderSpec(
        provider_type=ProviderType.OPENAI,
        display_name="OpenAI",
        env_key="CORVUS_OPENAI_API_KEY",
        base_url_env="CORVUS_OPENAI_BASE_URL",
        default_base_url="https://api.openai.com/v1",
        requires_api_key=True,
        placeholder="sk-...",
    ),
    ProviderSpec(
        provider_type=ProviderType.ANTHROPIC,
        display_name="Anthropic",
        env_key="CORVUS_ANTHROPIC_API_KEY",
        base_url_env="CORVUS_ANTHROPIC_BASE_URL",
        default_base_url="https://api.anthropic.com",
        requires_api_key=True,
        placeholder="sk-ant-...",
    ),
    ProviderSpec(
        provider_type=ProviderType.OPENROUTER,
        display_name="OpenRouter",
        env_key="CORVUS_OPENROUTER_API_KEY",
        base_url_env="CORVUS_OPENROUTER_BASE_URL",
        default_base_url="https://openrouter.ai/api/v1",
        requires_api_key=True,
        placeholder="sk-or-...",
    ),
    ProviderSpec(
        provider_type=ProviderType.GROQ,
        display_name="Groq",
        env_key="CORVUS_GROQ_API_KEY",
        base_url_env="CORVUS_GROQ_BASE_URL",
        default_base_url="https://api.groq.com/openai/v1",
        requires_api_key=True,
        placeholder="gsk_...",
    ),
    ProviderSpec(
        provider_type=ProviderType.GEMINI,
        display_name="Google Gemini",
        env_key="CORVUS_GEMINI_API_KEY",
        base_url_env="CORVUS_GEMINI_BASE_URL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        requires_api_key=True,
        placeholder="AIza...",
    ),
    ProviderSpec(
        provider_type=ProviderType.GROK,
        display_name="xAI Grok",
        env_key="CORVUS_GROK_API_KEY",
        base_url_env="CORVUS_GROK_BASE_URL",
        default_base_url="https://api.x.ai/v1",
        requires_api_key=True,
        placeholder="xai-...",
    ),
]

SPEC_BY_TYPE: dict[ProviderType, ProviderSpec] = {
    spec.provider_type: spec for spec in PROVIDER_SPECS
}

_API_KEY_ATTR: dict[ProviderType, str] = {
    ProviderType.OPENAI: "openai_api_key",
    ProviderType.ANTHROPIC: "anthropic_api_key",
    ProviderType.OPENROUTER: "openrouter_api_key",
    ProviderType.GROQ: "groq_api_key",
    ProviderType.GEMINI: "gemini_api_key",
    ProviderType.GROK: "grok_api_key",
}

_BASE_URL_ATTR: dict[ProviderType, str] = {
    ProviderType.OLLAMA: "ollama_base_url",
    ProviderType.OPENAI: "openai_base_url",
    ProviderType.ANTHROPIC: "anthropic_base_url",
    ProviderType.OPENROUTER: "openrouter_base_url",
    ProviderType.GROQ: "groq_base_url",
    ProviderType.GEMINI: "gemini_base_url",
    ProviderType.GROK: "grok_base_url",
}


def get_provider_api_key(provider_type: ProviderType) -> str | None:
    attr = _API_KEY_ATTR.get(provider_type)
    if not attr:
        return None
    return getattr(get_settings(), attr, None)


def get_provider_base_url(provider_type: ProviderType) -> str:
    attr = _BASE_URL_ATTR[provider_type]
    return getattr(get_settings(), attr)


def is_provider_configured(provider_type: ProviderType) -> bool:
    if provider_type == ProviderType.OLLAMA:
        return True
    key = get_provider_api_key(provider_type)
    return bool(key and key.strip())


def mask_api_key(key: str | None) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "••••"
    return f"{key[:4]}••••{key[-4:]}"


def _update_env_file(env_path: Path, updates: dict[str, str | None]) -> None:
    """Safely update or remove keys in a .env file without clobbering unrelated entries."""
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        k = stripped.split("=", 1)[0].strip()
        if k in remaining:
            val = remaining.pop(k)
            if val is not None:
                new_lines.append(f"{k}={val}")
        else:
            new_lines.append(line)

    for k, val in remaining.items():
        if val is not None:
            new_lines.append(f"{k}={val}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass


def save_provider_credentials(
    provider_type: ProviderType,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    env_path: Path | None = None,
) -> None:
    """Update runtime Settings, os.environ, and persist to .env."""
    settings = get_settings()
    spec = SPEC_BY_TYPE[provider_type]
    env_updates: dict[str, str | None] = {}

    if spec.env_key is not None and provider_type in _API_KEY_ATTR:
        clean_key = api_key.strip() if api_key else None
        setattr(settings, _API_KEY_ATTR[provider_type], clean_key or None)
        if clean_key:
            os.environ[spec.env_key] = clean_key
            env_updates[spec.env_key] = clean_key
        else:
            os.environ.pop(spec.env_key, None)
            env_updates[spec.env_key] = None

    if base_url is not None:
        clean_url = base_url.strip() or spec.default_base_url
        setattr(settings, _BASE_URL_ATTR[provider_type], clean_url)
        os.environ[spec.base_url_env] = clean_url
        env_updates[spec.base_url_env] = clean_url

    target_env = env_path or (Path.cwd() / ".env")
    if env_updates:
        _update_env_file(target_env, env_updates)


async def test_and_list_models(
    provider_type: ProviderType,
    *,
    timeout: float = 8.0,
) -> tuple[list[str], str | None]:
    """Fetch live models for a provider. Returns (models, error_message)."""
    try:
        provider = ProviderFactory.create(provider_type)
        async with provider:
            models = await asyncio.wait_for(provider.list_models(), timeout=timeout)
            return list(models), None
    except Exception as exc:
        return [], str(exc)


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """Single selectable model in the unified Select model picker."""

    provider_type: ProviderType
    provider_label: str
    model: str
    badge: str


async def discover_all_models(
    active_provider: ProviderType,
    active_models: list[str] | None = None,
) -> tuple[list[ModelEntry], dict[ProviderType, str]]:
    """Query all configured providers concurrently for live available models."""
    configured = [
        spec.provider_type
        for spec in PROVIDER_SPECS
        if is_provider_configured(spec.provider_type)
    ]
    if active_provider not in configured:
        configured.insert(0, active_provider)

    async def _fetch(ptype: ProviderType) -> tuple[ProviderType, list[str], str | None]:
        if ptype == active_provider and active_models is not None:
            return ptype, list(active_models), None
        models, err = await test_and_list_models(ptype, timeout=6.0)
        return ptype, models, err

    results = await asyncio.gather(*[_fetch(p) for p in configured])

    entries: list[ModelEntry] = []
    errors: dict[ProviderType, str] = {}
    for ptype, models, err in results:
        spec = SPEC_BY_TYPE[ptype]
        badge = "Local" if ptype == ProviderType.OLLAMA else "Cloud"
        if err:
            errors[ptype] = err
            continue
        for m in models:
            entries.append(
                ModelEntry(
                    provider_type=ptype,
                    provider_label=spec.display_name.replace(" (Local)", ""),
                    model=m,
                    badge="Free" if ":free" in m.lower() else badge,
                )
            )
    return entries, errors


class TUIPrefs:
    """Persists favorite models and recently used models in ~/.corvustui/tui_prefs.json."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (Path.home() / ".corvustui" / "tui_prefs.json")
        self.favorites: list[str] = []
        self.recent: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
            self.favorites = [str(x) for x in data.get("favorites", [])]
            self.recent = [str(x) for x in data.get("recent", [])]
        except Exception:
            pass

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"favorites": self.favorites, "recent": self.recent}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    @staticmethod
    def key_for(provider_type: ProviderType, model: str) -> str:
        return f"{provider_type.value}/{model}"

    def is_favorite(self, provider_type: ProviderType, model: str) -> bool:
        return self.key_for(provider_type, model) in self.favorites

    def toggle_favorite(self, provider_type: ProviderType, model: str) -> bool:
        k = self.key_for(provider_type, model)
        if k in self.favorites:
            self.favorites.remove(k)
            self._save()
            return False
        self.favorites.insert(0, k)
        self._save()
        return True

    def record_recent(self, provider_type: ProviderType, model: str) -> None:
        k = self.key_for(provider_type, model)
        self.recent = [x for x in self.recent if x != k]
        self.recent.insert(0, k)
        self.recent = self.recent[:8]
        self._save()
