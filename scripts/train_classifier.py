"""Fine-tune microsoft/deberta-v3-small on FastAPI issue splits.

PRD §Three-model comparison (Q4, Q6, Q7, Q8): full fine-tune, discriminative LR,
linear warmup+decay, weight decay 0.01, batch 16, 3-5 epochs, early-stop on val
macro-F1 (patience 1). Tracked in TensorBoard (W&B blocked in user region —
see DECISIONS.md).

Usage (from repo root in WSL2 with GPU):
    GITHUB_TOKEN=... VAULT_ADDR=http://localhost:8200 VAULT_TOKEN=dev-root-token \
        MC_BLOB_FROM_HOST=1 \
        uv run --extra model-server --extra evals \
        python scripts/train_classifier.py

Outputs:
    data/models/classifier/v1/   model.safetensors + tokenizer.json + config.json + ...
    runs/                        TensorBoard logs (view: tensorboard --logdir runs)
    docs/model_card.md           rewritten with measured metrics + pinned SHA
    app/infra/_classifier_registry.py   WEIGHTS_SHA256 rewritten

Then uploads the artifact to s3://mc-models/classifier/v1/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import torch
from sklearn.metrics import accuracy_score, classification_report, f1_score
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    EarlyStoppingCallback,
    Trainer,
    TrainingArguments,
)
from torch.utils.data import Dataset

from model_server.preprocess import build_input

LABELS: tuple[str, ...] = ("bug", "feature", "docs", "question")
LABEL2ID = {label: i for i, label in enumerate(LABELS)}
ID2LABEL = {i: label for label, i in LABEL2ID.items()}

BASE_MODEL = "microsoft/deberta-v3-small"
MAX_LEN = 512

SPLITS_DIR = Path("data/splits")
OUT_DIR = Path("data/models/classifier/v1")
MODEL_CARD_PATH = Path("docs/model_card.md")
REGISTRY_PATH = Path("app/infra/_classifier_registry.py")
MINIO_PREFIX = "classifier/v1"
MINIO_BUCKET = "mc-models"


# ---------- dataset ----------


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


class IssueDataset(Dataset):
    def __init__(self, records: list[dict], tokenizer) -> None:
        self.texts = [build_input(r["title"], r["body"]) for r in records]
        self.labels = [LABEL2ID[r["label"]] for r in records]
        self.tokenizer = tokenizer

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        enc = self.tokenizer(
            self.texts[idx],
            truncation=True,
            max_length=MAX_LEN,
            padding=False,
        )
        return {
            **{k: torch.tensor(v) for k, v in enc.items()},
            "labels": torch.tensor(self.labels[idx]),
        }


def collate(batch: list[dict], pad_id: int) -> dict[str, torch.Tensor]:
    max_len = max(len(b["input_ids"]) for b in batch)
    out: dict[str, list] = {"input_ids": [], "attention_mask": [], "labels": []}
    for b in batch:
        n = len(b["input_ids"])
        pad = max_len - n
        out["input_ids"].append(
            torch.cat([b["input_ids"], torch.full((pad,), pad_id, dtype=torch.long)])
        )
        out["attention_mask"].append(
            torch.cat([b["attention_mask"], torch.zeros(pad, dtype=torch.long)])
        )
        out["labels"].append(b["labels"])
    return {k: torch.stack(v) for k, v in out.items()}


# ---------- discriminative LR ----------


def build_param_groups(model) -> list[dict]:
    """Encoder base LR 2e-5; classifier head 1e-4; weight decay 0.01 on non-bias/LN.

    Four consolidated groups (one per LR × decay combination) — keeping a group
    per param tensor confuses accelerate's mixed-precision dispatch.
    """
    no_decay_names = ("bias", "LayerNorm.weight", "LayerNorm.bias")

    def is_head(name: str) -> bool:
        return "classifier" in name or "pooler" in name

    def is_no_decay(name: str) -> bool:
        return any(nd in name for nd in no_decay_names)

    buckets: dict[tuple[bool, bool], list] = {
        (False, False): [],  # encoder, decay
        (False, True): [],  # encoder, no decay
        (True, False): [],  # head, decay
        (True, True): [],  # head, no decay
    }
    for name, param in model.named_parameters():
        if not param.requires_grad:
            continue
        buckets[(is_head(name), is_no_decay(name))].append(param)

    groups = [
        {"params": buckets[(False, False)], "lr": 2e-5, "weight_decay": 0.01},
        {"params": buckets[(False, True)], "lr": 2e-5, "weight_decay": 0.0},
        {"params": buckets[(True, False)], "lr": 1e-4, "weight_decay": 0.01},
        {"params": buckets[(True, True)], "lr": 1e-4, "weight_decay": 0.0},
    ]
    return [g for g in groups if g["params"]]


# ---------- metrics ----------


def compute_metrics(eval_pred) -> dict[str, float]:
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=1)
    return {
        "accuracy": float(accuracy_score(labels, preds)),
        "macro_f1": float(f1_score(labels, preds, average="macro", zero_division=0)),
    }


def per_class_metrics(y_true, y_pred) -> dict[str, dict]:
    report: dict = classification_report(  # type: ignore[assignment]
        y_true,
        y_pred,
        labels=list(range(len(LABELS))),
        target_names=list(LABELS),
        output_dict=True,
        zero_division=0,
    )
    return {
        lbl: {"f1": float(report[lbl]["f1-score"]), "support": float(report[lbl]["support"])}
        for lbl in LABELS
    }


# ---------- SHA + registry + model card ----------


def weights_sha(weights_path: Path) -> str:
    h = hashlib.sha256()
    with weights_path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def rewrite_registry(sha: str) -> None:
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    text = re.sub(r'WEIGHTS_SHA256: str = "[^"]*"', f'WEIGHTS_SHA256: str = "{sha}"', text)
    REGISTRY_PATH.write_text(text, encoding="utf-8")


def write_model_card(
    sha: str,
    test_metrics: dict,
    train_counts: Counter,
    val_counts: Counter,
    test_counts: Counter,
    dataset_manifest_sha: str,
) -> None:
    pc = test_metrics["per_class"]
    body = f"""# Model Card — Classifier v1

