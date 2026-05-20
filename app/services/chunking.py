"""Content-aware dual chunker.

Docs (RST):
  - Header-recursive on H1/H2/H3 (RST underline conventions).
  - Sub-split sections > 500 tokens with 50-token overlap.
  - Drop chunks < 50 tokens.
  - Code blocks preserved verbatim (contrast with classifier's <CODE> swap).
  - Parent-document retrieval: windowed sections also emit a parent chunk
    (full section text); each child carries parent_id (slice 07).

Issues (per-comment):
  - Chunk 1: "Issue #N: <title>\\n\\n<body>".
  - Each top-level comment is one chunk.
  - Comments < 30 tokens merged with prior.
  - is_answer = comment author_association in {OWNER, MEMBER, COLLABORATOR}.
  - Each comment is the parent of itself (no further sub-windowing in slice 07);
    parent_id stays NULL — the retrieval pipeline treats NULL-parent chunks
    as their own parent.

Token counting uses the bge-base tokenizer so the budget matches the embedder.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Iterable

from app.domain.chunks import Chunk, stable_chunk_id, stable_parent_id

DOCS_MAX_TOKENS = 500
DOCS_OVERLAP_TOKENS = 50
DOCS_MIN_TOKENS = 50
ISSUE_MERGE_TOKENS = 30
MAINTAINER_ASSOC = {"OWNER", "MEMBER", "COLLABORATOR"}

RST_UNDERLINE_CHARS = set("=-~^+*\"'`#:.")
HEADING_RE = re.compile(r"^([=\-~^+*\"'`#:.])\1{2,}\s*$")


@lru_cache(maxsize=1)
def _tokenizer():
    from pathlib import Path

    from transformers import AutoTokenizer

    cache_dir = os.environ.get("MC_MODEL_CACHE", str(Path.home() / ".cache" / "mc-models"))
    return AutoTokenizer.from_pretrained("BAAI/bge-base-en-v1.5", cache_dir=cache_dir)


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_tokenizer().encode(text, add_special_tokens=False))


# ---------- RST parsing ----------


def parse_rst_sections(text: str) -> list[tuple[int, str, str]]:
    """Return list of (level, title, body). Level is 1-indexed; 0 = preamble.

    Heading detection: a non-blank text line followed by an underline of one
    repeated punctuation char (>=3, length >= len(title)). The first underline
    char encountered defines H1; the second new char defines H2; etc.
    """
    lines = text.splitlines()
    level_of: dict[str, int] = {}
    sections: list[tuple[int, str, list[str]]] = [(0, "", [])]

    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            i + 1 < len(lines)
            and line.strip()
            and HEADING_RE.match(lines[i + 1])
            and len(lines[i + 1].strip()) >= len(line.strip())
        ):
            ch = lines[i + 1].strip()[0]
            if ch not in level_of:
                level_of[ch] = len(level_of) + 1
            sections.append((level_of[ch], line.strip(), []))
            i += 2
            continue
        sections[-1][2].append(line)
        i += 1

    return [(lvl, title, "\n".join(body).strip()) for lvl, title, body in sections]


def _breadcrumbs(sections: Iterable[tuple[int, str, str]]) -> list[tuple[str, str, str]]:
    """For each non-preamble section emit (breadcrumb, anchor, body)."""
    stack: list[tuple[int, str]] = []
    out: list[tuple[str, str, str]] = []
    for lvl, title, body in sections:
        if lvl == 0:
            continue
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        stack.append((lvl, title))
        breadcrumb = " > ".join(t for _, t in stack)
        anchor = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        out.append((breadcrumb, anchor, body))
    return out


def _windowed(token_ids: list[int], max_tokens: int, overlap: int) -> list[list[int]]:
    step = max_tokens - overlap
    windows: list[list[int]] = []
    start = 0
    n = len(token_ids)
    while start < n:
        end = min(start + max_tokens, n)
        windows.append(token_ids[start:end])
        if end == n:
            break
        start += step
    return windows


# ---------- public API ----------


def chunk_docs(rst_text: str, source_id: str) -> list[Chunk]:
    """source_id is the RST file path relative to the docs root.

    Slice 07: windowed sections also emit a parent chunk carrying the full
    section text; each child references it via parent_id. Non-windowed
    sections emit a single chunk with parent_id=None (self-parent semantics).
    Existing slice-06 child chunk IDs are unchanged.
    """
    chunks: list[Chunk] = []
    seq = 0
    tok = _tokenizer()
    for breadcrumb, anchor, body in _breadcrumbs(parse_rst_sections(rst_text)):
        if not body.strip():
            continue
        token_ids = tok.encode(body, add_special_tokens=False)
        if len(token_ids) <= DOCS_MAX_TOKENS:
            if len(token_ids) < DOCS_MIN_TOKENS:
                continue
            cid = stable_chunk_id("docs", source_id, seq)
            chunks.append(
                Chunk(
                    id=cid,
                    content_type="docs",
                    source_id=source_id,
                    chunk_seq=seq,
                    text=body,
                    breadcrumb=breadcrumb,
                    section_path=f"{source_id}#{anchor}",
                )
            )
            seq += 1
            continue
        # Windowed path: emit a parent chunk carrying the full section text,
        # then each windowed child with parent_id set.
        parent_id = stable_parent_id("docs", source_id, anchor)
        chunks.append(
            Chunk(
                id=parent_id,
                content_type="docs",
                source_id=source_id,
                chunk_seq=-1,
                text=body,
                breadcrumb=breadcrumb,
                section_path=f"{source_id}#{anchor}",
            )
        )
        for window in _windowed(token_ids, DOCS_MAX_TOKENS, DOCS_OVERLAP_TOKENS):
            if len(window) < DOCS_MIN_TOKENS:
                continue
            piece = tok.decode(window, skip_special_tokens=True)
            cid = stable_chunk_id("docs", source_id, seq)
            chunks.append(
                Chunk(
                    id=cid,
                    content_type="docs",
                    source_id=source_id,
                    chunk_seq=seq,
                    text=piece,
                    parent_id=parent_id,
                    breadcrumb=breadcrumb,
                    section_path=f"{source_id}#{anchor}",
                )
            )
            seq += 1
    return chunks


def chunk_issue(record: dict) -> list[Chunk]:
    """record shape matches data/splits/rag_holdout.jsonl entries."""
    number = record["number"]
    source_id = str(number)
    closed_at = record.get("closed_at")
    labels = record.get("labels") or []

    pieces: list[tuple[str, bool]] = []
    head = f"Issue #{number}: {record.get('title', '')}\n\n{record.get('body') or ''}".strip()
    pieces.append((head, False))

    for c in record.get("comments") or []:
        body = (c.get("body") or "").strip()
        if not body:
            continue
        is_answer = (c.get("author_association") or "").upper() in MAINTAINER_ASSOC
        pieces.append((body, is_answer))

    merged: list[tuple[str, bool]] = []
    for text, is_ans in pieces:
        if not merged:
            merged.append((text, is_ans))
            continue
        if count_tokens(text) < ISSUE_MERGE_TOKENS:
            prev_text, prev_ans = merged[-1]
            merged[-1] = (prev_text + "\n\n" + text, prev_ans or is_ans)
        else:
            merged.append((text, is_ans))

    chunks: list[Chunk] = []
    for seq, (text, is_ans) in enumerate(merged):
        cid = stable_chunk_id("issue", source_id, seq)
        chunks.append(
            Chunk(
                id=cid,
                content_type="issue",
                source_id=source_id,
                chunk_seq=seq,
                text=text,
                labels=labels,
                is_answer=is_ans,
                closed_at=closed_at,
            )
        )
    return chunks
