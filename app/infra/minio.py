"""MinIO blob adapter.

Single bucket `mc-evals` is reused for datasets, models, eval reports — distinct prefixes.
Credentials resolve from Vault `api/blob` (slice 01 seed) at construction time.
"""

from __future__ import annotations

import os
from pathlib import Path

from minio import Minio
from minio.error import S3Error

from app.domain.exceptions import BlobError
from app.infra.vault import get_vault


def _endpoint_for_host_run(endpoint: str) -> str:
    # Vault stores `minio:9000` (compose-internal). Host-run scripts need localhost:9000.
    if os.environ.get("MC_BLOB_FROM_HOST") and endpoint.startswith("minio:"):
        return endpoint.replace("minio:", "localhost:", 1)
    return endpoint


class MinIOClient:
    def __init__(self, secrets: dict | None = None) -> None:
        s = secrets or get_vault().load("api/blob")
        self._client = Minio(
            endpoint=_endpoint_for_host_run(s["endpoint"]),
            access_key=s["access_key"],
            secret_key=s["secret_key"],
            secure=False,
        )
        self._default_bucket = s.get("bucket", "mc-evals")

    @property
    def default_bucket(self) -> str:
        return self._default_bucket

    def ensure_bucket(self, name: str | None = None) -> str:
        bucket = name or self._default_bucket
        try:
            if not self._client.bucket_exists(bucket):
                self._client.make_bucket(bucket)
        except S3Error as exc:
            raise BlobError(f"ensure_bucket({bucket}) failed: {exc}") from exc
        return bucket

    def put_file(self, key: str, path: str | Path, bucket: str | None = None) -> None:
        b = self.ensure_bucket(bucket)
        try:
            self._client.fput_object(b, key, str(path))
        except S3Error as exc:
            raise BlobError(f"put_file s3://{b}/{key} failed: {exc}") from exc
