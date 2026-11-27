"""MCP server configuration loading (Claude Desktop-compatible JSON/YAML)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from corvustui.mcp.protocol import MCPConfigError

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """One configured MCP server."""

    name: str
    transport: str
    command: tuple[str, ...] | None = None
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    inherit_env: bool = True
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise MCPConfigError("MCP server name must not be empty")
        if self.command is not None:
            object.__setattr__(
                self,
                "command",
                tuple(str(item) for item in self.command),
            )
        if self.transport not in {"stdio", "http"}:
            raise MCPConfigError(
                f"MCP server '{self.name}' has unsupported transport '{self.transport}'"
            )
        if self.transport == "stdio":
            if not self.command:
                raise MCPConfigError(
                    f"MCP server '{self.name}' uses stdio but has no command"
                )
            if self.url:
                raise MCPConfigError(
                    f"MCP server '{self.name}' cannot have both command and url"
                )
        if self.transport == "http":
            if not self.url:
                raise MCPConfigError(
                    f"MCP server '{self.name}' uses http but has no url"
                )
            if not self.url.startswith(("http://", "https://")):
                raise MCPConfigError(
                    f"MCP server '{self.name}' url must start with http:// or https://"
                )
            if self.command:
                raise MCPConfigError(
                    f"MCP server '{self.name}' cannot have both command and url"
                )
        if self.timeout <= 0:
            raise MCPConfigError(
                f"MCP server '{self.name}' timeout must be greater than zero"
            )
        if self.cwd is not None:
            object.__setattr__(self, "cwd", Path(self.cwd).expanduser().resolve())

    @classmethod
    def from_dict(
        cls,
        name: str,
        raw: dict[str, Any],
        *,
        base_dir: Path | None = None,
        default_timeout: float = 30.0,
    ) -> MCPServerConfig:
        if not isinstance(raw, dict):
            raise MCPConfigError(f"MCP server '{name}' must be a mapping")

        command_value = raw.get("command")
        args_value = raw.get("args") or []
        if isinstance(args_value, str):
            args_value = [args_value]
        if not isinstance(args_value, list) or not all(
            isinstance(item, (str, int, float)) for item in args_value
        ):
            raise MCPConfigError(f"MCP server '{name}' args must be a list of strings")

        command: tuple[str, ...] | None = None
        if command_value is not None:
            if isinstance(command_value, str):
                if not command_value.strip():
                    raise MCPConfigError(f"MCP server '{name}' command must not be empty")
                command = (
                    _expand(command_value),
                    *(_expand(str(item)) for item in args_value),
                )
            elif isinstance(command_value, list) and command_value:
                command = (
                    *(_expand(str(item)) for item in command_value),
                    *(_expand(str(item)) for item in args_value),
                )
            else:
                raise MCPConfigError(
                    f"MCP server '{name}' command must be a string or non-empty list"
                )

        url = raw.get("url") or raw.get("serverUrl")
        if url is not None and not isinstance(url, str):
            raise MCPConfigError(f"MCP server '{name}' url must be a string")
        if isinstance(url, str):
            url = _expand(url)

        transport = raw.get("transport") or raw.get("type")
        if transport is None:
            transport = "http" if url else "stdio"
        if not isinstance(transport, str):
            raise MCPConfigError(f"MCP server '{name}' transport must be a string")

        env = _string_mapping(raw.get("env") or {}, f"MCP server '{name}' env")
        headers = _string_mapping(
            raw.get("headers") or {},
            f"MCP server '{name}' headers",
        )

        cwd_value = raw.get("cwd")
        cwd = None
        if cwd_value:
            if not isinstance(cwd_value, str):
                raise MCPConfigError(f"MCP server '{name}' cwd must be a string")
            cwd = Path(_expand(cwd_value)).expanduser()
            if not cwd.is_absolute() and base_dir is not None:
                cwd = base_dir / cwd

        timeout_value = raw.get("timeout", default_timeout)
        try:
            timeout = float(timeout_value)
        except (TypeError, ValueError) as exc:
            raise MCPConfigError(
                f"MCP server '{name}' timeout must be a number"
            ) from exc

        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise MCPConfigError(f"MCP server '{name}' enabled must be a boolean")

        inherit_env = raw.get("inherit_env", True)
        if not isinstance(inherit_env, bool):
            raise MCPConfigError(
                f"MCP server '{name}' inherit_env must be a boolean"
            )

        return cls(
            name=name,
            transport=transport,
            command=command,
            cwd=cwd,
            env=env,
            inherit_env=inherit_env,
            url=url,
            headers=headers,
            timeout=timeout,
            enabled=enabled,
        )

    @property
    def command_line(self) -> str:
        return " ".join(self.command or ())


def load_mcp_config(
    path: str | Path | None,
    *,
    default_timeout: float = 30.0,
) -> list[MCPServerConfig]:
    """Load server definitions from a JSON or YAML file."""

    if path is None:
        return []

    config_path = Path(path).expanduser()
    if not config_path.exists():
        raise MCPConfigError(f"MCP config not found: {config_path}")
    if not config_path.is_file():
        raise MCPConfigError(f"MCP config is not a file: {config_path}")

    text = config_path.read_text(encoding="utf-8")
    data: Any
    if config_path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MCPConfigError(
                f"MCP config is not valid JSON ({config_path}): {exc}"
            ) from exc
    else:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise MCPConfigError(
                f"MCP config is not valid YAML ({config_path}): {exc}"
            ) from exc

    if not isinstance(data, dict):
        raise MCPConfigError("MCP config must be a mapping")

    servers = data.get("mcpServers")
    if servers is None:
        servers = data.get("mcp_servers")
    if servers is None:
        servers = data.get("servers")
    if servers is None:
        raise MCPConfigError(
            "MCP config must contain 'mcpServers' (or 'servers')"
        )

    configs: list[MCPServerConfig] = []
    seen: set[str] = set()
    if isinstance(servers, dict):
        for name, raw in servers.items():
            if not isinstance(name, str):
                raise MCPConfigError("MCP server names must be strings")
            if name in seen:
                raise MCPConfigError(f"Duplicate MCP server name: {name}")
            seen.add(name)
            configs.append(
                MCPServerConfig.from_dict(
                    name,
                    raw,
                    base_dir=config_path.parent,
                    default_timeout=default_timeout,
                )
            )
    elif isinstance(servers, list):
        for index, raw in enumerate(servers):
            if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
                raise MCPConfigError(
                    f"MCP server entry {index} must be a mapping with a string name"
                )
            entry = dict(raw)
            name = entry.pop("name")
            if name in seen:
                raise MCPConfigError(f"Duplicate MCP server name: {name}")
            seen.add(name)
            configs.append(
                MCPServerConfig.from_dict(
                    name,
                    entry,
                    base_dir=config_path.parent,
                    default_timeout=default_timeout,
                )
            )
    else:
        raise MCPConfigError("MCP 'mcpServers' must be a mapping or a list")

    return configs


def _string_mapping(raw: Any, label: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise MCPConfigError(f"{label} must be a mapping")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise MCPConfigError(f"{label} keys must be strings")
        if not isinstance(value, (str, int, float, bool)):
            raise MCPConfigError(f"{label}.{key} must be a scalar")
        result[key] = _expand(str(value))
    return result


def _expand(value: str) -> str:
    """Expand ``${VAR}`` references and fail on an unset variable."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise MCPConfigError(
                f"MCP config references unset environment variable ${{{name}}}"
            )
        return os.environ[name]

    return _ENV_PATTERN.sub(replace, value)
