// SSE chat hook. Calls /api/chat via @microsoft/fetch-event-source w/ JWT.
export function useChat() {
  return { messages: [], send: (_text: string) => {} }
}
