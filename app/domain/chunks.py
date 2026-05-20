"""Chunk dataclasses + stable ID helper."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

ContentType = Literal["docs", "issue"]


def stable_chunk_id(content_type: ContentType, source_id: str, chunk_seq: int) -> str:
    raw = f"{content_type}|{source_id}|{chunk_seq}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Chunk:
    id: str
    content_type: ContentType
    source_id: str
    chunk_seq: int
    text: str
    parent_id: str | None = None
    breadcrumb: str | None = None
    section_path: str | None = None
    labels: list[str] | None = None
    is_answer: bool | None = None
    closed_at: str | None = None
    embedding: list[float] = field(default_factory=list)


@dataclass
class RetrievedChunk:
    id: str
    content_type: ContentType
    source_id: str
    text: str
    score: float
    breadcrumb: str | None = None
    section_path: str | None = None
    labels: list[str] | None = None
    is_answer: bool | None = None

    def citation(self) -> str:
        if self.content_type == "docs":
            return self.breadcrumb or self.section_path or self.source_id
        return f"#{self.source_id}"
