# Maintainer's Copilot — PRD

**Project:** AIE Week 7 — Maintainer's Copilot
**Author:** Tarek Aljundi
**Status:** Ready for implementation
**Source brief:** `project-7-brief.md`
**Deadline:** End of week. Friday — 10-minute presentation.

---

## Problem Statement

An open-source maintainer of a popular Python project (pandas) is drowning in a long tail of incoming issues. Every issue costs cognitive load: read it, decide if it's a bug or a question or a feature request, recognize the code-shaped entities involved, recall whether a similar thread was already resolved, and answer or route accordingly. Most of this work is repetitive but not mechanizable by simple rules — it requires reading prose, understanding code references, and remembering past decisions across many conversations.

Existing tools either don't read issue text at all (basic GitHub label automations), generate generic LLM responses that aren't grounded in the project's docs, or are commercial chat widgets without the controls a serious maintainer needs (per-corpus retrieval quality, embedding-source provenance, redaction of secrets pasted by users, auditability of cross-conversation memory writes, ability to embed safely on a third-party host page with origin allowlisting).

The maintainer needs a tool that classifies, summarizes, retrieves, answers, and remembers — with every decision in that pipeline backed by a measured number on a project-specific eval set, and with the production hygiene (secrets in a vault, logs redacted, traces visible, CI gates on regressions) appropriate to a tool that handles arbitrary user-pasted text including credentials.

## Solution

A self-hostable assistant called **Maintainer's Copilot** that:

- **Classifies** any pasted GitHub issue into one of `bug`, `feature`, `docs`, `question` using a fine-tuned small encoder, compared head-to-head on the same test split against a classical ML baseline (TF-IDF + LogReg) and an off-the-shelf LLM baseline (Groq Llama 3.3 70B). Deployment choice is justified by a number, not a guess.

- **Extracts code-shaped entities** (identifiers, file paths, error types, module paths, version strings, PR refs, URLs) from issue text via spaCy + regex EntityRuler, surfaced as a chatbot tool.

- **Summarizes** long issue threads into a 3-line WHAT / ASK / STATE triage summary suitable for a maintainer skimming overnight activity.

- **Answers questions** about the project, grounded in an advanced RAG pipeline over the project's documentation and a held-out slice of resolved issues. The pipeline combines content-aware chunking with parent-document retrieval, hybrid sparse + dense retrieval via weighted reciprocal rank fusion, cross-encoder reranking, HyDE query transformation on the dense side, and LLM-routable metadata filters. Every step has a measured retrieval-quality lift on a hand-curated 25-question golden set.

- **Remembers across conversations** via episodic long-term memory in pgvector, written only through an explicit `write_memory` tool (no auto-summarization), with every memory write producing an audit-log row.

- **Has two frontends**: a Streamlit admin/user app for authenticated maintainers (login, full chat, memory inspector, widget configuration), and a standalone embeddable widget (Preact bundle, ~35 KB gzipped, single IIFE file, iframe-encapsulated) that any host page can drop in with a single `<script>` tag. Origin allowlisting is enforced per widget config via CSP `frame-ancestors` and CORS, sourced from the database not from environment variables.

- **Refuses to boot** if any production prerequisite is missing (Vault unreachable, classifier weights absent or SHA-mismatched, tracing misconfigured, eval thresholds set to zero, prompt SHAs not pinned).

- **Has two CI eval gates** (classification + RAG) with committed non-zero thresholds, dual-gate semantics (absolute floor + regression margin against the previous green build), and a redaction-correctness test. Any of these failing blocks merge.

- **Has visible traces**: every LLM call, tool call, RAG retrieval, and memory operation appears as a span in Langfuse, attributable to a conversation/session, with trace IDs joinable to structured logs.

- **Has secrets in Vault and blob in MinIO** with no secret values committed to source.

## User Stories

### Open-source maintainer (primary persona)

