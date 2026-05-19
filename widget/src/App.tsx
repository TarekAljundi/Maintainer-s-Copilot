import { useState } from 'preact/hooks'
import { ChatPanel } from './components/ChatPanel'

export function App() {
  const [open, setOpen] = useState(false)
  return open ? <ChatPanel onClose={() => setOpen(false)} /> : (
    <button onClick={() => setOpen(true)}>Chat</button>
  )
}
