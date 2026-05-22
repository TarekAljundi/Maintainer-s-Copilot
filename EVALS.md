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

**Slice 16 re-run (2026-05-21):** all 4 stacks above replicated identically on
the current fixed code path. The slice-07 numbers are the same numbers on the
post-fix reranker — no separate "fixed-stack" column needed. Per-question
breakdowns at `reports/rag_eval_{naive,hybrid,hybrid_rerank,full}.json`.

Each ingredient earns its place: hybrid RRF lifts MRR by tightening the rank of GT hits already in the candidate set; rerank surfaces 1 additional GT hit (hit@5 0.440→0.480); HyDE+parent jumps docs hit@5 from 0.444 to 0.556 by paraphrasing the user question into a doc-shaped passage that better matches the chunk language.

**Observed trade-off:** issue hit@5 regresses (0.714 naive → 0.429 full). HyDE's hypothetical passage is docs-shaped, biasing dense search toward documentation; the reranker can't fully recover. Mitigations belong in a future slice — either route to a dedicated issue-aware HyDE prompt when `content_types=['issue']` is set by the LLM, or skip HyDE when the sparse side has a high-confidence match.

### Embedder ablation (slice 07, single axis)

| Model | dim | Stack | hit@5 | MRR@10 |
|---|---:|---|---:|---:|
| BAAI/bge-base-en-v1.5  | 768 | naive | 0.440 | 0.248 |
| BAAI/bge-small-en-v1.5 | 384 | naive | 0.440 | **0.322** |

bge-small matches bge-base on hit@5 and beats it on MRR by +0.074 — at our corpus scale (~2.6k child chunks) the bge-base capacity advantage doesn't translate. Production stays on **bge-base** because the full stack (bge-base + reranker + HyDE + parent) reaches MRR@10=0.328 — above bge-small naive — and the embedder is shared with the memory service (slice 11) which needs the longer-range vectors. Logged as a follow-up: revisit bge-small if the chatbot is ever deployed under a CPU-only constraint.

### Generation eval (RAGAS)

RAGAS 0.2.5 pinned. Embedder for the answer_relevancy semantic step = bge-base
(same as retrieval). Judge provider selectable at runtime via `LLM_PROVIDER`
env var (groq | openrouter); both go through `evals/rag/ragas_eval.py` against
an OpenAI-compatible client.

| Stack | n | faithfulness | answer_relevancy | Source |
|---|---:|---:|---:|---|
| full | 3 (smoke) | 0.89 (n=1; 2 NaN) | 0.89 | `reports/ragas_smoke.json` |
| full | 5 (subset) | **0.955** (n=4) | **0.846** (n=5) | `reports/ragas.json` |

The 3-question smoke run (slice 07) validated the pipeline end-to-end. A full
25-Q run does not fit a free-tier daily quota: RAGAS spends ~3 judge calls per
metric per question, so 25 questions need ~75-100 LLM calls — above both the
Groq 100k-token/day and the OpenRouter 50-request/day free caps.

**Quota-capped subset run (2026-05-22).** `ragas_eval.py` gained a hard
request-budget guard (`--request-budget`, default 25): a shared httpx hook
counts only *successful* (HTTP 2xx) judge calls — a 429 is the rate limiter
rejecting the request before the model runs, so it consumes no quota and is
not counted — and aborts once the cap is reached. The run scores a stratified
5-question subset (`--indices 0,4,12,15,18` — 3 docs + 2 issue questions) on
the full stack:

- faithfulness **0.955** (n=4; the 5th question's faithfulness job reached
  the 24th of 25 budgeted calls before it could finish).
- answer_relevancy **0.846** (n=5, all scored).
- Judge = Groq llama-3.3-70b, temp=0. Answers were generated once via
  OpenRouter Nemotron and cached (`reports/ragas_answers.json`); the scoring
  run re-uses them with `--skip-gen`, so re-scoring spends zero answer-gen
  quota. Serial scoring (`RAGAS_MAX_WORKERS=1`) avoids the per-minute token
  bursts that 429-stormed earlier parallel attempts — the final run logged 24
  successful calls across 43 attempts (the 19 extra were free 429 retries).

**To run the full 25-Q version** (needs paid quota or a higher free tier):
```
LLM_PROVIDER=groq RAGAS_MAX_WORKERS=1 \
    POSTGRES_HOST=localhost POSTGRES_PORT_INTERNAL=5432 \
    python -m evals.rag.ragas_eval --request-budget 100 --out reports/ragas.json
```
With answers already cached in `reports/ragas_answers.json`, add `--skip-gen`
to re-score only (no answer-gen quota spent).

### Spearman ρ — judge calibration

Hand-label form: `evals/rag/hand_labels.csv` — 5 questions (Q1 CSV-na-values,
Q3 fillna/dropna, Q12 Excel, Q17 docstring policy, Q23 observed groupby) with
AI-recommended Likert scores already filled (scores: 5, 4, 2, 5, 2 — captured
during slice 07). `python -m evals.rag.spearman` computes ρ against
`reports/ragas.json` answer_relevancy.

Status: **not computable from the 2026-05-22 subset run.** The hand-labeled
questions are golden indices 0, 2, 11, 16, 22; the quota-capped RAGAS subset
was indices 0, 4, 12, 15, 18 — they overlap in only one question, and a rank
correlation needs RAGAS `answer_relevancy` and hand-labels on the *same*
question set. To compute ρ, re-score the aligned subset with
`ragas_eval.py --indices 0,2,11,16,22` (no `--skip-gen` — answers for the new
indices must be generated), then run `python -m evals.rag.spearman`. Decision
rule when it lands: if post-calibration ρ < 0.6, `answer_relevancy` is demoted
to `advisory: true` in `eval_thresholds.yaml` per the judge disagreement
protocol; if 0.6 ≤ ρ < 0.8 the judge stays gating but the EVALS.md narrative
flags the lower confidence.
