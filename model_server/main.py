"""FastAPI inference server. Loads classifier + NER + reranker + embedder at startup.

Slice 05: classifier (slice 03) + NER are wired. Reranker / embedder stubs
return False on /health until their owning slices (07) land.

Endpoints: /health, /classify, /extract
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from model_server.classifier import Classifier
from model_server.ner import NERService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

_classifier: Classifier | None = None
_ner: NERService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _classifier, _ner
    if os.environ.get("MC_SKIP_CLASSIFIER_LOAD") == "1":
        log.warning("MC_SKIP_CLASSIFIER_LOAD=1 — serving with classifier_loaded=false")
    else:
        try:
            _classifier = Classifier()
            log.info("classifier ready, weights_sha=%s", _classifier.weights_sha)
        except Exception:
            log.exception("classifier load failed; /health will report classifier_loaded=false")
    if os.environ.get("MC_SKIP_NER_LOAD") == "1":
        log.warning("MC_SKIP_NER_LOAD=1 — serving with ner_loaded=false")
    else:
        try:
            _ner = NERService()
        except Exception:
            log.exception("NER load failed; /health will report ner_loaded=false")
    yield


app = FastAPI(title="model-server", lifespan=lifespan)


class ClassifyRequest(BaseModel):
    text: str
    title: str | None = None


class ExtractRequest(BaseModel):
    text: str


@app.get("/health")
def health() -> dict:
    return {
        "classifier_loaded": _classifier is not None,
        "weights_sha": _classifier.weights_sha if _classifier else "",
        "ner_loaded": _ner is not None,
        "reranker_loaded": False,
        "embedder_loaded": False,
    }


@app.post("/classify")
def classify(req: ClassifyRequest) -> dict:
    if _classifier is None:
        raise HTTPException(status_code=503, detail="classifier_not_loaded")
    return _classifier.predict(req.text, title=req.title)


@app.post("/extract")
def extract(req: ExtractRequest) -> dict:
    if _ner is None:
        raise HTTPException(status_code=503, detail="ner_not_loaded")
    return {"entities": _ner.extract(req.text)}
