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

### RAG retrieval (slice 06 baseline)

Golden set: 25 hand-curated `(question, ideal_answer, ground_truth_chunk_ids)` triples at `evals/rag/golden.jsonl` — 18 docs + 7 issue questions. GT chunk IDs resolved deterministically from `evals/rag/drafts.jsonl` filters via Postgres FTS (see `scripts/build_rag_golden.py --mode auto`). 1-2 GT chunks per question.

Corpus: pandas-dev/pandas @ `d2dc148a71b44ff76431e2c2e249bf453bea7254` (1416 docs chunks from `getting_started + user_guide + development`) + 260 closed pandas issues from `rag_holdout.jsonl` (1205 issue chunks). Total 2621 chunks indexed under HNSW (m=16, ef_construction=64), bge-base-en-v1.5, 768-d.

| Stack | n | hit@5 | MRR@10 | docs hit@5 (n=18) | issue hit@5 (n=7) |
|---|---:|---:|---:|---:|---:|
| naive (dense only) | 25 | **0.440** | **0.248** | 0.333 | 0.714 |
| + Hybrid (FTS + RRF) | | TBD | TBD | TBD | TBD |
| + Rerank | | TBD | TBD | TBD | TBD |
| + HyDE + parent-doc | | TBD | TBD | TBD | TBD |

Issues outperform docs at the floor because issue questions reference the maintainer answer directly (high lexical overlap; bge is asymmetric-trained for this shape). Docs questions are paraphrased lookups against multi-chunk sections — slice 07's hybrid + rerank should help most here.

### Generation eval (RAGAS)

Lands with slice 13.
