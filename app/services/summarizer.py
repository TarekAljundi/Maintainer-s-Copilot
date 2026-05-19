"""3-line WHAT / ASK / STATE triage summary for long issue threads.

PRD §NLP tools as services (Q10): lives behind the API, not the model server
(no weights to load). Groq llama-3.3-70b-versatile, temp 0. Threads longer
than ~8k tokens are truncated head-only.

The prompt enforces:
  - Exactly three lines, prefixed `WHAT:`, `ASK:`, `STATE:` (no markdown headers).
  - Max 80 words across the three lines combined.

Caller (chatbot tool) converts a `SummarizerFailure` into the `{ok:false}`
envelope; this module only ever raises.
"""

from __future__ import annotations

import logging
from typing import Any

from groq import Groq  # type: ignore[import-not-found]

from app.domain.exceptions import LLMProviderError, SummarizerFailure
from app.infra.vault import get_vault

log = logging.getLogger(__name__)

MODEL = "llama-3.3-70b-versatile"
TEMPERATURE = 0.0

# Head-only truncation. 8k tokens ≈ 32k chars at ~4 chars/token. Documented as
# approximate in PRD §"Threads longer than 8k tokens truncated head-only";
# tighter token accounting would require adding tiktoken to the api extra.
MAX_INPUT_CHARS = 32_000

SYSTEM_PROMPT = (
    "You are a triage assistant for an open-source maintainer. "
    "Summarize the supplied issue thread in exactly THREE lines, in this order:\n"
    "WHAT: one sentence — the technical problem or request.\n"
    "ASK: one sentence — what the reporter is asking the maintainer to do.\n"
    "STATE: one sentence — current status (e.g. open / investigating / awaiting reproduction / "
    "fixed in main / waiting on user).\n"
    "Total length across all three lines must not exceed 80 words. "
    "Do NOT use markdown headers, bullets, or code fences. "
    "Do NOT add any preamble or trailing commentary."
)


def _api_key() -> str:
    try:
        secrets = get_vault().cached("api/llm")
    except Exception as exc:
        raise LLMProviderError(f"vault api/llm not cached: {exc}") from exc
    key = secrets.get("groq_api_key") or ""
    if not key:
        raise LLMProviderError("groq_api_key missing from vault api/llm")
    return key


def _truncate_head(text: str, max_chars: int = MAX_INPUT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    # Head-only: keep the first max_chars, append a single sentinel so the LLM
    # knows the rest was cut. Cheap, deterministic, no tokenizer dependency.
    return text[:max_chars].rstrip() + "\n\n[…thread truncated…]"


def summarize(text: str, *, client: Any | None = None) -> str:
    """Return a 3-line summary. Raises SummarizerFailure on any LLM error."""
    if not text or not text.strip():
        raise SummarizerFailure("empty input")

    payload = _truncate_head(text)
    groq_client = client or Groq(api_key=_api_key())

    try:
        completion = groq_client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": payload},
            ],
            temperature=TEMPERATURE,
        )
    except Exception as exc:
        raise SummarizerFailure(f"groq call failed: {exc}") from exc

    try:
        content = (completion.choices[0].message.content or "").strip()
    except (AttributeError, IndexError) as exc:
        raise SummarizerFailure(f"unexpected groq response shape: {exc}") from exc

    if not content:
        raise SummarizerFailure("empty completion from groq")
    return content
