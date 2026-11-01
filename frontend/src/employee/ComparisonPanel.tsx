// The comparison and the transparent breakdown, as one screen over the chat (full screen on a phone).
// Top: today and each option, each answering with its monthly net in large type and the change against today.
// Then "למה?", the breakdown table (the engine's lines, folded: one click away), the assumptions, and the
// actions: another selection or "תיקון נתונים". Built from the same results the cards use, so it updates with
// the same sync. No amount is calculated here.
import type { CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import type { CatalogItem, Expects, MessageIn, OptionResult } from '../api/types'
import { STALE_NOTE } from './MessageList'
import { Money } from '../format/Amount'
import { Icon } from '../ui/Icon'
import { useDialog } from '../ui/useDialog'
import type { Cell } from './comparison'
import { CarName, CopayLine, NetChange, StateBadge } from './ResultCard'
import { SelectionCard } from './SelectionCard'
import { rankResults } from './SummaryCard'
import { breakdownRows, driverBars, resultState, toneOf } from './resultView'

interface Props {
  results: OptionResult[]
  expects: Expects | null
  disabled: boolean
  onClose: () => void
  onSend: (body: MessageIn, display: string) => Promise<boolean>
  onCorrect: () => void
  /** calculated before the last change: shown as history, never as the current result */
  stale?: boolean
  details?: Record<string, CatalogItem>
  /** opened from the option map (1/11): a comparison to look at. It never changes the chat's comparison; only
   *  "להעביר להשוואה בצ'אט" does, through the same selection the chat uses */
  browse?: boolean
  /** after the browsed cars were sent to the chat's comparison */
  onMoved?: () => void
}

/** The browsed cars may move into the chat's comparison only as the chat offers them (its options question). */
export function canMoveToChat(ids: string[], optionsField: { choices: { id: string }[] } | null, stale: boolean): boolean {
  return optionsField != null && !stale && ids.length > 0 && ids.every((id) => optionsField.choices.some((c) => c.id === id))
}

export const BROWSE_NOTE = "השוואה לעיון בלבד. ההשוואה בצ'אט לא משתנה."
export const MOVE_TO_CHAT = "להעביר להשוואה בצ'אט"

function CellView({ cell }: { cell: Cell }) {
  if (cell.kind === 'money') {
    const n = Number(cell.value)
    const sign = cell.signed && n !== 0 ? (n < 0 ? 'negative' : 'positive') : undefined
    return <Money value={cell.value} signed={cell.signed} className={sign} />
  }
  if (cell.kind === 'text') return <>{cell.text}</>
  return <span className="muted" aria-label="אין">—</span>
}

export function ComparisonPanel({ results, stale = false, expects, disabled, onClose, onSend, onCorrect, details,
                                  browse = false, onMoved }: Props) {
  const shown = results.slice(0, 3)
  const rows = breakdownRows(shown)
  const ranked = stale ? shown : rankResults(shown)       // never rank an out-of-date result
  const today = shown.find((i) => i.result.today)?.result.today ?? null
  // the pending-copay assumption is already the "ממתין לאישור" tag in the table
  const assumptions = [...new Set(shown.flatMap((i) => i.result.assumptions))].filter((x) => !x.includes('ממתינה לאישור נציג'))
  const optionsField = expects?.fields.find((f) => f.field === 'options') ?? (expects?.field === 'options' ? expects : null)
  const why = shown.filter((i) => i.result.status === 'OK')
  const closeRef = useDialog(onClose)
  // the browsed cars can move into the chat's comparison when the chat offers these cars (its options question)
  const movable = browse && canMoveToChat(shown.map((i) => i.option_id), optionsField, stale)
  const moveToChat = async () => {
    if (await onSend({ choices: shown.map((i) => i.option_id), field: 'options' }, shown.map((i) => i.label).join(', '))) {
      onMoved?.()
    }
  }

  // rendered at the end of <body>: above the top bar, outside the chat's own stacking context
  return createPortal(
    <div className="panel-backdrop" onClick={onClose}>
      <aside className="comparison-panel" role="dialog" aria-modal="true" aria-label="טבלת השוואה"
             onClick={(e) => e.stopPropagation()}>
        <header className="panel-header">
          <div>
            <p className="panel-kicker">{browse ? 'לעיון' : 'פירוט החישוב'}</p>
            <h2>טבלת השוואה</h2>
          </div>
          <button ref={closeRef} type="button" className="panel-close" onClick={onClose} aria-label="סגירה">
            <Icon name="close" size={22} />
          </button>
        </header>

        <div className={stale ? 'panel-body result-stale' : 'panel-body'}>
          {stale && <p className="stale-note" role="status">{STALE_NOTE}</p>}
          {browse && <p className="browse-note" role="note"><Icon name="eye" size={16} />{BROWSE_NOTE}</p>}
          {/* side by side: today and each option, ranked */}
          <section className="compare-stage" aria-label="השוואה" style={{ '--cols': ranked.length + 1 } as CSSProperties}>
            <div className="compare-col is-today">
              <span className="compare-kicker">היום</span>
              {today ? <CarName label={today.label} /> : <span className="car-title">—</span>}
              <span className="answer is-compact">
                <span className="answer-label">הנטו שלכם היום</span>
                <span className="answer-figure">
                  {today ? <Money value={today.net_display} /> : <span className="muted">—</span>}
                </span>
              </span>
              {today && <span className="compare-line"><span>השתתפות</span><Money value={today.copay} /></span>}
            </div>
            {ranked.map((i, n) => {
              const r = i.result
              const ok = r.status === 'OK' && r.delta_display != null
              const tone = ok ? toneOf(r.delta_display) : 'flat'
              const best = !stale && ok && n === 0 && ranked.length > 1
              return (
                <div key={i.option_id} className={`compare-col tone-${tone}${best ? ' is-best' : ''}`}>
                  <span className="compare-kicker">
                    <span className="num">{n + 1}</span>
                    {best && <span className="tag tag-brand tag-plain">הנטו הגבוה ביותר</span>}
                  </span>
                  <CarName label={i.label} />
                  <StateBadge state={resultState(r)} />
                  {ok ? (
                    <>
                      <span className="answer is-compact">
                        <span className="answer-label">הנטו החדש</span>
                        <span className="answer-figure"><Money value={r.alternative!.net_display} /></span>
                        <NetChange value={r.delta_display!} />
                      </span>
                      {r.copay_source ? <CopayLine result={r} compact /> : (
                        <span className="compare-line">
                          <span>השתתפות</span>
                          <Money value={r.alternative!.copay} />
                          {r.copay_pending && <span className="tag tag-pending">ממתין לאישור</span>}
                        </span>
                      )}
                      {r.why && <span className="compare-why">{r.why}</span>}
                    </>
                  ) : (
                    <span className="compare-why">{r.reason}</span>
                  )}
                </div>
              )
            })}
          </section>

          {why.length > 0 && (
            <section className="why-box" aria-labelledby="why-title">
              <h3 id="why-title" className="section-title">למה?</h3>
              {why.map((i) => (
                <div key={i.option_id} className="why-item">
                  <strong><CarName label={i.label} /></strong>
                  {i.result.reason && <p>{i.result.reason}</p>}
                  <ul>
                    {driverBars(i.result.drivers).map((d) => (
                      <li key={d.code} className={`driver tone-${d.tone}`} style={{ '--share': d.share } as CSSProperties}>
                        <span className="driver-label">{d.label}</span>
                        <span className="driver-bar" aria-hidden="true"><i /></span>
                        <Money value={d.value} signed className={Number(d.value) < 0 ? 'negative' : 'positive'} />
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </section>
          )}

          {/* the transparent breakdown, one click away */}
          <details className="fold breakdown">
            <summary>
              <Icon name="table" size={20} className="fold-icon" />
              <h3 id="breakdown-title" className="fold-title">
                איך זה חושב?
                <span className="fold-hint">פירוט החישוב שורה אחר שורה. כל הסכומים לחודש</span>
              </h3>
              <Icon name="chevronDown" size={20} className="fold-chevron" />
            </summary>
            <div className="fold-body">
              <div className="table-scroll">
                <table className="compare" role="table">
                  <thead role="rowgroup">
                    <tr role="row">
                      <th scope="col" role="columnheader">פריט</th>
                      <th scope="col" role="columnheader">
                        היום{today && <span className="th-sub">{today.label}</span>}
                      </th>
                      {shown.map((i) => <th key={i.option_id} scope="col" role="columnheader" className="alt-col">{i.label}</th>)}
                    </tr>
                  </thead>
                  <tbody role="rowgroup">
                    {rows.map((r) => (
                      <tr key={r.key} role="row" className={`row-${r.key}${r.strong ? ' row-strong' : ''}`}>
                        <th scope="row" role="rowheader">{r.label}</th>
                        <td role="cell" data-label="היום"><CellView cell={r.today} /></td>
                        {r.alts.map((c, n) => (
                          <td key={shown[n].option_id} role="cell" className="alt-col" data-label={shown[n].label}>
                            <CellView cell={c} />
                            {r.key === 'copay' && shown[n].result.copay_pending && (
                              <span className="tag tag-pending">ממתין לאישור</span>
                            )}
                            {r.key === 'copay' && shown[n].result.copay_source === 'representative' && (
                              <span className="tag tag-up">אושר על ידי הנציג</span>
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {assumptions.length > 0 && (
                <div className="tags assumptions">{assumptions.map((a) => <span key={a} className="tag">{a}</span>)}</div>
              )}
            </div>
          </details>

          {!browse && optionsField && optionsField.choices.length > 0 && (
            <section className="swap-box" aria-labelledby="swap-title">
              <h3 id="swap-title"><Icon name="swap" size={18} />לשנות בחירה</h3>
              <SelectionCard f={optionsField} fieldParam="options" disabled={disabled} onSend={onSend}
                             initial={shown.map((i) => i.option_id)} onDone={onClose} details={details} />
            </section>
          )}
        </div>

        <footer className="panel-actions">
          {movable && (
            <button type="button" className="chip chip-primary" disabled={disabled} onClick={() => void moveToChat()}>
              <Icon name="swap" size={16} />{MOVE_TO_CHAT}
            </button>
          )}
          <button type="button" className="chip chip-quiet" disabled={disabled} onClick={onCorrect}>
            <Icon name="edit" size={16} />תיקון נתונים
          </button>
        </footer>
      </aside>
    </div>,
    document.body,
  )
}
