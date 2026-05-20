import { useEffect, useRef } from 'preact/hooks'
import { marked } from 'marked'
import type { ChatMessage } from '../hooks/useChat'

interface Props {
  messages: ChatMessage[]
  pending: boolean
  greeting?: string
}

function renderMarkdown(text: string): string {
  try {
    return marked.parse(text, { async: false }) as string
  } catch {
    return text
  }
}

export function MessageList({ messages, pending, greeting }: Props) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages, pending])

  if (messages.length === 0 && !pending) {
    return (
      <div class="mc-messages" ref={ref}>
        <div class="mc-empty">
          <strong>{greeting || 'How can I help?'}</strong>
          Ask about an issue, paste code, or just say hi.
        </div>
      </div>
    )
  }

  return (
    <div class="mc-messages" ref={ref}>
      {messages.map((m) => (
        <div class={`mc-msg mc-msg-${m.role}`} key={m.id}>
          <div class="mc-bubble">
            {m.role === 'assistant' ? (
              <div
                class="mc-md"
                // eslint-disable-next-line react/no-danger
                dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }}
              />
            ) : (
              <div class="mc-md">{m.content}</div>
            )}
          </div>
          {m.toolCalls && m.toolCalls.length > 0 && (
            <ul class="mc-tools">
              {m.toolCalls.map((t) => (
                <li key={t.id}>
                  <code>{t.name}</code>
                  {t.ok === false && <span class="mc-tool-fail">failed</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      {pending && (
        <div class="mc-thinking" aria-label="Assistant is typing">
          <span></span>
          <span></span>
          <span></span>
        </div>
      )}
    </div>
  )
}
