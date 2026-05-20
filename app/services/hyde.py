"""HyDE — Hypothetical Document Embeddings.

Given a user query, generate a short hypothetical pandas-doc passage and use
its embedding as the dense-side query. The sparse side still uses the
original query (HyDE-style passages lose keyword signal).

Caching: results persist to data/cache/hyde/{sha256(query)}.txt so the eval
re-runs (RRF weight tuning, ablations) don't re-burn Groq tokens.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from groq import Groq

from app.domain.exceptions import LLMProviderError, RAGRetrievalFailure
from app.infra import tracing
from app.infra.vault import get_vault

log = logging.getLogger(__name__)

MODEL = "llama-3.3-70b-versatile"
TEMPERATURE = 0.0
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "hyde.md"
CACHE_DIR = Path("data/cache/hyde")


def _api_key() -> str:
    """Resolve via Vault; fall back to the GROQ_API_KEY env var when Vault is
    unreachable. Vault wins when both are present (production deployment).
    """
    try:
        secrets = get_vault().cached("api/llm")
        key = secrets.get("groq_api_key") or ""
        if key:
            return key
    except Exception:
        pass
    env_key = os.environ.get("GROQ_API_KEY") or ""
    if env_key:
        return env_key
    raise LLMProviderError("groq_api_key missing (no Vault entry, no GROQ_API_KEY env)")


def _cache_path(query: str) -> Path:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:32]
    return CACHE_DIR / f"{digest}.txt"


def _render_prompt(query: str) -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").replace("{query}", query)


@tracing.observe(as_type="generation", name="hyde.generate")
def generate(query: str, *, client: Any | None = None, use_cache: bool = True) -> str:
    """Return a 3-4 sentence hypothetical pandas-doc passage answering the query.

    Raises RAGRetrievalFailure on any LLM error so the caller can fall back to
    using the original query embedding.
    """
    if use_cache:
        cp = _cache_path(query)
        if cp.exists():
            return cp.read_text(encoding="utf-8")

    user_prompt = _render_prompt(query)
    groq_client = client or Groq(api_key=_api_key())

    try:
        completion = groq_client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=TEMPERATURE,
        )
    except Exception as exc:
        raise RAGRetrievalFailure(f"HyDE groq call failed: {exc}") from exc

    try:
        passage = (completion.choices[0].message.content or "").strip()
    except (AttributeError, IndexError) as exc:
        raise RAGRetrievalFailure(f"HyDE unexpected groq response: {exc}") from exc

    if not passage:
        raise RAGRetrievalFailure("HyDE empty completion")

    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(query).write_text(passage, encoding="utf-8")
    return passage


def clear_cache() -> int:
    """Remove all cached HyDE passages. Returns the number of files deleted."""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for p in CACHE_DIR.glob("*.txt"):
        p.unlink()
        n += 1
    return n
