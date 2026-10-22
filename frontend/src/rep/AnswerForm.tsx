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
  const newCarCopay = q.missing_field === 'NEW_CAR_COPAY'
  // a stated group the occupations table contradicts: the two candidates from the context, or another letter
  const groups = q.missing_field === 'ELIGIBILITY_GROUP'
    ? [q.context.stated_group, q.context.table_group].filter((g): g is string => !!g) : []
  const quick = confirmValue !== null || groups.length > 0
  const [other, setOther] = useState(!quick)
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
            {newCarCopay ? 'אישור סכום ברירת המחדל' : 'אישור'}{' '}
            <bdi className="num">{newCarCopay ? '(' : ''}{Number(confirmValue).toLocaleString('he-IL')} ₪{newCarCopay ? ')' : ''}</bdi>
          </button>
          {!other && <button type="button" className="chip chip-quiet" disabled={busy} onClick={() => setOther(true)}>סכום אחר</button>}
        </div>
      )}

      {groups.length > 0 && (
        <div className="confirm-row" role="group" aria-label="קבוצת הזכאות">
          {groups.map((g, n) => (
            <button key={`${n}-${g}`} type="button" className={n === 0 ? 'chip' : 'chip chip-primary'} disabled={busy}
                    onClick={() => submit(g)}>
              {n === 0 ? 'כפי שהעובד ציין' : 'לפי טבלת העיסוקים'}: קבוצה {g}
            </button>
          ))}
          {!other && <button type="button" className="chip chip-quiet" disabled={busy} onClick={() => setOther(true)}>קבוצה אחרת</button>}
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
                {q.answer_type === 'MONEY' ? 'סכום (₪)' : q.answer_type === 'DATE' ? 'תאריך'
                  : groups.length > 0 ? 'אות הקבוצה' : 'תשובה'}
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
        {/* the date is real only for a new car's copay: the employee is shown "תחול מ-" / "בתוקף מ-" with it */}
        {newCarCopay && (
          <div className="form-field">
            <label htmlFor={`d-${q.id}`}>ההשתתפות חלה מתאריך</label>
            <input id={`d-${q.id}`} type="date" value={date} disabled={busy} onChange={(e) => setDate(e.target.value)} />
          </div>
        )}
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
