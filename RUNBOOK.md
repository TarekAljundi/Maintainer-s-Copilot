# Runbook

## First boot from a fresh clone

```bash
cp .env.example .env
# fill in VAULT_TOKEN (any string for dev)
docker compose up
```

Order of bring-up:
1. `vault`, `db`, `redis`, `minio` come up healthy.
2. `langfuse` boots (slow, ~30s).
3. `migrate` runs `alembic upgrade head` and exits 0.
4. `model-server` loads classifier + reranker + embedder + spaCy.
5. `api` runs 8 boot checks; refuses to start if any fail.
6. `chatbot`, `widget`, `host`, `host-blocked` come up.

## Vault paths
See ARCH.md / PRD Q30.

## Vault outage during runtime
- Cached secrets keep serving.
- Background poll every 60s emits warning if down >5min.
- App refuses to RE-boot until Vault reachable.

## Eval baseline reset
After intentional regression (e.g. swapping the deployed model), manually promote the new baseline:
```bash
uv run python -m evals.promote --sha <main-commit-sha>
```

## Groq rate-limit notes
Free tier ≈ 30 RPM on llama-3.3-70b. The RAG eval honors `RAG_EVAL_RPM_BUDGET`
(0 = unlimited; CI sets `25`) and sleeps `60/budget` seconds between requests.
The CI `smoke_and_evals` job runs with the `placeholder` GROQ key by default —
LLM-dependent eval models (`llm` classification baseline, HyDE in `--stack full`)
are skipped if no real key is configured as a GitHub repo secret. Add a real key
as `secrets.GROQ_API_KEY` to enable them.

## CI / GitHub Actions
4 stages — see `.github/workflows/ci.yml`:

1. **lint** (ruff + format-check + pyright + `bump_prompt_shas.py --check`), **unit** (`tests/unit` + `tests/test_layers.py`), **redaction** (`tests/integration/test_redaction_boundaries.py`) — all parallel.
2. **build** (matrix: api, model_server, streamlit, widget, migrate).
3. **smoke_and_evals**: `docker compose up`, `wait_healthy.sh`, hit `/api/health`, run `tests/smoke` + `pytest -m requires_pg` (the 10 PG-gated integration tests), classification + RAG retrieval evals, `evals.compare` against `s3://mc-evals/main/latest.json` (with `--bootstrap` for the first-ever run), upload the report to `s3://mc-evals/runs/<sha>.json`, gate.
4. **promote_baseline** (main only): `evals.promote --sha <sha>` copies the run to `main/latest.json` + `main/sha=<sha>.json`.

**Dual-gate semantics** (`evals.gate`): a metric passes iff `current >= floor AND current >= baseline - regression_margin`. Floors + margins live in `eval_thresholds.yaml`. The bootstrap run (no baseline) passes the regression check by definition.

## Add a tool live (user story 45)
PRD §User stories — *"as a maintainer I can add a new tool to the chatbot in
under 5 minutes without touching the API routers or repositories."* The minimal
diff:

1. New file `app/services/<your_tool>.py` — the business logic, one function or
   one small class with a narrow interface.
2. New file `app/chatbot/tools/<your_tool>.py` — a thin LLM-facing wrapper:
   ```python
   from app.chatbot.tools._base import register
   from app.services.your_tool import do_thing

   @register(name="your_tool", description="<one-line for the LLM>")
   async def your_tool(arg: str) -> dict:
       return {"result": do_thing(arg)}
   ```
3. (Nothing else.) The agent loop picks the tool up at import time; no router
   change, no repository change, no migration.

The layer-boundary test (`tests/test_layers.py`) guards step 1 — services may
not directly import `sqlalchemy`/`redis`/raw `httpx`; talk to infra through the
`app.infra.*` ports.

A 1-minute screencast of this flow lives in the Friday demo deck as fallback.

## Langfuse first-run (boot check #6 bootstrap)

