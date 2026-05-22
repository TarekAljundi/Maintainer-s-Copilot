"""RAGAS generation eval: faithfulness + answer_relevancy.

For each of the 25 golden questions:
  1. Run the full slice-07 RAG pipeline (HyDE → hybrid → rerank → parent).
  2. Generate an LLM answer via Groq grounded in the retrieved contexts.
  3. Run RAGAS faithfulness + answer_relevancy with a Groq judge (temp=0).

RAGAS version pinned in pyproject.toml [evals]. Embedding model for the
answer_relevancy semantic similarity step is the same bge-base used in
retrieval (consistent + already cached locally).

Usage (db on localhost:5432, GROQ_API_KEY in env):
    POSTGRES_HOST=localhost POSTGRES_PORT_INTERNAL=5432 \\
        python -m evals.rag.ragas_eval [--out reports/ragas.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import threading
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ragas_eval")

GOLDEN_PATH = Path("evals/rag/golden.jsonl")

# Provider config — mirrors app/infra/llm_groq.py but resolves the API key
# from env so we don't need Vault reachable from the host. Switch with
# LLM_PROVIDER=groq|openrouter; default groq. LLM_MODEL overrides the model.
_PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "default_model": "nvidia/nemotron-3-super-120b-a12b:free",
    },
}

ANSWER_PROMPT = (
    "You are answering a pandas question grounded in the supplied passages. "
    "Use ONLY the passages; do not invent facts. Cite each fact with the "
    "passage number it came from (e.g. [1]). 3-6 sentences. No preamble.\n\n"
    "Question: {question}\n\nPassages:\n{passages}\n\nAnswer:"
)


def _format_passages(chunks: list[Any]) -> str:
    out = []
    for i, c in enumerate(chunks, 1):
        out.append(f"[{i}] {c.text}")
    return "\n\n".join(out)


def _provider_config() -> tuple[str, str, str, str]:
    """Returns (name, base_url, api_key, model)."""
    name = (os.environ.get("LLM_PROVIDER") or "groq").strip().lower()
    cfg = _PROVIDERS.get(name)
    if cfg is None:
        raise RuntimeError(f"unknown LLM_PROVIDER={name!r}; expected one of {list(_PROVIDERS)}")
    key = os.environ.get(cfg["env_key"]) or ""
    if not key or key == "placeholder":
        raise RuntimeError(
            f"{cfg['env_key']} env required for RAGAS eval under LLM_PROVIDER={name}"
        )
    model = os.environ.get("LLM_MODEL") or cfg["default_model"]
    return name, cfg["base_url"], key, model


class _BudgetExceeded(RuntimeError):
    """Raised by the httpx request hook once the LLM request budget is spent."""


class _RequestBudget:
    """Process-wide cap on *quota-consuming* LLM requests.

    Only HTTP 2xx responses consume provider quota — a 429 (rate-limited) call
    is rejected before the model runs, so it costs nothing and must not count.
    The request hook aborts once `limit` successful calls are on the books; the
    response hook is what records a success. Retrying through rate limits is
    therefore free and never drains the budget.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.ok = 0  # successful (2xx) requests — the real quota meter
        self.attempts = 0  # every request sent, including 429s and retries
        self._lock = threading.Lock()

    def check(self) -> None:
        """Pre-request: abort once the success cap is reached."""
        with self._lock:
            if self.ok >= self.limit:
                raise _BudgetExceeded(
                    f"LLM request budget of {self.limit} successful calls "
                    "reached — aborting before spending more provider quota"
                )
            self.attempts += 1

    def record(self, status_code: int) -> None:
        """Post-response: count only quota-consuming 2xx calls."""
        if 200 <= status_code < 300:
            with self._lock:
                self.ok += 1


def _budgeted_http_clients(budget: _RequestBudget) -> tuple[Any, Any]:
    """(sync, async) httpx clients whose hooks enforce `budget`."""
    import httpx

    def _req(_request: Any) -> None:
        budget.check()

    def _resp(response: Any) -> None:
        budget.record(response.status_code)

    async def _areq(_request: Any) -> None:
        budget.check()

    async def _aresp(response: Any) -> None:
        budget.record(response.status_code)

    timeout = httpx.Timeout(180.0, connect=15.0)
    sync = httpx.Client(timeout=timeout, event_hooks={"request": [_req], "response": [_resp]})
    asyncc = httpx.AsyncClient(
        timeout=timeout, event_hooks={"request": [_areq], "response": [_aresp]}
    )
    return sync, asyncc


