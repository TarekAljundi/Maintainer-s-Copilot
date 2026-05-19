"""ModelServerClient: retry + ClassifierUnavailable mapping."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.domain.exceptions import ClassifierUnavailable
from app.infra.model_server_client import ModelServerClient


def _ok_response() -> httpx.Response:
    req = httpx.Request("POST", "http://x:8001/classify")
    return httpx.Response(200, json={"label": "bug", "confidence": 0.91, "scores": {}}, request=req)


def test_classify_returns_payload():
    with patch("httpx.post", return_value=_ok_response()):
        out = ModelServerClient("http://x:8001").classify("text")
    assert out["label"] == "bug"
    assert out["confidence"] == 0.91


def test_classify_retries_on_first_failure():
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return _ok_response()

    with patch("httpx.post", side_effect=fake_post):
        out = ModelServerClient("http://x:8001").classify("text")
    assert out["label"] == "bug"
    assert calls["n"] == 2


def test_classify_raises_classifier_unavailable_after_retries():
    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(ClassifierUnavailable):
            ModelServerClient("http://x:8001").classify("text")


def test_health_raises_classifier_unavailable_on_error():
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(ClassifierUnavailable):
            ModelServerClient("http://x:8001").health()
