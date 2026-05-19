"""FastAPI app factory + lifespan w/ boot checks. See PRD §Boot-time refusal."""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.domain.exceptions import VaultError
from app.infra.vault import REQUIRED_PATHS, get_vault


@asynccontextmanager
async def lifespan(app: FastAPI):
    vault = get_vault()

    # Boot check #1: Vault reachable (unsealed).
    if not vault.health():
        print("BOOT FAIL #1: vault unreachable or sealed", file=sys.stderr)
        raise SystemExit(1)

    # Boot check #2: all required secret paths load.
    try:
        vault.load_all(REQUIRED_PATHS)
    except VaultError as exc:
        print(f"BOOT FAIL #2: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    # TODO slices 02-14: checks #3-#8 (db head, classifier loaded, classifier SHA,
    # tracing, eval thresholds, prompt SHAs).
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Maintainer's Copilot API", lifespan=lifespan)
    app.include_router(chat_router, prefix="/api")
    return app


app = create_app()
