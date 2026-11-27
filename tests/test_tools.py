"""Tests for built-in tools, aliases, and search fallbacks."""

from __future__ import annotations

import ddgs
import httpx
import pytest

from corvustui.tools import builtin
from corvustui.tools.builtin import (
    DuckDuckGoSearchTool,
    EditFileTool,
    ListFilesTool,
    ReadFileTool,
    ShellTool,
    WebSearchTool,
    WriteFileTool,
)

# --- Write / read / edit / list -------------------------------------------


@pytest.mark.asyncio
async def test_write_file_accepts_aliases(tmp_path):
    target = tmp_path / "nested" / "note.txt"
    result = await WriteFileTool().execute(
        output_file=str(target),
        value="alias content",
    )

    assert result.success
    assert target.read_text() == "alias content"


@pytest.mark.asyncio
async def test_write_file_primary_parameters(tmp_path):
    target = tmp_path / "note.txt"
    result = await WriteFileTool().execute(path=str(target), content="primary")

    assert result.success
    assert target.read_text() == "primary"


@pytest.mark.asyncio
async def test_write_file_allows_explicit_empty_content(tmp_path):
    target = tmp_path / "empty.txt"
    result = await WriteFileTool().execute(path=str(target), content="")

    assert result.success
    assert target.exists()
    assert target.read_text() == ""


@pytest.mark.asyncio
async def test_write_file_missing_parameters(tmp_path):
    tool = WriteFileTool()

    assert not (await tool.execute()).success
    assert not (await tool.execute(path=str(tmp_path / "a.txt"))).success


@pytest.mark.asyncio
async def test_write_file_accepts_file_path_alias(tmp_path):
    target = tmp_path / "alias.txt"
    result = await WriteFileTool().execute(file_path=str(target), content="x")

    assert result.success
    assert target.read_text() == "x"


