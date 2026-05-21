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
All three models cleanly classify `bug` (7/7) and tie on `feature` (5/7, the 2 misses go to `bug`) and `docs` (3/4, 1 miss to `bug`). Only `question` separates them: deberta + classical get 4/7, llm gets 5/7. **deberta and classical produce literally identical predictions on all 25 records** — the title prefix (`BUG:`/`ENH:`/`DOC:`/`QST:`) is so dominant a feature on this slice that architecture barely matters; the 6 shared misses are 5 records with `BUG:`-prefixed titles but non-bug GitHub labels + 1 short body that lacks a prefix entirely.

### Test-split comparison (n=993) — the deciding numbers

The golden ties; the held-out test split does not. Run on the same artifact:

| Model | Accuracy | Macro-F1 | F1 bug | F1 feature | F1 docs | F1 question |
|---|---:|---:|---:|---:|---:|---:|
| deberta | **0.9517** | **0.8959** | 0.9686 | 0.9483 | 0.9167 | **0.7500** |
| classical | 0.9345 | 0.8431 | 0.9560 | 0.9028 | 0.9280 | 0.5854 |
| Δ deberta − classical | +0.017 | **+0.053** | +0.013 | +0.046 | −0.011 | **+0.165** |

**Defense (one line):** **deberta deployed** — on the n=993 held-out test split deberta beats classical by +0.053 macro-F1 (driven mostly by +0.16 F1 on the minority `question` class), beats llm on both latency (31× faster p50, 237ms vs 7.5s) and cost ($0 vs $1.11/1k), and is the only model with the SHA-pinned + model-carded artifact the PRD's boot-check #5 requires. The 25-row golden tying deberta and classical is corpus signal — pandas's title-prefix convention dominates that small slice — not evidence the fine-tune is wasted.

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
- Embedding: `BAAI/bge-base-en-v1.5` (production). Ablation: `bge-small-en-v1.5` ties on hit@5 (0.440), beats by +0.074 MRR@10 (0.322 vs 0.248). bge-base kept because the full stack (rerank+HyDE+parent) reaches MRR=0.328 — above bge-small naive — and the embedder is shared with the slice-11 memory service.
- Chunking: content-aware dual (header-recursive docs, per-comment issues) + parent-document. 1,696 docs chunks (280 parents + 1,416 children/standalone) + 1,205 issue chunks at pandas SHA `d2dc148`.
- Hybrid: Postgres FTS + dense, weighted RRF (k=60). **Tuned weights = (w_d=0.5, w_s=1.0)**, picked by MRR@10 on the grid `{(1.0,0.5),(1.0,1.0),(1.0,1.5),(0.5,1.0)}` (see `reports/rrf_grid.json`). Sparse outweighs dense on this golden because question wording overlaps lexically with both docs section bodies and maintainer-answer comments.
- Reranker: `BAAI/bge-reranker-base` cross-encoder over the fused top-20 → top-5.
- Query xform: HyDE on the dense side only (sparse keeps original query — HyDE'd passages lose keyword signal). Prompt at `prompts/hyde.md`; results cached at `data/cache/hyde/{sha256(query)}.txt`.
- Metadata filters: `content_types`, `labels`, `is_answer`, `min_closed_at`, `breadcrumb_prefix`. Post-filter with over-fetch (top-200 each side → fuse 20 → rerank 5) so HNSW isn't restricted to a tiny subset.

## RAG numbers
| Stack | hit@5 | MRR@10 | faithfulness | answer_relevancy |
|---|---:|---:|---:|---:|
| Naive (dense only)            | 0.440 | 0.248 | n/a | n/a |
| + Hybrid (FTS + weighted RRF) | 0.440 | 0.285 | n/a | n/a |
| + Rerank                      | 0.480 | 0.308 | n/a | n/a |
| + HyDE + parent-doc (**full**) | **0.520** | **0.328** | smoke 0.89 (n=1) | smoke 0.89 |

Full 25-Q RAGAS generation eval pending Groq TPD reset — see EVALS.md §"Generation eval (RAGAS)".

## Chatbot
- LLM: Groq `llama-3.3-70b-versatile` (default) or OpenRouter `nvidia/nemotron-3-super-120b-a12b:free` via `LLM_PROVIDER` env switch. Both clients are OpenAI-compatible — `app/infra/llm_groq.py` routes the same `stream_chat_with_tools` parser through either base URL. Vault `secret/api/llm` carries both `groq_api_key` and `openrouter_api_key`. Smoke verified Nemotron tool-use (write_memory) + cross-conv recall on 2026-05-20. Full bake-off (25-Q golden + tool-call discipline probe) is a follow-up; default stays Groq until the numbers land.
- Tools: classify_issue, extract_entities, summarize_thread, search_knowledge, write_memory.
- Memory: episodic in pgvector. Auto-recall on turn start. Explicit `write_memory` tool only.
- Redis TTLs: conv 24h sliding, tool 1h, embed 24h, RL 60s.
- Streaming: SSE via fetch-event-source.

### Long-term memory (slices 08+10+11 bundle)
- **Schema (migration 003):** `episodic_memories(id UUID, user_id UUID FK→users CASCADE, conversation_id, memory_type='episodic', summary, entities TEXT[], source_msg_ids TEXT[], embedding VECTOR(768), created_at, last_recalled_at)`. Indexes: HNSW (m=16, ef_construction=64) on `embedding` with `vector_cosine_ops`, GIN on `entities`, btree on `(user_id, created_at DESC)`.
- **Audit-log shape:** single shared `audit_log(id BIGSERIAL, actor, action, target_type, target_id, ts, trace_id, payload JSONB)`. One table absorbs memory write/recall, role changes, widget config changes, conversation deletions (PRD U22) — no per-domain table migration when those land.
- **Write atomicity:** `MemoryService.write` runs the memory INSERT + audit INSERT inside one asyncpg transaction. Redaction happens BEFORE the embed call so the vector never encodes the raw secret either.
- **Recall:** auto-injected at start of each turn via a `<recalled_memories>` block in the system prompt (top-5 cosine ≥ 0.6 against the current user message). Empty list still emits `<recalled_memories/>` so the model learns the contract. Recall errors fail-open (memory is augmentation, not correctness).
- **Recall audit row:** best-effort — if the `INSERT INTO audit_log` fails, the recall result is still returned to the caller. Memory writes remain strictly atomic.
- **`write_memory` tool gating:** explicit-only per PRD U9. Tool description carries both a "USE WHEN" rubric (explicit asks to remember + explicit long-running focus statements) and a "DO NOT USE WHEN" guard (chit-chat, classification, RAG, summarization).
- **User-id threading to tools:** `current_user_id` and `current_conversation_id` ContextVars in `app/domain/tools.py`, set by `ChatbotService.run_turn` before tool dispatch. Less invasive than a tool-factory refactor and keeps `TOOL_DISPATCH` as a plain function table.

## Auth (slice 10)
- **fastapi-users** with JWT, signing key resolved from Vault `shared/jwt.signing_key` at boot (NOT `.env`). Two roles: `user`, `admin` (CHECK constraint on `users.role`).
- **SQLAlchemy + asyncpg side-by-side:** the `users` table is owned by SQLAlchemy because fastapi-users requires it; every other table (memory, audit, chunks) uses raw asyncpg. Mixed ORMs in one process is acceptable for a single-table footprint; rewriting all repos as SQLAlchemy is out of scope.
- **JWT validator** accepts `sub=<uuid>` (real user) and `sub=widget_session:<uuid>` (slice-13 anon widget). `current_principal` dep returns a `User` or `AnonWidgetSession`. `current_user` (fastapi-users default) is used for admin-only endpoints where widget sessions never apply.
- **Anonymous widget users:** `write_memory` returns `{ok:false, error:"requires_authed_user"}` for `widget_session:*` principals. Real widget-keyed memory wiring lands with slice 13.
- **Revocation:** `POST /auth/jwt/revoke` sets `session:revoked:{jti}` in Redis with TTL = remaining JWT lifetime. fastapi-users' default JWT strategy doesn't mint a `jti`, so the endpoint no-ops for user tokens until slice 13's anon widget tokens (which do mint jti) come online. Redis outage on revoke is logged but doesn't fail the request.

## Observability
- Tracing: Langfuse v2 self-host. Session = conversation. Generation/tool/retrieval span types.
- Redaction: vendor-prefixed token regexes + email + URL creds + user paths. 3-boundary hookup at structlog processor, Langfuse `mask` callback, MemoryService.write summary path. Full pattern table in SECURITY.md.

## Widget
- Stack: Preact + preact/compat + Tailwind + marked + fetch-event-source.
- Bundle target: ~35 KB gzipped. Actual = TBD.
- Embed: loader.js -> iframe -> /widget/{id}/embed w/ CSP frame-ancestors from DB.

## CI (slice 15)
- **4-stage GitHub Actions** pipeline (`.github/workflows/ci.yml`): lint+unit+redaction parallel → build matrix (api, model_server, streamlit, widget, migrate) → `docker compose up` smoke + classification + RAG eval + dual-gate compare + MinIO upload → promote-baseline on main only. The boot validator (slice 14) runs at lifespan, so a green smoke means all 8 checks passed in a real container.
- **Eval baseline** at `s3://mc-evals/main/latest.json` is the source of truth for the regression diff. `evals.compare --bootstrap` PASSES when no baseline exists so the first green main build seeds it via `evals.promote`. CI uses the ephemeral in-compose MinIO; prod swap is a Vault edit at `api/blob`, not a code change.
- **Dual-gate** (`evals.gate`): `current >= floor AND current >= baseline - regression_margin`. Floors + margins per metric in `eval_thresholds.yaml`; boot check #7 (slice 14) already enforces 0<x<1 at startup.
- **Rate-limit posture for CI RAG eval**: `evals.rag.run` honors `RAG_EVAL_RPM_BUDGET` (env / CLI). CI sets `25` to stay under Groq's 30 RPM free tier; sleeps `60/budget` between requests. No paid-tier dependency.
- **Layer-boundary AST tests** (`tests/test_layers.py`): walks `ast.Import` + `ast.ImportFrom` per layer. Routers + services may not import raw `sqlalchemy`/`redis` (services also forbidden from top-level `httpx`); repos may not import `fastapi`. `CARVEOUT_ALLOW` maps file → allowed packages — currently `app/api/auth.py` (fastapi-users + sqlalchemy) and `app/repositories/users.py` (fastapi + fastapi-users + sqlalchemy). Adding a new exception is a one-line PR.
- **PG-gated tests**: `requires_pg` pytest marker on the 10 integration tests that hit Postgres. Unit stage skips them (`-m "not requires_pg"`); smoke stage runs them after `docker compose up`.
- **RAG eval in CI**: retrieval-only (`--stack hybrid_rerank`). RAGAS gen eval (faithfulness + answer-relevancy) is left to a separate workflow_dispatch run to avoid Groq TPD on every PR. The dual-gate carries `rag.faithfulness` / `rag.answer_relevancy` through transparently when present.
- **GROQ_API_KEY in CI**: optional. Defaults to `placeholder`; the `llm` classification baseline + `--stack full` HyDE are skipped when absent. Set `secrets.GROQ_API_KEY` on the repo to enable.
- **User story 45** (add-a-tool-live): not a CI gate; RUNBOOK §"Add a tool live" + a 1-minute screencap in the Friday deck.
- **Classifier carve-out in CI**: the fine-tuned DeBERTa classifier needs GPU + training to produce artifacts; CI carries none of that. Three coupled mitigations: a `minio-init` compose service that mc-creates `mc-models` + `mc-evals` (so dev workflows with trained artifacts still work); `MC_SKIP_CLASSIFIER_LOAD=1` on model-server (load is skipped, `/classify` returns 503, the rest of the server is fine); `MC_BOOT_SKIP_CLASSIFIER=1` on API (boot check #4 becomes a no-op with a WARN log). CI classification eval runs `--models classical` only — sklearn TF-IDF+LR fits itself from the bundled golden set, so it still gives a real ML signal against the per-class F1 floors. DeBERTa dual-gate + weights SHA pin (check #5 silently passes when `WEIGHTS_SHA256=""`) are verified locally + in the Friday demo, not in CI. Considered alternatives: (a) stubbing a fake classifier into MinIO — would pass boot check #4 but fail the macro_f1 floor, so still red; (b) training in CI — needs GPU and minutes per PR, wrong cost shape for a gate.
