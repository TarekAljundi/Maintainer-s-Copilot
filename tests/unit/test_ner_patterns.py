"""NER regex patterns — verified as plain Python regexes, no spaCy needed.

spaCy applies these as token-level REGEX rules inside an EntityRuler; what
matters at the pattern level is that each regex matches the strings spaCy will
present as a single token. The integration test (slice 15 smoke) covers the
full pipeline; this file gates the patterns themselves so CI catches regex
bugs in the 5-second unit job.
"""

from __future__ import annotations

import re

import pytest

from model_server.ner import LABELS, PATTERNS, build_entity_ruler_patterns

# (label, sample) → must match
POSITIVE_CASES = [
    ("IDENTIFIER", "foo_bar"),
    ("IDENTIFIER", "user_id"),
    ("IDENTIFIER", "fooBar"),
    ("IDENTIFIER", "handleRequest"),
    ("FILE_PATH", "app/main.py"),
    ("FILE_PATH", "docs/index.md"),
    ("FILE_PATH", "pyproject.toml"),
    ("FILE_PATH", "data/splits/train.jsonl".replace(".jsonl", ".json")),
    ("ERROR_TYPE", "ValueError"),
    ("ERROR_TYPE", "RequestValidationError"),
    ("ERROR_TYPE", "DeprecationWarning"),
    ("VERSION", "0.109.0"),
    ("VERSION", "v1.2"),
    ("VERSION", "0.114.0rc1"),
    ("PR_REF", "tiangolo/fastapi#1234"),
    ("URL", "https://github.com/fastapi/fastapi/issues/9246"),
    ("URL", "http://localhost:8000/health"),
    ("MODULE", "app.services.summarizer"),
    ("MODULE", "pydantic.fields.Field"),
]

# (label, sample) → must NOT match (these are the false positives we care about)
NEGATIVE_CASES = [
    ("IDENTIFIER", "hello"),  # plain lowercase word — too generic to claim
    ("IDENTIFIER", "Test"),  # PascalCase first letter caps — not snake/camel rule
    ("FILE_PATH", "main"),  # no extension
    ("FILE_PATH", "see also.md and"),  # spaces — won't be one token anyway
    ("ERROR_TYPE", "Errorless"),  # contains "Error" but doesn't end with it
    ("ERROR_TYPE", "error"),  # lowercase
    ("VERSION", "1234"),  # bare integer
    ("VERSION", "12.x"),  # non-numeric segment
    ("PR_REF", "#1234"),  # 2-token case handled by separate rule, not this regex
    ("URL", "github.com/foo/bar"),  # no scheme
    ("MODULE", "foo"),  # single segment
    ("MODULE", "Foo.Bar"),  # uppercase first letter — not snake-style modules
]


@pytest.mark.parametrize("label,sample", POSITIVE_CASES, ids=lambda x: str(x))
def test_pattern_matches_positive(label: str, sample: str) -> None:
    assert label in LABELS, f"unknown label {label!r}"
    pat = re.compile(PATTERNS[label])
    assert pat.match(sample), f"pattern for {label} failed to match {sample!r}"


@pytest.mark.parametrize("label,sample", NEGATIVE_CASES, ids=lambda x: str(x))
def test_pattern_rejects_negative(label: str, sample: str) -> None:
    pat = re.compile(PATTERNS[label])
    assert not pat.match(sample), f"pattern for {label} unexpectedly matched {sample!r}"


def test_entity_ruler_pattern_shape() -> None:
    """EntityRuler patterns: each is {label, pattern: [...]}. PR_REF has both 1-tok + 2-tok."""
    patterns = build_entity_ruler_patterns()
    labels_emitted = [p["label"] for p in patterns]
    # One pattern per LABELS entry, plus the extra 2-token PR_REF rule.
    assert sorted(labels_emitted) == sorted(list(LABELS) + ["PR_REF"])
    for p in patterns:
        assert "pattern" in p
        assert isinstance(p["pattern"], list) and len(p["pattern"]) >= 1
        for token in p["pattern"]:
            assert isinstance(token, dict)
            # Either {"TEXT": "..."} or {"TEXT": {"REGEX": "..."}}
            assert "TEXT" in token