@pytest.mark.asyncio
async def test_read_file_accepts_file_path_alias(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello")

    result = await ReadFileTool().execute(file_path=str(target))

    assert result.success
    assert result.output == "hello"


@pytest.mark.asyncio
async def test_read_file_live_content(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello")

    result = await ReadFileTool().execute(path=str(target))

    assert result.success
    assert result.output == "hello"
    assert result.metadata["truncated"] is False
    assert result.metadata["size"] == 5


@pytest.mark.asyncio
async def test_read_file_truncates_large_files(tmp_path):
    target = tmp_path / "big.txt"
    target.write_text("x" * 20_000)

    result = await ReadFileTool().execute(path=str(target))

    assert result.success
    assert result.metadata["truncated"] is True
    assert result.metadata["size"] == 20_000
    assert len(result.output) < 12_500
    assert "truncated" in result.output


@pytest.mark.asyncio
async def test_read_file_missing(tmp_path):
    result = await ReadFileTool().execute(path=str(tmp_path / "missing.txt"))
    assert not result.success
    assert "File not found" in result.error


@pytest.mark.asyncio
async def test_edit_file_replaces_unique_text(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("alpha beta gamma")

    result = await EditFileTool().execute(
        path=str(target),
        old_string="beta",
        new_string="delta",
    )

    assert result.success
    assert target.read_text() == "alpha delta gamma"
    assert "diff" in result.metadata


@pytest.mark.asyncio
async def test_edit_file_refuses_ambiguous_replacement(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("x x")

    result = await EditFileTool().execute(
        path=str(target),
        old_string="x",
        new_string="y",
    )

    assert not result.success
    assert "found 2 times" in result.error
    assert target.read_text() == "x x"


@pytest.mark.asyncio
async def test_edit_file_replace_all(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("x x")

    result = await EditFileTool().execute(
        path=str(target),
        old_string="x",
        new_string="y",
        replace_all=True,
    )

    assert result.success
    assert target.read_text() == "y y"


@pytest.mark.asyncio
async def test_edit_file_accepts_aliases_and_string_boolean(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("x x")

    result = await EditFileTool().execute(
        file_path=str(target),
        old_string="x",
        new_string="y",
        replace_all="true",
    )

    assert result.success
    assert target.read_text() == "y y"


@pytest.mark.asyncio
async def test_edit_file_rejects_empty_old_string(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello")

    result = await EditFileTool().execute(
        path=str(target),
        old_string="",
        new_string="x",
    )

    assert not result.success
    assert "must not be empty" in result.error
    assert target.read_text() == "hello"


@pytest.mark.asyncio
async def test_edit_file_missing_old_string(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello")

    result = await EditFileTool().execute(
        path=str(target),
        old_string="nope",
        new_string="x",
    )

    assert not result.success
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_list_files(tmp_path):
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "a.txt").write_text("a")

    result = await ListFilesTool().execute(path=str(tmp_path))

    assert result.success
    assert result.metadata["count"] == 2
    assert result.output.splitlines() == ["a.txt", "b.txt"]


@pytest.mark.asyncio
async def test_list_files_accepts_directory_alias(tmp_path):
    (tmp_path / "a.txt").write_text("a")

    result = await ListFilesTool().execute(directory=str(tmp_path), recursive="false")

    assert result.success
    assert result.output == "a.txt"


# --- Shell ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_shell_accepts_sh_alias():
    result = await ShellTool().execute(sh="printf alias")
    assert result.success
    assert result.output == "alias"


@pytest.mark.asyncio
async def test_shell_runs_primary_command():
    result = await ShellTool().execute(command="printf primary")
    assert result.success
    assert result.output == "primary"


@pytest.mark.asyncio
async def test_shell_ignores_output_file_alias():
    result = await ShellTool().execute(
        sh="printf ok",
        output_file="some-model-confusion.txt",
    )
    assert result.success
    assert result.output == "ok"


@pytest.mark.asyncio
async def test_shell_missing_command():
    result = await ShellTool().execute()
    assert not result.success
    assert "Missing required parameter" in result.error


@pytest.mark.asyncio
async def test_shell_nonzero_exit():
    result = await ShellTool().execute(command="exit 3")
    assert not result.success
    assert result.metadata["returncode"] == 3


@pytest.mark.asyncio
async def test_shell_timeout():
    result = await ShellTool().execute(command="sleep 2", timeout=0.05)
    assert not result.success
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_shell_accepts_string_timeout():
    result = await ShellTool().execute(command="printf ok", timeout="5")
    assert result.success
    assert result.output == "ok"


# --- Web search -----------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"results": []}
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.tavily.com/search")
            raise httpx.HTTPStatusError(
                "error",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self):
        return self._payload


class FakeAsyncClient:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, json):
        FakeAsyncClient.calls.append({"url": url, "json": json})
        return FakeResponse(
            200,
            {
                "results": [
                    {"title": "Title A", "url": "https://a.test", "content": "Content A"},
                    {"title": "Title B", "url": "https://b.test", "content": "Content B"},
                ]
            },
        )


@pytest.mark.asyncio
async def test_web_search_uses_search_alias(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(builtin.httpx, "AsyncClient", FakeAsyncClient)

    tool = WebSearchTool()
    tool.api_key = "test-key"
    result = await tool.execute(search="corvus", max_results=1)

    assert result.success
    assert "Title A" in result.output
    assert result.metadata["result_count"] == 1
    assert FakeAsyncClient.calls[0]["json"]["query"] == "corvus"


@pytest.mark.asyncio
async def test_web_search_coerces_string_max_results(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(builtin.httpx, "AsyncClient", FakeAsyncClient)

    tool = WebSearchTool()
    tool.api_key = "test-key"
    result = await tool.execute(query="corvus", max_results="1")

    assert result.success
    assert result.metadata["result_count"] == 1
    assert FakeAsyncClient.calls[0]["json"]["max_results"] == 1


@pytest.mark.asyncio
async def test_web_search_requires_key():
    tool = WebSearchTool()
    tool.api_key = None

    result = await tool.execute(query="corvus")

    assert not result.success
    assert "Tavily API key" in result.error
    assert "duckduckgo_search" in result.error


@pytest.mark.asyncio
async def test_web_search_missing_query():
    tool = WebSearchTool()
    tool.api_key = "key"

    result = await tool.execute()

    assert not result.success
    assert "Missing required parameter" in result.error


# --- DuckDuckGo fallback --------------------------------------------------


class FakeDDGS:
    def __init__(self, results, errors):
        self.results = results
        self.errors = errors
        self.backends: list[str] = []

    def text(self, query, **kwargs):
        backend = kwargs.get("backend")
        self.backends.append(backend)
        if backend in self.errors:
            raise RuntimeError(self.errors[backend])
        return self.results


def install_ddgs(monkeypatch, results, errors=None):
    fake = FakeDDGS(results, errors or {})
    monkeypatch.setattr(ddgs, "DDGS", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_duckduckgo_first_backend_success(monkeypatch):
    fake = install_ddgs(
        monkeypatch,
        [{"title": "T", "href": "https://example.test", "body": "B"}],
    )

    result = await DuckDuckGoSearchTool().execute(query="corvus")

    assert result.success
    assert "Title: T" in result.output
    assert "URL: https://example.test" in result.output
    assert result.metadata["result_count"] == 1
    assert fake.backends == ["duckduckgo"]


@pytest.mark.asyncio
async def test_duckduckgo_falls_back_to_next_backend(monkeypatch):
    fake = install_ddgs(
        monkeypatch,
        [{"title": "Fallback", "href": "https://example.test", "body": "B"}],
        errors={"duckduckgo": "blocked"},
    )

    result = await DuckDuckGoSearchTool().execute(query="corvus")

    assert result.success
    assert "Fallback" in result.output
    assert fake.backends == ["duckduckgo", "bing"]


@pytest.mark.asyncio
async def test_duckduckgo_all_backends_fail(monkeypatch):
    fake = install_ddgs(
        monkeypatch,
        [],
        errors={
            "duckduckgo": "blocked",
            "bing": "timeout",
            "brave": "rate limited",
        },
    )

    result = await DuckDuckGoSearchTool().execute(query="corvus")

    assert not result.success
    assert "all backends" in result.error.lower()
    assert "duckduckgo" in result.error
    assert fake.backends == ["duckduckgo", "bing", "brave"]


@pytest.mark.asyncio
async def test_duckduckgo_empty_results_is_not_an_error(monkeypatch):
    install_ddgs(monkeypatch, [])

    result = await DuckDuckGoSearchTool().execute(query="corvus")

    assert result.success
    assert result.output == "No results found"
    assert result.metadata["result_count"] == 0


@pytest.mark.asyncio
async def test_duckduckgo_accepts_search_alias_and_limits_results(monkeypatch):
    install_ddgs(
        monkeypatch,
        [
            {"title": "One", "href": "https://1.test", "body": "1"},
            {"title": "Two", "href": "https://2.test", "body": "2"},
            {"title": "Three", "href": "https://3.test", "body": "3"},
        ],
    )

    result = await DuckDuckGoSearchTool().execute(search="corvus", max_results=2)

    assert result.success
    assert result.output.count("Title:") == 2
    assert result.metadata["result_count"] == 2


@pytest.mark.asyncio
async def test_duckduckgo_coerces_string_max_results(monkeypatch):
    install_ddgs(
        monkeypatch,
        [
            {"title": "One", "href": "https://1.test", "body": "1"},
            {"title": "Two", "href": "https://2.test", "body": "2"},
        ],
    )

    result = await DuckDuckGoSearchTool().execute(query="corvus", max_results="1")

    assert result.success
    assert result.metadata["result_count"] == 1


@pytest.mark.asyncio
async def test_duckduckgo_missing_query():
    result = await DuckDuckGoSearchTool().execute()
    assert not result.success
    assert "Missing required parameter" in result.error


def test_truncate_helper():
    text, truncated = builtin._truncate("abcdef", 3)
    assert truncated is True
    assert text.startswith("abc")
    assert "3 more characters" in text

    text, truncated = builtin._truncate("abc", 10)
    assert truncated is False
    assert text == "abc"
