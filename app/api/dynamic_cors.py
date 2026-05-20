"""Dynamic per-widget CORS middleware.

For `/widget/{id}/*` and `/api/chat` requests carrying an `Origin` header, the
allowlist comes from the widget config row (Redis-cached). For everything
else, the middleware is a no-op — chat from the streamlit/widget already
flows server-side, so no global CORS allowlist is needed.

Behavior:
    - Preflight `OPTIONS` with matched origin → 204 with the allow-* headers.
    - Actual request with matched origin → response gains
      `Access-Control-Allow-Origin`, `Vary: Origin`, credentials/methods/headers.
    - Origin not in allowlist → no CORS headers (browser blocks).
"""

from __future__ import annotations

import re
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_WIDGET_PATH_RE = re.compile(r"^/widget/(?P<id>[^/]+)(?:/|$)")
_CHAT_PATH_RE = re.compile(r"^/api/chat(?:/|$)")

_DEFAULT_HEADERS = "Authorization, Content-Type, X-Request-Id"
_DEFAULT_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"


def _widget_id_for_request(request: Request) -> str | None:
    """Extract the widget id this request is scoped to.

    `/widget/{id}/*` — id is in the URL path.
    `/api/chat`     — id comes from the bearer JWT (the principal helper knows
                      it), but middlewares can't await the auth dep; we read the
                      raw token's payload here to avoid coupling. Failure to
                      decode is treated as "no widget" (chat will fail at the
                      auth dep, not CORS).
    """
    path = request.url.path
    m = _WIDGET_PATH_RE.match(path)
    if m:
        return m.group("id")
    if _CHAT_PATH_RE.match(path):
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(None, 1)[1]
            return _widget_id_from_jwt(token)
    return None


def _widget_id_from_jwt(token: str) -> str | None:
    try:
        import jwt as pyjwt

        from app.api.auth import _jwt_secret

        payload = pyjwt.decode(
            token,
            _jwt_secret(),
            algorithms=["HS256"],
            audience="fastapi-users:auth",
            options={"verify_exp": False},
        )
    except Exception:
        return None
    sub = payload.get("sub", "")
    if isinstance(sub, str) and sub.startswith("widget_session:"):
        return payload.get("widget_id")
    return None


async def _allowed_origins(widget_id: str) -> tuple[str, ...]:
    try:
        from app.services.widget_config import default_service

        return await default_service().allowed_origins(widget_id)
    except Exception:
        return ()


def _apply_cors(response: Response, origin: str) -> None:
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Headers"] = _DEFAULT_HEADERS
    response.headers["Access-Control-Allow-Methods"] = _DEFAULT_METHODS
    response.headers["Access-Control-Expose-Headers"] = "X-Request-Id"


class DynamicCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        origin = request.headers.get("origin")
        if not origin:
            return await call_next(request)

        widget_id = _widget_id_for_request(request)
        if widget_id is None:
            return await call_next(request)

        origins = await _allowed_origins(widget_id)
        origin_n = (origin or "").rstrip("/")
        matched = origin if any((o or "").rstrip("/") == origin_n for o in origins) else None

        if request.method == "OPTIONS" and matched is not None:
            # Preflight: respond directly with the CORS headers.
            resp = Response(status_code=204)
            _apply_cors(resp, matched)
            req_headers = request.headers.get("access-control-request-headers")
            if req_headers:
                resp.headers["Access-Control-Allow-Headers"] = req_headers
            return resp

        response = await call_next(request)
        if matched is not None:
            _apply_cors(response, matched)
        return response
