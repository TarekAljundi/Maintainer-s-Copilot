"""VaultClient unit tests. hvac is mocked at the boundary."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.domain.exceptions import VaultError
from app.infra.vault import VaultClient


def _client(monkeypatch) -> tuple[VaultClient, MagicMock]:
    fake_hvac = MagicMock()
    monkeypatch.setattr("app.infra.vault.hvac.Client", lambda url, token: fake_hvac)
    c = VaultClient(addr="http://vault:8200", token="dev-token")
    return c, fake_hvac


def test_load_caches_secret(monkeypatch):
    c, fake = _client(monkeypatch)
    fake.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"groq_api_key": "gsk_x"}}
    }
    data = c.load("api/llm")
    assert data == {"groq_api_key": "gsk_x"}
    assert c.cached("api/llm") == {"groq_api_key": "gsk_x"}


def test_cached_raises_when_missing(monkeypatch):
    c, _ = _client(monkeypatch)
    with pytest.raises(VaultError):
        c.cached("api/llm")


def test_load_wraps_underlying_errors(monkeypatch):
    c, fake = _client(monkeypatch)
    fake.secrets.kv.v2.read_secret_version.side_effect = RuntimeError("404")
    with pytest.raises(VaultError):
        c.load("api/missing")


def test_health_false_on_exception(monkeypatch):
    c, fake = _client(monkeypatch)
    fake.sys.read_health_status.side_effect = ConnectionError("boom")
    assert c.health() is False


def test_health_true_when_unsealed(monkeypatch):
    c, fake = _client(monkeypatch)
    fake.sys.read_health_status.return_value = {"initialized": True, "sealed": False}
    assert c.health() is True
