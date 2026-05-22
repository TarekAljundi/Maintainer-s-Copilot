FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
COPY app ./app
COPY model_server ./model_server
COPY streamlit_app ./streamlit_app
COPY .streamlit ./.streamlit
RUN uv pip install --system -e ".[streamlit]"
EXPOSE 8501
CMD ["streamlit", "run", "streamlit_app/Home.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
