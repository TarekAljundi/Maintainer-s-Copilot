"""pull_dataset pure-logic tests. No network, no MinIO."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import pull_dataset as pd  # noqa: E402


def test_resolve_label_pandas_taxonomy_to_canonical():
    """pandas labels: Bug/Enhancement/Docs/Usage Question -> canonical 4 classes."""
    assert pd.resolve_label(["Bug"]) == "bug"
    assert pd.resolve_label(["Enhancement"]) == "feature"
    assert pd.resolve_label(["Docs"]) == "docs"
    assert pd.resolve_label(["Documentation"]) == "docs"  # alt spelling
    assert pd.resolve_label(["Usage Question"]) == "question"


def test_resolve_label_priority_tie_break():
    # bug > feature > docs > question  (after mapping through LABEL_MAP)
    assert pd.resolve_label(["Docs", "Bug", "Usage Question"]) == "bug"
    assert pd.resolve_label(["Enhancement", "Docs"]) == "feature"
    assert pd.resolve_label(["Docs", "Usage Question"]) == "docs"
    assert pd.resolve_label(["Usage Question"]) == "question"


def test_resolve_label_ignores_non_canonical_labels():
    """Workflow / component / subcomponent labels are ignored."""
    assert pd.resolve_label(["Needs Discussion", "Performance", "IO", "Indexing"]) is None
    assert pd.resolve_label(["Docs", "Needs Discussion", "good first issue"]) == "docs"


def test_resolve_label_unlabeled_returns_none():
    assert pd.resolve_label([]) is None
    assert pd.resolve_label(["random-other-label"]) is None


def test_is_eligible_filters():
    base = {"state": "closed", "created_at": "2021-05-01T00:00:00Z"}
    assert pd.is_eligible(base)
    assert not pd.is_eligible({**base, "pull_request": {"url": "x"}})
    assert not pd.is_eligible({**base, "state": "open"})
    assert not pd.is_eligible({**base, "state_reason": "not_planned"})
    assert not pd.is_eligible({**base, "created_at": "2019-12-31T00:00:00Z"})


def test_time_split_70_10_15_5_oldest_first():
    records = [{"closed_at": f"2020-01-{i:02d}T00:00:00Z"} for i in range(1, 21)]
    splits = pd.time_split(records)
    assert len(splits["train"]) == 14  # 70% of 20
    assert len(splits["val"]) == 2  # 10%
    assert len(splits["test"]) == 3  # 15%
    assert len(splits["rag_candidates"]) == 1  # remainder (5%)
    # Train is oldest, rag_candidates is newest.
    assert splits["train"][0]["closed_at"] < splits["rag_candidates"][-1]["closed_at"]


def test_has_maintainer_answer_via_comment_association():
    """pandas doesn't have an 'answered' label, so maintainer-comment is the sole signal."""
    comments = [{"author_association": "MEMBER", "body": "hi"}]
    assert pd.has_maintainer_answer([], comments)
    assert not pd.has_maintainer_answer([], [{"author_association": "NONE"}])
    assert not pd.has_maintainer_answer([], [])
    # Labels are ignored in the new pandas-based logic.
    assert not pd.has_maintainer_answer(["answered"], [])


def test_per_class_counts():
    records = [{"label": "bug"}, {"label": "bug"}, {"label": "docs"}, {"label": "question"}]
    counts = pd.per_class_counts(records)
    assert counts["bug"] == 2
    assert counts["docs"] == 1
    assert counts["question"] == 1
    assert counts.get("feature", 0) == 0
