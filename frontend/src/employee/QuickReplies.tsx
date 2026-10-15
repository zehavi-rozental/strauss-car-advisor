// Buttons built from the server's expects only. A click sends a structured choice; the server checks it.
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api/client'
import type { CarSearchItem, Expects, ExpectsField, MessageIn } from '../api/types'
import { fieldLabel } from './labels'
import { Q2_FIELD_NAMES } from './formFields'

type Send = (body: MessageIn, display: string) => Promise<boolean>

const SELECT_THRESHOLD = 8

export function QuickReplies({ expects, disabled, onSend, hideNumbers }:
  { expects: Expects | null; disabled: boolean; onSend: Send; hideNumbers?: boolean }) {
  if (!expects) return null
  const fields = expects.fields.length ? expects.fields : expects.type !== 'none' ? [expects] : []
  const grouped = fields.length > 1
  const visible = fields.filter((f) => f.choices.length > 0 || f.quick_replies.length > 0 || f.type === 'car_search')
  if (!visible.length) return null
  // number fields offer only quick replies ("אין", "לא ידוע"); in a grouped question each one is named
  // after its field, so "אין" never goes to a field the employee didn't mean
  const inForm = (f: ExpectsField) => !!hideNumbers && f.field !== null && Q2_FIELD_NAMES.has(f.field)
  const numbers = grouped ? visible.filter((f) => f.type === 'number' && !inForm(f)) : []
  // with the profile form on screen, its own "אין" buttons replace these chips
  const others = visible.filter((f) => !numbers.includes(f) && !inForm(f))
  return (
    <div className="quick-replies">
      {others.map((f) => (
        <FieldReplies key={f.field ?? f.type} f={f} fieldParam={grouped ? f.field ?? undefined : undefined}
                      disabled={disabled} onSend={onSend} />
      ))}
      {numbers.length > 0 && (
        <div className="reply-row">
          {numbers.flatMap((f) => f.quick_replies.map((q) => {
            const label = `${fieldLabel(f.field ?? '')}: ${q.label}`
            return (
              <button key={`${f.field}:${q.id}`} type="button" className="chip chip-quiet" disabled={disabled}
                      onClick={() => onSend({ choice: q.id, field: f.field ?? undefined }, label)}>
                {label}
              </button>
            )
          }))}
        </div>
      )}
    </div>
  )
}

function FieldReplies({ f, fieldParam, disabled, onSend }:
  { f: ExpectsField; fieldParam?: string; disabled: boolean; onSend: Send }) {
  const one = (id: string, label: string) => onSend({ choice: id, field: fieldParam }, label)
  const quick = f.quick_replies.map((q) => (
    <button key={q.id} type="button" className="chip chip-quiet" disabled={disabled} onClick={() => one(q.id, q.label)}>
      {q.label}
    </button>
  ))

  if (f.type === 'multi_choice') return <MultiChoice f={f} fieldParam={fieldParam} disabled={disabled} onSend={onSend} quick={quick} />
  if (f.type === 'car_search') return <CarSearch fieldParam={fieldParam} disabled={disabled} onSend={onSend} quick={quick} />

  if (f.choices.length > SELECT_THRESHOLD) {
    return (
      <div className="reply-row">
        <select className="reply-select" disabled={disabled} defaultValue=""
                onChange={(e) => {
                  const c = f.choices.find((x) => x.id === e.target.value)
                  if (c) one(c.id, c.label)
                }}>
          <option value="" disabled>בחירה מהרשימה…</option>
          {f.choices.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </select>
        {quick}
      </div>
    )
  }
  return (
    <div className="reply-row">
      {f.choices.map((c) => (
        <button key={c.id} type="button" className="chip" disabled={disabled} onClick={() => one(c.id, c.label)}>
          {c.label}
        </button>
      ))}
      {quick}
    </div>
  )
}

function MultiChoice({ f, fieldParam, disabled, onSend, quick }:
  { f: ExpectsField; fieldParam?: string; disabled: boolean; onSend: Send; quick: ReactNode }) {
  const [picked, setPicked] = useState<string[]>([])
  const max = f.max ?? 1
  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < max ? [...p, id] : p))
  const submit = async () => {
    const labels = f.choices.filter((c) => picked.includes(c.id)).map((c) => c.label).join(', ')
    if (await onSend({ choices: picked, field: fieldParam ?? f.field ?? undefined }, labels)) setPicked([])
  }
  return (
    <div className="reply-block">
      <div className="reply-hint">אפשר לבחור עד {max}</div>
      <div className="reply-row">
        {f.choices.map((c) => (
          <button key={c.id} type="button" className={picked.includes(c.id) ? 'chip chip-on' : 'chip'}
                  aria-pressed={picked.includes(c.id)} disabled={disabled} onClick={() => toggle(c.id)}>
            {c.label}
          </button>
        ))}
      </div>
      <div className="reply-row">
        <button type="button" className="chip chip-primary" disabled={disabled || !picked.length} onClick={submit}>
          חשב
        </button>
        {quick}
      </div>
    </div>
  )
}

function CarSearch({ fieldParam, disabled, onSend, quick }:
  { fieldParam?: string; disabled: boolean; onSend: Send; quick: ReactNode }) {
  const [q, setQ] = useState('')
  const [found, setFound] = useState<CarSearchItem[]>([])
  useEffect(() => {
    const query = q.trim()
    if (query.length < 2) {
      setFound([])
      return
    }
    let live = true
    const t = window.setTimeout(() => {
      api.searchCars(query).then((r) => { if (live) setFound(r) }).catch(() => { if (live) setFound([]) })
    }, 250)
    return () => {
      live = false
      window.clearTimeout(t)
    }
  }, [q])
  return (
    <div className="reply-block">
      <div className="reply-row">
        <input className="reply-search" type="search" value={q} maxLength={80} disabled={disabled}
               placeholder="חיפוש הרכב שלך היום…" aria-label="חיפוש הרכב שלך היום"
               onChange={(e) => setQ(e.target.value)} />
        {quick}
      </div>
      {found.length > 0 && (
        <div className="reply-row">
          {found.map((c) => (
            <button key={c.id} type="button" className="chip" disabled={disabled}
                    onClick={() => onSend({ choice: c.id, field: fieldParam }, c.label)}>
              {c.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
