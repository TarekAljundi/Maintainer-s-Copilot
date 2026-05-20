// Shared widget types. Context-free now — we pass these through props instead
// (Preact 10 context with a null default was misbehaving in the IIFE bundle).

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

export interface WidgetRuntime {
  config: WidgetConfig
  session: WidgetSession
  apiBase: string
}
