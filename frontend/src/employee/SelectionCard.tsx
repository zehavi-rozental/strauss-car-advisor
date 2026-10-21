// Alternatives as toggle cards, typographic (no car pictures): the model name large, year, drive, group and the
// catalog details. The index number on each card is the same number the employee can type ("1, 3"): the
// server numbers the options in this order.
import { useState } from 'react'
import type { CatalogItem, ExpectsField, MessageIn } from '../api/types'
import { Money } from '../format/Amount'
import { Icon } from '../ui/Icon'
import { OPT_OUT_ID } from './comparison'
import { DRIVE_ICON, driveLabel, splitLabel } from './labels'
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
  /** catalog rows by code, for the details line (optional: the cards work without them) */
  details?: Record<string, CatalogItem>
}

export function SelectionCard({ f, fieldParam, disabled, onSend, initial, onDone, details }: Props) {
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
          const { name, year } = splitLabel(c.label)
          const d = details?.[c.id]
          return (
            <button key={c.id} type="button" aria-pressed={on} disabled={disabled || blocked}
                    title={blocked ? limitHint(max) : undefined} data-drive={c.drive ?? undefined}
                    className={`car-card${on ? ' car-card-on' : ''}${optOut ? ' car-card-optout' : ''}`}
                    onClick={() => setPicked((p) => toggleSelection(p, c.id, max))}>
              <span className="car-card-top">
                <span className="car-index num" aria-hidden="true">{i + 1}</span>
                <span className="car-check" aria-hidden="true"><Icon name="check" size={14} /></span>
              </span>
              <span className={`car-name${optOut ? '' : ' latin'}`}>{name}</span>
              {!optOut && (
                <span className="car-meta">
                  {year && <span className="car-year num">{year}</span>}
                  {drive && (
                    <span className={`car-drive drive-${c.drive}`}>
                      <Icon name={DRIVE_ICON[c.drive ?? ''] ?? 'road'} size={14} />{drive}
                    </span>
                  )}
                  {c.group && <span className="car-group">קבוצה {c.group}</span>}
                </span>
              )}
              {!optOut && d && (
                <span className="car-facts">
                  <span><span className="car-fact-label">שווי שימוש</span> <Money value={d.use_value} /></span>
                  <span><span className="car-fact-label">השתתפות לפי הקטלוג</span> <Money value={d.default_copay} /></span>
                </span>
              )}
              <span className="car-petal" aria-hidden="true" />
            </button>
          )
        })}
      </div>
      <div className="selection-bar">
        <span className="selection-counter" role="status" aria-live="polite">
          <span className="selection-dots" aria-hidden="true">
            {Array.from({ length: max }, (_, k) => <i key={k} className={k < picked.length ? 'is-on' : undefined} />)}
          </span>
          {counterLabel(picked.length, max)}{full ? `. ${limitHint(max)}` : ''}
        </span>
        {rank && (
          <button type="button" className="chip chip-quiet" disabled={disabled} onClick={askRank}>
            מה הכי משתלם לי?
          </button>
        )}
        <button type="button" className="chip chip-primary" disabled={disabled || picked.length === 0} onClick={submit}>
          השוואה
          <Icon name="chevron" size={16} />
        </button>
      </div>
    </div>
  )
}
