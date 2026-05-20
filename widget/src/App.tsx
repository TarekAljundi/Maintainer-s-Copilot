import { useEffect, useRef, useState } from 'preact/hooks'
import { ChatPanel } from './components/ChatPanel'
import { useWidget } from './context'

export function App() {
  const { config, themeOverride } = useWidget()
  const [open, setOpen] = useState(false)
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

  const primaryColor = themeOverride || config.primary_color

  return (
    <div ref={containerRef} style={{ ['--mc-primary' as any]: primaryColor }}>
      {open ? (
        <ChatPanel onClose={() => setOpen(false)} />
      ) : (
        <button
          class="mc-launcher"
          aria-label="Open chat"
          onClick={() => setOpen(true)}
          style={{ background: primaryColor }}
        >
          {config.greeting_text || 'Chat'}
        </button>
      )}
    </div>
  )
}
