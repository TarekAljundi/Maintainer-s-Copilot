FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv

# Layer 1: heavy ML deps. Re-runs only when this explicit list changes — NOT
# when pyproject.toml or source code changes.
# Keep this list in sync with the `model-server` extras' large items.
RUN uv pip install --system \
    torch transformers accelerate sentence-transformers \
    spacy sentencepiece tiktoken \
 && python -m spacy download en_core_web_sm

# Layer 2: lighter infra/service deps. Cheap to reinstall.
# Mirrors pyproject top-level `dependencies` + small bits from extras.
RUN uv pip install --system \
    fastapi uvicorn pydantic pydantic-settings httpx structlog minio hvac

# Layer 3: source. Source changes invalidate only --no-deps install (fast).
COPY pyproject.toml ./
COPY app ./app
COPY model_server ./model_server
RUN uv pip install --system --no-deps -e .

EXPOSE 8001
CMD ["uvicorn", "model_server.main:app", "--host", "0.0.0.0", "--port", "8001"]
