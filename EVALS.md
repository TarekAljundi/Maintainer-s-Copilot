# Evals

## Classification
- 25 hand-curated issues, separate from test split.
- Macro-F1, per-class F1, confusion matrix.
- Run against all 3 models.

## RAG
- 25 (question, ideal_answer, ground_truth_chunk_ids) triples.
- Retrieval: hit@5, MRR@10 (deterministic, set intersection / rank arithmetic).
- Generation: RAGAS w/ Groq Llama 3.3 70B judge, temp=0, version pinned.
- Hand-label 5/25 on 1-5 Likert. Spearman ρ between hand + judge reported.

## Judge disagreement protocol
If post-calibration Spearman ρ < 0.6 -> demote metric to advisory (not gating) in `eval_thresholds.yaml`.

## CI gate semantics
For each metric: PASS iff `current >= floor AND current >= baseline - margin`.
Baseline lives at `s3://mc-evals/main/latest.json`.

## Numbers

### RAG retrieval — cumulative ingredient table (slice 07)

Golden set: 25 hand-curated `(question, ideal_answer, ground_truth_chunk_ids)` triples at `evals/rag/golden.jsonl` — 18 docs + 7 issue questions.

Corpus: pandas-dev/pandas @ `d2dc148a71b44ff76431e2c2e249bf453bea7254`. Slice 07 re-ingest produced **2,901 chunks** (1,696 docs incl. 280 parents + 1,205 issue) indexed under HNSW (m=16, ef_construction=64), bge-base-en-v1.5, 768-d. The 7-pt Welsh tsvector + GIN index backs the FTS side. RRF weights tuned on the grid `{(1.0,0.5),(1.0,1.0),(1.0,1.5),(0.5,1.0)}` by MRR@10 → **winner (w_d=0.5, w_s=1.0)** (see `reports/rrf_grid.json` + DECISIONS.md).

| Stack | n | hit@5 | MRR@10 | docs hit@5 (n=18) | issue hit@5 (n=7) | Δ MRR vs naive |
|---|---:|---:|---:|---:|---:|---:|
| naive (dense only)              | 25 | 0.440 | 0.248 | 0.333 | 0.714 | — |
| + Hybrid (FTS + weighted RRF)   | 25 | 0.440 | 0.285 | 0.333 | 0.714 | +0.037 |
| + Rerank (bge-reranker-base)    | 25 | 0.480 | 0.308 | 0.444 | 0.571 | +0.060 |
| + HyDE + parent-doc (**full**)  | 25 | **0.520** | **0.328** | **0.556** | 0.429 | +0.080 |

Each ingredient earns its place: hybrid RRF lifts MRR by tightening the rank of GT hits already in the candidate set; rerank surfaces 1 additional GT hit (hit@5 0.440→0.480); HyDE+parent jumps docs hit@5 from 0.444 to 0.556 by paraphrasing the user question into a doc-shaped passage that better matches the chunk language.

**Observed trade-off:** issue hit@5 regresses (0.714 naive → 0.429 full). HyDE's hypothetical passage is docs-shaped, biasing dense search toward documentation; the reranker can't fully recover. Mitigations belong in a future slice — either route to a dedicated issue-aware HyDE prompt when `content_types=['issue']` is set by the LLM, or skip HyDE when the sparse side has a high-confidence match.

### Embedder ablation (slice 07, single axis)

| Model | dim | Stack | hit@5 | MRR@10 |
|---|---:|---|---:|---:|
| BAAI/bge-base-en-v1.5  | 768 | naive | 0.440 | 0.248 |
| BAAI/bge-small-en-v1.5 | 384 | naive | 0.440 | **0.322** |

bge-small matches bge-base on hit@5 and beats it on MRR by +0.074 — at our corpus scale (~2.6k child chunks) the bge-base capacity advantage doesn't translate. Production stays on **bge-base** because the full stack (bge-base + reranker + HyDE + parent) reaches MRR@10=0.328 — above bge-small naive — and the embedder is shared with the memory service (slice 11) which needs the longer-range vectors. Logged as a follow-up: revisit bge-small if the chatbot is ever deployed under a CPU-only constraint.

### Generation eval (RAGAS)

RAGAS 0.2.5 pinned, judge = Groq `llama-3.3-70b-versatile` at temp 0. Embedder for the answer_relevancy semantic step = bge-base (same as retrieval).

| Stack | n | faithfulness | answer_relevancy | Source |
|---|---:|---:|---:|---|
| full | 3 (smoke) | 0.89 (n=1; 2 NaN) | 0.89 | `reports/ragas_smoke.json` |
| full | 25 (target) | TBD | TBD | pending Groq TPD reset |

The 3-question smoke run validates the pipeline end-to-end. The full 25-Q run was blocked mid-execution by Groq's daily TPD cap (100k tokens/day, used up by combined classification + RAG eval work earlier in the day). Re-run after the next UTC midnight reset:

```
set -a; source .env; set +a
POSTGRES_HOST=localhost POSTGRES_PORT_INTERNAL=5432 \
    PYTHONPATH=. python -m evals.rag.ragas_eval --out reports/ragas.json
```

### Spearman ρ — judge calibration

Hand-label form: `evals/rag/hand_labels.csv` — 5 questions (Q1 CSV-na-values, Q3 fillna/dropna, Q12 Excel, Q17 docstring policy, Q23 observed groupby) with AI-recommended Likert scores and rationale. Maintainer fills the `human_score` column; `python -m evals.rag.spearman` computes ρ against `reports/ragas.json` answer_relevancy.

Status: ρ computation blocked on the full 25-Q RAGAS run completing (above). If post-calibration ρ < 0.6, `answer_relevancy` is demoted to `advisory: true` in `eval_thresholds.yaml` per the judge disagreement protocol; if 0.6 ≤ ρ < 0.8 the judge stays gating but the EVALS.md narrative flags the lower confidence.
