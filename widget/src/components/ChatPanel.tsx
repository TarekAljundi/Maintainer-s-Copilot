import { MessageList } from './MessageList'
import { InputBox } from './InputBox'

export function ChatPanel({ onClose }: { onClose: () => void }) {
  return (
    <div>
      <MessageList />
      <InputBox />
    </div>
  )
}
