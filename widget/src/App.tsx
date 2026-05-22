import { useEffect, useRef, useState } from 'preact/hooks'
import { ChatPanel } from './components/ChatPanel'
import type { WidgetRuntime } from './context'

export function App({ config, session, apiBase }: WidgetRuntime) {
  const [open, setOpen] = useState(false)
  const [themeOverride, setThemeOverride] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)

  // Tell the loader to resize the iframe whenever our container changes size.
  useEffect(() => {
    const el = containerRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        parent.postMessage(
          { type: 'mc:resize', payload: { width: Math.ceil(width), height: Math.ceil(height) } },
          '*',
        )
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [open])

  // mc:theme override from the host page.
  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      if (!ev.data || typeof ev.data !== 'object') return
      if (ev.data.type === 'mc:theme' && ev.data.payload?.primaryColor) {
        setThemeOverride(String(ev.data.payload.primaryColor))
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  const t = config.theme
  // The host-page `mc:theme` override (if any) retints the accent only.
  const accent = themeOverride || t.accent
  const launcherLabel = (config.greeting_text || 'Chat').slice(0, 30)

  return (
    // inline-block so the container shrink-wraps the launcher / panel; the
    // ResizeObserver then reports the true widget size and the loader sizes
    // the iframe to match (collapsed pill vs. open panel).
    <div
      ref={containerRef}
      style={{
        ['--mc-panel' as any]: t.panel,
        ['--mc-surface' as any]: t.surface,
        ['--mc-border' as any]: t.border,
        ['--mc-fg' as any]: t.fg,
        ['--mc-muted' as any]: t.muted,
        ['--mc-accent' as any]: accent,
        ['--mc-on-accent' as any]: t.on_accent,
        display: 'inline-block',
        verticalAlign: 'top',
      }}
    >
      {open ? (
        <ChatPanel
          config={config}
          session={session}
          apiBase={apiBase}
          onClose={() => setOpen(false)}
        />
      ) : (
        <button
          class="mc-launcher"
          aria-label="Open chat"
          onClick={() => setOpen(true)}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
          </svg>
          <span>{launcherLabel}</span>
        </button>
      )}
    </div>
  )
}
