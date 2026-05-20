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
