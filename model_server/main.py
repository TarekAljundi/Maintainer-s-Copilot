"""FastAPI inference server. Loads classifier + reranker + embedder + spaCy at startup.

Slice 03: classifier loads from MinIO; /classify + /health are wired. Reranker,
embedder, spaCy stubs return False on /health for now (filled in later slices).

Endpoints: /health, /classify
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from model_server.classifier import Classifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

_classifier: Classifier | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _classifier
    if os.environ.get("MC_SKIP_CLASSIFIER_LOAD") == "1":
        log.warning("MC_SKIP_CLASSIFIER_LOAD=1 — serving with classifier_loaded=false")
    else:
        try:
            _classifier = Classifier()
            log.info("classifier ready, weights_sha=%s", _classifier.weights_sha)
        except Exception:
            log.exception("classifier load failed; /health will report classifier_loaded=false")
    yield


app = FastAPI(title="model-server", lifespan=lifespan)


class ClassifyRequest(BaseModel):
    text: str
    title: str | None = None


@app.get("/health")
def health() -> dict:
    return {
        "classifier_loaded": _classifier is not None,
        "weights_sha": _classifier.weights_sha if _classifier else "",
        "reranker_loaded": False,
        "embedder_loaded": False,
    }


@app.post("/classify")
def classify(req: ClassifyRequest) -> dict:
    if _classifier is None:
        raise HTTPException(status_code=503, detail="classifier_not_loaded")
    return _classifier.predict(req.text, title=req.title)
