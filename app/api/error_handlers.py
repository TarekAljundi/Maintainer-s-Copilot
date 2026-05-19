"""Single exception handler mapping AppError -> JSON envelope w/ trace_id + request_id."""
from fastapi import FastAPI


def register(app: FastAPI) -> None:
    # @app.exception_handler(AppError) -> JSONResponse
    # @app.exception_handler(Exception) -> 500 envelope
    ...