class _BGEEmbeddings:
    """LangChain-shaped embeddings backed by sentence-transformers bge-base.

    RAGAS LangchainEmbeddingsWrapper expects an object with embed_documents
    and embed_query; we don't need the rest of the LangChain Embeddings ABC.
    """

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        cache_dir = os.environ.get("MC_MODEL_CACHE", str(Path.home() / ".cache" / "mc-models"))
        self._m = SentenceTransformer("BAAI/bge-base-en-v1.5", device="cpu", cache_folder=cache_dir)
        self._m.max_seq_length = 512

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        v = self._m.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return v.tolist()

    def embed_query(self, text: str) -> list[float]:
        prefixed = "Represent this sentence for searching relevant passages: " + text
        v = self._m.encode([prefixed], normalize_embeddings=True, show_progress_bar=False)
        return v[0].tolist()


async def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default=GOLDEN_PATH, type=Path)
    parser.add_argument("--out", default=Path("reports/ragas.json"), type=Path)
    parser.add_argument("--limit", type=int, default=None, help="cap questions for debug runs")
    parser.add_argument(
        "--answers-cache",
        default=Path("reports/ragas_answers.json"),
        type=Path,
        help="Checkpoint file for question/answer/contexts triples; skips answer-gen if present",
    )
    parser.add_argument(
        "--skip-gen",
        action="store_true",
        help="Skip answer-gen and load from --answers-cache (re-run scoring only)",
    )
    parser.add_argument(
        "--request-budget",
        type=int,
        default=int(os.environ.get("RAGAS_REQUEST_BUDGET", "25") or 25),
        help="Cap on successful (quota-consuming) LLM calls; 429s and retries "
        "are free. Calls past the cap abort without spending more quota.",
    )
    parser.add_argument(
        "--indices",
        default=None,
        help="Comma-separated golden indices to score (stratified subset). Overrides --limit.",
    )
    args = parser.parse_args()

    provider_name, base_url, api_key, model = _provider_config()
    log.info("provider=%s model=%s", provider_name, model)

    if not args.golden.exists():
        log.error("missing golden: %s", args.golden)
        return 1

    golden = [
        json.loads(line)
        for line in args.golden.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    golden = [g for g in golden if g.get("ground_truth_chunk_ids")]
    if args.indices:
        picked = [int(x) for x in args.indices.split(",") if x.strip()]
        golden = [golden[i] for i in picked]
        log.info("selected golden indices %s", picked)
    elif args.limit:
        golden = golden[: args.limit]
    log.info("loaded %d golden records", len(golden))

    budget = _RequestBudget(args.request_budget)
    sync_http, async_http = _budgeted_http_clients(budget)
    log.info("request budget: %d successful LLM calls (429s/retries free)", budget.limit)

    if args.skip_gen:
        if not args.answers_cache.exists():
            log.error("--skip-gen set but %s missing", args.answers_cache)
            return 1
        rows = json.loads(args.answers_cache.read_text(encoding="utf-8"))
        log.info("loaded %d cached answers from %s", len(rows), args.answers_cache)
    else:
        # Local model-server replacement so rerank works without the container.
        from evals.rag.run import _LocalModelServer
        from app.services.rag import RAGService
        from openai import OpenAI

        rag = RAGService(model_server=_LocalModelServer())
        llm_client = OpenAI(api_key=api_key, base_url=base_url, http_client=sync_http)

        rows: list[dict] = []
        try:
            for i, g in enumerate(golden, 1):
                q = g["question"]
                log.info("[%d/%d] %s", i, len(golden), q[:60])
                hits = await rag.retrieve(q, top_k=5, stack="full")
                contexts = [h.text for h in hits]
                prompt = ANSWER_PROMPT.format(question=q, passages=_format_passages(hits))
                comp = llm_client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                )
                answer = (comp.choices[0].message.content or "").strip()
                rows.append(
                    {
                        "question": q,
                        "answer": answer,
                        "contexts": contexts,
                        "ground_truth": g.get("ideal_answer", ""),
                    }
                )
        except _BudgetExceeded as e:
            log.warning(
                "answer-gen stopped early: %s (%d/%d generated)",
                e,
                len(rows),
                len(golden),
            )
        # Checkpoint after answer-gen so scoring can be retried without
        # re-burning provider quota on the LLM completions.
        args.answers_cache.parent.mkdir(parents=True, exist_ok=True)
        args.answers_cache.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        log.info("checkpointed %d answers -> %s", len(rows), args.answers_cache)

    # Hand the (question, answer, contexts) triples to RAGAS for scoring.
    log.info("scoring with RAGAS (%s judge, temp=0)...", provider_name)
    from datasets import Dataset
    from langchain_openai import ChatOpenAI
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, faithfulness
    from ragas.run_config import RunConfig

    ds = Dataset.from_list(
        [
            {
                "question": r["question"],
                "answer": r["answer"],
                "contexts": r["contexts"],
                "ground_truth": r["ground_truth"],
            }
            for r in rows
        ]
    )
    # ChatOpenAI handles both Groq and OpenRouter (both OpenAI-compatible).
    # Same budgeted httpx clients as answer-gen so the judge shares the cap.
    judge = LangchainLLMWrapper(
        ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.0,
            http_client=sync_http,
            http_async_client=async_http,
        )
    )
    emb = LangchainEmbeddingsWrapper(_BGEEmbeddings())
    # Free-tier providers (Groq, OpenRouter) get burst-throttled by RAGAS's
    # default max_workers=16. Drop to 3 to stay under typical RPM caps;
    # override via RAGAS_MAX_WORKERS env if you have paid quota.
    max_workers = int(os.environ.get("RAGAS_MAX_WORKERS", "3"))
    max_retries = int(os.environ.get("RAGAS_MAX_RETRIES", "1"))
    log.info("ragas max_workers=%d max_retries=%d", max_workers, max_retries)
    # raise_exceptions=False: a budget-blocked row becomes NaN instead of
    # killing the whole run, so the aggregate covers whatever did score.
    result = evaluate(
        dataset=ds,
        metrics=[faithfulness, answer_relevancy],
        llm=judge,
        embeddings=emb,
        run_config=RunConfig(max_workers=max_workers, timeout=180, max_retries=max_retries),
        raise_exceptions=False,
    )
    df = result.to_pandas()

    def _num(x: Any) -> float | None:
        """NaN -> None so json.dumps stays strict-valid (no bare NaN tokens)."""
        v = float(x)
        return None if math.isnan(v) else v

    aggregate = {
        "faithfulness": _num(df["faithfulness"].mean()),
        "answer_relevancy": _num(df["answer_relevancy"].mean()),
    }
    n_scored = int(df["faithfulness"].notna().sum())
    per_question = []
    for r, (_, row) in zip(rows, df.iterrows()):
        per_question.append(
            {
                "question": r["question"],
                "answer": r["answer"],
                "ground_truth": r["ground_truth"],
                "contexts": r["contexts"],
                "faithfulness": _num(row["faithfulness"]),
                "answer_relevancy": _num(row["answer_relevancy"]),
            }
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "stack": "full",
                "n": len(rows),
                "n_scored": n_scored,
                "request_budget": budget.limit,
                "requests_spent": budget.ok,
                "requests_attempted": budget.attempts,
                "aggregate": aggregate,
                "per_question": per_question,
            },
            indent=2,
        )
    )
    fth, rel = aggregate["faithfulness"], aggregate["answer_relevancy"]
    log.info("=" * 50)
    log.info("faithfulness:     %s", f"{fth:.3f}" if fth is not None else "n/a")
    log.info("answer_relevancy: %s", f"{rel:.3f}" if rel is not None else "n/a")
    log.info("scored:           %d/%d questions", n_scored, len(rows))
    log.info(
        "requests spent:   %d ok / %d budget  (%d attempts incl. retries)",
        budget.ok,
        budget.limit,
        budget.attempts,
    )
    log.info("report:           %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
