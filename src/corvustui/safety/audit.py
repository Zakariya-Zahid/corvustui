"""Append-only JSONL audit log for tool decisions and results."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from corvustui.safety.redact import redact_secrets


class AuditLogger:
    """Write one JSON object per line, redacting secrets before persistence."""

    def __init__(self, path: Path | None):
        self.path = Path(path).expanduser() if path is not None else None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def write(self, event: dict[str, Any]) -> None:
        if self.path is None:
            return

        payload = dict(event)
        payload.setdefault("timestamp", datetime.now(UTC).isoformat())
        serialised = json.dumps(payload, ensure_ascii=False, default=str)
        serialised = redact_secrets(serialised)

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Create the log with owner-only permissions; it contains tool
            # arguments and output previews even after redaction.
            descriptor = os.open(
                self.path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                # ``os.open`` mode only applies on creation; tighten an
                # existing file as well.
                os.fchmod(descriptor, 0o600)
            except OSError:
                pass
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(serialised + "\n")

    def read(self) -> list[dict[str, Any]]:
        if self.path is None or not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    events.append(value)
        return events
