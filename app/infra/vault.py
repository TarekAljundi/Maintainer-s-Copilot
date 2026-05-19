"""VaultClient — boot-cache secrets, fail-closed on runtime outage.

Interface:
    load(path) -> dict       # read + cache, raises VaultError if path missing
    cached(path) -> dict     # cache-only, raises if not pre-loaded
    health() -> bool         # used by boot check + runtime monitor
"""

from __future__ import annotations

import os
from typing import Iterable

import hvac

from app.domain.exceptions import VaultError


# Q30: KV v2 layout under `secret/`.
REQUIRED_PATHS: tuple[str, ...] = (
    "shared/jwt",
    "api/llm",
    "api/tracing",
    "api/db",
    "api/redis",
    "api/blob",
    "streamlit/api",
)


class VaultClient:
    def __init__(
        self,
        addr: str | None = None,
        token: str | None = None,
        mount_point: str = "secret",
    ) -> None:
        self._addr = addr or os.environ.get("VAULT_ADDR", "http://vault:8200")
        self._token = token or os.environ.get("VAULT_TOKEN", "")
        self._mount = mount_point
        self._client = hvac.Client(url=self._addr, token=self._token)
        self._cache: dict[str, dict] = {}

    def health(self) -> bool:
        try:
            status = self._client.sys.read_health_status(method="GET")
        except Exception:
            return False
        # hvac returns either a dict (200/429/472/473/501/503) or a Response.
        if isinstance(status, dict):
            return bool(status.get("initialized")) and not status.get("sealed", True)
        return status.status_code in (200, 429, 472, 473)

    def load(self, path: str) -> dict:
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                path=path, mount_point=self._mount, raise_on_deleted_version=True
            )
        except Exception as exc:
            raise VaultError(f"vault read failed for {path}: {exc}") from exc
        try:
            data = resp["data"]["data"]
        except (KeyError, TypeError) as exc:
            raise VaultError(f"unexpected vault response shape for {path}") from exc
        self._cache[path] = data
        return data

    def cached(self, path: str) -> dict:
        if path not in self._cache:
            raise VaultError(f"secret path not pre-loaded: {path}")
        return self._cache[path]

    def load_all(self, paths: Iterable[str] = REQUIRED_PATHS) -> None:
        for p in paths:
            self.load(p)


_singleton: VaultClient | None = None


def get_vault() -> VaultClient:
    global _singleton
    if _singleton is None:
        _singleton = VaultClient()
    return _singleton
