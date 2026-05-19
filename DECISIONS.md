# Decisions

Every decision backed by a number on the project's golden set. Numbers filled as evals land.

## Dataset
- Repo: `fastapi/fastapi` closed issues since 2020-01-01 (renamed from `tiangolo/fastapi`; both names redirect to the same GitHub repository id `160919119`).
- Label mapping: strict + tiebreak `bug > feature > docs > question`. Workflow labels (`answered`, `reviewed`) and component labels (`security`, `dependencies`) are ignored for classification. Unlabeled excluded from train/val/test (kept in `unlabeled.jsonl` for hand-curated golden sets).
- Splits (time-stratified by `closed_at` asc): 70% train, 10% val, 15% test, 5% RAG held-out.
- RAG held-out = newest 5% intersected with (has maintainer comment OR `answered` label).

### Dataset v1 manifest
- Pulled `2026-05-19` at git SHA `55dcc6d` (slice 01 head; before this slice landed).
- MinIO: `s3://mc-evals/datasets/fastapi-issues/v1/{train,val,test,rag_holdout,unlabeled}.jsonl` + `manifest.json`.
- Counts: train=1952, val=279, test=418, rag_holdout=96, unlabeled=149. Per-class is heavily `question`-skewed; `docs` is sparse (n=0 in train/val/test, n=4 in rag_holdout) — slice 03 must handle this (drop class, oversample, or stratified-by-class then time-ordered within class).
- Verify locally: `python -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" data/splits/train.jsonl` then diff against `manifest.json`.

## Classifier
- Fine-tuned: `microsoft/deberta-v3-small`, full FT + discriminative LR (encoder 2e-5, head 1e-4). + class-weighted cross-entropy (inverse frequency, mean-normalized). bf16, gradient checkpointing, bsz 8 + grad-accum 2 (eff 16). Test macro-F1 = **0.328** (acc 0.967). Val macro-F1 = 0.499.
- Classical: TF-IDF (word 1-2 + char 3-5) + LogReg balanced. Macro-F1 = TBD.
- LLM baseline: Groq `llama-3.3-70b-versatile`, 4-shot, temp=0, tool_use. Macro-F1 = TBD.
- Deployment: TBD (filled after eval).

### `docs` label is structurally sparse in the issue stream
On the fastapi/fastapi corpus the `docs` label appears **772 times across all records but only on 11 closed issues** (the other 761 are pull requests, filtered out). After the bug > feature > docs > question tie-break, 7 of those 11 are taken by `feature`, leaving **4 final `docs` records** — all of which end up in the RAG held-out slice (newest 5%).
- Implication: the classifier has zero training examples for `docs` and zero test examples to score it on. Per-class F1 for docs is undefined.
- Mitigations considered and rejected: (a) include docs PRs → violates "closed issues" AC; (b) drop docs class to make a 3-class model → deviates from PRD's 4-class spec; (c) heuristic regex on title/body for "documentation"/"readme" → fragile, adds bias.
- Accepted: keep the 4-class vocabulary, document the corpus reality. Slice 04 baselines will face the same constraint.

### Class-weighted loss (slice 03)
Added inverse-frequency class weighting to the training-time CrossEntropy because the corpus is 97% `question`. Without it the model collapsed to "always predict question." Weights help val macro-F1 (0 → 0.499) but **don't transfer to test macro-F1** (still 0.328) — the test minority classes (1 bug, 13 features) need stronger generalization than the 38-39 training examples per class allow. The intervention is architecturally correct; the data ceiling remains.

## RAG
- Embedding: `BAAI/bge-base-en-v1.5` vs `bge-small-en-v1.5` ablation. hit@5 = TBD vs TBD.
- Chunking: content-aware dual (header-recursive docs, per-comment issues) + parent-document.
- Hybrid: Postgres FTS + dense, weighted RRF (k=60). Tuned weights = TBD.
- Reranker: `BAAI/bge-reranker-base`.
- Query xform: HyDE on dense side.
- Metadata filters: content_type, labels, is_answer, breadcrumb_prefix, post-filter w/ over-fetch.

## RAG numbers (TBD)
| Stack | hit@5 | MRR@10 | faithfulness | answer_relevancy |
|---|---|---|---|---|
| Naive (fixed-512 + dense) | | | n/a | n/a |
| + Hybrid (FTS + RRF) | | | n/a | n/a |
| + Rerank | | | n/a | n/a |
| + HyDE + parent-doc | | | | |

## Chatbot
- LLM: Groq `llama-3.3-70b-versatile`. All slots.
- Tools: classify_issue, extract_entities, summarize_thread, search_knowledge, write_memory.
- Memory: episodic in pgvector. Auto-recall on turn start. Explicit `write_memory` tool only.
- Redis TTLs: conv 24h sliding, tool 1h, embed 24h, RL 60s.
- Streaming: SSE via fetch-event-source.

## Observability
- Tracing: Langfuse v2 self-host. Session = conversation. Generation/tool/retrieval span types.
- Redaction: vendor-prefixed token regexes + email + URL creds + user paths. 3-boundary hookup.

## Widget
- Stack: Preact + preact/compat + Tailwind + marked + fetch-event-source.
- Bundle target: ~35 KB gzipped. Actual = TBD.
- Embed: loader.js -> iframe -> /widget/{id}/embed w/ CSP frame-ancestors from DB.
