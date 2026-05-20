"""Demo-only convenience endpoint. NOT used by production widgets.

The demo host pages (demo/host, demo/host-blocked) need to embed *some*
widget, but baking the UUID into the HTML means rebuilding the nginx
containers every time the admin creates/edits a widget. Instead, the demo
host pages include `<script src="/demo/host.js">`, and this endpoint emits a
tiny bootstrap script that resolves the current demo widget id at runtime.

Selection rule: most recently updated widget whose `allowed_origins`
includes the demo host's URL (defaults to `http://localhost:${HOST_PORT}`,
fall back to localhost:8087). Lets the admin "switch the demo" just by
updating any widget — no rebuild.
"""

from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import Response

from app.services.widget_config import default_service as widget_svc


router = APIRouter(tags=["demo"])


def _demo_host_url() -> str:
    port = os.environ.get("HOST_PORT", "8087")
    return os.environ.get("DEMO_HOST_URL", f"http://localhost:{port}").rstrip("/")


def _api_base() -> str:
    return os.environ.get("PUBLIC_API_BASE", "http://localhost:8000").rstrip("/")


def _norm(o: str) -> str:
    return (o or "").strip().rstrip("/")


async def _resolve_demo_widget_id() -> str | None:
    """Return the most recently updated widget whose allowed_origins includes
    the demo host URL, or None if nothing matches. Trailing slashes ignored on
    both sides — the browser never sends them in `window.location.origin`."""
    demo = _norm(_demo_host_url())
    widgets = await widget_svc().list_(limit=200, offset=0)
    matches = [w for w in widgets if any(_norm(o) == demo for o in w.allowed_origins)]
    if not matches:
        return None
    matches.sort(key=lambda w: w.updated_at, reverse=True)
    return matches[0].id


_NO_WIDGET_JS = """
(function () {
  if (window.console && console.warn) {
    console.warn(
      '[mc-demo] no widget config has allowed_origins=%o — create one in the ' +
      'Streamlit Admin > Widgets page and add this host to allowed_origins.',
      window.location.origin
    );
  }
})();
""".strip()


def _emit_embed_js(widget_id: str, api_base: str) -> str:
    return f"""
(function () {{
  var s = document.createElement('script');
  s.src = {api_base!r} + '/widget.js';
  s.setAttribute('data-widget-id', {widget_id!r});
  s.setAttribute('data-api-base', {api_base!r});
  document.currentScript
    ? document.currentScript.parentNode.insertBefore(s, document.currentScript)
    : document.body.appendChild(s);
}})();
""".strip()


@router.get("/demo/host.js")
async def demo_host_js() -> Response:
    wid = await _resolve_demo_widget_id()
    body = _emit_embed_js(wid, _api_base()) if wid else _NO_WIDGET_JS
    return Response(
        content=body,
        media_type="application/javascript; charset=utf-8",
        headers={
            # Short cache so admins see their changes within a minute without
            # hammering the api on every page load.
            "Cache-Control": "public, max-age=30",
            "X-Content-Type-Options": "nosniff",
        },
    )
