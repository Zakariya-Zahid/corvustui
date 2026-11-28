"""Built-in tools for file system, shell, and web operations."""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import Any

import httpx

from corvustui.config.settings import get_settings
from corvustui.todos import TodoStore
from corvustui.tools.base import Tool, ToolResult
from corvustui.tools.registry import ToolRegistry

# Keep tool observations bounded so one tool call cannot blow up the context.
MAX_TOOL_OUTPUT = 12_000


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n\n... [truncated, {len(text) - limit} more characters]", True


def _as_int(value: Any, default: int) -> int:
    """Coerce model-supplied numbers, which are often strings."""
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


class ReadFileTool(Tool):
    """Read a file from the filesystem."""

    name = "read_file"
    description = "Read the contents of a file"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read"},
            "file_path": {"type": "string", "description": "Path to the file to read (alias for path)"},
        },
        "required": ["path"],
    }

    async def execute(self, path: str | None = None, file_path: str | None = None) -> ToolResult:
        actual_path = path or file_path
        if not actual_path:
            return ToolResult.err("Missing required parameter: path (or file_path)")
        try:
            resolved = Path(actual_path).expanduser().resolve()
            if not resolved.exists():
                return ToolResult.err(f"File not found: {actual_path}")
            if not resolved.is_file():
                return ToolResult.err(f"Not a file: {actual_path}")

            total_size = resolved.stat().st_size
            with resolved.open("r", encoding="utf-8", errors="replace") as handle:
                content = handle.read(MAX_TOOL_OUTPUT + 1)

            truncated = len(content) > MAX_TOOL_OUTPUT
            if truncated:
                content = content[:MAX_TOOL_OUTPUT] + (
                    f"\n\n... [truncated, file is {total_size} bytes; "
                    f"showing first {MAX_TOOL_OUTPUT} characters]"
                )

            return ToolResult.ok(
                content,
                path=str(resolved),
                size=total_size,
                returned_chars=len(content),
                truncated=truncated,
            )
        except Exception as e:
            return ToolResult.err(str(e))


class WriteFileTool(Tool):
    """Write content to a file."""

    name = "write_file"
    description = "Write content to a file (creates directories if needed)"
    requires_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "Content to write"},
            "value": {"type": "string", "description": "Content to write (alias for content)"},
            "output_file": {"type": "string", "description": "Path to the file to write (alias for path)"},
            "file_path": {"type": "string", "description": "Path to the file to write (alias for path)"},
        },
        "required": ["path", "content"],
    }

    async def execute(
        self,
        path: str | None = None,
        content: str | None = None,
        value: str | None = None,
        output_file: str | None = None,
        file_path: str | None = None,
    ) -> ToolResult:
        # Accept aliases for compatibility with models that use different
        # parameter names.  An explicitly empty content string is valid and
        # creates an empty file; only "not supplied" counts as missing.
        actual_path = path or output_file or file_path
        actual_content = content if content is not None else value
        if not actual_path:
            return ToolResult.err("Missing required parameter: path (or output_file/file_path)")
        if actual_content is None:
            return ToolResult.err("Missing required parameter: content (or value)")
        try:
            resolved = Path(actual_path).expanduser().resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(actual_content, encoding="utf-8")
            size = len(actual_content.encode("utf-8"))
            return ToolResult.ok(f"Written {size} bytes to {resolved}", path=str(resolved))
        except Exception as e:
            return ToolResult.err(str(e))


