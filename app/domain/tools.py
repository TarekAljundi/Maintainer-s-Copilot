"""Tool schemas + dispatch table for the chatbot agent loop.

Slice 03: `classify_issue`.
Slice 05: + `extract_entities`, `summarize_thread`.
Slice 06: + `search_knowledge` (dense-only naive RAG).
`write_memory` lands in slice 11.

Each schema includes both a "use when" rubric and a "do not use when" guard
to bound the LLM's selection (PRD Q28 mitigation for Llama tool-call drift).

Tool handlers may be either sync or async; the chatbot agent loop awaits the
return value when it's a coroutine.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

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


SEARCH_KNOWLEDGE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": (
            "Search the pandas project knowledge base (docs + resolved issues) and return "
            "up to 5 cited passages. "
            "USE WHEN: the user asks a project question ('how do I X', 'why does Y happen', "
            "'what does Z do', or anything about pandas behavior, docs, or past issues). "
            "DO NOT USE WHEN: the user pastes raw issue text and asks for a classification, "
            "summary, or entity extraction — those are handled by other tools. "
            "FILTERS: pass `content_types=['docs']` for API/usage how-tos; "
            "`content_types=['issue']` + `is_answer=true` for 'has this been fixed' / 'past "
            "maintainer decision' questions; `breadcrumb_prefix='User Guide > IO'` to scope "
            "within a docs subtree; `labels=['Bug']` to filter resolved-issue search by "
            "the issue's GitHub labels."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "content_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["docs", "issue"]},
                    "description": ("Restrict search to docs and/or issues. Omit for both."),
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Only return issue chunks tagged with ANY of these GitHub labels "
                        "(e.g. ['Bug'], ['Enhancement', 'IO']). Ignored on docs results."
                    ),
                },
                "is_answer": {
                    "type": "boolean",
                    "description": (
                        "If true, only return issue comments from a maintainer "
                        "(OWNER/MEMBER/COLLABORATOR). Use for 'what did the maintainers say' "
                        "questions."
                    ),
                },
                "min_closed_at": {
                    "type": "string",
                    "description": (
                        "ISO-8601 timestamp; only return issues closed on or after this "
                        "date. Use for 'recently fixed' questions."
                    ),
                },
                "breadcrumb_prefix": {
                    "type": "string",
                    "description": (
                        "Restrict docs results to sections whose breadcrumb starts with "
                        "this prefix (e.g. 'User Guide > IO tools', 'Group by')."
                    ),
                },
            },
            "required": ["query"],
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


async def _tool_search_knowledge(
    query: str,
    content_types: list[str] | None = None,
    labels: list[str] | None = None,
    is_answer: bool | None = None,
    min_closed_at: str | None = None,
    breadcrumb_prefix: str | None = None,
) -> dict:
    from app.domain.chunks import RetrievalFilters
    from app.services.rag import RAGService

    filters = RetrievalFilters(
        content_types=content_types,
        labels=labels,
        is_answer=is_answer,
        min_closed_at=min_closed_at,
        breadcrumb_prefix=breadcrumb_prefix,
    )
    svc = RAGService()
    hits = await svc.retrieve(query, top_k=5, filters=filters)
    return {
        "ok": True,
        "results": [
            {
                "citation": h.citation(),
                "content_type": h.content_type,
                "source_id": h.source_id,
                "score": round(h.score, 4),
                "text": h.text,
            }
            for h in hits
        ],
    }


def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Convert any ToolFailure into the LLM-visible {ok:false} envelope.

    Works for both sync and async handlers; the chatbot loop awaits the
    return value when it's a coroutine.
    """

    if inspect.iscoroutinefunction(fn):

        async def arunner(**kwargs):
            try:
                return await fn(**kwargs)
            except ToolFailure as exc:
                return {"ok": False, "error": exc.code, "detail": str(exc)}

        return arunner

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
    SEARCH_KNOWLEDGE_SCHEMA,
]
TOOL_DISPATCH: dict[str, Callable[..., Any]] = {
    "classify_issue": _wrap(_tool_classify_issue),
    "extract_entities": _wrap(_tool_extract_entities),
    "summarize_thread": _wrap(_tool_summarize_thread),
    "search_knowledge": _wrap(_tool_search_knowledge),
}
