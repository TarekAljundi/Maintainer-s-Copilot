"""AnonSessionService.mint contract — token shape + enabled_tools snapshot.

Stubs Vault for `_jwt_secret` and patches the WidgetConfigService.get to
return a fixed config. JWT decode round-trips on the same secret.
"""

from __future__ import annotations

from datetime import datetime, timezone

import jwt as pyjwt
import pytest

from app.domain.widget import WidgetConfig
from app.services import anon_session as svc_mod


SECRET = "test-secret"


@pytest.fixture(autouse=True)
def _patch_vault(monkeypatch):
    # `anon_session` imports `_jwt_secret` into its own namespace at import time
    # — patch the alias so the live mint path picks up the stub.
    monkeypatch.setattr("app.services.anon_session._jwt_secret", lambda: SECRET)


def _cfg(enabled: tuple[str, ...]) -> WidgetConfig:
    now = datetime.now(timezone.utc)
    return WidgetConfig(
        id="11111111-1111-1111-1111-111111111111",
        name="w",
        allowed_origins=("http://x",),
        primary_color="#000",
        position="br",
        greeting_text="hi",
        enabled_tools=enabled,
        created_at=now,
        updated_at=now,
    )


async def test_mint_returns_widget_session_jwt(monkeypatch):
    cfg = _cfg(("classify_issue", "search_knowledge"))

    class _Fake:
        async def get(self, _wid):
            return cfg

    s = svc_mod.AnonSessionService(configs=_Fake())
    minted = await s.mint(cfg.id)

    assert minted.widget_id == cfg.id
    assert minted.enabled_tools == ("classify_issue", "search_knowledge")
    assert minted.token.count(".") == 2  # JWT

    payload = pyjwt.decode(
        minted.token, SECRET, algorithms=["HS256"], audience="fastapi-users:auth"
    )
    assert payload["sub"].startswith("widget_session:")
    assert payload["sub"].split(":", 1)[1] == minted.widget_session_id
    assert payload["widget_id"] == cfg.id
    assert payload["enabled_tools"] == list(cfg.enabled_tools)
    assert "jti" in payload
    assert payload["exp"] > payload["iat"]


async def test_mint_snapshots_enabled_tools_at_mint_time(monkeypatch):
    """If the admin changes enabled_tools later, the minted token still
    carries the tools that were enabled when it was minted."""
    cfg = _cfg(("classify_issue",))
    seq = {"calls": 0}

    class _Fake:
        async def get(self, _wid):
            seq["calls"] += 1
            return cfg if seq["calls"] == 1 else _cfg(("write_memory",))

    s = svc_mod.AnonSessionService(configs=_Fake())
    minted = await s.mint(cfg.id)
    assert minted.enabled_tools == ("classify_issue",)


async def test_mint_propagates_notfound(monkeypatch):
    from app.domain.exceptions import NotFoundError

    class _Fake:
        async def get(self, _wid):
            raise NotFoundError("nope")

    s = svc_mod.AnonSessionService(configs=_Fake())
    with pytest.raises(NotFoundError):
        await s.mint("missing")
