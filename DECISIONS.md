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
- Fine-tuned: `microsoft/deberta-v3-small`, full FT + discriminative LR (encoder 2e-5, head 1e-4). Macro-F1 = TBD.
- Classical: TF-IDF (word 1-2 + char 3-5) + LogReg balanced. Macro-F1 = TBD.
- LLM baseline: Groq `llama-3.3-70b-versatile`, 4-shot, temp=0, tool_use. Macro-F1 = TBD.
- Deployment: TBD (filled after eval).

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