class EditFileTool(Tool):
    """Edit a file by replacing specific text (shows diff)."""

    name = "edit_file"
    description = "Edit a file by replacing old_string with new_string. Returns a unified diff showing changes."
    requires_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to edit"},
            "file_path": {"type": "string", "description": "Path to the file to edit (alias for path)"},
            "old_string": {"type": "string", "description": "Exact text to replace (must be unique)"},
            "new_string": {"type": "string", "description": "New text to insert"},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def _generate_diff(self, old_content: str, new_content: str, path: str) -> str:
        """Generate a unified diff between old and new content."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="")
        return "\n".join(diff)

    async def execute(
        self,
        path: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        replace_all: bool | str = False,
        file_path: str | None = None,
    ) -> ToolResult:
        actual_path = path or file_path
        if not actual_path:
            return ToolResult.err("Missing required parameter: path (or file_path)")
        if old_string is None or new_string is None:
            return ToolResult.err("Missing required parameter: old_string and new_string")
        if old_string == "":
            return ToolResult.err(
                "old_string must not be empty; provide the exact text to replace."
            )
        replace_all = _as_bool(replace_all)
        try:
            resolved = Path(actual_path).expanduser().resolve()
            if not resolved.exists():
                return ToolResult.err(f"File not found: {actual_path}")
            if not resolved.is_file():
                return ToolResult.err(f"Not a file: {actual_path}")

            old_content = resolved.read_text(encoding="utf-8")
            
            # Count occurrences
            count = old_content.count(old_string)
            if count == 0:
                return ToolResult.err("old_string not found in file. Please verify the exact text including whitespace.")
            if count > 1 and not replace_all:
                return ToolResult.err(f"old_string found {count} times. Use replace_all=true or provide more context to make it unique.")

            # Perform replacement
            if replace_all:
                new_content = old_content.replace(old_string, new_string)
            else:
                new_content = old_content.replace(old_string, new_string, 1)

            # Write the file
            resolved.write_text(new_content, encoding="utf-8")

            # Generate diff
            diff = self._generate_diff(old_content, new_content, str(resolved))

            return ToolResult.ok(
                f"Edited {resolved} ({count} occurrence{'s' if count > 1 else ''} replaced)\n\n```diff\n{diff}\n```",
                path=str(resolved),
                diff=diff,
                replacements=count,
            )
        except Exception as e:
            return ToolResult.err(str(e))


class ListFilesTool(Tool):
    """List files in a directory."""

    name = "list_files"
    description = "List files and directories in a given path"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to list", "default": "."},
            "directory": {"type": "string", "description": "Directory path to list (alias for path)"},
            "recursive": {"type": "boolean", "description": "List recursively", "default": False},
        },
        "required": [],
    }

    async def execute(
        self,
        path: str | None = None,
        recursive: bool | str = False,
        directory: str | None = None,
    ) -> ToolResult:
        actual_path = path or directory or "."
        recursive = _as_bool(recursive)
        try:
            dir_path = Path(actual_path).expanduser().resolve()
            if not dir_path.exists():
                return ToolResult.err(f"Directory not found: {actual_path}")
            if not dir_path.is_dir():
                return ToolResult.err(f"Not a directory: {actual_path}")

            if recursive:
                files = [str(p.relative_to(dir_path)) for p in dir_path.rglob("*")]
            else:
                files = [p.name for p in dir_path.iterdir()]

            files = sorted(files)
            total = len(files)
            limited = files[:600]
            text = "\n".join(limited)
            if total > len(limited):
                text += f"\n\n... [truncated, {total - len(limited)} more entries]"
            return ToolResult.ok(text, count=total, path=str(dir_path), truncated=total > len(limited))
        except Exception as e:
            return ToolResult.err(str(e))


class ShellTool(Tool):
    """Execute shell commands."""

    name = "shell"
    description = "Execute a shell command and return output"
    requires_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command to execute"},
            "sh": {"type": "string", "description": "Command to execute (alias for command)"},
            "cwd": {"type": "string", "description": "Working directory", "default": "."},
            "timeout": {"type": "number", "description": "Timeout in seconds", "default": 30},
            "output_file": {"type": "string", "description": "Ignored - for compatibility with models that confuse write_file params"},
        },
        "required": ["command"],
    }

    async def execute(
        self,
        command: str = "",
        sh: str = "",
        cwd: str | None = ".",
        timeout: float | str = 30,
        output_file: str = "",
    ) -> ToolResult:
        # Accept both 'command' and 'sh' parameter names for compatibility
        # Ignore output_file (some models confuse shell with write_file)
        actual_command = command or sh
        if not actual_command:
            return ToolResult.err("Missing required parameter: command (or sh)")
        timeout = _as_float(timeout, 30)
        if timeout <= 0:
            timeout = 30
        try:
            work_dir = Path(cwd or ".").expanduser().resolve()
            process = await asyncio.create_subprocess_shell(
                actual_command,
                cwd=work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult.err(f"Command timed out after {timeout}s")

            output = stdout.decode("utf-8", errors="replace").strip()
            error = stderr.decode("utf-8", errors="replace").strip()
            output, output_truncated = _truncate(output)
            error, error_truncated = _truncate(error)

            if process.returncode == 0:
                text = output
                if error:
                    text += f"\n[stderr]\n{error}"
                return ToolResult.ok(
                    text or "Command completed successfully (no output)",
                    returncode=0,
                    truncated=output_truncated or error_truncated,
                )
            return ToolResult.err(
                f"Command failed (exit {process.returncode}):\n{error}\n{output}",
                returncode=process.returncode,
                stdout=output,
                stderr=error,
                truncated=output_truncated or error_truncated,
            )
        except Exception as e:
            return ToolResult.err(str(e))


class WebSearchTool(Tool):
    """Search the web using Tavily API (requires API key)."""

    name = "web_search"
    description = "Search the web for current information (requires Tavily API key)"
    requires_network = True
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "search": {"type": "string", "description": "Search query (alias for query)"},
            "max_results": {"type": "integer", "description": "Maximum results", "default": 5},
        },
        "required": ["query"],
    }

    def __init__(self):
        self.api_key = get_settings().tavily_api_key

    async def execute(self, query: str = "", search: str = "", max_results: int | str = 5) -> ToolResult:
        actual_query = query or search
        if not actual_query:
            return ToolResult.err("Missing required parameter: query (or search)")
        max_results = max(1, min(_as_int(max_results, 5), 20))
        if not self.api_key:
            return ToolResult.err(
                "Tavily API key not configured. Set CORVUS_TAVILY_API_KEY in .env, "
                "or use 'duckduckgo_search' for free search."
            )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": self.api_key,
                        "query": actual_query,
                        "max_results": max_results,
                        "search_depth": "basic",
                    },
                )
                if response.status_code == 401:
                    return ToolResult.err("Tavily rejected the API key (401). Check CORVUS_TAVILY_API_KEY.")
                response.raise_for_status()
                data = response.json()

            results = data.get("results", [])
            formatted = []
            for r in results[:max_results]:
                content = r.get("content") or ""
                content, _ = _truncate(content, 500)
                formatted.append(
                    f"Title: {r.get('title') or 'N/A'}\n"
                    f"URL: {r.get('url') or 'N/A'}\n"
                    f"Content: {content}"
                )

            return ToolResult.ok(
                "\n\n---\n\n".join(formatted) if formatted else "No results found",
                query=actual_query,
                result_count=len(formatted),
            )
        except httpx.HTTPStatusError as e:
            return ToolResult.err(
                f"Tavily search failed (HTTP {e.response.status_code}): {e.response.text[:300]}"
            )
        except Exception as e:
            return ToolResult.err(f"Search failed: {e}")


class DuckDuckGoSearchTool(Tool):
    """Search the web using DuckDuckGo HTML scraping (free, no API key)."""

    name = "duckduckgo_search"
    description = "Search the web using DuckDuckGo (free, no API key required). Note: Rate limited, may trigger CAPTCHAs with heavy use."
    requires_network = True
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "search": {"type": "string", "description": "Search query (alias for query)"},
            "max_results": {"type": "integer", "description": "Maximum results", "default": 5},
        },
        "required": ["query"],
    }

    async def execute(self, query: str = "", search: str = "", max_results: int | str = 5) -> ToolResult:
        # Accept both 'query' and 'search' parameter names for compatibility
        actual_query = query or search
        if not actual_query:
            return ToolResult.err("Missing required parameter: query (or search)")
        max_results = max(1, min(_as_int(max_results, 5), 20))

        try:
            # Try new package name first (ddgs), then old (duckduckgo_search)
            try:
                from ddgs import DDGS as DDGSClient
            except ImportError:
                from duckduckgo_search import DDGS as DDGSClient
        except ImportError:
            return ToolResult.err("ddgs not installed. Run: pip install ddgs")

        try:
            ddgs = DDGSClient()

            # Try backends in order. Keep the last real error so a total
            # failure is reported as an error instead of "No results found".
            results: list[dict[str, Any]] = []
            errors: list[str] = []
            for backend in ["duckduckgo", "bing", "brave"]:
                try:
                    results = list(ddgs.text(
                        actual_query,
                        max_results=max_results,
                        backend=backend,
                        region="us-en",
                        safesearch="moderate",
                        timeout=15,
                    ))
                    if results:
                        break
                except Exception as exc:
                    errors.append(f"{backend}: {exc}")
                    continue

            if not results:
                if errors and len(errors) == 3:
                    return ToolResult.err(
                        "DuckDuckGo search failed on all backends: " + "; ".join(errors)
                    )
                return ToolResult.ok("No results found", query=actual_query, result_count=0)

            formatted = []
            for r in results[:max_results]:
                title = r.get("title") or "N/A"
                url = r.get("href") or r.get("url") or "N/A"
                body = r.get("body") or r.get("content") or r.get("snippet") or ""
                body, _ = _truncate(body, 500)
                formatted.append(
                    f"Title: {title}\n"
                    f"URL: {url}\n"
                    f"Content: {body}"
                )

            return ToolResult.ok(
                "\n\n---\n\n".join(formatted),
                query=actual_query,
                result_count=len(formatted),
            )
        except Exception as e:
            return ToolResult.err(f"DuckDuckGo search failed: {e}")


class TodoWriteTool(Tool):
    """Maintain the agent's visible task list for multi-step work."""

    name = "todo_write"
    description = (
        "Create or update the task list for a multi-step task. Pass the COMPLETE "
        "list every time. Keep exactly one task in_progress and mark tasks "
        "completed immediately after finishing them."
    )
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The complete task list, in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Imperative task description.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "activeForm": {
                            "type": "string",
                            "description": "Present-continuous label while in progress.",
                        },
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    }

    def __init__(self, store: TodoStore | None = None) -> None:
        self.store = store or TodoStore()

    async def execute(self, todos: list[dict[str, Any]] | None = None, **kwargs) -> ToolResult:
        raw = todos
        if raw is None:
            raw = kwargs.get("items")
        if raw is None:
            raw = kwargs.get("tasks")
        if raw is None:
            return ToolResult.err(
                "todo_write expects a 'todos' array of {content, status} objects."
            )

        updated = self.store.replace_from_arguments({"todos": raw})
        if updated is None:
            return ToolResult.err(
                "Invalid todo list. Each item needs a non-empty 'content' and a "
                "'status' of pending, in_progress, or completed."
            )
        return ToolResult.ok(self.store.render_plain(), count=len(updated))


def register_builtin_tools(
    registry: ToolRegistry,
    *,
    todo_store: TodoStore | None = None,
) -> None:
    """Register all built-in tools."""
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(ListFilesTool())
    registry.register(ShellTool())
    registry.register(WebSearchTool())
    registry.register(DuckDuckGoSearchTool())
    registry.register(TodoWriteTool(todo_store))