1. As a maintainer, I want to paste an issue's text into a chat and get an instant classification (bug/feature/docs/question), so that I can route or label it without reading the whole thread.
2. As a maintainer, I want the classification to be backed by a model that has been measured on issues from my own project, so that I trust it more than a generic LLM judgment.
3. As a maintainer, I want to see the per-class F1 numbers and a confusion matrix for the classifier on a held-out test split, so that I know exactly where it succeeds and fails.
4. As a maintainer, I want the chatbot to extract code-shaped entities (function names, file paths, error types, versions, PR references) from an issue, so that I can filter retrieval by those entities and find related material faster.
5. As a maintainer, I want to ask the chatbot to summarize a long issue thread into one paragraph organized as WHAT / ASK / STATE, so that I can skim overnight activity in under a minute per thread.
6. As a maintainer, I want to ask questions about the project's behavior (e.g. "how do dependencies work in background tasks?") and get an answer grounded in the actual project documentation and past resolved issues, so that I don't have to grep the docs myself.
7. As a maintainer, I want the chatbot's answers to cite the specific docs section or resolved issue it pulled from, so that I can verify and follow up.
8. As a maintainer, I want the chatbot to remember explicit facts I tell it (e.g. "I'm focused on middleware-order regressions this week") across conversations, so that subsequent sessions pick up where I left off.
9. As a maintainer, I want memory writes to happen only when I ask, not as automatic summarization of every conversation, so that the memory store stays signal-heavy.
10. As a maintainer, I want every memory write to be auditable with actor, action, target, timestamp, and trace ID, so that I can review or revoke things I've stored.
11. As a maintainer, I want the chatbot to filter retrieval by metadata (docs only, issues only, maintainer-answered chunks only, recent issues only, a docs subtree), so that I can scope answers precisely.
12. As a maintainer, I want to chat with the assistant through a familiar Streamlit page after logging in with my email and password, so that I can use the full feature set including a memory inspector.
13. As a maintainer, I want a memory inspector page that lists my episodic memories with their summaries and entities, so that I can review what the assistant remembers about me.
14. As a maintainer, I want streaming token output during chat, so that the experience feels responsive on long answers.
15. As a maintainer, I want each chat turn to be a single trace tree in the observability backend with one span per LLM call, tool call, and retrieval, so that I can debug latency outliers and tool failures myself.
16. As a maintainer, I want the assistant to recover gracefully when a tool fails (e.g. the classifier endpoint is down) by telling me the tool was unavailable and answering from its own knowledge with a hedge, so that the app never returns a 500 to me.
17. As a maintainer, I want any string I paste that looks like a secret (GitHub PAT, OpenAI key, Groq key, JWT, password in a URL) to be redacted before it reaches logs, traces, or memory, so that pasting a stack trace doesn't leak credentials.
18. As a maintainer, I want the assistant to refuse to echo secrets back in its own replies (e.g. paraphrasing a key I pasted), so that no surface of the system rebroadcasts what redaction caught.
19. As a maintainer, I want the assistant to never invent issue numbers, PR refs, or API names, so that I don't waste time chasing hallucinations.

### Admin

20. As an admin, I want to create and edit widget configurations including allowed origins, theme (primary color, position), greeting text, and enabled tools, so that I can deploy distinct widget instances to distinct host apps.
21. As an admin, I want to invite new users by email, so that I control who has access to the Streamlit surface.
22. As an admin, I want to view the audit log of every memory write, every widget config change, every conversation deletion, and every role change, so that I can demonstrate compliance.
23. As an admin, I want a generated embed snippet (single `<script>` tag with `data-widget-id`) for each widget config, so that I can hand it to a host-app developer without writing integration docs.
24. As an admin, I want the system to refuse to boot if any eval threshold has been disabled or set to zero, so that someone can't sneak in a CI bypass.
25. As an admin, I want a model card document for the fine-tuned classifier listing architecture, hyperparameters, training data hash, and final metrics, so that I can audit what was deployed.

### Host-app developer (embeds the widget)

26. As a host-app developer, I want to embed the chatbot widget on my page by pasting one `<script>` tag with `data-widget-id`, so that integration is a five-second copy-paste.
27. As a host-app developer, I want the widget bundle to be small (~35 KB gzipped target) and load asynchronously, so that it doesn't degrade my page's performance.
28. As a host-app developer, I want the widget's appearance (primary color, bubble position) configured server-side via the widget config, so that I can change theming without redeploying.
29. As a host-app developer, I want the widget to broadcast only namespaced `mc:*` postMessage events (resize, ready, theme) to my host page, so that there are no collisions with other embeds.
30. As a host-app developer, I want my host page to receive `mc:resize` postMessages so the iframe can be sized to its content, so that the widget UX feels native.

### Operator / SRE persona (also the project's maintainer in solo mode)

31. As an operator, I want `docker compose up` from a fresh clone (after `cp .env.example .env` and filling in the Vault root token) to bring the entire stack up, so that onboarding takes minutes.
32. As an operator, I want a `migrate` container to run `alembic upgrade head` and exit successfully before the `api` container starts, so that the API never serves traffic against an un-migrated schema.
33. As an operator, I want the API to refuse to boot if Vault is unreachable, classifier weights are missing or SHA-mismatched, tracing is misconfigured, any eval threshold is zero, or any committed prompt's SHA does not match its pinned value, so that I never serve a misconfigured system.
34. As an operator, I want secrets cached at boot from Vault so that a transient Vault outage during runtime does not take the live service down, but I want the service to refuse to re-boot until Vault is reachable again, so that fail-closed semantics apply at the right moment.
35. As an operator, I want every secret (LLM keys, JWT signing key, database password, Redis password, MinIO credentials, Langfuse keys) to resolve from Vault at startup, with no secret values present in source or `.env`, so that `grep -ri 'sk-' app/` returns nothing outside the Vault-reading module.
36. As an operator, I want CI on every push to run lint, type-check, both eval suites against golden sets, the redaction test, and a stack smoke-test, so that regressions are blocked.
37. As an operator, I want each CI run to upload an `eval_report.json` to MinIO and to diff every metric against the previous green main build, with regressions below the committed margin blocking merge, so that quality goes only one way.

