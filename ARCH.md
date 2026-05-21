# Architecture

## Services (docker-compose)

| Service | Role |
|---|---|
| `api` | FastAPI: auth, chat, memory, RAG orchestration, widget config |
| `chatbot` | Streamlit: login, full chat, memory inspector, admin |
| `widget` | Static server: built Preact bundle + loader.js |
| `model-server` | FastAPI inference: classifier, NER, reranker, embedder |
| `host` / `host-blocked` | Demo nginx hosts (allowed + blocked origin, side-by-side CSP demo) |
| `migrate` | One-shot `alembic upgrade head` |
| `minio-init` | One-shot bucket creator (`mc-models`, `mc-evals`) |
| `vault-init` | One-shot Vault seeder (see RUNBOOK §Vault paths) |
| `db` | postgres:16 + pgvector |
| `redis` | redis:7 |
| `minio` | blob: models, eval reports, plots, snapshots |
| `vault` | dev mode |
| `langfuse` | tracing UI (v2 self-host) |

## Layers (api service)

```
       ┌──────────────────────────────────────────────────┐
       │  app/api/         (routers — HTTP only, shallow) │
       └──────────────────────────────────────────────────┘
                                 │   service calls
                                 ▼
       ┌──────────────────────────────────────────────────┐
       │  app/services/    (business logic, DEEP modules) │
       └──────────────────────────────────────────────────┘
                  │                        │
        repo calls│              port calls│
                  ▼                        ▼
       ┌──────────────────┐    ┌────────────────────────────┐
       │ app/repositories │    │ app/infra/  (DI ports +    │
       │ (SQL only, 1 per │    │ deep adapters: Vault,      │
       │  table)          │    │ MinIO, Redis, ModelServer, │
       │                  │    │ Tracing, Redactor, …)      │
       └──────────────────┘    └────────────────────────────┘
                  │                        │
                  └────────┬───────────────┘
                           ▼
                       Postgres / Redis / MinIO / Vault / Langfuse
```

Rules (enforced by `tests/test_layers.py`):

- **Routers** (`app/api/`) — HTTP only. No `sqlalchemy.*`, no `redis.*`. Carve-out:
  `app/api/auth.py` may import `fastapi_users.db` (unavoidable for the
  fastapi-users library; hiding it behind a port is out of scope).
- **Services** (`app/services/`) — business logic, transaction boundaries, cache +
  memory invalidation. No raw `sqlalchemy`/`redis`/`httpx`. The DI surface is
  `app/infra.*`.
- **Repositories** (`app/repositories/`) — SQL only, one per table. No `fastapi`.
  Carve-out: `app/repositories/users.py` (fastapi-users requires `Depends` +
  `SQLAlchemyUserDatabase`).
- **Infra** (`app/infra/`) — adapters and deep modules. Owns all third-party
  client construction; everything upstream gets typed handles.
- **Domain** (`app/domain/`) — Pydantic models, tools, exception hierarchy. No I/O.

## Deep modules (PRD §Modules)

The 12 modules with narrow interfaces and significant hidden complexity. Each
rarely changes its interface; almost all the slice-to-slice churn happens
behind these signatures.

| Module | Interface | Hides |
|---|---|---|
| Redactor (`app/infra/redaction.py`) | `redact(text)`, `redact_obj(obj)` | Pattern table, ordering, walk semantics |
| VaultClient (`app/infra/vault.py`) | `load(path)`, `cached(path)`, `health()` | hvac, KV v2 path mangling, boot-cache |
| TracingPort (`app/infra/tracing.py`) | `@observe`, `trace_id()`, `mask()` | Langfuse SDK; swappable to OTEL/Phoenix |
| PromptRegistry (`prompts/_registry.py`) | `Prompt.X.render(**ctx)` typed accessor | File loading, SHA pinning |
| ChatbotService (`app/services/chatbot.py`) | `run_turn(conv_id, user_msg, user) -> AsyncIterator[Event]` | Agent loop, tool dispatch, memory injection, streaming event shape |
| MemoryService (`app/services/memory.py`) | `write(...)`, `recall(user_id, query, k, min_sim)` | pgvector, atomic audit insert, redaction-before-embed |
| RAGService (`app/services/rag.py`) | `retrieve(query, filters?) -> list[RetrievedChunk]` | HyDE, hybrid retrieval (dense+FTS+RRF), rerank, parent-doc expansion |
| ModelServerClient (`app/infra/model_server_client.py`) | `classify / extract / summarize / rerank / embed` | HTTP, retries, mapping network failures to ToolFailure subclasses |
| WidgetConfigService (`app/services/widget_config.py`) | `get/create/list/update/delete`, `allowed_origins(id)` | DB CRUD + Redis cache invalidation |
| AnonSessionService (`app/services/anon_session.py`) | `mint(widget_id) -> token` | JWT minting + `enabled_tools` snapshot at mint time |
| EvalHarness (`evals/`) | `run_classification() -> EvalReport`, `run_rag() -> EvalReport` | Golden-set loading, metric computation, MinIO upload, baseline diffing |
| BootValidator (`app/boot/validator.py`) | `validate_all() -> None` (raises `InfraError`) | All 8 boot checks, ordering, `BOOT FAIL #N` mapping |