## Architecture
- Base: `microsoft/deberta-v3-small` (44M params, 12 layers, 384 hidden)
- Head: linear over 4 classes (bug, feature, docs, question)
- Tokenizer: DebertaV2Tokenizer (sentencepiece), max length 512

## Training data
- Source: `fastapi/fastapi` closed issues, >= 2020-01-01
- Label mapping: strict + tiebreak `bug > feature > docs > question`
- Splits: 70/10/15 train/val/test, time-stratified by `closed_at` ascending
- Preprocessing: title+body, code blocks -> `<CODE>` placeholder, 512-tok head trunc
- Dataset manifest sha256: `{dataset_manifest_sha}`
- Per-class counts (train / val / test):
  - bug: {train_counts.get("bug", 0)} / {val_counts.get("bug", 0)} / {test_counts.get("bug", 0)}
  - feature: {train_counts.get("feature", 0)} / {val_counts.get("feature", 0)} / {test_counts.get("feature", 0)}
  - docs: {train_counts.get("docs", 0)} / {val_counts.get("docs", 0)} / {test_counts.get("docs", 0)}
  - question: {train_counts.get("question", 0)} / {val_counts.get("question", 0)} / {test_counts.get("question", 0)}

## Hyperparameters
- Freeze policy: full fine-tune
- LR: encoder 2e-5, classifier head 1e-4 (discriminative)
- Scheduler: linear warmup 10%, linear decay
- Weight decay: 0.01 (non-bias, non-LN)
- Batch size: 16
- Epochs: up to 5, early stop on val macro-F1, patience 1
- Tracking: TensorBoard (W&B blocked in user region; see DECISIONS.md)

## Metrics (test split)
- Accuracy: {test_metrics["accuracy"]:.4f}
- Macro-F1: {test_metrics["macro_f1"]:.4f}
- Per-class F1: bug={pc["bug"]["f1"]:.4f}, feature={pc["feature"]["f1"]:.4f}, docs={pc["docs"]["f1"]:.4f}, question={pc["question"]["f1"]:.4f}
- Per-class support: bug={int(pc["bug"]["support"])}, feature={int(pc["feature"]["support"])}, docs={int(pc["docs"]["support"])}, question={int(pc["question"]["support"])}
- p50/p99 latency: TBD (measured in slice 04 baselines comparison)

## Weights
- Bucket / prefix: `s3://{MINIO_BUCKET}/{MINIO_PREFIX}/`
- Files: `model.safetensors`, `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`, `spm.model`, `config.json`
- SHA-256 (model.safetensors): `{sha}`
- Pinned in: `app/infra/_classifier_registry.py::WEIGHTS_SHA256`
- Boot check #5 compares the model-server-reported SHA against this pin.

## Limitations
- Trained on FastAPI-only English issues. No generalization claim beyond that corpus.
- Head-only truncation biases toward signal in the first 512 tokens.
- `<CODE>` placeholder strips lexical content of code blocks; the classifier cannot use code identifiers as features.
- Class imbalance: `docs` is severely under-represented in the time-stratified split (train support {train_counts.get("docs", 0)}, test support {test_counts.get("docs", 0)}); F1 on that class is not meaningful at this scale.
"""
    MODEL_CARD_PATH.write_text(body, encoding="utf-8")


# ---------- upload ----------


def upload_artifact(out_dir: Path) -> None:
    """Upload the saved artifact to s3://mc-models/classifier/v1/. Lazy-imports MinIO."""
    from app.infra.minio import MinIOClient

    client = MinIOClient()
    for path in sorted(out_dir.iterdir()):
        if path.is_file():
            key = f"{MINIO_PREFIX}/{path.name}"
            client.put_file(key, path, bucket=MINIO_BUCKET)
            print(f"  uploaded s3://{MINIO_BUCKET}/{key}")


