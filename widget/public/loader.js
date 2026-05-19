// Served at /widget.js. Reads data-widget-id, fetches config, injects iframe.
// PostMessage protocol: mc:resize (iframe->host), mc:ready (iframe->host), mc:theme (host->iframe).
(() => {
  const tag = document.currentScript;
  if (!tag) return;
  const widgetId = tag.dataset.widgetId;
  const apiBase = tag.dataset.apiBase || window.location.origin;
  if (!widgetId) return;

  fetch(`${apiBase}/widget/${widgetId}/config`)
    .then(r => r.json())
    .then(cfg => {
      const iframe = document.createElement('iframe');
      iframe.src = `${apiBase}/widget/${widgetId}/embed`;
      iframe.title = 'Maintainer Copilot';
      iframe.style.cssText = `
        position: fixed;
        ${cfg.position === 'bottom-left' ? 'left:20px' : 'right:20px'};
        bottom: 20px;
        width: 72px; height: 72px;
        border: 0; z-index: 2147483646;
        color-scheme: light;
      `;
      document.body.appendChild(iframe);

      window.addEventListener('message', (e) => {
        if (e.source !== iframe.contentWindow) return;
        const msg = e.data || {};
        if (msg.type === 'mc:resize') {
          iframe.style.width = msg.width + 'px';
          iframe.style.height = msg.height + 'px';
        }
      });
    });
})();
