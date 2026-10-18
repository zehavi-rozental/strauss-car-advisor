// The one primary input for the current question, chosen by widgetsFor(expects). A click sends a
// structured choice; the server checks it against `expects`. Typing stays possible in the composer.
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api/client'
import type { CarSearchItem, Expects, ExpectsField, MessageIn } from '../api/types'
import { fieldLabel } from './labels'
import { SelectionCard } from './SelectionCard'
import { widgetsFor } from './widgets'
import type { Widget } from './widgets'

type Send = (body: MessageIn, display: string) => Promise<boolean>

interface Props {
  expects: Expects | null
  disabled: boolean
  onSend: Send
  selected?: string[]
  onDone?: () => void
}

const TITLES: Record<string, string> = { car: 'הרכב שלך היום', options: 'רכבים להשוואה' }

export function AnswerWidgets({ expects, disabled, onSend, selected, onDone }: Props) {
  const widgets = widgetsFor(expects).filter((w) => w.kind !== 'profile_form')
  if (!widgets.length) return null
  const grouped = (expects?.fields.length ?? 0) > 1
  // Q3 (car + alternatives): two labelled sections in one card; a partial Q3 shows only what is open
  const titled = widgets.some((w) => w.kind === 'car_search' || w.kind === 'selection')
  return (
    <div className="answer-card">
      {widgets.map((w) => {
        const fieldParam = grouped ? w.field.field ?? undefined : undefined
        const body = <WidgetBody key={w.field.field ?? w.kind} w={w} fieldParam={fieldParam} disabled={disabled}
                                 onSend={onSend} selected={selected} onDone={onDone} />
        const title = titled ? TITLES[w.field.field ?? ''] : undefined
        return title
          ? <section key={w.field.field} className="q-section"><h4>{title}</h4>{body}</section>
          : body
      })}
    </div>
  )
}

function WidgetBody({ w, fieldParam, disabled, onSend, selected, onDone }:
  { w: Widget; fieldParam?: string; disabled: boolean; onSend: Send; selected?: string[]; onDone?: () => void }) {
  const f = w.field
  const one = (id: string, label: string) => onSend({ choice: id, field: fieldParam }, label)
  const quick = f.quick_replies.filter((q) => w.kind !== 'selection' || q.id !== 'rank').map((q) => {
    const label = f.type === 'number' && fieldParam ? `${fieldLabel(f.field ?? '')}: ${q.label}` : q.label
    return (
      <button key={q.id} type="button" className="chip chip-quiet" disabled={disabled} onClick={() => one(q.id, label)}>
        {label}
      </button>
    )
  })
  switch (w.kind) {
    case 'selection':
      return <SelectionCard f={f} fieldParam={fieldParam} disabled={disabled} onSend={onSend} initial={selected}
                            onDone={onDone} />
    case 'car_search':
      return <CarSearch fieldParam={fieldParam} disabled={disabled} onSend={onSend} quick={quick} />
    case 'select':
      return (
        <div className="reply-row">
          <select className="reply-select" disabled={disabled} defaultValue=""
                  onChange={(e) => {
                    const c = f.choices.find((x) => x.id === e.target.value)
                    if (c) void one(c.id, c.label)
                  }}>
            <option value="" disabled>בחירה מהרשימה…</option>
            {f.choices.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select>
          {quick}
        </div>
      )
    case 'buttons':
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
    default:
      return <div className="reply-row">{quick}</div>
  }
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
               placeholder="חיפוש דגם, למשל: קורולה" aria-label="חיפוש הרכב שלך היום"
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

export type { ExpectsField }
