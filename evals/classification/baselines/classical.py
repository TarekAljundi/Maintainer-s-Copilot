"""Classical baseline: TF-IDF (word 1-2 + char_wb 3-5) -> LogisticRegression.

PRD §Three-model comparison (Q4, Q6, Q7, Q8):
  - FeatureUnion of word n-grams (1-2) and char_wb n-grams (3-5).
  - LogisticRegression(class_weight='balanced').
  - Hyperparameter `C` tuned on val via 5-point grid (winner by macro-F1).
  - Same splits as the fine-tuned model: fit on train, tune on val.

Same preprocessing as the fine-tuned classifier: `model_server.preprocess.build_input`
replaces fenced + inline code with `<CODE>`. Train/serve skew is the cardinal sin.

NB: `docs` has zero records in train, so `LogisticRegression` simply does not
include it in `classes_`. At predict time, the model literally cannot emit
`docs`. This is reported honestly in DECISIONS.md, not papered over.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable

import joblib  # type: ignore[import-untyped]
from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import f1_score  # type: ignore[import-untyped]
from sklearn.pipeline import FeatureUnion, Pipeline  # type: ignore[import-untyped]

from model_server.preprocess import build_input

LABELS: tuple[str, ...] = ("bug", "feature", "docs", "question")
SPLITS_DIR = Path("data/splits")
ARTIFACT_PATH = Path("data/models/classifier_classical_v1.joblib")
C_GRID: tuple[float, ...] = (0.05, 0.25, 1.0, 4.0, 16.0)
SEED = 42


def _load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def _texts_labels(records: list[dict]) -> tuple[list[str], list[str]]:
    texts = [build_input(r.get("title"), r.get("body")) for r in records]
    labels = [r["label"] for r in records]
    return texts, labels


def _build_pipeline(C: float) -> Pipeline:
    word = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2, sublinear_tf=True)
    char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)
    return Pipeline(
        [
            ("tfidf", FeatureUnion([("word", word), ("char", char)])),
            (
                "lr",
                LogisticRegression(
                    C=C,
                    class_weight="balanced",
                    max_iter=2000,
                    solver="lbfgs",
                    random_state=SEED,
                ),
            ),
        ]
    )


def fit_with_grid(
    train_records: list[dict],
    val_records: list[dict],
) -> tuple[Pipeline, dict]:
    """Fit pipeline on train, pick C on val by macro-F1. Returns (best_pipe, grid_report)."""
    X_train, y_train = _texts_labels(train_records)
    X_val, y_val = _texts_labels(val_records)

    grid: list[dict] = []
    best_pipe: Pipeline | None = None
    best_score = -1.0
    best_C = C_GRID[0]
    for C in C_GRID:
        pipe = _build_pipeline(C)
        pipe.fit(X_train, y_train)
        preds = pipe.predict(X_val)
        macro = float(f1_score(y_val, preds, average="macro", zero_division=0))  # type: ignore[arg-type]
        grid.append({"C": C, "val_macro_f1": macro})
        if macro > best_score:
            best_score = macro
            best_C = C
            best_pipe = pipe
    assert best_pipe is not None
    return best_pipe, {"grid": grid, "best_C": best_C, "best_val_macro_f1": best_score}


def train_and_persist(artifact_path: Path = ARTIFACT_PATH) -> dict:
    train_records = _load_jsonl(SPLITS_DIR / "train.jsonl")
    val_records = _load_jsonl(SPLITS_DIR / "val.jsonl")
    pipe, report = fit_with_grid(train_records, val_records)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"pipeline": pipe, "classes": list(pipe.classes_), "report": report},
        artifact_path,
    )
    return report


def _load_artifact(artifact_path: Path) -> tuple[Pipeline, list[str]]:
    payload = joblib.load(artifact_path)
    return payload["pipeline"], list(payload["classes"])


def predict_batch(
    texts: Iterable[str],
    artifact_path: Path = ARTIFACT_PATH,
) -> tuple[list[str], list[float]]:
    """Return (predicted labels, per-record wall-clock latency in ms).

    Latency is measured per-record (not batch-amortized) so p50/p99 are comparable
    against the LLM and DeBERTa baselines, which both run one-at-a-time.
    """
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"missing {artifact_path} — run `python -m evals.classification.baselines.classical`"
        )
    pipe, classes = _load_artifact(artifact_path)
    preds: list[str] = []
    latencies_ms: list[float] = []
    for t in texts:
        t0 = time.perf_counter()
        y = pipe.predict([t])[0]
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        preds.append(str(y))
    # Sanity: every predicted label must be in our LABELS vocabulary, even if
    # the artifact didn't learn all 4 classes.
    for p in preds:
        if p not in LABELS:
            raise RuntimeError(f"predicted out-of-vocab label: {p!r}")
    return preds, latencies_ms


def can_predict(label: str, artifact_path: Path = ARTIFACT_PATH) -> bool:
    _, classes = _load_artifact(artifact_path)
    return label in classes


def main() -> int:
    report = train_and_persist()
    print(json.dumps(report, indent=2))
    print(f"saved -> {ARTIFACT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
