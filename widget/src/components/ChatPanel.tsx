import { useState } from 'preact/hooks'
import { MessageList } from './MessageList'
import { InputBox } from './InputBox'
import { useChat } from '../hooks/useChat'
import { useWidget } from '../context'

interface Props {
  onClose: () => void
}

export function ChatPanel({ onClose }: Props) {
  const { config } = useWidget()
  const [conversationId] = useState(() =>
    Math.random().toString(36).slice(2) + Date.now().toString(36),
  )
  const { messages, pending, send } = useChat({ conversationId })

  return (
    <div class="mc-panel" role="dialog" aria-label={config.name}>
      <header class="mc-header" style={{ background: 'var(--mc-primary)' }}>
        <span>{config.name}</span>
        <button class="mc-close" aria-label="Close" onClick={onClose}>
          ×
        </button>
      </header>
      <MessageList messages={messages} pending={pending} />
      <InputBox onSend={send} disabled={pending} />
    </div>
  )
}
