# Decisions

Every decision backed by a number on the project's golden set. Numbers filled as evals land.

## Dataset
- Repo: `pandas-dev/pandas` closed issues since 2020-01-01. **Swapped from `fastapi/fastapi`** mid-project for materially better class balance + ~10× more labeled records — see §"Why we swapped the corpus" below.
- Label mapping: `LABEL_MAP` in `scripts/pull_dataset.py` translates raw pandas labels (`Bug`, `Enhancement`, `Docs`/`Documentation`, `Usage Question`) into the canonical 4 classes, then the tiebreak `bug > feature > docs > question` resolves multi-label issues (most actionable wins). Labels not in the map (workflow/component/subcomponent, e.g. `Needs Discussion`, `Performance`, `IO`, `Indexing`, `good first issue`) are ignored for classification. Unlabeled excluded from train/val/test (kept in `unlabeled.jsonl` for hand-curated golden sets).
- Splits (time-stratified by `closed_at` asc): 70% train, 10% val, 15% test, 5% RAG held-out.
- RAG held-out = newest 5% intersected with maintainer-association comment present. (The `answered` workflow label was a fastapi convention; pandas doesn't use it.)

### Why we swapped the corpus
- The fastapi pull (slice 02 v1) produced train=1952, val=279, test=418, rag_holdout=96 — but 97% of labeled records were `question` (train had 0 `docs`, 38 `bug`, 39 `feature`). Class-weighted training (slice 03) lifted val macro-F1 from 0 → 0.499 but test macro-F1 stalled at 0.328 because there were only 1 test bug + 13 test features to score against. Slice 04 baselines surfaced the same data ceiling: deberta collapsed to "always predict question" on the golden set.
- pandas has 4 cleanly-labeled canonical classes (`Bug`, `Enhancement`, `Docs`, `Usage Question`) applied to thousands of closed issues since 2020 → expected ~10× the labeled volume with non-zero per-class support across all four classes.
- The framework (training script, baselines orchestrator, NER + summarizer wiring) is corpus-agnostic; only artifacts (splits, classifier weights, golden sets, recorded numbers) needed rebuilding.

### Dataset v2 manifest (pandas)
- Pulled `2026-05-20` at git SHA `0c6de2e` (slice 05 head; corpus-swap-to-pandas branch from there).
- Pull strategy: `scripts/pull_dataset.py` uses the GitHub Search API with monthly `created:` windows (`is:issue is:closed`), one cache file per month under `data/raw/issues_window_YYYY-MM.json`. The legacy `/issues` endpoint capped pagination at ~10k items per query and ~85% of those were PRs (waste); the search-API windowed pull bypasses both problems and yields ~5× the labeled records over a single month-floor pull.
- MinIO: `s3://mc-evals/datasets/pandas-issues/v1/{train,val,test,rag_holdout,unlabeled}.jsonl` + `manifest.json` (uploaded after retrain + rebaseline land in this PR).
- Counts: total labeled = **6,553** (vs fastapi v1 = 2,745), unlabeled = 2,835.

| split | n | bug | feature | docs | question |
|---|---:|---:|---:|---:|---:|
| train | 4,638 | 2,746 | 693 | 681 | 518 |
| val   |   662 |   360 | 157 | 116 |  29 |
| test  |   993 |   616 | 163 | 198 |  16 |
| rag_holdout | 260 | 140 | 56 | 54 | 10 |

All four classes have material train + test support (vs fastapi where train had 0 docs and test had 1 bug, 13 feature, 0 docs). `question` is the smallest class but present in every split — classification golden can be drawn 4-class.

- Verify locally: `python -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" data/splits/train.jsonl` then diff against `manifest.json`.

## Classifier
- Fine-tuned: `microsoft/deberta-v3-small`, full FT + discriminative LR (encoder 2e-5, head 1e-4) + class-weighted cross-entropy (inverse frequency, mean-normalized). bf16, gradient checkpointing, bsz 8 + grad-accum 2 (eff 16). Trained 3 epochs (early-stop on val macro-F1, patience 1). Val macro-F1 best = **0.844** (epoch 2). Test macro-F1 = **0.896**, accuracy = **0.952**. Wall time: 612s on RTX 4060. Weights SHA-256: `f4a5c67f8e72119a97270bb7a93a3657bcf9ee3db73cbe4f74123636261a0975` (pinned in `app/infra/_classifier_registry.py`).
- Per-class test F1: bug=0.969 (n=616), feature=0.948 (n=163), docs=0.917 (n=198), question=0.750 (n=16). Question is the weakest cell — only 16 test records, small-sample variance — but the model is actually predicting it, not collapsing.
- Class weights at train start: bug=0.28 (majority, down-weighted), feature=1.11, docs=1.13, question=1.48 (minority, up-weighted). Healthy distribution — vs fastapi v1 where the inverse weighting amplified the 97%-question prior unevenly.
- Classical: TF-IDF (word 1-2 + char_wb 3-5) + LogReg balanced, `C` tuned on val via 5-point grid. Same splits as deberta. Numbers refreshed when slice-04-equivalent rerun lands in this PR.
- LLM baseline: Groq `llama-3.3-70b-versatile`, 4-shot, temp=0, tool_use (single tool `classify_issue`, label enum forced). `docs` few-shot drawn from train (no longer synthesized — pandas has 681 docs records in train).
- Deployment: **deberta** (PRD-locked, boot check #5 SHA-pins the artifact). One-line defense filled after baselines rerun.

### Three-model comparison (slice 04 re-run on pandas v2)

Golden set: 25 hand-curated records sampled from `rag_holdout.jsonl` (stratified 7/7/4/7 across bug/feature/docs/question, seed=42), separate from the time-stratified test split. Latency is per-record wall-clock; cost from Groq's posted llama-3.3-70b-versatile rate ($0.59/1M in, $0.79/1M out, retrieved 2026-05-19).

| Model | Accuracy | Macro-F1 | F1 bug | F1 feature | F1 docs | F1 question | p50 ms | p99 ms | $/1k |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deberta (deployed) | 0.760 | 0.779 | 0.700 | 0.833 | 0.857 | 0.727 |  237.2 |  379.4 | $0.00 |
| classical          | 0.760 | 0.779 | 0.700 | 0.833 | 0.857 | 0.727 |    4.4 |    5.5 | $0.00 |
| llm (Groq 4-shot)  | 0.800 | 0.815 | 0.737 | 0.833 | 0.857 | 0.833 | 7476.3 |14825.5 | $1.11 |

Confusion matrix — **deployed (deberta)**, rows = true, cols = pred, order = [bug, feature, docs, question]:
```
                pred:
                bug  feature  docs  question
true bug         7       0      0       0
true feature     2       5      0       0
true docs        1       0      3       0
true question    3       0      0       4
```
All three models cleanly classify `bug` (7/7) and tie on `feature` (5/7, the 2 misses go to `bug`) and `docs` (3/4, 1 miss to `bug`). Only `question` separates them: deberta + classical get 4/7, llm gets 5/7. The 3-3 misclassifications all come back as `bug` — likely because pandas users label "BUG:"-prefixed titles that are actually usage questions (the golden review noted this).

**Defense (one line):** **deberta deployed** — macro-F1 = 0.779, only 0.036 behind llm (0.815), with 31× lower p50 latency (237ms vs 7.5s) and zero per-prediction cost; classical matches deberta's F1 exactly (literally same predictions on this golden) but lacks the model card + SHA pin the boot check enforces, and is in the comparison only as the brief's mandatory classical baseline.

### Historical: fastapi v1 (pre-corpus-swap, kept for narrative)

The original fastapi pull surfaced a data ceiling that motivated the swap.
- **Trained classifier** (slice 03, fastapi): test macro-F1 = 0.328, acc = 0.967; val macro-F1 = 0.499. Class-weighted CE lifted val from 0 → 0.499 but failed to transfer to test because the test minority classes had n=1 bug, n=13 feature, n=0 docs.
- **`docs` sparsity** (fastapi): `docs` appeared on 772 records but 761 were PRs (filtered); after the bug > feature > docs > question tiebreak, 7 of the remaining 11 were taken by `feature`, leaving 4 final docs records — all in the RAG held-out slice. Classifier had zero training examples for docs.
- **Baselines on fastapi golden** (n=25, drawn from fastapi rag_holdout):

  | Model | Accuracy | Macro-F1 | p50 ms | p99 ms | $/1k |
  |---|---:|---:|---:|---:|---:|
  | deberta (deployed) | 0.280 | 0.109 |  250.0 |  325.5 | $0.00 |
  | classical          | 0.320 | 0.203 |    3.6 |    9.8 | $0.00 |
  | llm (Groq 4-shot)  | 0.560 | 0.459 | 8488.2 |15219.9 | $1.10 |
  
  deberta collapsed to "always predict question" on golden — matched the 97% question prior in the time-stratified train split, not the golden's balanced label distribution. This collapse is the headline reason the corpus was swapped.

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
