// Shared widget types. Context-free now — we pass these through props instead
// (Preact 10 context with a null default was misbehaving in the IIFE bundle).

// Resolved palette for the admin-selected preset (see
// app/domain/widget_themes.py). The bundle applies these verbatim as CSS
// custom properties — it never needs to know the preset list itself.
export interface WidgetTheme {
  label: string
  panel: string
  surface: string
  border: string
  fg: string
  muted: string
  accent: string
  on_accent: string
}

export interface WidgetConfig {
  id: string
  name: string
  primary_color: string
  position: string
  greeting_text: string
  enabled_tools: string[]
  theme: WidgetTheme
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
