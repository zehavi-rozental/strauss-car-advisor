import { useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, repApi } from '../api/client'
import type { RepQuestion } from '../api/types'
import { Icon } from '../ui/Icon'
import { checkMoney } from './repForm'

interface Props {
  question: RepQuestion
  token: string
  onAnswered: () => void
  onUnauthorized: () => void
  onConflict: () => void
}

export function AnswerForm({ question: q, token, onAnswered, onUnauthorized, onConflict }: Props) {
  const confirmValue = q.mode === 'CONFIRM' && q.answer_type === 'MONEY' ? q.suggested_value : null
  const [other, setOther] = useState(confirmValue === null)
  const [value, setValue] = useState('')
  const [date, setDate] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (answer: string) => {
    setBusy(true)
    setError(null)
    try {
      await repApi.answer(token, q.id, {
        value: answer, ...(date ? { effective_date: date } : {}), ...(note.trim() ? { note: note.trim() } : {}),
      })
      onAnswered()
    } catch (e) {
      if (!(e instanceof ApiError)) setError('משהו השתבש. נסו שוב.')
      else if (e.status === 401) onUnauthorized()
      else if (e.status === 409) { setError('כבר נענתה'); onConflict() }
      else setError(e.message_he)
    } finally {
      setBusy(false)
    }
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (q.answer_type === 'MONEY') {
      const c = checkMoney(value)
      if (!c.ok) return setError(c.message)
      return void submit(c.value)
    }
    if (!value.trim()) return setError('יש להזין תשובה')
    void submit(value.trim())
  }

  return (
    <form className="answer-form" onSubmit={onSubmit}>
      {confirmValue !== null && (
        <div className="confirm-row">
          <button type="button" className="chip chip-primary" disabled={busy} onClick={() => submit(confirmValue)}>
            <Icon name="check" size={18} />
            אישור <bdi className="num">{Number(confirmValue).toLocaleString('he-IL')} ₪</bdi>
          </button>
          {!other && <button type="button" className="chip chip-quiet" disabled={busy} onClick={() => setOther(true)}>סכום אחר</button>}
        </div>
      )}

      {other && (
        <div className="form-field">
          {q.answer_type === 'BOOLEAN' ? (
            <div className="reply-row" role="group" aria-label="תשובה">
              {[['true', 'כן'], ['false', 'לא']].map(([v, l]) => (
                <button key={v} type="button" className={value === v ? 'chip chip-on' : 'chip'} aria-pressed={value === v}
                        disabled={busy} onClick={() => setValue(v)}>{l}</button>
              ))}
            </div>
          ) : (
            <>
              <label htmlFor={`a-${q.id}`}>
                {q.answer_type === 'MONEY' ? 'סכום (₪)' : q.answer_type === 'DATE' ? 'תאריך' : 'תשובה'}
              </label>
              <input id={`a-${q.id}`} value={value} disabled={busy} aria-invalid={error ? true : undefined}
                     type={q.answer_type === 'DATE' ? 'date' : 'text'}
                     inputMode={q.answer_type === 'MONEY' ? 'decimal' : undefined}
                     onChange={(e) => { setValue(e.target.value); setError(null) }} />
            </>
          )}
        </div>
      )}

      <div className="form-grid answer-extras">
        <div className="form-field">
          <label htmlFor={`d-${q.id}`}>בתוקף מתאריך</label>
          <input id={`d-${q.id}`} type="date" value={date} disabled={busy} onChange={(e) => setDate(e.target.value)} />
        </div>
        <div className="form-field">
          <label htmlFor={`n-${q.id}`}>הערה לעובד</label>
          <input id={`n-${q.id}`} value={note} maxLength={500} disabled={busy} onChange={(e) => setNote(e.target.value)} />
        </div>
      </div>

      {error && <p className="field-error" role="alert">{error}</p>}
      {other && (
        <div className="form-actions">
          <button type="submit" className="chip chip-primary" disabled={busy || !value.trim()}>
            {busy ? 'שולח…' : 'שליחת תשובה'}
          </button>
        </div>
      )}
    </form>
  )
}
