"""Domain + infra exception hierarchy. See PRD Q23."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code: str = "app_error"
    status_code: int = 500

    def __init__(self, message: str = "", **extras: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extras: dict[str, Any] = dict(extras)


class DomainError(AppError): ...


class NotFoundError(DomainError):
    code = "not_found"
    status_code = 404


class ConflictError(DomainError):
    code = "conflict"
    status_code = 409


class ValidationError(DomainError):
    code = "invalid_input"
    status_code = 422


class PermissionDenied(DomainError):
    code = "forbidden"
    status_code = 403


class RateLimitExceeded(DomainError):
    code = "rate_limited"
    status_code = 429


class ToolFailure(AppError):
    """Caught by ChatbotService; never reaches API handler."""

    code = "tool_failure"


class ClassifierUnavailable(ToolFailure):
    code = "tool_failure.classifier"


class RAGRetrievalFailure(ToolFailure):
    code = "tool_failure.rag"


class NERFailure(ToolFailure):
    code = "tool_failure.ner"


class SummarizerFailure(ToolFailure):
    code = "tool_failure.summarizer"


class MemoryWriteFailure(ToolFailure):
    code = "tool_failure.memory"


class InfraError(AppError):
    code = "upstream_unavailable"
    status_code = 503


class VaultError(InfraError):
    code = "vault_unavailable"


class LLMProviderError(InfraError):
    code = "llm_unavailable"


class DatabaseError(InfraError):
    code = "db_unavailable"


class BlobError(InfraError):
    code = "blob_unavailable"
