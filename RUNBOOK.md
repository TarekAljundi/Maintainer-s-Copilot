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

## Langfuse first-run
First boot creates a project. Disable signups (env). Capture project keys -> Vault `api/tracing`.
