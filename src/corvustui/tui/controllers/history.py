"""Persistent prompt history manager."""

from __future__ import annotations

import json
from pathlib import Path


class PromptHistory:
    """Stores submitted prompts to disk and supports Up/Down navigation."""

    def __init__(self, path: Path | None = None, max_entries: int = 500) -> None:
        self.path = path or (Path.home() / ".corvustui" / "prompt_history.jsonl")
        self.max_entries = max_entries
        self._entries: list[str] = []
        self._cursor: int = 0
        self._draft: str = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            items: list[str] = []
            for line in lines[-self.max_entries :]:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    text = data.get("prompt") if isinstance(data, dict) else None
                except json.JSONDecodeError:
                    text = line
                if text and (not items or items[-1] != text):
                    items.append(text)
            self._entries = items[-self.max_entries :]
            self._cursor = len(self._entries)
        except OSError:
            pass

    def add(self, prompt: str) -> None:
        prompt = prompt.strip()
        if not prompt:
            return
        if self._entries and self._entries[-1] == prompt:
            self._cursor = len(self._entries)
            return

        self._entries.append(prompt)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]
        self._cursor = len(self._entries)
        self._draft = ""

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"prompt": prompt}) + "\n")
        except OSError:
            pass

    def reset_navigation(self) -> None:
        self._cursor = len(self._entries)
        self._draft = ""

    def previous(self, current_text: str) -> str | None:
        if not self._entries:
            return None
        if self._cursor == len(self._entries):
            self._draft = current_text
        if self._cursor > 0:
            self._cursor -= 1
            return self._entries[self._cursor]
        return self._entries[0]

    def next(self) -> str | None:
        if not self._entries:
            return None
        if self._cursor < len(self._entries) - 1:
            self._cursor += 1
            return self._entries[self._cursor]
        self._cursor = len(self._entries)
        return self._draft
