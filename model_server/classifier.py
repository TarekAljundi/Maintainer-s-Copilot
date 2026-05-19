"""DeBERTa-v3-small fine-tuned classifier. Loads weights from MinIO at startup.

Reports its actual weights SHA-256 to /health so the API's boot check #5 can
verify it matches the pinned value in app.infra._classifier_registry.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path

import torch  # type: ignore[import-not-found]
from transformers import AutoModelForSequenceClassification, AutoTokenizer  # type: ignore[import-not-found]

from model_server.preprocess import build_input

log = logging.getLogger(__name__)

LABELS: tuple[str, ...] = ("bug", "feature", "docs", "question")
MAX_LEN = 512
CACHE_DIR = Path(os.environ.get("MC_MODEL_CACHE", "/tmp/models/classifier/v1"))
REQUIRED_FILES: tuple[str, ...] = ("model.safetensors", "config.json", "tokenizer.json")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_artifact(bucket: str, prefix: str) -> None:
    """Download every object under s3://bucket/prefix/ into CACHE_DIR."""
    from app.infra.minio import MinIOClient

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    client = MinIOClient()
    raw = client._client  # the underlying minio.Minio
    objs = list(raw.list_objects(bucket, prefix=f"{prefix}/", recursive=True))
    if not objs:
        raise RuntimeError(f"no objects at s3://{bucket}/{prefix}/")
    for obj in objs:
        key = obj.object_name
        if key is None:
            continue
        name = key.split("/")[-1]
        dest = CACHE_DIR / name
        if dest.exists() and dest.stat().st_size > 0:
            continue
        log.info("downloading s3://%s/%s", bucket, key)
        raw.fget_object(bucket, key, str(dest))
    for required in REQUIRED_FILES:
        if not (CACHE_DIR / required).exists():
            raise RuntimeError(f"required artifact file missing after download: {required}")


class Classifier:
    def __init__(self, bucket: str = "mc-models", prefix: str = "classifier/v1") -> None:
        _download_artifact(bucket, prefix)
        log.info("loading classifier from %s", CACHE_DIR)
        self.tokenizer = AutoTokenizer.from_pretrained(str(CACHE_DIR))
        self.model = AutoModelForSequenceClassification.from_pretrained(str(CACHE_DIR))
        self.model.eval()
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.weights_sha = _sha256(CACHE_DIR / "model.safetensors")
        self._lock = threading.Lock()

    @torch.no_grad()
    def predict(self, text: str, title: str | None = None) -> dict:
        if title is not None:
            text = build_input(title, text)
        enc = self.tokenizer(
            text,
            truncation=True,
            max_length=MAX_LEN,
            return_tensors="pt",
            padding=False,
        ).to(self.device)
        with self._lock:
            logits = self.model(**enc).logits
        probs = torch.softmax(logits, dim=-1).squeeze(0).cpu().tolist()
        idx = int(torch.argmax(logits, dim=-1).item())
        return {
            "label": LABELS[idx],
            "confidence": float(probs[idx]),
            "scores": {LABELS[i]: float(p) for i, p in enumerate(probs)},
        }
