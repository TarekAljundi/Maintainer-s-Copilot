"""LLM baseline: Groq `llama-3.3-70b-versatile`, 4-shot, temp 0, tool_use enforced.

PRD §Three-model comparison (Q4, Q6, Q7, Q8) — single tool schema yielding
`{"label": "bug|feature|docs|question"}`, structured output enforced via
`tool_choice` so the model cannot return free text.

Few-shot composition:
  - bug, feature, question: one record per class drawn from train.jsonl (seeded).
  - docs: synthesized — the corpus has zero `docs` records in train (see
    DECISIONS.md §"docs label is structurally sparse in the issue stream").
    A short, realistic stand-in is used so the prompt is balanced across
    all four classes.

Reads `GROQ_API_KEY` from env (not Vault) so this is offline-from-the-stack:
the eval runs in CI and locally without a Vault container, with the secret
injected by the workflow.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

LABELS: tuple[str, ...] = ("bug", "feature", "docs", "question")
MODEL = "llama-3.3-70b-versatile"
TEMPERATURE = 0.0
MAX_BODY_CHARS_FEWSHOT = 600  # tight, keeps prompt under 2k tokens with 4 shots

# Groq pricing for llama-3.3-70b-versatile (per 1M tokens), retrieved 2026-05-19
# from groq.com/pricing. Locked here for reproducibility; revisit before submission.
PRICE_INPUT_PER_1M_USD = 0.59
PRICE_OUTPUT_PER_1M_USD = 0.79

SPLITS_DIR = Path("data/splits")
FEWSHOT_SEED = 17

CLASSIFY_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_issue",
        "description": (
            "Assign exactly one label to a GitHub issue. "
            "Use this tool for every input — do not produce free-text output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "enum": list(LABELS),
                    "description": (
                        "bug: a reported defect with reproducible incorrect behavior. "
                        "feature: a request for new capability, enhancement, or API. "
                        "docs: a complaint about missing, incorrect, or unclear documentation. "
                        "question: a usage question, request for help, or unclear behavior the "
                        "user is asking about rather than asserting is broken."
                    ),
                }
            },
            "required": ["label"],
            "additionalProperties": False,
        },
    },
}

SYSTEM_PROMPT = (
    "You triage GitHub issues for the fastapi/fastapi repository. "
    "For every issue, call the `classify_issue` tool with exactly one of: "
    "bug, feature, docs, question. Use the description in the tool schema to choose. "
    "Never reply in free text — only via the tool."
)

DOCS_FEWSHOT_TITLE = "Docs: missing example for nested Depends in tutorial"
DOCS_FEWSHOT_BODY = (
    "The 'Dependencies > Sub-dependencies' tutorial page does not include a runnable "
    "example for the common case where a dependency itself yields and depends on another "
    "yield dependency. The narrative implies it works the same as flat Depends, but the "
    "lifecycle ordering of `yield` in nested cases is non-obvious. Adding a 6-line snippet "
    "+ a note about teardown order would close this gap."
)


def _load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def _trim(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + " […]"


def _shape_user_message(title: str | None, body: str | None, max_chars: int | None = None) -> str:
    title = (title or "").strip()
    body = (body or "").strip()
    if max_chars is not None:
        body = _trim(body, max_chars)
    if body:
        return f"Title: {title}\n\nBody:\n{body}"
    return f"Title: {title}"


def _build_fewshot_messages() -> list[dict]:
    """One example per class. `docs` is synthesized; bug/feature/question come from train."""
    rng = random.Random(FEWSHOT_SEED)
    train = _load_jsonl(SPLITS_DIR / "train.jsonl")
    by_label: dict[str, list[dict]] = {label: [] for label in LABELS}
    for r in train:
        if r["label"] in by_label:
            by_label[r["label"]].append(r)

    messages: list[dict] = []
    for label in LABELS:
        if label == "docs":
            user_content = _shape_user_message(DOCS_FEWSHOT_TITLE, DOCS_FEWSHOT_BODY)
        else:
            pool = by_label[label]
            if not pool:
                raise RuntimeError(
                    f"few-shot construction needs a {label!r} example in train but the "
                    f"split has zero — re-check data/splits/train.jsonl"
                )
            ex = rng.choice(pool)
            user_content = _shape_user_message(
                ex.get("title"), ex.get("body"), MAX_BODY_CHARS_FEWSHOT
            )
        # User shows issue; assistant calls the tool with the right label.
        messages.append({"role": "user", "content": user_content})
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_fewshot_{label}",
                        "type": "function",
                        "function": {
                            "name": "classify_issue",
                            "arguments": json.dumps({"label": label}),
                        },
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"call_fewshot_{label}",
                "name": "classify_issue",
                "content": json.dumps({"ok": True}),
            }
        )
    return messages


@dataclass
class _LLMUsage:
    input_tokens: int
    output_tokens: int


def _cost_usd(usage: _LLMUsage) -> float:
    return (
        usage.input_tokens * PRICE_INPUT_PER_1M_USD + usage.output_tokens * PRICE_OUTPUT_PER_1M_USD
    ) / 1_000_000


def _api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY environment variable is not set; LLM baseline needs it")
    return key


def _make_client():  # type: ignore[no-untyped-def]
    from groq import Groq  # type: ignore[import-not-found]

    return Groq(api_key=_api_key())


def _parse_tool_label(completion) -> str:  # type: ignore[no-untyped-def]
    choice = completion.choices[0]
    msg = choice.message
    tool_calls = getattr(msg, "tool_calls", None) or []
    for tc in tool_calls:
        fn = getattr(tc, "function", None)
        if fn and fn.name == "classify_issue":
            try:
                args = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            label = args.get("label")
            if label in LABELS:
                return label
    # Fallback: parse a free-text reply for one of the labels (shouldn't trigger
    # with tool_choice forced, but Groq sometimes returns text on a refusal).
    content = (msg.content or "").lower()
    for label in LABELS:
        if label in content:
            return label
    raise RuntimeError(
        f"LLM returned no usable label: tool_calls={tool_calls!r} content={content!r}"
    )


def predict_batch(
    records: Iterable[dict],
) -> tuple[list[str], list[float], dict]:
    """Predict labels for golden records.

    Returns (labels, per-record latency in ms, usage_report).
    usage_report = {"total_input_tokens", "total_output_tokens", "total_cost_usd", "n"}
    """
    fewshot = _build_fewshot_messages()
    client = _make_client()

    labels_out: list[str] = []
    latencies_ms: list[float] = []
    total_in = 0
    total_out = 0
    n = 0

    for rec in records:
        user_msg = _shape_user_message(rec.get("title"), rec.get("body"))
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *fewshot,
            {"role": "user", "content": user_msg},
        ]
        t0 = time.perf_counter()
        completion = client.chat.completions.create(
            model=MODEL,
            messages=messages,  # type: ignore[arg-type]
            tools=[CLASSIFY_TOOL],  # type: ignore[arg-type]
            tool_choice={"type": "function", "function": {"name": "classify_issue"}},
            temperature=TEMPERATURE,
        )
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        labels_out.append(_parse_tool_label(completion))

        usage = completion.usage
        if usage is not None:
            total_in += getattr(usage, "prompt_tokens", 0) or 0
            total_out += getattr(usage, "completion_tokens", 0) or 0
        n += 1

    usage_report = {
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_cost_usd": _cost_usd(_LLMUsage(total_in, total_out)),
        "n": n,
        "model": MODEL,
        "price_input_per_1m_usd": PRICE_INPUT_PER_1M_USD,
        "price_output_per_1m_usd": PRICE_OUTPUT_PER_1M_USD,
    }
    return labels_out, latencies_ms, usage_report


# Convenience: predict from `texts` (titles already embedded, body=None).
def predict_texts(texts: Iterable[str]) -> tuple[list[str], list[float], dict]:
    return predict_batch([{"title": t, "body": None} for t in texts])


def _self_test() -> int:
    """Print the few-shot prompt without calling the API. Sanity check shape."""
    msgs = _build_fewshot_messages()
    print(f"few-shot messages: {len(msgs)}")
    for m in msgs:
        head = (m.get("content") or "")[:80].replace("\n", " ")
        print(f"  {m['role']:>10}  {head}")
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
