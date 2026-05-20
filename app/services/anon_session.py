"""AnonSessionService — mints widget-scoped anonymous JWTs.

The minted token has shape `{sub: "widget_session:<uuid4>", widget_id,
enabled_tools, jti, exp, aud, iat}` and is decoded by the same
`current_principal` dep that handles authed users (see app/api/auth.py).

`enabled_tools` is snapshotted at mint time per PRD Q22 so an admin editing a
widget's tool list does not retroactively un-grant tools to live sessions.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import jwt as pyjwt

from app.api.auth import JWT_LIFETIME_SECONDS, _jwt_secret
from app.domain.exceptions import NotFoundError
from app.services.widget_config import WidgetConfigService
from app.services.widget_config import default_service as _widget_default_service


@dataclass(frozen=True, slots=True)
class MintedAnonSession:
    token: str
    widget_session_id: str
    widget_id: str
    enabled_tools: tuple[str, ...]
    expires_in: int


class AnonSessionService:
    def __init__(self, configs: WidgetConfigService | None = None) -> None:
        self._configs = configs or _widget_default_service()

    async def mint(self, widget_id: str) -> MintedAnonSession:
        cfg = await self._configs.get(widget_id)
        widget_session_id = uuid.uuid4().hex
        now = int(time.time())
        ttl = JWT_LIFETIME_SECONDS
        payload = {
            "sub": f"widget_session:{widget_session_id}",
            "widget_id": widget_id,
            "enabled_tools": list(cfg.enabled_tools),
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + ttl,
            "aud": "fastapi-users:auth",
        }
        token = pyjwt.encode(payload, _jwt_secret(), algorithm="HS256")
        return MintedAnonSession(
            token=token,
            widget_session_id=widget_session_id,
            widget_id=widget_id,
            enabled_tools=tuple(cfg.enabled_tools),
            expires_in=ttl,
        )


_default: AnonSessionService | None = None


def default_service() -> AnonSessionService:
    global _default
    if _default is None:
        _default = AnonSessionService()
    return _default


__all__ = [
    "AnonSessionService",
    "MintedAnonSession",
    "default_service",
    "NotFoundError",  # re-export so route handlers can catch it without a long path
]
