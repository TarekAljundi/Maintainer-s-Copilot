"""FastAPI inference server. Loads classifier + reranker + embedder + spaCy at startup.

Endpoints: /health, /model_card, /classify, /ner, /rerank, /embed
"""

from fastapi import FastAPI

app = FastAPI(title="model-server")


@app.get("/health")
def health() -> dict:
    return {"classifier_loaded": False, "reranker_loaded": False, "embedder_loaded": False}
