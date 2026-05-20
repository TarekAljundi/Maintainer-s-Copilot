"""Redactor — single source of truth, no external deps. See PRD Q20.

Interface:
    redact(text) -> str
    redact_obj(obj) -> obj   # recursive on dict/list/tuple/str

Patterns: vendor-prefixed tokens, JWT, URL creds, password kv, email,
user-identifying paths. NOT redacted (defensible, see SECURITY.md):
IPs, names, phones, generic high-entropy strings.

Hooked at 3 boundaries:
- structlog processor (app/infra/logging.py)
- Langfuse mask callback (app/infra/tracing.py)
- MemoryService.write summary path (app/services/memory.py)
"""

from __future__ import annotations

import re
from typing import Any


PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # URL credentials must run BEFORE generic password kv / email matchers so the
    # whole `scheme://user:pass@host` is replaced as one token.
    (
        "url_credentials",
        re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/@:]+:[^\s/@]+@[^\s]+"),
    ),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?!ant-)[A-Za-z0-9_\-]{20,}\b")),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[bpa]-[A-Za-z0-9\-]{10,}\b")),
    # JWT: three dot-separated base64url segments. Require minimum length per
    # segment so we don't match e.g. "a.b.c".
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
    (
        "password_kv",
        re.compile(r"(?i)\b(password|passwd|pwd)\s*[=:]\s*[^\s,;&]+"),
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ),
    # User-identifying paths. Capture the segment up to (but not including)
    # the next path separator or end-of-string.
    (
        "user_path",
        re.compile(r"(?:[Cc]:\\Users\\|/Users/|/home/)[^\s\\/:*?\"<>|]+"),
    ),
]


def redact(text: str) -> str:
    """Replace each match with [REDACTED:<category>]. Order is significant."""
    if not isinstance(text, str) or not text:
        return text
    for name, pattern in PATTERNS:
        text = pattern.sub(f"[REDACTED:{name}]", text)
    return text


def redact_obj(obj: Any) -> Any:
    """Recursively redact strings inside dict / list / tuple / set values.

    Dict keys are NOT redacted (they are usually structural). Non-string scalars
    pass through unchanged.
    """
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(v) for v in obj)
    if isinstance(obj, set):
        return {redact_obj(v) for v in obj}
    return obj