### AIE grader (verifies on Friday)

38. As a grader, I want to see three different model approaches (classical ML, fine-tuned encoder, off-the-shelf LLM) trained on the same splits with the same inputs and compared on accuracy, macro-F1, per-class F1, latency, and cost, so that the deployment choice is defended by numbers.
39. As a grader, I want to see retrieval-quality numbers (hit@5, MRR@10) on a 25-question golden set comparing naive fixed-size + dense retrieval against the production stack (content-aware chunking + parent-doc + hybrid + rerank + HyDE), so that every advanced-RAG ingredient earns its place.
40. As a grader, I want to see RAGAS faithfulness and answer-relevancy scores against a frozen LLM judge, plus Spearman correlation between the judge and the maintainer's hand-labels on 5 of the 25 questions, so that the eval methodology itself is auditable.
41. As a grader, I want to see the trace tree of a real conversation in the tracing UI including one error-path trace, so that observability is provably wired, not just claimed.
42. As a grader, I want to see the widget loading on an allowed host and being blocked by `frame-ancestors` on a disallowed host using real browser network and console output, so that origin allowlisting is provably enforced from the database.
43. As a grader, I want to see a redaction test that pastes a fake GitHub token and asserts it never appears unredacted in logs, traces, or memory, so that the redaction claim is testable not aspirational.
44. As a grader, I want layer boundaries enforced by tests (routers don't import SQLAlchemy or Redis; repositories don't import FastAPI), so that the architecture grade is mechanical not subjective.
45. As a grader, I want to be able to ask "add a new tool live" on Friday and see a tool added in `app/services/...` and exposed via the chatbot in a few minutes without touching the API or repository layers, so that the layered architecture is verified by demonstration.

## Implementation Decisions

### Dataset and labels (Q1-Q5)

- The corpus is closed issues from `pandas-dev/pandas` created on or after 2020-01-01, deduped, with state not equal to "not planned", and carrying at least one of pandas's canonical class labels (`Bug`, `Enhancement`, `Docs`/`Documentation`, `Usage Question`), which map to our 4 classes via `LABEL_MAP` in `scripts/pull_dataset.py`. (Corpus was originally `tiangolo/fastapi`; swapped for pandas due to better class balance and ~10× more labeled records — see DECISIONS.md §Dataset.)
- Label mapping is strict: each raw GitHub label maps to its canonical class via `LABEL_MAP` (`Bug`→bug, `Enhancement`→feature, `Docs`/`Documentation`→docs, `Usage Question`→question). Multi-label issues are tie-broken with priority `bug > feature > docs > question` (most actionable wins). Labels not in the map (workflow/component/subcomponent, e.g. `Needs Discussion`, `Performance`, `IO`) are ignored for classification. Unlabeled issues are excluded from train/val/test but eligible for hand-curated golden sets.
- Splits are time-stratified by `closed_at` ascending: 70% train, 10% val, 15% test, 5% RAG held-out. The RAG held-out slice is the most recent issues intersected with "has a maintainer answer or `answered` label". Per-class counts are reported per split; classes with fewer than 5 examples in test are annotated `n=N` in eval reports.
- Classifier input is title + body concatenated, with code blocks replaced by a single `<CODE>` placeholder token, truncated to 512 tokens head-only. NER, summarizer, and RAG corpus use fuller context (code preserved verbatim).

### Three-model comparison (Q4, Q6, Q7, Q8)

- **Fine-tuned model:** `microsoft/deberta-v3-small`. Full fine-tune with discriminative learning rate (encoder base LR 2e-5, classifier head 1e-4). Linear warmup 10% of steps, linear decay. Weight decay 0.01 on non-bias/non-layer-norm params. Batch size 16 (or 8 with grad accum). 3-5 epochs with early stop on val macro-F1, patience 1. Training tracked in Weights & Biases free tier. Final artifact saved to MinIO at `models/classifier/v1/` with a model card listing architecture, hyperparameters, training data hash, weights SHA-256, and final metrics.
- **Classical baseline:** TF-IDF feature union of word n-grams (1-2) and char-wb n-grams (3-5) into Logistic Regression with `class_weight='balanced'`. Hyperparameter `C` tuned on val via 5-point grid. Same splits as the fine-tuned model.
- **LLM baseline:** Groq `llama-3.3-70b-versatile` with a 4-shot prompt (one in-context example per class drawn from train), temperature 0, structured output enforced via `tool_use` with a single-tool schema yielding `{"label": "bug|feature|docs|question"}`. Same inputs, same test split.
- **Comparison output** in DECISIONS.md: accuracy, macro-F1, per-class F1, p50/p99 latency, and cost per 1k predictions. Deployment choice defended in one line.

### NLP tools as services (Q9, Q10)