def dataset_manifest_sha() -> str:
    p = SPLITS_DIR / "manifest.json"
    if not p.exists():
        return "unknown"
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


# ---------- training ----------


def train(args) -> int:
    print(f"loading tokenizer + model: {BASE_MODEL}")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL,
        num_labels=len(LABELS),
        id2label=ID2LABEL,
        label2id=LABEL2ID,
        torch_dtype=torch.float32,
    )
    model = model.float()  # encoder is stored fp16 on HF; cast to fp32 for stable training

    train_recs = load_jsonl(SPLITS_DIR / "train.jsonl")
    val_recs = load_jsonl(SPLITS_DIR / "val.jsonl")
    test_recs = load_jsonl(SPLITS_DIR / "test.jsonl")
    print(f"train={len(train_recs)} val={len(val_recs)} test={len(test_recs)}")

    train_ds = IssueDataset(train_recs, tokenizer)
    val_ds = IssueDataset(val_recs, tokenizer)
    test_ds = IssueDataset(test_recs, tokenizer)

    pad_id = tokenizer.pad_token_id

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Effective batch = per_device_train_batch_size * grad_accum (PRD calls for 16 or
    # 8+accum). On 8GB cards 8+accum-2 with BF16 + grad checkpointing fits in VRAM.
    eff = args.batch_size
    per_dev = 8 if eff >= 16 else max(eff, 1)
    grad_accum = max(eff // per_dev, 1)
    use_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()

    training_args = TrainingArguments(
        output_dir=str(OUT_DIR / "_hf"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=per_dev,
        per_device_eval_batch_size=per_dev * 2,
        gradient_accumulation_steps=grad_accum,
        learning_rate=2e-5,
        eval_strategy="epoch",
        save_strategy="epoch",
        logging_dir="runs",
        logging_steps=50,
        report_to=["tensorboard"],
        load_best_model_at_end=True,
        metric_for_best_model="macro_f1",
        greater_is_better=True,
        warmup_ratio=0.1,
        lr_scheduler_type="linear",
        weight_decay=0.01,
        seed=args.seed,
        save_total_limit=2,
        bf16=use_bf16,
        fp16=False,
        max_grad_norm=1.0,
        gradient_checkpointing=True,
    )

    class DiscriminativeLRTrainer(Trainer):
        def create_optimizer(self):
            if self.optimizer is None:
                self.optimizer = torch.optim.AdamW(build_param_groups(self.model))
            return self.optimizer

    trainer_cls = Trainer if args.no_disc else DiscriminativeLRTrainer
    trainer = trainer_cls(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        compute_metrics=compute_metrics,
        data_collator=lambda b: collate(b, pad_id),
        callbacks=[EarlyStoppingCallback(early_stopping_patience=1)],
    )

    print("training ...")
    t0 = time.time()
    trainer.train()
    print(f"train wall time: {time.time() - t0:.1f}s")

    print("\nevaluating on test split ...")
    preds_out = trainer.predict(test_ds)
    y_pred = np.argmax(preds_out.predictions, axis=1)
    y_true = preds_out.label_ids
    test_metrics = {
        "accuracy": accuracy_score(y_true, y_pred),
        "macro_f1": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "per_class": per_class_metrics(y_true, y_pred),
    }
    print(json.dumps(test_metrics, indent=2, default=float))

    # Save final artifact (load_best_model_at_end already restored best weights)
    print(f"\nsaving artifact -> {OUT_DIR}")
    if (OUT_DIR / "_hf").exists():
        shutil.rmtree(OUT_DIR / "_hf", ignore_errors=True)
    trainer.save_model(str(OUT_DIR))
    tokenizer.save_pretrained(str(OUT_DIR))

    weights_path = OUT_DIR / "model.safetensors"
    if not weights_path.exists():
        weights_path = next(OUT_DIR.glob("*.safetensors"), None)
        if weights_path is None:
            raise RuntimeError("no safetensors written")
    sha = weights_sha(weights_path)
    print(f"weights SHA-256: {sha}")

    rewrite_registry(sha)
    write_model_card(
        sha=sha,
        test_metrics=test_metrics,
        train_counts=Counter(r["label"] for r in train_recs),
        val_counts=Counter(r["label"] for r in val_recs),
        test_counts=Counter(r["label"] for r in test_recs),
        dataset_manifest_sha=dataset_manifest_sha(),
    )

    # Persist a sidecar metrics.json for slice 04 compare.
    (OUT_DIR / "metrics.json").write_text(
        json.dumps({**test_metrics, "weights_sha256": sha}, indent=2, default=float) + "\n",
        encoding="utf-8",
    )

    if not args.no_upload:
        print("\nuploading to MinIO ...")
        upload_artifact(OUT_DIR)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument(
        "--no-disc",
        action="store_true",
        help="disable discriminative LR (uses TrainingArgs single LR — sanity check)",
    )
    args = parser.parse_args()
    os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
    return train(args)


if __name__ == "__main__":
    sys.exit(main())
