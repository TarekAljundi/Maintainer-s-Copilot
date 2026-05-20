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
Free tier ~30 req/min on some models. CI eval throttles via `asyncio.Semaphore(5)`.

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
