"""Redactor — single source of truth, no external deps. See PRD Q20.

Interface:
    redact(text) -> str
    redact_obj(obj) -> obj   # recursive on dict/list/str

Patterns: vendor-prefixed tokens, JWT, URL creds, email, user paths.
NOT redacted (defensible): IP, names, generic high-entropy strings.

Hooked at 3 boundaries: structlog processor, Langfuse mask, memory write.
"""

PATTERNS: list[tuple[str, str]] = [
    # ("name", r"regex"),
]
