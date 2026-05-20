"""Unit tests for the dual chunker (no model load — uses a stub tokenizer)."""

from __future__ import annotations

import re

import pytest

from app.services import chunking


class _FakeTokenizer:
    """Whitespace tokenizer that mimics the HF API used by chunking.py."""

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        return [i for i, _ in enumerate(re.findall(r"\S+", text))]

    def decode(self, token_ids: list[int], skip_special_tokens: bool = True) -> str:
        # Decode is only called on the windowed re-tokenization path, where we
        # can't recover the original text from indices. Tests that exercise
        # that path supply enough tokens via direct text instead.
        return " ".join(["tok"] * len(token_ids))


@pytest.fixture(autouse=True)
def _stub_tokenizer(monkeypatch):
    monkeypatch.setattr(chunking, "_tokenizer", lambda: _FakeTokenizer())


def test_parse_rst_sections_recognises_h1_h2():
    text = "Top\n===\npreamble line\n\nSub\n---\nchild body line\n"
    sections = chunking.parse_rst_sections(text)
    assert sections[0] == (0, "", "")
    assert sections[1] == (1, "Top", "preamble line")
    assert sections[2] == (2, "Sub", "child body line")


def test_chunk_docs_drops_short_section_and_assigns_breadcrumb():
    text = (
        "Top\n"
        "===\n"
        "tiny\n\n"  # below DOCS_MIN_TOKENS — dropped
        "Sub\n"
        "---\n"
        + ("longword " * 60).strip()  # ~60 tokens, kept as single chunk
        + "\n"
    )
    chunks = chunking.chunk_docs(text, source_id="doc/source/foo.rst")
    assert len(chunks) == 1
    c = chunks[0]
    assert c.content_type == "docs"
    assert c.source_id == "doc/source/foo.rst"
    assert c.breadcrumb == "Top > Sub"
    assert c.section_path == "doc/source/foo.rst#sub"


def test_chunk_docs_stable_ids():
    text = "Top\n===\n" + ("word " * 60) + "\n"
    a = chunking.chunk_docs(text, "x.rst")
    b = chunking.chunk_docs(text, "x.rst")
    assert [c.id for c in a] == [c.id for c in b]


def test_chunk_docs_non_windowed_section_has_no_parent():
    """Sections at or below DOCS_MAX_TOKENS emit a single chunk with parent_id=None."""
    text = "Top\n===\n" + ("word " * 60) + "\n"  # ~60 tokens, single chunk
    chunks = chunking.chunk_docs(text, "x.rst")
    assert len(chunks) == 1
    assert chunks[0].parent_id is None
    assert chunks[0].chunk_seq == 0


def test_chunk_docs_windowed_section_emits_parent_first():
    """Long sections emit a parent chunk (chunk_seq=-1) followed by N children
    (each carrying parent_id), preserving slice-06 child IDs."""
    long_body = " ".join(f"w{i}" for i in range(700))  # 700 tokens > DOCS_MAX_TOKENS
    text = f"Top\n===\n{long_body}\n"
    chunks = chunking.chunk_docs(text, "x.rst")

    parents = [c for c in chunks if c.chunk_seq == -1]
    children = [c for c in chunks if c.chunk_seq >= 0]

    assert len(parents) == 1
    assert parents[0].parent_id is None
    assert parents[0].text == long_body
    assert parents[0].section_path == "x.rst#top"

    assert len(children) >= 2  # 700 / (500-50) > 1
    for child in children:
        assert child.parent_id == parents[0].id
        assert child.section_path == "x.rst#top"

    # parent emitted before children
    assert chunks[0] is parents[0]


def test_chunk_issue_chunks_have_null_parent():
    """Issue chunks self-parent (parent_id=None); slice-07 doesn't sub-window comments."""
    record = {
        "number": 99,
        "title": "x",
        "body": "x " * 50,
        "labels": [],
        "closed_at": None,
        "comments": [{"body": "y " * 50, "author_association": "MEMBER"}],
    }
    chunks = chunking.chunk_issue(record)
    assert all(c.parent_id is None for c in chunks)


def test_chunk_issue_merges_tiny_comment():
    record = {
        "number": 42,
        "title": "BUG: something",
        "body": "long body " * 50,
        "labels": ["Bug"],
        "closed_at": "2025-01-01T00:00:00Z",
        "comments": [
            {"body": "tiny", "author_association": "NONE"},  # < 30 tokens, merges
            {"body": "real answer " * 50, "author_association": "MEMBER"},
        ],
    }
    chunks = chunking.chunk_issue(record)
    assert len(chunks) == 2
    assert chunks[0].content_type == "issue"
    assert chunks[0].source_id == "42"
    assert chunks[0].text.startswith("Issue #42: BUG: something")
    assert "tiny" in chunks[0].text  # merged into the head chunk
    assert chunks[1].is_answer is True


def test_chunk_issue_is_answer_only_for_maintainer():
    record = {
        "number": 7,
        "title": "t",
        "body": "x " * 50,
        "labels": [],
        "closed_at": None,
        "comments": [
            {"body": "drive-by " * 50, "author_association": "NONE"},
            {"body": "maintainer " * 50, "author_association": "OWNER"},
        ],
    }
    chunks = chunking.chunk_issue(record)
    assert chunks[0].is_answer is False
    assert chunks[1].is_answer is False
    assert chunks[2].is_answer is True
