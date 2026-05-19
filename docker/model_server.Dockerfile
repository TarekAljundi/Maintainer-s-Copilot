FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
COPY app ./app
COPY model_server ./model_server
RUN uv pip install --system -e ".[model-server]"
RUN python -m spacy download en_core_web_sm
EXPOSE 8001
CMD ["uvicorn", "model_server.main:app", "--host", "0.0.0.0", "--port", "8001"]
