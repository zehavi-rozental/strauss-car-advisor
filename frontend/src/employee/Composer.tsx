import { useState } from 'react'

// Free text is the primary input: the box is always enabled (typing never waits); only sending waits for
// the previous turn. The backend's settings.max_message_chars (not exposed by the API); the server still enforces it.
export const MAX_MESSAGE_CHARS = 1000

export function Composer({ disabled, onSend, placeholder = 'כתבו הודעה…' }:
  { disabled: boolean; onSend: (text: string) => Promise<boolean>; placeholder?: string }) {
  const [text, setText] = useState('')
  const submit = async () => {
    const value = text.trim()
    if (!value || disabled) return
    setText('')
    if (!(await onSend(value))) setText(value)
  }
  const left = MAX_MESSAGE_CHARS - text.length
  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); submit() }}>
      <textarea
        value={text}
        rows={1}
        maxLength={MAX_MESSAGE_CHARS}
        placeholder={placeholder}
        aria-label="הודעה"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
      />
      {left < 100 && <span className="counter">{left}</span>}
      <button type="submit" className="send" disabled={disabled || !text.trim()} aria-label="שליחה">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path d="M20 12 4 4l3 8-3 8 16-8Z" fill="currentColor" transform="matrix(-1 0 0 1 24 0)" />
        </svg>
      </button>
    </form>
  )
}
