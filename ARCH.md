# Architecture

## Services (docker-compose)

| Service | Role |
|---|---|
| `api` | FastAPI: auth, chat, memory, RAG orchestration, widget config |
| `chatbot` | Streamlit: login, full chat, memory inspector, admin |
| `widget` | Static server: built Preact bundle + loader.js |
| `model-server` | FastAPI inference: classifier, NER, reranker, embedder |
| `host` | Demo nginx serving an allowed-origin host page |
| `host-blocked` | Demo nginx for blocked-origin demo |
| `migrate` | One-shot alembic upgrade head |
| `db` | postgres:16 + pgvector |
| `redis` | redis:7 |
| `minio` | blob: models, eval reports, plots, snapshots |
| `vault` | dev mode |
| `langfuse` | tracing UI |

## Layers (api service)

- `app/api/` — HTTP only. No SQLAlchemy/Redis imports.
- `app/services/` — business logic, tx boundaries, cache + memory invalidation.
- `app/repositories/` — SQL only. No HTTP. One per table.
- `app/domain/` — Pydantic domain models, tools, exceptions.
- `app/infra/` — Vault, MinIO, Redis, Groq, model-server client, Langfuse, redaction, structlog, db.

## Data flow

- User msg -> `app/api/chat.py` -> `app/services/chatbot.py` (agent loop) ->
  tool dispatch (services/rag, services/memory, model-server client) ->
  Groq LLM -> SSE stream back.
- Trace: one Langfuse trace per chat turn, session_id = conversation_id.
- Memory recall: auto-injected from `app/services/memory.py` at turn start.

## Refuse to boot (8 checks)

See `app/main.py` lifespan. PRD Q30.
