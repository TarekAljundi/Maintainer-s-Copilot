"""Tool schemas + dispatch table for the chatbot agent loop.

Slice 03: only `classify_issue` is registered. Remaining four tools
(`extract_entities`, `summarize_thread`, `search_knowledge`, `write_memory`)
are added by their owning slices (05, 05, 07, 11).

Each schema includes both a "use when" rubric and a "do not use when" guard
to bound the LLM's selection (PRD Q28 mitigation for Llama tool-call drift).
"""

from __future__ import annotations

from typing import Callable

from app.domain.exceptions import ToolFailure
from app.infra.model_server_client import ModelServerClient


CLASSIFY_ISSUE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "classify_issue",
        "description": (
            "Classify a GitHub-style issue body into exactly one of "
            "{bug, feature, docs, question}. "
            "USE WHEN: the user pastes issue text and asks 'what kind', 'classify', "
            "'label this', or wants a triage decision. "
            "DO NOT USE WHEN: the user asks a general project question, requests a summary, "
            "asks about an entity in the text, or wants a docs answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Full issue body (or title+body)."}
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}


def _tool_classify_issue(text: str) -> dict:
    ms = ModelServerClient()
    result = ms.classify(text)
    return {"ok": True, "label": result["label"], "confidence": result["confidence"]}


def _wrap(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Convert any ToolFailure into the LLM-visible {ok:false} envelope."""

    def runner(**kwargs):
        try:
            return fn(**kwargs)
        except ToolFailure as exc:
            return {"ok": False, "error": exc.code, "detail": str(exc)}

    return runner


TOOL_SCHEMAS: list[dict] = [CLASSIFY_ISSUE_SCHEMA]
TOOL_DISPATCH: dict[str, Callable[..., dict]] = {
    "classify_issue": _wrap(_tool_classify_issue),
}
