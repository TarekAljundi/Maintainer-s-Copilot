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
MODEL = "llama-3.3-70b-versatile"

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


def _groq_key() -> str:
    k = os.environ.get("GROQ_API_KEY") or ""
    if not k:
        raise RuntimeError("GROQ_API_KEY env required for RAGAS eval")
    return k


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
    args = parser.parse_args()

    _groq_key()  # fail fast if missing

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

    # Local model-server replacement so rerank works without the container.
    from evals.rag.run import _LocalModelServer
    from app.services.rag import RAGService
    from groq import Groq

    rag = RAGService(model_server=_LocalModelServer())
    groq_client = Groq(api_key=_groq_key())

    rows: list[dict] = []
    for i, g in enumerate(golden, 1):
        q = g["question"]
        log.info("[%d/%d] %s", i, len(golden), q[:60])
        hits = await rag.retrieve(q, top_k=5, stack="full")
        contexts = [h.text for h in hits]
        prompt = ANSWER_PROMPT.format(question=q, passages=_format_passages(hits))
        comp = groq_client.chat.completions.create(
            model=MODEL,
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

    # Hand the (question, answer, contexts) triples to RAGAS for scoring.
    log.info("scoring with RAGAS (Groq judge, temp=0)...")
    from datasets import Dataset
    from langchain_groq import ChatGroq
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, faithfulness

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
    judge = LangchainLLMWrapper(ChatGroq(model=MODEL, temperature=0.0, api_key=_groq_key()))
    emb = LangchainEmbeddingsWrapper(_BGEEmbeddings())
    result = evaluate(
        dataset=ds,
        metrics=[faithfulness, answer_relevancy],
        llm=judge,
        embeddings=emb,
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
