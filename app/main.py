"""FastAPI app factory + lifespan w/ boot checks. See PRD §Boot-time refusal."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.domain.exceptions import ClassifierUnavailable, VaultError
from app.infra._classifier_registry import WEIGHTS_SHA256
from app.infra.model_server_client import ModelServerClient
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

    # Boot check #4: model-server reports classifier_loaded=true.
    # Boot check #5: model-server-reported weights_sha matches the pinned value
    #                in app.infra._classifier_registry.WEIGHTS_SHA256.
    try:
        ms_health = ModelServerClient().health()
    except ClassifierUnavailable as exc:
        print(f"BOOT FAIL #4: model-server unreachable: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if not ms_health.get("classifier_loaded"):
        print("BOOT FAIL #4: model-server reports classifier_loaded=false", file=sys.stderr)
        raise SystemExit(1)

    reported_sha = ms_health.get("weights_sha") or ""
    if WEIGHTS_SHA256 and reported_sha != WEIGHTS_SHA256:
        print(
            f"BOOT FAIL #5: weights SHA mismatch — pinned={WEIGHTS_SHA256} reported={reported_sha}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # TODO slices 12/14/15: checks #3 (db head), #6 (tracing), #7 (eval thresholds),
    # #8 (prompt SHAs).
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Maintainer's Copilot API", lifespan=lifespan)
    app.include_router(chat_router, prefix="/api")
    return app


app = create_app()
