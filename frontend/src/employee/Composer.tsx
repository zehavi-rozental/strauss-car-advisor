import { useEffect, useRef, useState } from 'react'
import { Icon } from '../ui/Icon'

// Free text is the primary input: the box is always enabled (typing never waits); only sending waits for
// the previous turn. The backend's settings.max_message_chars (not exposed by the API); the server still enforces it.
export const MAX_MESSAGE_CHARS = 1000

// An unsent draft survives a trip to the representative area and back (this tab only, never sent anywhere).
const DRAFT_KEY = 'sca.draft'
const readDraft = () => { try { return sessionStorage.getItem(DRAFT_KEY) ?? '' } catch { return '' } }
const writeDraft = (v: string) => { try { if (v) sessionStorage.setItem(DRAFT_KEY, v); else sessionStorage.removeItem(DRAFT_KEY) } catch { /* no storage */ } }

export function Composer({ disabled, onSend, placeholder = 'כתבו הודעה…', autoFocus = false }:
  { disabled: boolean; onSend: (text: string) => Promise<boolean>; placeholder?: string; autoFocus?: boolean }) {
  const [text, setText] = useState(readDraft)
  const box = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { writeDraft(text) }, [text])
  useEffect(() => {
    // the first screen invites typing; on a phone the keyboard would hide the opening, so desktop only
    if (autoFocus && window.matchMedia?.('(pointer: fine)').matches) box.current?.focus({ preventScroll: true })
  }, [autoFocus])

  const submit = async () => {
    const value = text.trim()
    if (!value || disabled) return
    setText('')
    if (!(await onSend(value))) setText(value)
  }
  const left = MAX_MESSAGE_CHARS - text.length
  const ready = !disabled && text.trim().length > 0
  return (
    <form className={`composer${ready ? ' is-ready' : ''}`} onSubmit={(e) => { e.preventDefault(); submit() }}>
      <textarea
        ref={box}
        value={text}
        rows={1}
        maxLength={MAX_MESSAGE_CHARS}
        placeholder={placeholder}
        aria-label="הודעה"
        enterKeyHint="send"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
      />
      {left < 100 && <span className="counter num" aria-live="polite">{left}</span>}
      <button type="submit" className="send" disabled={!ready} aria-label="שליחה">
        <Icon name="send" size={20} />
      </button>
    </form>
  )
}
