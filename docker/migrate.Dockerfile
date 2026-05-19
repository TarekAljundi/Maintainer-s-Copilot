FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY model_server ./model_server
RUN uv pip install --system -e ".[api]"
CMD ["alembic", "upgrade", "head"]
