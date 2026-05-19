"""Summarizer service: prompt shape + truncation + failure mapping."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.domain.exceptions import SummarizerFailure
from app.services import summarizer


def _make_client(content: str) -> MagicMock:
    """Build a mock Groq client whose chat.completions.create returns `content`."""
    client = MagicMock()
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = content
    client.chat.completions.create.return_value = completion
    return client


def test_summarize_returns_completion_content() -> None:
    fake = _make_client("WHAT: a bug\nASK: please fix\nSTATE: open")
    out = summarizer.summarize("hello there", client=fake)
    assert out == "WHAT: a bug\nASK: please fix\nSTATE: open"
    call = fake.chat.completions.create.call_args
    # Prompt shape: system + user, model + temp 0.
    assert call.kwargs["model"] == summarizer.MODEL
    assert call.kwargs["temperature"] == 0.0
    messages = call.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "WHAT" in messages[0]["content"] and "ASK" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "hello there"}


def test_summarize_truncates_head_over_threshold() -> None:
    big = "A" * (summarizer.MAX_INPUT_CHARS + 5_000)
    fake = _make_client("WHAT: x\nASK: y\nSTATE: z")
    summarizer.summarize(big, client=fake)
    user_payload = fake.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert len(user_payload) <= summarizer.MAX_INPUT_CHARS + 100  # +sentinel
    assert user_payload.endswith("[…thread truncated…]")
    # First chars preserved (head-only).
    assert user_payload.startswith("A" * 100)


def test_summarize_passes_short_text_unchanged() -> None:
    fake = _make_client("ok")
    summarizer.summarize("short", client=fake)
    user_payload = fake.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert user_payload == "short"  # not appended-to


def test_summarize_empty_raises() -> None:
    with pytest.raises(SummarizerFailure):
        summarizer.summarize("", client=_make_client("ignored"))
    with pytest.raises(SummarizerFailure):
        summarizer.summarize("   ", client=_make_client("ignored"))


def test_summarize_groq_error_mapped_to_summarizer_failure() -> None:
    fake = MagicMock()
    fake.chat.completions.create.side_effect = RuntimeError("network blew up")
    with pytest.raises(SummarizerFailure):
        summarizer.summarize("hi", client=fake)


def test_summarize_empty_completion_raises() -> None:
    fake = _make_client("")
    with pytest.raises(SummarizerFailure):
        summarizer.summarize("hi", client=fake)
