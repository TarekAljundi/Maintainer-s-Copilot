import { render } from 'preact'
import { App } from './App'
import type { WidgetConfig, WidgetSession } from './context'
import './styles/index.css'

declare global {
  interface Window {
    __MC_WIDGET_ID__?: string
    __MC_API_BASE__?: string
  }
}

const widgetId = window.__MC_WIDGET_ID__ || ''
const apiBase = (window.__MC_API_BASE__ || '').replace(/\/+$/, '')

async function bootstrap() {
  if (!widgetId || !apiBase) {
    console.warn('[maintainer-copilot] missing __MC_WIDGET_ID__ / __MC_API_BASE__')
    return
  }

  const [config, session] = (await Promise.all([
    fetch(`${apiBase}/widget/${widgetId}/config`).then((r) => {
      if (!r.ok) throw new Error(`config ${r.status}`)
      return r.json()
    }),
    fetch(`${apiBase}/widget/${widgetId}/session`, { method: 'POST' }).then((r) => {
      if (!r.ok) throw new Error(`session ${r.status}`)
      return r.json()
    }),
  ])) as [WidgetConfig, WidgetSession]

  const root = document.getElementById('root')
  if (!root) return

  render(<App config={config} session={session} apiBase={apiBase} />, root)

  // Tell the loader we're alive so it fades the iframe in.
  parent.postMessage({ type: 'mc:ready' }, '*')
}

bootstrap().catch((err) => {
  console.error('[maintainer-copilot] bootstrap failed:', err)
})
