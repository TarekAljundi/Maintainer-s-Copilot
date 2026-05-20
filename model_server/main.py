"""FastAPI inference server. Loads classifier + NER + embedder at startup.

Slice 05: classifier + NER wired. Slice 06: embedder wired (bge-base).
Reranker stub returns False on /health until slice 07.

Endpoints: /health, /classify, /extract, /embed
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from model_server.classifier import Classifier
from model_server.embedder import Embedder
from model_server.ner import NERService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

_classifier: Classifier | None = None
_ner: NERService | None = None
_embedder: Embedder | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _classifier, _ner, _embedder
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
    if os.environ.get("MC_SKIP_EMBEDDER_LOAD") == "1":
        log.warning("MC_SKIP_EMBEDDER_LOAD=1 — serving with embedder_loaded=false")
    else:
        try:
            _embedder = Embedder()
        except Exception:
            log.exception("embedder load failed; /health will report embedder_loaded=false")
    yield


app = FastAPI(title="model-server", lifespan=lifespan)


class ClassifyRequest(BaseModel):
    text: str
    title: str | None = None


class ExtractRequest(BaseModel):
    text: str


class EmbedRequest(BaseModel):
    texts: list[str]
    mode: str = "passage"


@app.get("/health")
def health() -> dict:
    return {
        "classifier_loaded": _classifier is not None,
        "weights_sha": _classifier.weights_sha if _classifier else "",
        "ner_loaded": _ner is not None,
        "reranker_loaded": False,
        "embedder_loaded": _embedder is not None,
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


@app.post("/embed")
def embed(req: EmbedRequest) -> dict:
    if _embedder is None:
        raise HTTPException(status_code=503, detail="embedder_not_loaded")
    if req.mode not in ("query", "passage"):
        raise HTTPException(status_code=400, detail="mode must be 'query' or 'passage'")
    return {"embeddings": _embedder.encode(req.texts, mode=req.mode)}
