"""FastAPI app factory + lifespan. Boot checks live in app/boot — the
lifespan delegates to BootValidator.validate_all() which surfaces the first
failing check as `BOOT FAIL #N: ...` + SystemExit(1)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import error_handlers
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.memory import router as memory_router
from app.api.middleware import RequestIDMiddleware
from app.boot import BootValidator
from app.infra.logging import configure as configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    await BootValidator().validate_all()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Maintainer's Copilot API", lifespan=lifespan)
    app.add_middleware(RequestIDMiddleware)
    error_handlers.register(app)
    app.include_router(chat_router, prefix="/api")
    app.include_router(memory_router, prefix="/api")
    app.include_router(auth_router)
    app.include_router(admin_router, prefix="/api")
    return app


app = create_app()
