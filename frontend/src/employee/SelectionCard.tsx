// Alternatives as toggle cards (replaces the numbered text list and the chips). The index number on each
// card is the same number the employee can type ("1, 3"): the server numbers the options in this order.
import { useState } from 'react'
import type { ExpectsField, MessageIn } from '../api/types'
import { OPT_OUT_ID } from './comparison'
import { driveLabel } from './labels'
import { counterLabel, isBlocked, limitHint, toggleSelection } from './widgets'

type Send = (body: MessageIn, display: string) => Promise<boolean>

interface Props {
  f: ExpectsField
  fieldParam?: string
  disabled: boolean
  onSend: Send
  /** ids already chosen (the "לשנות בחירה" flow opens with the current selection checked) */
  initial?: string[]
  onDone?: () => void
}

export function SelectionCard({ f, fieldParam, disabled, onSend, initial, onDone }: Props) {
  const max = f.max ?? 1
  const [picked, setPicked] = useState<string[]>(() => (initial ?? []).filter((id) => f.choices.some((c) => c.id === id)))
  const full = picked.length >= max
  const field = fieldParam ?? f.field ?? undefined
  const rank = f.quick_replies.find((q) => q.id === 'rank')

  const submit = async () => {
    const labels = f.choices.filter((c) => picked.includes(c.id)).map((c) => c.label).join(', ')
    if (await onSend({ choices: picked, field }, labels)) onDone?.()
  }
  const askRank = async () => {
    if (rank && (await onSend({ choice: rank.id, field }, rank.label))) onDone?.()
  }

  return (
    <div className="selection">
      <div className="card-grid">
        {f.choices.map((c, i) => {
          const on = picked.includes(c.id)
          const blocked = isBlocked(picked, c.id, max)
          const optOut = c.id === OPT_OUT_ID
          const drive = driveLabel(c.drive)
          return (
            <button key={c.id} type="button" aria-pressed={on} disabled={disabled || blocked}
                    title={blocked ? limitHint(max) : undefined}
                    className={`car-card${on ? ' car-card-on' : ''}${optOut ? ' car-card-optout' : ''}`}
                    onClick={() => setPicked((p) => toggleSelection(p, c.id, max))}>
              <span className="car-index" aria-hidden="true">{i + 1}</span>
              <span className="car-name">{c.label}</span>
              {!optOut && (
                <span className="car-meta">
                  {c.group && <span className="badge">קבוצה {c.group}</span>}
                  {drive && <span className={`badge badge-${c.drive}`}>{drive}</span>}
                </span>
              )}
            </button>
          )
        })}
      </div>
      <div className="selection-bar">
        <span className="selection-counter" role="status" aria-live="polite">
          {counterLabel(picked.length, max)}{full ? `. ${limitHint(max)}` : ''}
        </span>
        <button type="button" className="chip chip-primary" disabled={disabled || picked.length === 0} onClick={submit}>
          השוואה
        </button>
        {rank && (
          <button type="button" className="chip chip-quiet" disabled={disabled} onClick={askRank}>
            מה הכי משתלם לי?
          </button>
        )}
      </div>
    </div>
  )
}
