"""`GET /widget.js` — the public loader script. Hand-written (vs Vite-built) so
the snippet stays a single network hop and we can keep the embed contract in
one Python file alongside the embed route.

Contract:
    <script src=".../widget.js" data-widget-id="<uuid>"
            data-api-base="<optional override>"></script>

Behavior:
    1. Read `data-widget-id` + `data-api-base` from `document.currentScript`.
    2. Fetch `${API}/widget/${id}/config` for visual + tool config.
    3. Inject an iframe pointing at `${API}/widget/${id}/embed` positioned per
       the config's `position` field (br/bl/tr/tl).
    4. Bridge postMessages — `mc:resize` (iframe→host) sizes the iframe;
       `mc:ready` (iframe→host) fades it in; `mc:theme` (host→iframe) lets the
       host override the color at runtime.
    5. Origin validated on both sides via the `event.origin` against the API
       base derived above.
"""

from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["widget"])


def _default_api_base() -> str:
    return os.environ.get("PUBLIC_API_BASE", "").rstrip("/")


LOADER_JS_TEMPLATE = r"""
(function () {
  var script = document.currentScript;
  if (!script) {
    // IE/edge fallback — pick the last <script> tag with data-widget-id.
    var all = document.getElementsByTagName('script');
    for (var i = all.length - 1; i >= 0; i--) {
      if (all[i].getAttribute('data-widget-id')) { script = all[i]; break; }
    }
  }
  if (!script) return;
  var widgetId = script.getAttribute('data-widget-id');
  if (!widgetId) return;

  var apiBase = (script.getAttribute('data-api-base') || __MC_DEFAULT_API_BASE__).replace(/\/+$/, '');
  if (!apiBase) {
    var here = script.src.replace(/\/widget\.js.*$/, '');
    apiBase = here;
  }

  var POSITIONS = {
    br: { right: '16px', bottom: '16px' },
    bl: { left: '16px',  bottom: '16px' },
    tr: { right: '16px', top: '16px' },
    tl: { left: '16px',  top: '16px' }
  };

  function injectIframe(cfg) {
    var iframe = document.createElement('iframe');
    iframe.src = apiBase + '/widget/' + encodeURIComponent(widgetId) + '/embed';
    iframe.title = (cfg && cfg.name) || 'Chat';
    iframe.setAttribute('allow', 'clipboard-write');
    iframe.style.position = 'fixed';
    iframe.style.zIndex = '2147483647';
    iframe.style.border = '0';
    iframe.style.width = '380px';
    iframe.style.height = '88px';
    iframe.style.borderRadius = '12px';
    iframe.style.boxShadow = '0 10px 25px rgba(0,0,0,.18)';
    iframe.style.opacity = '0';
    iframe.style.transition = 'opacity .25s ease, height .2s ease, width .2s ease';
    iframe.style.background = 'transparent';
    iframe.style.colorScheme = 'normal';
    var pos = POSITIONS[(cfg && cfg.position) || 'br'] || POSITIONS.br;
    for (var k in pos) iframe.style[k] = pos[k];
    iframe.dataset.mcWidget = '1';
    iframe.dataset.mcId = widgetId;
    document.body.appendChild(iframe);

    var iframeOriginPromise = new URL(iframe.src).origin;

    function isValidIframeOrigin(o) { return o === iframeOriginPromise; }

    window.addEventListener('message', function (ev) {
      if (!isValidIframeOrigin(ev.origin)) return;
      var data = ev.data || {};
      if (typeof data !== 'object') return;
      if (data.type === 'mc:resize' && data.payload) {
        var w = Math.min(parseInt(data.payload.width, 10) || 380, 480);
        var h = Math.min(parseInt(data.payload.height, 10) || 88, 600);
        iframe.style.width = w + 'px';
        iframe.style.height = h + 'px';
      } else if (data.type === 'mc:ready') {
        iframe.style.opacity = '1';
      }
    });

    var hostTheme = script.getAttribute('data-theme');
    if (hostTheme) {
      iframe.addEventListener('load', function () {
        try {
          iframe.contentWindow.postMessage(
            { type: 'mc:theme', payload: { primaryColor: hostTheme } },
            iframeOriginPromise
          );
        } catch (_) {}
      });
    }
  }

  fetch(apiBase + '/widget/' + encodeURIComponent(widgetId) + '/config', {
    credentials: 'omit'
  }).then(function (r) {
    if (!r.ok) throw new Error('config ' + r.status);
    return r.json();
  }).then(injectIframe).catch(function (err) {
    // Visible-but-not-loud: log once, no UI artifact on the host page.
    if (window.console && console.warn) {
      console.warn('[maintainer-copilot] widget bootstrap failed:', err);
    }
  });
})();
""".strip()


def _render_loader_js() -> str:
    default_base_literal = '"' + _default_api_base() + '"' if _default_api_base() else '""'
    return LOADER_JS_TEMPLATE.replace("__MC_DEFAULT_API_BASE__", default_base_literal)


@router.get("/widget.js")
async def loader_js() -> Response:
    body = _render_loader_js()
    return Response(
        content=body,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )
