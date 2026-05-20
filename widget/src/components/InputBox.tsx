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
        placeholder="Ask anything…"
        disabled={disabled}
        aria-label="Message"
      />
      <button type="submit" disabled={disabled || !value.trim()}>
        Send
      </button>
    </form>
  )
}
