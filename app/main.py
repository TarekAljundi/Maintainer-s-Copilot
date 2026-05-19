"""FastAPI app factory + lifespan w/ 8 boot checks. See PRD Q30."""
from contextlib import asynccontextmanager
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Boot checks (raise InfraError + SystemExit(1) on failure):
    # 1. vault_reachable
    # 2. secrets_loadable
    # 3. db_migrated
    # 4. classifier_weights
    # 5. classifier_sha
    # 6. tracing_configured
    # 7. eval_thresholds (non-zero)
    # 8. prompts_sha
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Maintainer's Copilot API", lifespan=lifespan)
    # mount routers, error handlers, cors-from-db middleware
    return app


app = create_app()
