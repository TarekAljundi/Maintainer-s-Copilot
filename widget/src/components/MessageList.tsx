import { marked } from 'marked'
import type { ChatMessage } from '../hooks/useChat'

interface Props {
  messages: ChatMessage[]
  pending: boolean
}

function renderMarkdown(text: string): string {
  try {
    return marked.parse(text, { async: false }) as string
  } catch {
    return text
  }
}

export function MessageList({ messages, pending }: Props) {
  return (
    <div class="mc-messages">
      {messages.map((m) => (
        <div class={`mc-msg mc-msg-${m.role}`} key={m.id}>
          {m.role === 'assistant' ? (
            <div
              class="mc-md"
              // eslint-disable-next-line react/no-danger
              dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }}
            />
          ) : (
            <div class="mc-md">{m.content}</div>
          )}
          {m.toolCalls && m.toolCalls.length > 0 && (
            <ul class="mc-tools">
              {m.toolCalls.map((t) => (
                <li key={t.id}>
                  <code>{t.name}</code>
                  {t.ok === false && <span class="mc-tool-fail"> failed</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      {pending && <div class="mc-thinking">…</div>}
    </div>
  )
}
