// Buttons for the current question when it is closed (widgetsFor). A click sends a structured choice; the
// server checks it against `expects`. Open questions get nothing here: the employee types.
import { useState } from 'react'
import type { Expects, ExpectsField, MessageIn } from '../api/types'
import { widgetsFor } from './widgets'
import type { Widget } from './widgets'

type Send = (body: MessageIn, display: string) => Promise<boolean>

interface Props {
  expects: Expects | null
  disabled: boolean
  onSend: Send
  /** "לברר מול נציג" on today's copay: opens the inquiry draft (sent only when the employee sends it) */
  onCopayRep?: (car?: string, catalogAmount?: string) => void
}

export function AnswerWidgets({ expects, disabled, onSend, onCopayRep }: Props) {
  const widgets = widgetsFor(expects)
  if (!widgets.length) return null
  const grouped = (expects?.fields.length ?? 0) > 1
  return (
    <div className="reply-buttons">
      {widgets.map((w) => (
        w.field.field === 'copay'
          ? <CopayChoices key="copay" field={w.field} disabled={disabled} onSend={onSend} onRep={onCopayRep}
                          fieldParam={grouped ? 'copay' : undefined} />
          : <WidgetBody key={w.field.field ?? w.kind} w={w} disabled={disabled} onSend={onSend}
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
        <button key={c.id} type="button" className={c.id === 'skip' ? 'chip chip-quiet' : 'chip'}
                disabled={disabled} onClick={() => one(c.id, c.label)}>
          {c.label}
        </button>
      ))}
    </div>
  )
}

export type { ExpectsField }

/** An amount typed in "הסכום שונה": whole shekels or up to two decimals, at most five digits (the server checks
 * the range). */
export const copayAmountValid = (amount: string) => /^\d{1,5}([.,]\d{1,2})?$/.test(amount.trim())

/** Today's copay with a catalog amount (31/10): three choices only. "זה הסכום שלי" sends the catalog choice;
 * "הסכום שונה" opens an amount field here (the amount typed is the answer); "לברר מול נציג" opens the inquiry draft. */
function CopayChoices({ field, fieldParam, disabled, onSend, onRep }:
  { field: ExpectsField; fieldParam?: string; disabled: boolean; onSend: Send;
    onRep?: (car?: string, catalogAmount?: string) => void }) {
  const [other, setOther] = useState(false)
  const [amount, setAmount] = useState('')
  const valid = copayAmountValid(amount)
  const choice = (id: string) => field.choices.find((c) => c.id === id || c.id.startsWith(id + ':'))
  const mine = choice('catalog')
  return (
    <div className="reply-row copay-choices">
      {mine && (
        <button type="button" className="chip chip-primary" disabled={disabled}
                onClick={() => void onSend({ choice: mine.id, field: fieldParam ?? 'copay' }, mine.label)}>
          {mine.label}
        </button>
      )}
      <button type="button" className="chip" aria-expanded={other} disabled={disabled}
              onClick={() => setOther((o) => !o)}>
        {choice('other')?.label ?? 'הסכום שונה'}
      </button>
      {onRep && (
        <button type="button" className="chip chip-quiet" disabled={disabled}
                onClick={() => onRep(field.context?.car, field.context?.catalog_amount)}>
          {choice('rep')?.label ?? 'לברר מול נציג'}
        </button>
      )}
      {other && (
        <form className="copay-amount" onSubmit={(e) => {
          e.preventDefault()
          if (valid) void onSend({ text: `השתתפות ${amount.trim()}` }, `ההשתתפות החודשית שלי: ${amount.trim()} ₪`)
        }}>
          <label htmlFor="copay-amount">סכום ההשתתפות החודשי (₪)</label>
          <input id="copay-amount" inputMode="decimal" dir="ltr" autoFocus value={amount} disabled={disabled}
                 onChange={(e) => setAmount(e.target.value)} placeholder="300" />
          <button type="submit" className="btn btn-primary btn-sm" disabled={disabled || !valid}>אישור</button>
        </form>
      )}
    </div>
  )
}
