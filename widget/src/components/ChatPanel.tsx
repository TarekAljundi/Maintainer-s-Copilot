import { useState } from 'preact/hooks'
import { MessageList } from './MessageList'
import { InputBox } from './InputBox'
import { useChat } from '../hooks/useChat'
import type { WidgetRuntime } from '../context'

interface Props extends WidgetRuntime {
  onClose: () => void
}

function initialFor(name: string): string {
  const ch = (name || '?').trim().charAt(0).toUpperCase()
  return /[A-Z0-9]/.test(ch) ? ch : '?'
}

export function ChatPanel({ config, session, apiBase, onClose }: Props) {
  const [conversationId] = useState(() =>
    Math.random().toString(36).slice(2) + Date.now().toString(36),
  )
  const { messages, pending, send } = useChat({
    conversationId,
    apiBase,
    token: session.token,
  })

  return (
    <div class="mc-panel" role="dialog" aria-label={config.name}>
      <header class="mc-header">
        <div class="mc-avatar" aria-hidden="true">{initialFor(config.name)}</div>
        <div class="mc-title">
          <div class="name">{config.name}</div>
          <div class="sub">Online · usually replies in seconds</div>
        </div>
        <button class="mc-close" aria-label="Close chat" onClick={onClose}>
          ×
        </button>
      </header>
      <MessageList messages={messages} pending={pending} greeting={config.greeting_text} />
      <InputBox onSend={send} disabled={pending} />
    </div>
  )
}