The `langfuse` service is a self-hosted single-tenant install; first-boot
bootstrap is manual. Until real keys land in Vault the API runs in
*tracing-disabled* mode (no spans sent, boot check #6 is a no-op).

1. `docker compose up -d langfuse db redis` (no API yet).
2. Browse `http://localhost:${LANGFUSE_PORT}` → register the admin user
   (no SMTP; admin-only single-user mode per PRD §Out of Scope).
3. UI → create a project → copy `LF_PUBLIC_KEY` and `LF_SECRET_KEY`.
4. Disable open registration in the project settings.
5. Push the real keys into Vault, replacing the placeholders:
   ```bash
   docker compose exec vault sh -lc '
     vault kv put secret/api/tracing \
       langfuse_public_key="<lf_public>" \
       langfuse_secret_key="<lf_secret>" \
       langfuse_host="http://langfuse:3000"
   '
   ```
6. `docker compose up -d api` → boot check #6 hits Langfuse `/api/public/auth`
   via `langfuse.auth_check()`. Mismatch → `BOOT FAIL #6` and exit 1.

To force-disable tracing in dev (e.g. running tests against a partial stack):
set `LANGFUSE_DISABLED=1` in the api service env.

### Verifying trace tree after bootstrap

- Send a chat turn (`curl … /api/chat …`).
- Langfuse UI → Traces → most recent → tree should show
  `chat_turn` (root) → `memory.recall` (retrieval) → `llm.stream_chat_with_tools`
  (generation) → `tool.<name>` (tool, if any) → second `llm.stream_chat_with_tools`.
- The mandated **error-path trace** lives in the
  `tests/integration/test_tracing_error_path.py` integration test; running it
  against a live Langfuse container produces the demo artifact for slice 16.

## Widget admin bootstrap (slice 13)

The widget is embedded on third-party host pages via a single `<script>` tag.
Each widget is a row in `widget_configs` keyed by UUID; the row's
`allowed_origins` array drives both the iframe-embed CSP `frame-ancestors`
header and the dynamic CORS allowlist.

### 1. Create a widget config (admin only)

The admin needs a JWT with `role=admin` (see slice 10 / `auth/register` then
manually `UPDATE users SET role='admin' WHERE email = …;`).

```bash
TOKEN=...  # admin bearer
curl -X POST http://localhost:8000/api/admin/widgets \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "pandas-host-demo",
    "allowed_origins": ["http://localhost:8087"],
    "primary_color": "#1e293b",
    "position": "br",
    "greeting_text": "Ask me about pandas",
    "enabled_tools": ["classify_issue","extract_entities","summarize_thread","search_knowledge","write_memory"]
  }'
# => { "id": "<uuid>", ... }
```

Only the **allowed** demo host's origin (`http://localhost:8087`) is in the
allowlist. The **blocked** host (`http://localhost:8089`) is intentionally
omitted so its CSP enforcement is visible side-by-side.

### 2. Copy the embed snippet

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/admin/widgets/<uuid>/embed-snippet
# => { "snippet": "<script src=\"…/widget.js\" data-widget-id=\"<uuid>\"></script>" }
```

Paste the `snippet` into the `<body>` of the host page (or, in this repo,
replace `REPLACE_ME` in `demo/host/index.html` + `demo/host-blocked/index.html`
with the widget UUID).

### 3. Bring up the demo hosts

```bash
docker compose up -d host host-blocked
```

- Allowed host → `http://localhost:8087` — widget loads, chat works.
- Blocked host → `http://localhost:8089` — same snippet, but the iframe is
  refused by the browser. Open dev tools to see the CSP violation in the
  Console + the `Content-Security-Policy: frame-ancestors …` header on the
  `/widget/<uuid>/embed` response.

### 4. Anon-session lifecycle

The Preact bundle bootstraps by POSTing `/widget/<uuid>/session` (no auth) to
mint a JWT with `sub="widget_session:<uuid4>"`, `enabled_tools` snapshotted at
mint time. The token is bearer-passed on `/api/chat` exactly like an authed
user's JWT — the same `current_principal` dep resolves both.

Memory is keyed by `widget_session_id` for these tokens (see migration 004 —
nullable `user_id`, sibling `widget_session_id`, table-level CHECK). A widget
session can `write_memory` and auto-recall its own memories across
conversations within the JWT's lifetime; it cannot see another widget
session's memories.

### Editing the widget config

`PATCH /api/admin/widgets/<uuid>` accepts any subset of the fields. The Redis
cache (`widget_config:<uuid>`, TTL 60s) is invalidated on write.

`DELETE /api/admin/widgets/<uuid>` removes the row; existing minted sessions
keep working until their JWT expires (their `enabled_tools` snapshot is
intact), but new `/session` mints return 404.

### Audit trail

Every CRUD operation writes a row to `audit_log` with
`action="widget_config_<create|update|delete>"` and the admin's user UUID as
the actor. Query via `GET /api/admin/audit?actor=<admin-uuid>`.
