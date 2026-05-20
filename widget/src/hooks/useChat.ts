// Real SSE chat hook. Streams /api/chat via @microsoft/fetch-event-source with
// the widget's anon JWT in the Authorization header.

import { useCallback, useState } from 'preact/hooks'
import { fetchEventSource } from '@microsoft/fetch-event-source'

export interface ToolCall {
  id: string
  name: string
  ok?: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  toolCalls?: ToolCall[]
}

interface ChatEvent {
  type: 'token' | 'tool_call_start' | 'tool_call_result' | 'done' | 'error'
  content?: string
  name?: string
  args?: unknown
  result?: { ok?: boolean }
  msg_id?: string
  code?: string
  message?: string
}

function randomId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

interface UseChatArgs {
  conversationId: string
  apiBase: string
  token: string
}

export function useChat({ conversationId, apiBase, token }: UseChatArgs) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pending, setPending] = useState(false)

  const send = useCallback(
    async (text: string) => {
      const userMsg: ChatMessage = { id: randomId(), role: 'user', content: text }
      const assistantId = randomId()
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: 'assistant',
        content: '',
        toolCalls: [],
      }
      setMessages((prev) => [...prev, userMsg, assistantMsg])
      setPending(true)

      const controller = new AbortController()

      try {
        await fetchEventSource(`${apiBase}/api/chat`, {
          method: 'POST',
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ message: text, conversation_id: conversationId }),
          openWhenHidden: true,
          onmessage(ev) {
            if (ev.data === '[DONE]') {
              setPending(false)
              return
            }
            let parsed: ChatEvent
            try {
              parsed = JSON.parse(ev.data)
            } catch {
              return
            }
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id !== assistantId) return m
                if (parsed.type === 'token' && parsed.content) {
                  return { ...m, content: m.content + parsed.content }
                }
                if (parsed.type === 'tool_call_start' && parsed.name) {
                  const tc: ToolCall = { id: randomId(), name: parsed.name }
                  return { ...m, toolCalls: [...(m.toolCalls || []), tc] }
                }
                if (parsed.type === 'tool_call_result' && parsed.name) {
                  const tcs = m.toolCalls || []
                  const idx = [...tcs].reverse().findIndex((t) => t.name === parsed.name)
                  if (idx === -1) return m
                  const realIdx = tcs.length - 1 - idx
                  const updated = tcs.slice()
                  updated[realIdx] = { ...updated[realIdx], ok: parsed.result?.ok !== false }
                  return { ...m, toolCalls: updated }
                }
                if (parsed.type === 'error') {
                  return {
                    ...m,
                    content:
                      (m.content ? m.content + '\n\n' : '') +
                      `*error (${parsed.code || 'unknown'}): ${parsed.message || ''}*`,
                  }
                }
                return m
              }),
            )
          },
          onerror(err) {
            setPending(false)
            throw err
          },
        })
      } catch (err) {
        setPending(false)
        // eslint-disable-next-line no-console
        console.warn('[maintainer-copilot] chat stream ended:', err)
      }
    },
    [apiBase, token, conversationId],
  )

  return { messages, pending, send }
}
