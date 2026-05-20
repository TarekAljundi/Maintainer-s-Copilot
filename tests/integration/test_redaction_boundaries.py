"""Brief-mandated: a fake ghp_… token pasted into chat must never appear
unredacted in logs, traces, or memory.

PRD §Observability §Redaction hookup at three boundaries; §Tier 1 tests — Redactor;
user stories 17, 18, 43.

Boundary 1 (logs): real structlog config + capsys/capfd asserting JSON
                   output contains [REDACTED:github_token].
Boundary 2 (traces): direct call to tracing.mask() asserting the same.
Boundary 3 (memory): MemoryService.write with the embed call mocked and the
                     repository's insert mocked to capture the summary arg;
                     assert the persisted-summary arg contains the redaction
                     marker, not the raw token.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import pytest

from app.infra import logging as app_logging
from app.infra import tracing as app_tracing
from app.infra.redaction import redact

FAKE_TOKEN = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"


# ---------- Boundary 1: logs ----------


def test_boundary_1_logs_redact_github_token(capsys, monkeypatch):
    """A structlog event carrying the raw token emits a redacted JSON line."""

    # Force-reconfigure (the `_configured` guard makes configure() idempotent
    # in production but stateful across tests — flip it here).
    monkeypatch.setattr(app_logging, "_configured", False)
    # Pipe stdlib logging into a fresh handler we control.
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    try:
        app_logging.configure(level="INFO")
        logger = app_logging.get_logger("test")
        logger.info("user pasted token", payload={"raw": FAKE_TOKEN})
        handler.flush()
    finally:
        root.handlers = old_handlers
        monkeypatch.setattr(app_logging, "_configured", False)

    out = stream.getvalue()
    assert FAKE_TOKEN not in out, "raw token leaked into structured log line"
    assert "[REDACTED:github_token]" in out
    # JSON renderer is on; one line per event.
    line = next((ln for ln in out.splitlines() if "user pasted token" in ln), "")
    record = json.loads(line)
    assert "[REDACTED:github_token]" in record["payload"]["raw"]


# ---------- Boundary 2: traces ----------


def test_boundary_2_tracing_mask_redacts():
    """The mask() Langfuse callback walks span input/output before send."""
    span_input: dict[str, Any] = {
        "args": {"text": f"please remember {FAKE_TOKEN}"},
        "metadata": {"raw": [FAKE_TOKEN, "ok"]},
    }
    masked = app_tracing.mask(span_input)
    flat = json.dumps(masked)
    assert FAKE_TOKEN not in flat
    assert "[REDACTED:github_token]" in masked["args"]["text"]
    assert "[REDACTED:github_token]" in masked["metadata"]["raw"][0]
    assert masked["metadata"]["raw"][1] == "ok"


# ---------- Boundary 3: memory write path ----------


@pytest.mark.asyncio
async def test_boundary_3_memory_write_redacts_summary_before_persist(monkeypatch):
    """MemoryService.write must redact `summary` before:
      - embedding it (so the vector doesn't encode the secret)
      - persisting it (so the summary column doesn't carry the secret)
    """
    from app.services.memory import MemoryService

    # 1) Embed call — capture the texts that get embedded, assert they are
    #    already redacted, and return a valid 768-vector.
    embed_inputs: list[list[str]] = []

    class _FakeClient:
        def embed(self, texts: list[str], mode: str = "passage") -> list[list[float]]:
            embed_inputs.append(list(texts))
            return [[0.001] * 768]

    # 2) Persist call — capture the kwargs that would have gone to Postgres.
    captured_summary: dict[str, str] = {}

    async def _fake_insert(**kwargs):
        captured_summary["summary"] = kwargs["summary"]
        return "00000000-0000-0000-0000-000000000001"

    monkeypatch.setattr(
        "app.repositories.memory.insert_memory_with_audit", _fake_insert
    )

    svc = MemoryService(model_client=_FakeClient())
    await svc.write(
        user_id="00000000-0000-0000-0000-0000000000aa",
        summary=f"please remember {FAKE_TOKEN}",
        entities=["github_token_test"],
        conversation_id="conv-1",
    )

    # The text passed to embed must already be redacted.
    assert embed_inputs, "embed was never called"
    assert FAKE_TOKEN not in embed_inputs[0][0]
    assert "[REDACTED:github_token]" in embed_inputs[0][0]

    # The summary that was about to be persisted must be redacted.
    assert FAKE_TOKEN not in captured_summary["summary"]
    assert "[REDACTED:github_token]" in captured_summary["summary"]


# ---------- Pure pattern sanity (the brief asks for one assertion that the
# user story 18 — assistant doesn't echo secrets back — is testable. We assert
# at the redaction layer; system-prompt-level instruction lives in chatbot.py.) ----------


def test_user_story_18_redact_helper_strips_token_from_assistant_text():
    """If the assistant accidentally tried to echo the token (paraphrased or
    verbatim), the redactor catches the verbatim case before it lands in any
    audit-visible surface."""
    assistant_text = f"Sure — your token starts with `{FAKE_TOKEN}`. Got it."
    out = redact(assistant_text)
    assert FAKE_TOKEN not in out
    assert "[REDACTED:github_token]" in out
