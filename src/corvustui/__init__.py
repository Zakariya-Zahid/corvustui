"""CorvusTUI - Agentic Terminal Interface and Modular MCP Execution Engine."""

__version__ = "0.1.0"

from corvustui.config.settings import Settings, get_settings
from corvustui.context.manager import ContextManager
from corvustui.core.agent import AgentConfig, CorvusAgent
from corvustui.mcp import (
    MCPClient,
    MCPManager,
    MCPServerConfig,
    MCPTool,
    load_mcp_config,
)
from corvustui.memory.vector import ChromaMemory, InMemoryVectorMemory, VectorMemory
from corvustui.providers import ProviderFactory, ProviderType
from corvustui.safety import SafetyGuard, SafetyPolicy
from corvustui.tools import ToolRegistry, register_builtin_tools

__all__ = [
    "CorvusAgent",
    "AgentConfig",
    "ProviderFactory",
    "ProviderType",
    "MCPClient",
    "MCPManager",
    "MCPServerConfig",
    "MCPTool",
    "load_mcp_config",
    "SafetyGuard",
    "SafetyPolicy",
    "ToolRegistry",
    "register_builtin_tools",
    "ContextManager",
    "VectorMemory",
    "InMemoryVectorMemory",
    "ChromaMemory",
    "Settings",
    "get_settings",
]