- **NER** is spaCy `en_core_web_sm` plus a custom `EntityRuler` with regex patterns for `IDENTIFIER`, `FILE_PATH`, `ERROR_TYPE`, `VERSION`, `PR_REF`, `URL`, `MODULE`. Loaded into the `model_server` container at startup. Exposed over HTTP to the API.
- **Summarizer** is Groq `llama-3.3-70b-versatile` with a tight 3-line instruction (WHAT / ASK / STATE, max 80 words, no markdown headers), temperature 0. Threads truncated to 8k tokens head if longer. Lives behind the API rather than the model server (no model weights to load).

### Advanced RAG (Q11-Q16)

- **Embedding:** `BAAI/bge-base-en-v1.5` (110M params, 768-dim) for production. Compared on the RAG golden set against `BAAI/bge-base-en-v1.5`'s smaller sibling `BAAI/bge-small-en-v1.5` (33M, 384-dim) as a single-axis ablation. Query/passage prefix conventions used (asymmetric retrieval).
- **Vector store:** pgvector with HNSW index, cosine similarity, m=16, ef_construction=64.
- **Chunking:** content-aware dual chunker. Docs: header-recursive (split on H1/H2/H3, sub-split with 50-token overlap if a section exceeds 500 tokens, drop chunks shorter than 50 tokens). Issues: per-comment (title + body is chunk 1; each top-level comment is one chunk; comments shorter than 30 tokens merged with prior). Code blocks preserved verbatim in the RAG corpus (in contrast to the classifier's `<CODE>` swap). Metadata schema differs by content type and is unified by a `content_type` discriminator.
- **Parent-document retrieval:** child chunks are embedded and indexed; parents (full H2 section for docs, full comment for issues) are stored in the same `chunks` table with a `parent_id` foreign key. Retrieval returns parents to the LLM after deduping child hits.
- **Hybrid retrieval:** dense side runs pgvector HNSW search over child embeddings, top-50. Sparse side runs Postgres full-text search via a generated `tsvector` column with `ts_rank_cd`, top-50. Fusion is weighted reciprocal rank fusion with k=60: `score(doc) = w_d / (60 + rank_dense(doc)) + w_s / (60 + rank_sparse(doc))`. Weights `(w_d, w_s)` are tuned on the RAG golden set over a small grid (e.g. `{(1.0, 0.5), (1.0, 1.0), (1.0, 1.5), (0.5, 1.0)}`), winner selected by MRR@10, locked in DECISIONS.md.
- **Reranking:** `BAAI/bge-reranker-base` over the top-20 from fusion, returning top-5 chunks (deduped to parent IDs).
- **Query transformation:** HyDE on the dense side only. A short hypothetical pandas documentation passage is generated by Groq Llama 3.3 (temperature 0, no preamble, 3-4 sentences, code if relevant), embedded, and used as the dense query. The sparse side uses the original query (HyDE'd queries lose keyword signal).
- **Metadata filtering:** API exposes optional filter parameters `content_types`, `labels`, `is_answer`, `min_closed_at`, `breadcrumb_prefix`. Applied as post-filter with over-fetch (top-200 from each side before filter, then fuse to 20, rerank to 5) so the HNSW index isn't restricted to a tiny subset. Filter arguments are surfaced to the LLM via the `search_knowledge` tool schema; the chatbot's system prompt teaches when to apply each filter.

### Chatbot (Q17, Q18, Q28)

- **LLM:** Groq `llama-3.3-70b-versatile` for the chatbot, summarizer, LLM baseline, and RAGAS judge. Single vendor, single Vault secret, single tracing integration. Tool-call reliability tradeoff is mitigated by tight tool descriptions, JSON schema validation on tool arguments (a validation error is reflected back as a `tool_failure` so the LLM can retry with corrected arguments), and an explicit `ok/error` field in every tool result.
- **Tools (5):** `classify_issue(text)`, `extract_entities(text)`, `summarize_thread(text)`, `search_knowledge(query, content_types?, is_answer?, labels?, breadcrumb_prefix?)`, `write_memory(summary, entities?)`. Each tool description in the schema includes both a "use when" rubric and an explicit "do not use when" guard to bound the LLM's selection.
- **Long-term memory:** episodic only. Schema: `(id, user_id, conversation_id, memory_type='episodic', summary, entities[], source_msg_ids[], embedding VECTOR(768), created_at, last_recalled_at)`. HNSW index on `embedding`, GIN index on `entities`, btree on `(user_id, created_at DESC)`. Embeddings are computed from `summary` via bge-base. Every write produces an audit-log row.
- **Memory recall** is auto-injected at the start of each turn, not a tool. Top-5 memories for the current user with cosine similarity ≥ 0.6 against the current user message are loaded, rendered into the system prompt under a `<recalled_memories>` block, and an audit-log row with `action='recall'` is written.
- **Short-term memory** lives in Redis with the following keys and TTLs: `conv:{id}:msgs` (LIST, last 20 turns, 24h sliding TTL refreshed on every write); `conv:{id}:meta` (HASH, 24h sliding); `cache:tool:{tool}:{input_sha}` (1h hard); `cache:embed:{text_sha}` (24h hard); `rl:{user_id}:{minute_bucket}` (60s hard); `session:revoked:{jti}` (TTL = remaining JWT lifetime). Conversations are also persisted to Postgres so that the Redis boundary is a cache, not a source of truth.
- **Streaming protocol** is Server-Sent Events over a single POST endpoint (`text/event-stream` response from FastAPI's `StreamingResponse`). Events are namespaced JSON objects: `{type:'token', content}`, `{type:'tool_call_start', name, args}`, `{type:'tool_call_result', name, result}`, `{type:'done', msg_id}`, terminated with a `[DONE]` sentinel. The widget consumes via `@microsoft/fetch-event-source` so it can send an `Authorization` header.
- **Agent loop** is bounded to 6 tool-call iterations per turn. Temperature is 0.2 (small variation to break tool-selection loops, not so high that classification leaks creativity). Exceeding the iteration cap surfaces as `LLMProviderError("max_steps_exceeded")` to the API handler.
- **System prompt and few-shot examples** live in `prompts/*.md`, version-controlled, with SHA-256 digests pinned in a `_registry.py` module. The API refuses to boot if any prompt's actual SHA does not match the pinned value (a separate boot check below).

### Authentication and authorization

- **Authed users (Streamlit):** `fastapi-users` with JWT, email + password registration. Two roles: `user` and `admin`. JWT signing key resolves from Vault at startup.
- **Anonymous widget users:** the widget mints a short-lived anonymous JWT scoped to a `widget_id` with claim `{sub: "widget_session:<uuid>", widget_id, enabled_tools}`. Both schemes pass through the same JWT validator, differentiated by `sub` prefix. Conversations and episodic memory are keyed by `widget_session_id` for anonymous users and `user_id` for authed users.

### Widget and embed flow (Q21, Q22, Q24)

- **Frontend stack:** Preact 10 via `preact/compat` alias (drop-in React API), Vite, Tailwind (JIT purge), `marked` for markdown rendering, `@microsoft/fetch-event-source` for SSE. No state management library; `useReducer` + Context. Single IIFE bundle, target ~35 KB gzipped.
- **Loader script (`/widget.js`, target ~3 KB):** reads `data-widget-id` and optional `data-api-base` from its own `<script>` tag, fetches the widget's public config from `/widget/{id}/config`, injects an iframe pointing at `/widget/{id}/embed`, and listens for `mc:resize`, `mc:ready`, and `mc:theme` postMessage events.
- **Embed route** returns an HTML page with a `Content-Security-Policy` header whose `frame-ancestors` directive lists the widget's `allowed_origins` from the database. Inline scripts set `__MC_WIDGET_ID__` and `__MC_API_BASE__` globals for the bundle to bootstrap.
- **CORS allowlist** is computed dynamically per request from the widget config row (cached in Redis 60s), not from an environment variable.
- **postMessage protocol** is namespaced `mc:*` and uses three event types only: `mc:resize` (iframe → host with `{width, height}`), `mc:ready` (iframe → host on auth bootstrap complete), `mc:theme` (host → iframe with optional theme overrides). Origin is validated on both sides.

### Observability (Q19, Q20)

- **Tracing backend:** Langfuse v2, self-hosted as one additional container reusing the project's Postgres and Redis. Sessions are mapped 1:1 to conversations; every chat turn is a trace rooted at the user message. Generation, tool, and retrieval spans are typed accordingly. Span attributes (model name, token counts, latency, tool inputs/outputs) are passed through the redaction layer before send via Langfuse's `mask` callback.
- **Structured logging** via `structlog` with the trace ID bound to every log line for the same request, so logs are joinable to traces by trace ID.
- **Redaction patterns** (regex, applied before any log line, span attribute, or memory write leaves the service): OpenAI keys (`sk-…`), Anthropic keys (`sk-ant-…`), Groq keys (`gsk_…`), GitHub tokens (`gh[pousr]_…`), AWS access keys (`AKIA…`), Google API keys (`AIza…`), Slack tokens (`xox[bpa]-…`), JWT shapes, URL credentials (`scheme://user:pass@host`), `password=` / `passwd=` / `pwd=` key-value pairs, email addresses, and user-identifying path segments (`C:\Users\X\`, `/Users/X/`, `/home/X/`). Deliberately NOT redacted (with rationale documented in SECURITY.md): IP addresses, phone numbers, names, generic high-entropy strings — false-positive cost too high.
- **Redaction hookup at three boundaries:** the `structlog` processor pipeline, the Langfuse `mask` callback, and the memory service's write path.

### Exception handling (Q23)

- **Domain hierarchy:** `AppError` → `{DomainError, ToolFailure, InfraError}`. `DomainError` subclasses (`NotFoundError`, `ConflictError`, `ValidationError`, `PermissionDenied`, `RateLimitExceeded`) carry the expected HTTP status code. `ToolFailure` subclasses (`ClassifierUnavailable`, `RAGRetrievalFailure`, `NERFailure`, `SummarizerFailure`, `MemoryWriteFailure`) are caught inside the chatbot orchestrator and converted to `ToolResult(ok=False, ...)` returned to the LLM. `InfraError` subclasses (`VaultError`, `LLMProviderError`, `DatabaseError`, `BlobError`) map to 503.
- **Single API exception handler** translates any `AppError` into a structured JSON response containing `{code, message, request_id, trace_id, extras}`. Uncaught exceptions are logged with trace ID and request ID and returned as a generic 500 with the same envelope shape. Users never see a stack trace.
- **Layer rule:** repositories raise `NotFoundError` / `DatabaseError` only. Services raise domain errors. Infra adapters raise `InfraError` subclasses. Only `app/api/` knows about HTTP.

### Repo, packaging, infrastructure (Q25)

- Single repository, single `pyproject.toml` with optional-dependency groups (`api`, `model-server`, `streamlit`, `evals`, `dev`), `uv` for dependency resolution and lockfile, one `uv.lock` committed.
- Compose services: `api`, `chatbot` (Streamlit), `widget` (static bundle server), `model-server`, `host` (nginx serving the demo host page), `migrate` (alembic upgrade head then exit), `db` (postgres 16 with pgvector), `redis`, `minio`, `vault`, `langfuse`.
- `migrate` runs to completion before `api` starts (`depends_on` with `condition: service_completed_successfully`). `api` has `restart: no` so boot failures surface, do not silently loop.
- `.env.example` contains only the Vault root token and service ports. No secret values.

### Vault secret organization (Q30)

- KV v2 layout under `secret/`: `shared/jwt`, `api/llm`, `api/tracing`, `api/db`, `api/redis`, `api/blob`, `streamlit/api`. Each path's structure is documented in RUNBOOK.md.
- Secrets are loaded at boot and cached in `VaultClient._cache`. Runtime Vault outages do not take the live service down; new connections fail closed; the service refuses to re-boot if Vault is unreachable.

### Boot-time refusal (8 checks)

The API container's FastAPI lifespan runs eight checks in order. Any failure causes `SystemExit(1)`:

1. Vault reachable (sealed/unsealed check).
2. All required Vault secret paths load.
3. Database migration revision matches Alembic head.
4. Model server reports `classifier_loaded=true` over HTTP.
5. Model server's reported classifier weights SHA-256 matches the value pinned in the committed model card.
6. Langfuse auth check passes.
7. Every numeric eval threshold in `eval_thresholds.yaml` is strictly between 0 and 1 (none disabled).
8. Every committed prompt file's SHA-256 matches the value pinned in `prompts/_registry.py`.

### Evaluation (Q26, Q27)

- **Classification eval:** 25 hand-curated issues separate from the test split. Macro-F1, per-class F1, confusion matrix. Run against all three models in CI.
- **RAG eval:** 25 hand-curated `(question, ideal_answer, ground_truth_chunk_ids)` triples. Retrieval metrics (hit@5, MRR@10) computed deterministically by set intersection / rank arithmetic. Generation metrics (faithfulness, answer-relevancy) computed by RAGAS with the Groq Llama 3.3 70B judge at temperature 0, RAGAS version pinned. Maintainer hand-labels 5 of 25 on a 1-5 Likert; Spearman correlation between hand-labels and RAGAS reported in EVALS.md.
- **Judge disagreement protocol:** on disagreement, inspect each case (n=5 is small), categorize as (a) judge brittle → calibrate judge prompt and re-eval, (b) hand-label hasty → re-label and document revision, (c) genuine ambiguity → tighten rubric. If post-calibration ρ < 0.6 the metric is demoted to "advisory, not gating" in `eval_thresholds.yaml`.
- **CI pipeline (GitHub Actions, 4 stages):** stage 1 lint + type-check + unit + redaction (parallel); stage 2 build images (matrix); stage 3 compose-up smoke + classification eval + RAG eval + compare to baseline + upload `eval_report.json`; stage 4 promote-baseline (main only).
- **Eval thresholds** are committed in `eval_thresholds.yaml` with both an absolute `floor` and a per-metric `regression_margin`. A metric passes if `current >= floor AND current >= baseline - margin`. The baseline lives in MinIO at `s3://mc-evals/main/latest.json` and is replaced by a successful main build via the promote-baseline job.

### Modules (12 deep modules + shallow glue)

The following are the deep modules with narrow interfaces, each encapsulating significant complexity and rarely needing interface changes:

- **Redactor** — interface: `redact(text)`, `redact_obj(obj)`. Hides pattern list and walk semantics.
- **VaultClient** — interface: `load(path)`, `cached(path)`, `health()`. Hides hvac, KV v2 path mangling, boot-cache.
- **TracingPort** — interface: `@observe` decorator, `trace_id()`, `mask()`. Hides Langfuse SDK; swappable to OTEL/Phoenix without caller changes.
- **PromptRegistry** — interface: `Prompt.X.render(**ctx)` typed accessor per prompt. Hides file loading and SHA enforcement; boot check verifies pinned SHAs.
- **ChatbotService** — interface: `run_turn(conv_id, user_msg, user) -> AsyncIterator[Event]`. Hides agent loop, tool dispatch, memory injection, streaming event shape.
- **MemoryService** — interface: `write(user_id, summary, entities)`, `recall(user_id, query, top_k, min_similarity)`. Hides pgvector, audit log row insertion in the same transaction as the memory row.
- **RAGService** — interface: `retrieve(query, filters?) -> list[RetrievedChunk]`. Hides HyDE preprocessing, hybrid retrieval (dense + FTS, weighted RRF), reranking, parent-document expansion, metadata filtering, over-fetch logic.
- **ModelServerClient** — interface: `classify(text)`, `extract(text)`, `summarize(text)`, `rerank(query, candidates)`, `embed(texts, mode)`. Hides HTTP, retries, and mapping of network/HTTP failures to specific `ToolFailure` subclasses.
- **WidgetConfigService** — interface: `get(id)`, `create(...)`, `list(...)`, `update(id, ...)`, `delete(id)`, `allowed_origins(id)`. Hides database CRUD plus Redis cache invalidation.
- **AnonSessionService** — interface: `mint(widget_id) -> token`. Hides JWT minting and `enabled_tools` snapshot for the widget config at mint time.
- **EvalHarness** — interface: `run_classification() -> EvalReport`, `run_rag() -> EvalReport`. Hides golden-set loading, metric computation, MinIO upload, baseline diffing.
- **BootValidator** — interface: `validate_all() -> None` (raises `InfraError`). Orchestrates all 8 boot checks; each individual check is a standalone callable for unit testability.

Shallow surfaces (intentionally thin, no business logic):

- API routers in `app/api/` — translate HTTP to service calls only.
- Streamlit pages — UI only, call API endpoints.
- Repositories in `app/repositories/` — one per table, SQL queries only, no HTTP, no cache invalidation.
- Tool wrappers in `app/chatbot/tools/` — thin adapters that translate the LLM tool schemas to service calls.

## Testing Decisions

A good test in this codebase asserts external behavior of a deep module through its interface, never an implementation detail. The Redactor test calls `redact(text)` and asserts on the output string; it does not introspect the internal pattern list. The ChatbotService test injects a failing classifier dependency and asserts the user-visible behavior is "200 OK with a graceful message," not "this particular log line was written." The boot validator test invokes the full lifespan and asserts `SystemExit(1)` is raised when a threshold is set to zero, not the order in which checks ran.

Tests for deep modules use real infrastructure where the brief mandates it (the redaction test uses real `structlog`, a real Langfuse test client, and a real Postgres memory write) and use minimal mocking only at process boundaries (HTTP to the model server, the Groq API client).

Modules with tests in this PRD's scope:

**Tier 1 (mandatory or high-risk):**

- **Redactor.** Unit tests covering every pattern category (vendor token shapes, URL credentials, email, user paths). Integration tests at all three boundaries: a `caplog` test asserting a fake `ghp_…` token never appears in logs; a Langfuse test client capturing spans and asserting `[REDACTED:github_token]` in the span input; a memory-write test asserting the persisted `summary` column never contains the unredacted token. The brief mandates this test exists.
- **BootValidator and each individual check.** Each of the 8 checks is tested in isolation with a failure case injected: Vault unreachable, missing secret path, DB not at head, model server reporting `classifier_loaded=false`, classifier SHA mismatch, Langfuse auth failure, eval threshold zero, prompt SHA mismatch. Threshold-zero refusal is brief-mandated. Each check raises a specific `InfraError` and `SystemExit(1)` propagates from the lifespan.
- **ChatbotService tool-failure recovery.** Integration test that monkey-patches `ModelServerClient.classify` to raise `ClassifierUnavailable`, sends a chat turn, asserts the HTTP response is 200, asserts the streamed events contain a `tool_call_result` with `ok=false`, and asserts the final assistant message acknowledges the failure rather than crashing. Same pattern for `RAGRetrievalFailure` and `NERFailure`.
- **Layer boundary tests.** Static AST or grep-style tests scanning the codebase: routers in `app/api/` must not import `sqlalchemy` or `redis`; repositories in `app/repositories/` must not import `fastapi`; services must not import infra adapters directly except through dependency-injected interfaces.

**Tier 2 (additional coverage):**

- **MemoryService.** Integration test against a real Postgres + pgvector: `write(...)` inserts the memory row and an audit-log row in the same transaction; `recall(...)` with a query semantically similar to the stored summary returns it; redaction is applied to the `summary` field before persistence; a user can only recall their own memories (`user_id` scoping).
- **VaultClient.** Boot-cache test loads secrets at startup with a real Vault dev container; runtime-outage test stops the Vault container after boot, asserts cached secret reads still succeed, asserts `health()` returns `False`, asserts the API continues to serve `/health` requests on the cached config.
- **PromptRegistry.** SHA-enforcement test: writes a prompt file with one byte changed against the SHA pinned in `_registry.py`, runs the boot check, asserts `SystemExit(1)` with an error message identifying the offending prompt.

**RAG testing strategy:** the 25-question golden set running in CI with committed thresholds is the test for the RAG pipeline. No separate unit tests for `RAGService`. Regressions in retrieval quality (hit@5, MRR@10) or generation quality (faithfulness, answer-relevancy) below the committed floor or below the previous green main build minus the regression margin block merge.

**Prior art:** none, this is a greenfield repo. Test style will follow standard `pytest` + `pytest-asyncio` conventions with fixtures for the test stack (`db`, `redis`, `vault_dev`, `langfuse_test_client`).

## Out of Scope

- **Multi-tenant isolation beyond `user_id` scoping.** A single-tenant deployment with role-based access (`user`, `admin`) is sufficient for the demo. No row-level security policies, no per-tenant data partitioning, no organization concept.
- **Production-grade Langfuse deployment.** Self-host in dev mode, single admin user, no SMTP for password reset, no signups (admin pre-creates the project key). RUNBOOK.md documents this.
- **Production-grade Vault deployment.** Vault dev mode is used. No unsealing ceremony, no auto-unseal, no TLS to Vault. Production deployment would replace Vault dev with a proper unsealed cluster.
- **Active learning loop.** The classifier is trained once on the time-stratified split. No periodic retraining, no online learning, no human-in-the-loop label correction pipeline.
- **Multi-language support.** Corpus, prompts, and embeddings are all English-only. `bge-reranker-v2-m3` (multilingual) was deliberately rejected in favor of the English-only `bge-reranker-base`.
- **Streaming widget reconnect logic beyond SSE built-ins.** The SSE protocol's `Last-Event-ID` mechanism handles transient disconnects. No application-level reconnect with replay.
- **Rate limiting beyond per-user per-minute counters in Redis.** No global rate limit, no IP-based rate limit, no exponential backoff on the chatbot LLM calls.
- **Widget collapse animation and theming beyond primary color + position.** Functional UI only; no transition animations, no dark/light theme toggle from the widget side (host page can override via `mc:theme` postMessage).
- **Streamlit memory inspector beyond a read-only list.** No inline editing of memories, no manual recall trigger, no entity-graph visualization.
- **NER beyond regex EntityRuler.** No fine-tuned NER model, no LLM-based entity extraction, no entity linking to external knowledge bases.
- **Summarization model comparison.** Groq Llama 3.3 is used; no head-to-head against BART-CNN or other pre-trained summarizers (the brief allows either; LLM-driven was chosen for consistency with the rest of the stack).
- **Memory types beyond episodic.** No semantic facts about the user, no procedural rule memory.
- **Conversation deletion UI.** Conversations persist in Postgres; admin can delete via SQL or a future endpoint, but the demo does not exercise this.

## Further Notes

- **LLM choice rationale.** Originally Claude Haiku 4.5 was the recommended LLM for chatbot, summarizer, baseline, and judge. The maintainer does not have a Claude API key. The stack was unified on Groq `llama-3.3-70b-versatile`. Tool-call reliability on Llama is empirically lower than on Claude, which is mitigated by tight tool descriptions, JSON-schema validation reflected back to the LLM on argument errors, and a structured `ok/error` field in every tool result.
- **Rate-limit awareness.** Groq's free tier is ~30 requests per minute on some models. CI eval runs may need a semaphore and throttling, or a paid plan. Documented in RUNBOOK.md.
- **Friday presentation structure** is a 7-slot, 10-minute deck (problem, architecture, numbers across 3 slides, 3-minute live demo block, defended-decisions slide that pre-answers the brief's "Think About" provocations, rigor slide, close). Live demo includes widget chat with tool fan-out, cross-conversation memory recall, allowed-vs-blocked origin (CSP `frame-ancestors`), trace tree including one error-path trace, and a redaction proof. Each demo segment has a pre-recorded fallback screencap in case live demo fails on stage.
- **Scope-cut priority if behind:** drop the bge-small embedding ablation (run bge-base only); drop parent-document retrieval (return matched chunks directly); drop the Streamlit memory inspector page. Do not cut: three-way classifier comparison, both eval suites in CI, redaction test, origin allowlist demo, cross-conversation memory demo, refuse-to-boot checks. These are graded.
- **Submission tag** is `v0.1.0-week7`, pushed Friday morning after CI is green from a fresh clone smoke.
- **Deliverables alongside this PRD:** `ARCH.md`, `DECISIONS.md`, `RUNBOOK.md`, `EVALS.md`, `SECURITY.md`, `docs/model_card.md`. All referenced by the root README.
