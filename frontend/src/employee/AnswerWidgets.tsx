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
  // the representative's copay update is drawn under the answer itself (MessageList), never here (1/11)
  const widgets = widgetsFor(expects).filter((w) => w.field.field !== 'rep_copay')
  if (!widgets.length) return null
  const grouped = (expects?.fields.length ?? 0) > 1
  return (
    <div className="reply-buttons">
      {widgets.map((w) => (
        w.field.field === 'rep_copay'
          ? <RepCopayUpdate key="rep_copay" field={w.field} disabled={disabled} onSend={onSend} />
          : w.field.field === 'copay'
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

/** The representative's amount for today's copay (1/11): one button, then a short confirmation with the amount, the
 * car and the amount it replaces. Only "אישור העדכון" sends it; the server then removes the offer (applied once). */
export function RepCopayUpdate({ field, disabled, onSend }: { field: ExpectsField; disabled: boolean; onSend: Send }) {
  const [asking, setAsking] = useState(false)
  const choice = field.choices[0]
  const ctx = field.context ?? {}
  if (!choice) return null
  const shekel = (v?: string | null) => (v ? `${Number(v).toLocaleString('he-IL')} ₪` : '')
  const replaces = ctx.previous && ctx.previous !== ctx.amount ? ctx.previous : null
  const provisional = !replaces && ctx.provisional ? ctx.provisional : null
  return (
    <div className="reply-row rep-copay-update">
      {!asking ? (
        <button type="button" className="chip chip-primary" disabled={disabled} onClick={() => setAsking(true)}>
          {choice.label}
        </button>
      ) : (
        <div className="rep-copay-confirm" role="group" aria-label="אישור העדכון">
          <p>
            לעדכן את ההשתתפות החודשית{ctx.car ? <> ב-<bdi>{ctx.car}</bdi></> : null} ל-<strong>{shekel(ctx.amount)}</strong>,
            לפי תשובת הנציג?{replaces && <> הסכום יחליף את {shekel(replaces)} שמסרתם.</>}
            {provisional && <> החישוב הנוכחי מבוסס על {shekel(provisional)} לפי הקטלוג, והוא יחושב מחדש.</>}
          </p>
          <div className="reply-row">
            <button type="button" className="chip chip-primary" disabled={disabled}
                    onClick={() => void onSend({ choice: choice.id, field: 'rep_copay' }, `עדכון החישוב לפי תשובת הנציג: ${shekel(ctx.amount)}`)}>
              אישור העדכון
            </button>
            <button type="button" className="chip chip-quiet" disabled={disabled} onClick={() => setAsking(false)}>
              ביטול
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
