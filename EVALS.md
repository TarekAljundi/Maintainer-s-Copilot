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
Filled after each eval run. See latest in `reports/eval_report.json` or MinIO.
