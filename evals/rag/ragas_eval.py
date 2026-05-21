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
import os
import sys
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
    if args.limit:
        golden = golden[: args.limit]
    log.info("loaded %d golden records", len(golden))

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
        llm_client = OpenAI(api_key=api_key, base_url=base_url)

        rows: list[dict] = []
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
    judge = LangchainLLMWrapper(
        ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=0.0)
    )
    emb = LangchainEmbeddingsWrapper(_BGEEmbeddings())
    # Free-tier providers (Groq, OpenRouter) get burst-throttled by RAGAS's
    # default max_workers=16. Drop to 3 to stay under typical RPM caps;
    # override via RAGAS_MAX_WORKERS env if you have paid quota.
    max_workers = int(os.environ.get("RAGAS_MAX_WORKERS", "3"))
    log.info("ragas max_workers=%d", max_workers)
    result = evaluate(
        dataset=ds,
        metrics=[faithfulness, answer_relevancy],
        llm=judge,
        embeddings=emb,
        run_config=RunConfig(max_workers=max_workers, timeout=180),
    )
    df = result.to_pandas()

    aggregate = {
        "faithfulness": float(df["faithfulness"].mean()),
        "answer_relevancy": float(df["answer_relevancy"].mean()),
    }
    per_question = []
    for r, (_, row) in zip(rows, df.iterrows()):
        per_question.append(
            {
                "question": r["question"],
                "answer": r["answer"],
                "ground_truth": r["ground_truth"],
                "contexts": r["contexts"],
                "faithfulness": float(row["faithfulness"]),
                "answer_relevancy": float(row["answer_relevancy"]),
            }
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "stack": "full",
                "n": len(rows),
                "aggregate": aggregate,
                "per_question": per_question,
            },
            indent=2,
        )
    )
    log.info("=" * 50)
    log.info("faithfulness:     %.3f", aggregate["faithfulness"])
    log.info("answer_relevancy: %.3f", aggregate["answer_relevancy"])
    log.info("report:           %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
