// Buttons for the current question when it is closed (widgetsFor). A click sends a structured choice; the
// server checks it against `expects`. Open questions get nothing here: the employee types.
import type { Expects, ExpectsField, MessageIn } from '../api/types'
import { widgetsFor } from './widgets'
import type { Widget } from './widgets'

type Send = (body: MessageIn, display: string) => Promise<boolean>

interface Props {
  expects: Expects | null
  disabled: boolean
  onSend: Send
}

export function AnswerWidgets({ expects, disabled, onSend }: Props) {
  const widgets = widgetsFor(expects)
  if (!widgets.length) return null
  const grouped = (expects?.fields.length ?? 0) > 1
  return (
    <div className="reply-buttons">
      {widgets.map((w) => (
        <WidgetBody key={w.field.field ?? w.kind} w={w} disabled={disabled} onSend={onSend}
                    fieldParam={grouped ? w.field.field ?? undefined : undefined} />
      ))}
    </div>
  )
}

function WidgetBody({ w, fieldParam, disabled, onSend }:
  { w: Widget; fieldParam?: string; disabled: boolean; onSend: Send }) {
  const f = w.field
  const one = (id: string, label: string) => onSend({ choice: id, field: fieldParam ?? f.field ?? undefined }, label)
  if (w.kind === 'select') {
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
      </div>
    )
  }
  return (
    <div className="reply-row">
      {f.choices.map((c) => (
        <button key={c.id} type="button" className={f.field === 'ask_rep' ? 'chip chip-quiet' : 'chip'}
                disabled={disabled} onClick={() => one(c.id, c.label)}>
          {c.label}
        </button>
      ))}
    </div>
  )
}

export type { ExpectsField }
