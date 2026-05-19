"""Unit tests for slice 04 classification baselines.

Light-touch — every test should run in <2s without GROQ_API_KEY, the model
artifact, or MinIO. We're checking shapes and schemas, not predictive quality
(quality is verified by the golden-set eval gated in CI by slice 15).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.classification import run as run_module
from evals.classification.baselines import classical as classical_b
from evals.classification.baselines import llm as llm_b

GOLDEN = Path("evals/classification/golden.jsonl")


def _load_golden() -> list[dict]:
    with GOLDEN.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_golden_set_shape() -> None:
    """AC: 25 hand-curated records at evals/classification/golden.jsonl."""
    records = _load_golden()
    assert len(records) == 25, f"golden must be exactly 25 records, got {len(records)}"
    labels = [r["label"] for r in records]
    label_set = set(labels)
    assert label_set <= {"bug", "feature", "docs", "question"}
    # At least 3 of the 4 classes represented (docs may be small).
    assert len(label_set) >= 3
    # Every row has the required fields.
    for r in records:
        for key in ("id", "title", "body", "label"):
            assert key in r, f"record {r.get('id')!r} missing {key!r}"


def test_classical_pipeline_fits_and_predicts(tmp_path: Path) -> None:
    """Smoke: pipeline builds, fits on synthetic 3-class data, predicts in vocab."""
    train = [
        {"title": "crash on startup", "body": "stack trace shown below", "label": "bug"},
        {"title": "uvicorn fails", "body": "ImportError", "label": "bug"},
        {"title": "add option to disable docs", "body": "would be useful", "label": "feature"},
        {"title": "support python 3.13", "body": "please add", "label": "feature"},
        {"title": "how do I use Depends?", "body": "tutorial unclear", "label": "question"},
        {"title": "what is FastAPI?", "body": "newbie q", "label": "question"},
    ]
    val = [
        {"title": "broken on 0.110", "body": "regression", "label": "bug"},
        {"title": "add async hook", "body": "wishlist", "label": "feature"},
        {"title": "how to test?", "body": "no idea", "label": "question"},
    ]
    artifact = tmp_path / "clf.joblib"
    pipe, report = classical_b.fit_with_grid(train, val)
    assert "best_C" in report
    assert "best_val_macro_f1" in report
    assert len(report["grid"]) == len(classical_b.C_GRID)
    # Persist + reload + predict
    import joblib

    joblib.dump({"pipeline": pipe, "classes": list(pipe.classes_), "report": report}, artifact)
    preds, latencies = classical_b.predict_batch(
        ["the app crashes immediately"], artifact_path=artifact
    )
    assert len(preds) == 1
    assert preds[0] in classical_b.LABELS
    assert all(latency >= 0.0 for latency in latencies)


def test_classical_predict_artifact_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        classical_b.predict_batch(["x"], artifact_path=tmp_path / "nope.joblib")


def test_llm_fewshot_shape_no_api_call() -> None:
    """LLM few-shot composition is offline-checkable (no GROQ_API_KEY needed)."""
    msgs = llm_b._build_fewshot_messages()
    # 4 classes x 3 messages each (user/assistant/tool) = 12
    assert len(msgs) == 12
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "tool"] * 4
    # Every assistant turn carries a single tool call to classify_issue.
    for m in msgs:
        if m["role"] == "assistant":
            calls = m["tool_calls"]
            assert len(calls) == 1
            assert calls[0]["function"]["name"] == "classify_issue"
            args = json.loads(calls[0]["function"]["arguments"])
            assert args["label"] in llm_b.LABELS
    # The 4 emitted labels cover all 4 classes exactly once.
    emitted = []
    for m in msgs:
        if m["role"] == "assistant":
            emitted.append(json.loads(m["tool_calls"][0]["function"]["arguments"])["label"])
    assert sorted(emitted) == sorted(llm_b.LABELS)


def test_llm_tool_schema_enum_locked() -> None:
    schema = llm_b.CLASSIFY_TOOL["function"]["parameters"]
    label_field = schema["properties"]["label"]
    assert label_field["enum"] == list(llm_b.LABELS)
    assert schema["required"] == ["label"]


def test_metrics_shape() -> None:
    y_true = ["bug", "bug", "feature", "feature", "docs", "question"]
    y_pred = ["bug", "feature", "feature", "feature", "docs", "question"]
    m = run_module._metrics(y_true, y_pred, latencies_ms=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert m["n"] == 6
    assert set(m["per_class_f1"].keys()) == set(run_module.LABELS)
    assert m["confusion_labels"] == list(run_module.LABELS)
    assert len(m["confusion"]) == 4 and len(m["confusion"][0]) == 4
    assert 0.0 <= m["macro_f1"] <= 1.0
    assert m["p50_latency_ms"] > 0


def test_pick_deployed_prefers_macro_f1() -> None:
    per_model = {
        "a": {"macro_f1": 0.3, "p50_latency_ms": 10},
        "b": {"macro_f1": 0.5, "p50_latency_ms": 1000},
        "c": {"macro_f1": 0.5, "p50_latency_ms": 5},
    }
    # Highest macro-F1 wins, ties broken by lower p50.
    assert run_module._pick_deployed(per_model) == "c"


def test_eval_report_schema_keys(tmp_path: Path) -> None:
    """Build a minimal report from a stubbed runner set and assert downstream keys."""
    # Mock runners so this test doesn't need the deberta artifact or Groq key.
    stub_records = [
        {"title": "x", "body": "y", "label": "bug"},
        {"title": "z", "body": "w", "label": "feature"},
    ]
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text("\n".join(json.dumps(r) for r in stub_records) + "\n", encoding="utf-8")
    out_path = tmp_path / "out.json"

    # Inject a fake runner so we don't load any real model.
    def fake_runner(recs: list[dict]) -> dict:
        return {
            "predictions": [r["label"] for r in recs],
            "latencies_ms": [1.0] * len(recs),
            "cost_per_1k_usd": 0.0,
        }

    original = dict(run_module._RUNNERS)
    try:
        run_module._RUNNERS.clear()
        run_module._RUNNERS["fake"] = fake_runner
        rc = run_module.run(golden_path, out_path, models=["fake"])
    finally:
        run_module._RUNNERS.clear()
        run_module._RUNNERS.update(original)

    assert rc == 0
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert "golden_set" in report and "sha256" in report["golden_set"]
    assert "models" in report and "fake" in report["models"]
    fake = report["models"]["fake"]
    for key in (
        "accuracy",
        "macro_f1",
        "per_class_f1",
        "confusion",
        "confusion_labels",
        "p50_latency_ms",
        "p99_latency_ms",
        "cost_per_1k_usd",
    ):
        assert key in fake, f"missing key {key!r} in model report"
    assert report["deployed"] == "fake"
