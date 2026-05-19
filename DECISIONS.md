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
- Classical: TF-IDF (word 1-2 + char_wb 3-5) + LogReg balanced, `C` tuned on val via 5-point grid (winner C=0.05, val macro-F1 = 0.498 — grid flat because val is 99% `question`). Test split shares deberta's. `docs` has zero train records so the classical model literally cannot emit `docs`.
- LLM baseline: Groq `llama-3.3-70b-versatile`, 4-shot, temp=0, tool_use (single tool `classify_issue`, label enum forced). `docs` few-shot example synthesized — corpus has zero `docs` records in train.
- Deployment: **deberta** (PRD-locked, boot check #5 SHA-pins the artifact). Numbers below expose the trade-off honestly.

### `docs` label is structurally sparse in the issue stream
On the fastapi/fastapi corpus the `docs` label appears **772 times across all records but only on 11 closed issues** (the other 761 are pull requests, filtered out). After the bug > feature > docs > question tie-break, 7 of those 11 are taken by `feature`, leaving **4 final `docs` records** — all of which end up in the RAG held-out slice (newest 5%).
- Implication: the classifier has zero training examples for `docs` and zero test examples to score it on. Per-class F1 for docs is undefined.
- Mitigations considered and rejected: (a) include docs PRs → violates "closed issues" AC; (b) drop docs class to make a 3-class model → deviates from PRD's 4-class spec; (c) heuristic regex on title/body for "documentation"/"readme" → fragile, adds bias.
- Accepted: keep the 4-class vocabulary, document the corpus reality. Slice 04 baselines will face the same constraint.

### Class-weighted loss (slice 03)
Added inverse-frequency class weighting to the training-time CrossEntropy because the corpus is 97% `question`. Without it the model collapsed to "always predict question." Weights help val macro-F1 (0 → 0.499) but **don't transfer to test macro-F1** (still 0.328) — the test minority classes (1 bug, 13 features) need stronger generalization than the 38-39 training examples per class allow. The intervention is architecturally correct; the data ceiling remains.

### Three-model comparison (slice 04, golden n=25)

Golden set is 25 hand-curated records sampled from `rag_holdout.jsonl` (stratified 7/7/4/7 across bug/feature/docs/question, seed=42), separate from the time-stratified test split. Each model predicts on the same 25 inputs. Latency is per-record wall-clock; cost is computed from Groq's posted llama-3.3-70b-versatile rate ($0.59/1M in, $0.79/1M out, retrieved 2026-05-19).

| Model | Accuracy | Macro-F1 | F1 bug | F1 feature | F1 docs | F1 question | p50 ms | p99 ms | $/1k |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deberta (deployed) | 0.280 | 0.109 | 0.000 | 0.000 | 0.000 | 0.438 |  250.0 |  325.5 | $0.00 |
| classical          | 0.320 | 0.203 | 0.000 | 0.400 | 0.000 | 0.414 |    3.6 |    9.8 | $0.00 |
| llm (Groq 4-shot)  | 0.560 | 0.459 | 0.636 | 0.800 | 0.400 | 0.000 | 8488.2 |15219.9 | $1.10 |

Confusion matrix — **deployed (deberta)**, rows = true, cols = pred, order = [bug, feature, docs, question]:
```
                pred:
                bug  feature  docs  question
true bug         0       0      0       7
true feature     0       0      0       7
true docs        0       0      0       4
true question    0       0      0       7
```
deberta collapses to "always predict question" on golden (matches the corpus prior the time-stratified split learned, not the golden's balanced label distribution).

**Defense (one line):** **deberta deployed** per PRD lock + boot check #5 (SHA-pinned artifact + model card + zero per-prediction cost), even though llm leads macro-F1 by +0.35 — llm's ~34× latency (8.5s vs 250ms) and $1.10/1k make it untenable as the always-on classifier; classical's 70× latency win doesn't recover the F1 gap (0.109 → 0.203). Numbers also surface the train-split prior the deberta learned: revisit weighting or an augmented `docs`/`bug`/`feature` slice before the Friday demo if budget allows (PRD §"Scope-cut priority").

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
