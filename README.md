# Maintainer's Copilot

AIE Week 7 solo project. A self-hostable assistant for open-source maintainers
who drown in their own issue trackers: it **classifies** incoming issues into
bug / feature / docs / question, **summarizes** long threads, **answers**
project-specific questions from the docs + closed-issue corpus, and
**remembers** what each maintainer has told it. Built end-to-end on the
`pandas-dev/pandas` corpus — a fine-tuned classifier (DeBERTa-v3-small)
deployed alongside a retrieved + reranked advanced-RAG stack, an embeddable
Preact chat widget, and a dual-gate eval CI that refuses to merge on
regression.

## Quick start

```bash
cp .env.example .env
# fill VAULT_TOKEN (any string for dev)
docker compose up
```

The 8 boot checks (Vault, DB head, model artifacts, weights SHA pin, Langfuse
auth, eval thresholds, prompt SHAs) all run during the FastAPI lifespan; if
any fails the api container exits non-zero with `BOOT FAIL #N`. See
[RUNBOOK.md](RUNBOOK.md) §First boot for the bring-up order.

## Demo

![chat widget on the demo host page — classify → summarize → recall](docs/screenshots/widget-demo.png)
<!-- DROP IN: docs/screenshots/widget-demo.png before Friday — 1280×800 PNG of the widget mid-conversation -->

A 5-segment live demo (allowed-vs-blocked origin, cross-conversation memory
recall, multi-tool fan-out, Langfuse trace tree, redaction proof) is the
centerpiece of the Friday presentation; each segment has a pre-recorded
screencap fallback under `docs/screencaps/`.

## What makes this rigorous

- **Dual-gate eval CI.** Every metric in `eval_thresholds.yaml` must clear an
  absolute floor AND stay within a regression margin of the last green main
  baseline (stored in MinIO at `s3://mc-evals/main/latest.json`). The first
  green main seeds; subsequent merges either hold the line or are blocked.
- **Refuse-to-boot.** 8 boot checks (see ARCH.md §Refuse-to-boot) run in the
  api lifespan. A zeroed threshold, an empty Vault path, an unsynced DB, or a
  prompt-file SHA drift all surface as `BOOT FAIL #N` and exit the container
  before traffic.
- **Redaction at 3 boundaries.** Vendor-prefixed tokens, JWTs, URL creds,
  emails, and user paths are redacted at the structlog processor, the
  Langfuse `mask` callback, AND the memory-write path — the last one *before*
  the embedding call, so the vector cannot leak the secret either. See
  [SECURITY.md](SECURITY.md) §Boundaries.
- **Layer-boundary AST tests.** `tests/test_layers.py` walks every `.py` in
  `app/` and asserts routers never import `sqlalchemy`/`redis`, repositories
  never import `fastapi`, services never reach past `app.infra.*` for raw
  clients. Carve-outs are a one-line `CARVEOUT_ALLOW` entry — auditable.
- **Three-way classifier defense.** DeBERTa-v3-small (deployed), classical
  TF-IDF+LR, and a Groq llama-3.3-70b 4-shot baseline are all measured on
  the same 25-record golden + the 993-record held-out test split.
  Three-line one-liner defends deployment: deberta wins macro-F1 (+0.053 vs
  classical, driven by +0.16 F1 on the minority `question` class), wins
  latency vs LLM (31× faster), wins cost ($0 vs $1.11/1k), and is the only
  artifact the SHA-pinned boot check #5 accepts. Numbers in
  [DECISIONS.md](DECISIONS.md) §Three-model comparison.

## Docs

- [PRD.md](PRD.md) — product requirements (Q1–Q30 locked)
- [ARCH.md](ARCH.md) — architecture, deep modules, request lifecycle
- [DECISIONS.md](DECISIONS.md) — every decision backed by a number
- [RUNBOOK.md](RUNBOOK.md) — ops, recovery, Vault layout, CI carve-outs
- [EVALS.md](EVALS.md) — classification + RAG methodology + numbers
- [SECURITY.md](SECURITY.md) — redaction patterns + boundary diagram + threat model
- [docs/model_card.md](docs/model_card.md) — classifier card

## Submission

```
Project 7 — Tarek Aljundi
Repo: https://github.com/TarekAljundi/Maintainer-s-Copilot
Tag:  v0.1.0-week7
```
