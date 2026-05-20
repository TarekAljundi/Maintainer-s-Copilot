"""ChatbotService — agent loop, tool dispatch, mem inject, SSE event stream.

Interface:
    run_turn(user_msg, conversation_id, user_id) -> AsyncIterator[Event]

Slice 03: single-shot tool dispatch.
Slice 09: 6-step bounded agent loop, mid-stream LLM-error → SSE `error` event,
          tool failures land as `tool_call_result.ok=False` (via `_wrap` in
          app/domain/tools.py), so the LLM can hedge.
Slice 11: episodic memory auto-recall before the first LLM call, rendered into
          the system prompt under <recalled_memories>; ContextVar threading
          for the write_memory tool.
Slice 12: @observe spans on the agent loop + the recall step.
"""

from __future__ import annotations

import inspect
import json
import uuid
from typing import Any, AsyncIterator

from app.domain import tools as tools_module
from app.domain.exceptions import AppError, LLMProviderError
from app.domain.tools import TOOL_DISPATCH, TOOL_SCHEMAS
from app.infra import tracing
from app.infra.llm_groq import stream_chat_with_tools
from app.infra.logging import bind_trace_id

Event = dict[str, Any]

MAX_AGENT_ITERATIONS = 6

BASE_SYSTEM_PROMPT = (
    "You are Maintainer's Copilot, an assistant for an open-source project maintainer. "
    "Use the provided tools when they directly fit the user's request. "
    "When a tool returns a result, you MUST quote the exact field values from the result "
    "(label, confidence, ...) verbatim. Never substitute, paraphrase, or invent values. "
    "If the tool returned label='question', you write 'question' — not any other word. "
    "When search_knowledge returns passages, ground your answer in those passages and cite "
    "each fact using the result's `citation` field verbatim (e.g. 'User Guide > IO > CSV' "
    "or '#61809'). Do not invent breadcrumbs, section names, or issue numbers. "
    "When a tool returns ok=false (its upstream is unavailable), acknowledge the limitation "
    "to the user briefly and offer a best-effort answer from context instead of retrying "
    "the same tool with the same arguments.\n\n"
    "search_knowledge filters — apply when the user's intent narrows the scope:\n"
    "- 'how do I / API docs' → content_types=['docs']\n"
    "- 'has this been fixed / past decision / what did the maintainers say' → "
    "content_types=['issue'], is_answer=true\n"
    "- 'recently / since <date>' → min_closed_at=<ISO date>\n"
    "- topic-scoped lookup ('in IO tools', 'GroupBy section') → breadcrumb_prefix='<prefix>'\n"
    "- 'bug related to X' → content_types=['issue'], labels=['Bug']\n"
    "Omit filters entirely for open-ended questions.\n\n"
    "write_memory: fire only on explicit user asks to remember a fact, decision, or focus area, "
    "OR when the user explicitly states a long-running topic they want carried across sessions. "
    "Never auto-summarize previous chat turns into memories. Never fire on chit-chat, on "
    "classification requests, on RAG questions, or on summarization requests."
)


def _render_recalled(recalled: list[Any]) -> str:
    """Render a list of RecalledMemory into a <recalled_memories> block.

    Empty list returns an empty block — present in the prompt either way so
    the model learns the contract (silence = nothing remembered).
    """
    if not recalled:
        return "\n\n<recalled_memories/>"
    lines = ["\n\n<recalled_memories>"]
    for m in recalled:
        ent = ", ".join(m.entities) if m.entities else ""
        ent_part = f" [entities: {ent}]" if ent else ""
        lines.append(f"- ({m.similarity:.2f}) {m.summary}{ent_part}")
    lines.append("</recalled_memories>")
    return "\n".join(lines)


@tracing.observe(as_type="retrieval")
async def _recall(user_id: str | None, query: str) -> list[Any]:
    """Best-effort recall. Errors are swallowed — memory is augmentation, not
    correctness (slice plan §F)."""
    if not user_id:
        return []
    try:
        from app.services.memory import default_service

        svc = default_service()
        return await svc.recall(user_id=user_id, query=query, top_k=5, min_similarity=0.6)
    except Exception:
        return []


async def _dispatch_tool(tc: dict) -> dict:
    try:
        args = json.loads(tc["arguments"] or "{}")
    except json.JSONDecodeError:
        args = {}
    handler = TOOL_DISPATCH.get(tc["name"])
    if handler is None:
        return {"ok": False, "error": "tool_not_registered", "detail": tc["name"]}
    value = handler(**args)
    return await value if inspect.isawaitable(value) else value


@tracing.observe(name="chat_turn")
async def run_turn(
    user_msg: str,
    conversation_id: str | None = None,
    user_id: str | None = None,
) -> AsyncIterator[Event]:
    msg_id = uuid.uuid4().hex
    tracing.update_current_trace(
        session_id=conversation_id,
        user_id=user_id,
        tags=["chat"],
    )
    bind_trace_id(tracing.trace_id())

    user_token = tools_module.current_user_id.set(user_id)
    conv_token = tools_module.current_conversation_id.set(conversation_id)

    try:
        recalled = await _recall(user_id, user_msg)
        system_prompt = BASE_SYSTEM_PROMPT + _render_recalled(recalled)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        try:
            for iteration in range(MAX_AGENT_ITERATIONS):
                finish_reason: str | None = None
                pending_tool_calls: list[dict] = []

                async for ev in stream_chat_with_tools(messages, tools=TOOL_SCHEMAS):
                    if ev["type"] == "token":
                        yield {"type": "token", "content": ev["content"]}
                    elif ev["type"] == "stream_end":
                        finish_reason = ev["finish_reason"]
                        pending_tool_calls = ev["tool_calls"]

                if finish_reason != "tool_calls" or not pending_tool_calls:
                    break

                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments"],
                                },
                            }
                            for tc in pending_tool_calls
                        ],
                    }
                )

                for tc in pending_tool_calls:
                    try:
                        args_preview = json.loads(tc["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args_preview = {}
                    yield {"type": "tool_call_start", "name": tc["name"], "args": args_preview}
                    result = await _dispatch_tool(tc)
                    yield {"type": "tool_call_result", "name": tc["name"], "result": result}
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result),
                        }
                    )
            else:
                # Loop fell through without break → cap exceeded.
                raise LLMProviderError("max_steps_exceeded")
        except AppError as exc:
            yield {
                "type": "error",
                "code": exc.code,
                "message": exc.message or str(exc),
                "trace_id": tracing.trace_id(),
            }
        except Exception as exc:
            yield {
                "type": "error",
                "code": "internal_error",
                "message": str(exc) or "internal error",
                "trace_id": tracing.trace_id(),
            }

        yield {"type": "done", "msg_id": msg_id}
    finally:
        tools_module.current_user_id.reset(user_token)
        tools_module.current_conversation_id.reset(conv_token)