Shallow surfaces (intentionally thin, no business logic):

- API routers under `app/api/` — translate HTTP to service calls.
- Streamlit pages — UI only, call API endpoints.
- Repositories under `app/repositories/` — one per table, SQL only.
- Tool wrappers under `app/chatbot/tools/` — translate LLM tool schemas to service calls.

## Request lifecycle — one chat turn

What happens when a `POST /api/chat` comes in:

```
1.  app/api/chat.py:75  @router.post("")
        │  resolves current_principal (User | AnonWidgetSession)
        │  parses ChatRequest { user_msg, conversation_id }
        ▼
2.  app/services/chatbot.py:115  run_turn(...)
        │  tracing.update_current_trace(session_id=conv_id, …)   ← Langfuse span root
        │  bind_trace_id(...)                                    ← structlog ContextVar
        │  set current_user_id / widget_session_id / conv_id ContextVars
        ▼
3.  _recall(user_id, user_msg)            (chatbot.py:79)
        │  → MemoryService.recall(...)    (services/memory.py)
        │     ├─ embed(user_msg, mode='query')   via ModelServerClient
        │     ├─ pgvector top-k cosine ≥ 0.6      via repositories/memory.py
        │     └─ best-effort audit-log INSERT     (failure does NOT block recall)
        │  ← list[RecalledMemory] (or [] on infra error — fail-open)
        ▼
4.  system prompt assembled                                  (chatbot.py:138)
        │  BASE_SYSTEM_PROMPT + <recalled_memories>...</recalled_memories>
        │  messages = [system, user]
        ▼
5.  stream_chat_with_tools(messages, tools=TOOL_SCHEMAS)     (infra/llm_groq.py)
        │  routes to Groq OR OpenRouter (LLM_PROVIDER env switch)
        │  yields {type: 'token'|'tool_call_request', …}
        ▼
6.  for each tool_call_request:
        │  _dispatch_tool(tc)              (chatbot.py:102)
        │  → TOOL_DISPATCH[name](**args)   one of:
        │     • classify_issue   → ModelServerClient.classify
        │     • extract_entities → ModelServerClient.extract
        │     • summarize_thread → SummarizerService
        │     • search_knowledge → RAGService.retrieve (HyDE+hybrid+rerank+parent)
        │     • write_memory    → MemoryService.write (redact-before-embed)
        │  yields {type: 'tool_call_result', name, result}
        ▼
7.  second stream_chat_with_tools — assistant turns tool results into prose
        │  yields {type: 'token', content: …}
        ▼
8.  yields {type: 'done', msg_id} — caller maps the AsyncIterator to SSE frames
```

Three properties worth flagging:

- **Memory is augmentation, not correctness.** A recall failure logs + returns
  `[]`. The model still answers; it just answers without context.
- **The tool boundary is a port.** Tools are looked up in `TOOL_DISPATCH` (a
  plain function table); adding a new one is a new file under
  `app/chatbot/tools/` + a service. PRD user-story 45 codifies this.
- **Redaction runs before persistence**, not after. `MemoryService.write` redacts
  the summary BEFORE the embed call so the vector never encodes the raw secret.

## Refuse-to-boot (8 checks)

`app/boot/validator.py` runs all 8 in order during the FastAPI lifespan; the
first failure raises an `InfraError` subclass, the lifespan maps it to
`BOOT FAIL #N: <message>` and calls `SystemExit(1)`. The api container exits
unhealthy; docker compose surfaces it as a failed deploy.

| # | Check | Failure → |
|---:|---|---|
| 1 | Vault reachable                              | `VaultError` |
| 2 | All required Vault paths load                | `VaultError` |
| 3 | DB at Alembic head                           | `DatabaseError` |
| 4 | model-server reports `classifier_loaded=true`| `ClassifierUnavailable` |
| 5 | model-server `weights_sha` matches pin       | `InfraError` |
| 6 | Langfuse `auth_check()` succeeds             | `LLMProviderError` |
| 7 | every eval threshold strictly `0 < x < 1`    | `InfraError` (brief-mandated refusal) |
| 8 | every prompt file SHA matches `PROMPT_SHAS`  | `InfraError` |

Checks #4 + #5 share an env-var bypass (`MC_BOOT_SKIP_CLASSIFIER=1`) used in
CI where artifacts aren't available. See RUNBOOK §Classifier carve-out.

## See also

- [PRD.md](PRD.md) — locked product + design decisions Q1–Q30
- [DECISIONS.md](DECISIONS.md) — numbers behind every choice
- [RUNBOOK.md](RUNBOOK.md) — ops, recovery, Vault layout, CI carve-outs
- [SECURITY.md](SECURITY.md) — redaction patterns + boundary hookup diagram
- [EVALS.md](EVALS.md) — eval methodology + golden-set numbers
- [docs/model_card.md](docs/model_card.md) — classifier card
