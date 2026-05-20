import { createContext } from 'preact'
import { useContext, useEffect, useState } from 'preact/hooks'
import type { ComponentChildren } from 'preact'

export interface WidgetConfig {
  id: string
  name: string
  primary_color: string
  position: string
  greeting_text: string
  enabled_tools: string[]
}

export interface WidgetSession {
  token: string
  widget_session_id: string
  widget_id: string
  enabled_tools: string[]
  expires_in: number
}

interface WidgetCtxValue {
  config: WidgetConfig
  session: WidgetSession
  apiBase: string
  themeOverride: string | null
}

const WidgetCtx = createContext<WidgetCtxValue | null>(null)

interface ProviderProps {
  config: WidgetConfig
  session: WidgetSession
  apiBase: string
  children: ComponentChildren
}

export function WidgetProvider({ config, session, apiBase, children }: ProviderProps) {
  const [themeOverride, setThemeOverride] = useState<string | null>(null)

  useEffect(() => {
    // Listen for `mc:theme` from the host page. Origin check: anything (we're
    // inside an iframe; the parent could be any allowed origin).
    function onMessage(ev: MessageEvent) {
      if (!ev.data || typeof ev.data !== 'object') return
      if (ev.data.type === 'mc:theme' && ev.data.payload?.primaryColor) {
        setThemeOverride(String(ev.data.payload.primaryColor))
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  const value: WidgetCtxValue = { config, session, apiBase, themeOverride }
  return <WidgetCtx.Provider value={value}>{children}</WidgetCtx.Provider>
}

export function useWidget(): WidgetCtxValue {
  const v = useContext(WidgetCtx)
  if (!v) throw new Error('useWidget must be used inside WidgetProvider')
  return v
}
