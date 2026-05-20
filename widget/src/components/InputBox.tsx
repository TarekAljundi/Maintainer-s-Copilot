import { useState } from 'preact/hooks'

interface Props {
  onSend: (text: string) => void
  disabled?: boolean
}

export function InputBox({ onSend, disabled }: Props) {
  const [value, setValue] = useState('')

  function submit(ev: Event) {
    ev.preventDefault()
    const text = value.trim()
    if (!text || disabled) return
    onSend(text)
    setValue('')
  }

  return (
    <form class="mc-input" onSubmit={submit}>
      <input
        type="text"
        value={value}
        onInput={(e) => setValue((e.target as HTMLInputElement).value)}
        placeholder="Type a message…"
        disabled={disabled}
        aria-label="Message"
      />
      <button
        type="submit"
        class="mc-send"
        disabled={disabled || !value.trim()}
        aria-label="Send"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <line x1="22" y1="2" x2="11" y2="13"></line>
          <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
        </svg>
      </button>
    </form>
  )
}
