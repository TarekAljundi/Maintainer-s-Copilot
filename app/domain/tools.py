"""Tool schemas + dispatch table for the chatbot agent loop.

Slice 03: `classify_issue`.
Slice 05: + `extract_entities`, `summarize_thread`.
Remaining tools (`search_knowledge`, `write_memory`) land in their owning
slices (07, 11).

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


EXTRACT_ENTITIES_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "extract_entities",
        "description": (
            "Surface code-shaped entities in the supplied text: identifiers, file paths, "
            "error/exception types, version strings, PR references, URLs, module paths. "
            "USE WHEN: the user asks to 'find entities', 'what files/errors/versions/PRs are "
            "mentioned', or wants a structured list of code references in the text. "
            "DO NOT USE WHEN: the user wants a classification, a summary, a docs answer, "
            "or a free-text explanation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Raw text to scan for entities."}
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}


SUMMARIZE_THREAD_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "summarize_thread",
        "description": (
            "Produce a 3-line WHAT / ASK / STATE triage summary of a long issue thread. "
            "USE WHEN: the user pastes a multi-comment thread (or asks 'summarize this thread', "
            "'tldr', 'give me a triage summary'). "
            "DO NOT USE WHEN: the input is a single issue body without back-and-forth comments — "
            "in that case classify or extract entities instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Full thread text (title + body + comments concatenated).",
                }
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


def _tool_extract_entities(text: str) -> dict:
    ms = ModelServerClient()
    result = ms.extract(text)
    return {"ok": True, "entities": result.get("entities", [])}


def _tool_summarize_thread(text: str) -> dict:
    # Lazy import: keeps the import-time cost of `app.domain.tools` low for
    # callers that never invoke this tool (e.g. test fixtures, NER-only paths).
    from app.services import summarizer

    summary = summarizer.summarize(text)
    return {"ok": True, "summary": summary}


def _wrap(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Convert any ToolFailure into the LLM-visible {ok:false} envelope."""

    def runner(**kwargs):
        try:
            return fn(**kwargs)
        except ToolFailure as exc:
            return {"ok": False, "error": exc.code, "detail": str(exc)}

    return runner


TOOL_SCHEMAS: list[dict] = [
    CLASSIFY_ISSUE_SCHEMA,
    EXTRACT_ENTITIES_SCHEMA,
    SUMMARIZE_THREAD_SCHEMA,
]
TOOL_DISPATCH: dict[str, Callable[..., dict]] = {
    "classify_issue": _wrap(_tool_classify_issue),
    "extract_entities": _wrap(_tool_extract_entities),
    "summarize_thread": _wrap(_tool_summarize_thread),
}
