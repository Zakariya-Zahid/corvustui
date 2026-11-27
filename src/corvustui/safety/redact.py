"""Secret redaction for tool output, approval prompts, and audit logs.

The patterns are deliberately conservative: they target credentials that have
recognisable shapes (provider keys, bearer tokens, private keys, assignments)
rather than every long string.  Redaction is defence-in-depth, not a substitute
for not putting secrets in tool arguments in the first place.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# Patterns are applied in order.  Each entry is (compiled regex, replacement).
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # PEM / OpenSSH private key blocks.
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
            r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        REDACTED,
    ),
    # OpenAI, Anthropic, OpenRouter, and compatible provider keys.
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), REDACTED),
    # Google API keys.
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), REDACTED),
    # GitHub tokens.
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), REDACTED),
    # Slack tokens.
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), REDACTED),
    # AWS access key ids.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    # Tavily keys.
    (re.compile(r"\btvly-[A-Za-z0-9_-]{8,}\b"), REDACTED),
    # JSON Web Tokens.
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        REDACTED,
    ),
    # Authorization headers.
    (
        re.compile(r"(?i)\b(authorization\s*:\s*(?:bearer|basic)\s+)[^\s\"',;]+"),
        r"\1" + REDACTED,
    ),
    # JSON-style "key": "value" assignments.
    (
        re.compile(
            r'(?i)("(?:api[_-]?key|access[_-]?token|auth[_-]?token|'
            r'client[_-]?secret|secret[_-]?key|private[_-]?key|password|'
            r'passwd)"\s*:\s*")([^"\n]{4,})(")'
        ),
        r"\1" + REDACTED + r"\3",
    ),
    (
        re.compile(
            r'(?i)("(?:secret|token)"\s*:\s*")([^"\n]{12,})(")'
        ),
        r"\1" + REDACTED + r"\3",
    ),
    # key=value / key: value assignments for strong secret names.
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
            r"secret[_-]?key|private[_-]?key|password|passwd)"
            r"(\s*[:=]\s*)([\"']?)([^\s\"',;}\[\]]{4,})\3"
        ),
        r"\1\2\3" + REDACTED + r"\3",
    ),
    # Bare "secret"/"token" labels need a longer value to avoid redacting
    # ordinary prose such as "token: count".
    (
        re.compile(
            r"(?i)\b(secret|token)(\s*[:=]\s*)([\"']?)([^\s\"',;}\[\]]{12,})\3"
        ),
        r"\1\2\3" + REDACTED + r"\3",
    ),
)


def redact_secrets(text: str) -> str:
    """Return ``text`` with recognisable secrets replaced by ``[REDACTED]``."""

    if not text:
        return text

    redacted = text
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def contains_secret(text: str) -> bool:
    """Return True when redaction would change ``text``."""

    return bool(text) and redact_secrets(text) != text
