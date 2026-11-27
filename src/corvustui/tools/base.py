"""Base tool classes and result types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from corvustui.providers.base import ToolDefinition


@dataclass(slots=True)
class ToolResult:
    """Result of a tool execution."""

    success: bool
    output: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, output: str, **metadata) -> ToolResult:
        return cls(success=True, output=output, metadata=metadata)

    @classmethod
    def err(cls, error: str, **metadata) -> ToolResult:
        return cls(success=False, error=error, metadata=metadata)


class Tool(ABC):
    """Base class for all tools."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

    # Safety metadata.  The guard asks for approval before tools that can
    # mutate files, execute commands, or call external servers.
    requires_approval: bool = False
    requires_network: bool = False

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given arguments."""
        pass

    def to_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )


class FunctionTool(Tool):
    """Tool created from a callable function."""

    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[..., Any],
        parameters: dict[str, Any] | None = None,
    ):
        self.name = name
        self.description = description
        self._func = func
        self.parameters = parameters or self._infer_schema(func)

    def _infer_schema(self, func: Callable[..., Any]) -> dict[str, Any]:
        """Infer JSON schema from function signature."""
        import inspect
        sig = inspect.signature(func)
        properties = {}
        required = []

        for name, param in sig.parameters.items():
            if name in ("self", "cls"):
                continue
            param_type = param.annotation
            if param_type == inspect.Parameter.empty:
                param_type = str

            # Simple type mapping
            type_map = {
                str: "string",
                int: "integer",
                float: "number",
                bool: "boolean",
                list: "array",
                dict: "object",
            }
            prop_type = type_map.get(param_type, "string")

            properties[name] = {"type": prop_type}
            if param.default == inspect.Parameter.empty:
                required.append(name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    async def execute(self, **kwargs) -> ToolResult:
        try:
            import inspect
            if inspect.iscoroutinefunction(self._func):
                result = await self._func(**kwargs)
            else:
                result = self._func(**kwargs)
            return ToolResult.ok(str(result) if result is not None else "")
        except Exception as e:
            return ToolResult.err(str(e))