"""pull_dataset pure-logic tests. No network, no MinIO."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import pull_dataset as pd  # noqa: E402


def test_resolve_label_priority_tie_break():
    # bug > feature > docs > question
    assert pd.resolve_label(["docs", "bug", "question"]) == "bug"
    assert pd.resolve_label(["feature", "docs"]) == "feature"
    assert pd.resolve_label(["docs", "question"]) == "docs"
    assert pd.resolve_label(["question"]) == "question"


def test_resolve_label_ignores_workflow_and_component_labels():
    assert pd.resolve_label(["answered", "reviewed", "security", "dependencies"]) is None
    assert pd.resolve_label(["docs", "answered", "security"]) == "docs"


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


def test_has_maintainer_answer_via_answered_label():
    assert pd.has_maintainer_answer(["answered"], [])
    assert pd.has_maintainer_answer(["ANSWERED"], [])  # case insensitive


def test_has_maintainer_answer_via_comment_association():
    comments = [{"author_association": "MEMBER", "body": "hi"}]
    assert pd.has_maintainer_answer([], comments)
    assert not pd.has_maintainer_answer([], [{"author_association": "NONE"}])
    assert not pd.has_maintainer_answer([], [])


def test_per_class_counts():
    records = [{"label": "bug"}, {"label": "bug"}, {"label": "docs"}, {"label": "question"}]
    counts = pd.per_class_counts(records)
    assert counts["bug"] == 2
    assert counts["docs"] == 1
    assert counts["question"] == 1
    assert counts.get("feature", 0) == 0
