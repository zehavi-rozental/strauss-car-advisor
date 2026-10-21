// The comparison and the transparent breakdown, as one screen over the chat (full screen on a phone).
// Top: today and each option side by side with the big numbers. Then the breakdown table (the engine's lines),
// the assumptions, "למה?", and the actions: another selection or "תיקון נתונים". Built from the same results the
// cards use, so it updates with the same sync. No amount is calculated here.
import { useEffect, useRef } from 'react'
import type { CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import type { CatalogItem, Expects, MessageIn, OptionResult } from '../api/types'
import { Money } from '../format/Amount'
import { Icon } from '../ui/Icon'
import type { Cell } from './comparison'
import { CarName, StateBadge } from './ResultCard'
import { SelectionCard } from './SelectionCard'
import { rankResults } from './SummaryCard'
import { breakdownRows, deltaShares, driverBars, resultState, toneOf } from './resultView'

interface Props {
  results: OptionResult[]
  expects: Expects | null
  disabled: boolean
  onClose: () => void
  onSend: (body: MessageIn, display: string) => Promise<boolean>
  onCorrect: () => void
  details?: Record<string, CatalogItem>
}

function CellView({ cell }: { cell: Cell }) {
  if (cell.kind === 'money') return <Money value={cell.value} signed={cell.signed} />
  if (cell.kind === 'text') return <>{cell.text}</>
  return <span className="muted" aria-label="אין">—</span>
}

export function ComparisonPanel({ results, expects, disabled, onClose, onSend, onCorrect, details }: Props) {
  const shown = results.slice(0, 3)
  const rows = breakdownRows(shown)
  const ranked = rankResults(shown)
  const shares = deltaShares(shown)
  const today = shown.find((i) => i.result.today)?.result.today ?? null
  // the pending-copay assumption is already the "ממתין לאישור" tag in the table
  const assumptions = [...new Set(shown.flatMap((i) => i.result.assumptions))].filter((x) => !x.includes('ממתינה לאישור נציג'))
  const optionsField = expects?.fields.find((f) => f.field === 'options') ?? (expects?.field === 'options' ? expects : null)
  const why = shown.filter((i) => i.result.status === 'OK')
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    document.body.classList.add('has-sheet')
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.classList.remove('has-sheet')
      before?.focus?.()
    }
  }, [onClose])

  // rendered at the end of <body>: above the top bar, outside the chat's own stacking context
  return createPortal(
    <div className="panel-backdrop" onClick={onClose}>
      <aside className="comparison-panel" role="dialog" aria-modal="true" aria-label="טבלת השוואה"
             onClick={(e) => e.stopPropagation()}>
        <header className="panel-header">
          <div>
            <p className="panel-kicker">פירוט מלא</p>
            <h2>טבלת השוואה</h2>
          </div>
          <button ref={closeRef} type="button" className="panel-close" onClick={onClose} aria-label="סגירה">
            <Icon name="close" size={22} />
          </button>
        </header>

        <div className="panel-body">
          {/* side by side: today and each option, ranked */}
          <section className="compare-stage" aria-label="השוואה" style={{ '--cols': ranked.length + 1 } as CSSProperties}>
            <div className="compare-col is-today">
              <span className="compare-kicker">היום</span>
              {today ? <CarName label={today.label} /> : <span className="car-title">—</span>}
              <span className="compare-figure-label">נטו היום</span>
              {today ? <Money value={today.net_display} className="compare-net" /> : <span className="muted">—</span>}
              {today && <span className="compare-line"><span>השתתפות</span><Money value={today.copay} /></span>}
            </div>
            {ranked.map((i, n) => {
              const r = i.result
              const ok = r.status === 'OK' && r.delta_display != null
              const tone = ok ? toneOf(r.delta_display) : 'flat'
              const best = ok && n === 0 && ranked.length > 1
              return (
                <div key={i.option_id} className={`compare-col tone-${tone}${best ? ' is-best' : ''}`}
                     style={{ '--share': shares[i.option_id] ?? 0 } as CSSProperties}>
                  <span className="compare-kicker">
                    <span className="num">{n + 1}</span>
                    {best && <span className="tag tag-brand tag-plain">הכי משתלם</span>}
                  </span>
                  <CarName label={i.label} />
                  <StateBadge state={resultState(r)} />
                  {ok ? (
                    <>
                      <span className="compare-figure-label">שינוי בנטו החודשי</span>
                      <span className="compare-delta">
                        <span className="compare-shape" aria-hidden="true" />
                        <Money value={r.delta_display!} signed className={Number(r.delta_display) < 0 ? 'negative' : 'positive'} />
                      </span>
                      <span className="compare-line"><span>נטו חדש</span><Money value={r.alternative!.net_display} /></span>
                      <span className="compare-line">
                        <span>השתתפות</span>
                        <Money value={r.alternative!.copay} />
                        {r.copay_pending && <span className="tag tag-pending">ממתין לאישור</span>}
                      </span>
                      {r.why && <span className="compare-why">{r.why}</span>}
                    </>
                  ) : (
                    <span className="compare-why">{r.reason}</span>
                  )}
                </div>
              )
            })}
          </section>

          {/* the transparent breakdown */}
          <section className="breakdown" aria-labelledby="breakdown-title">
            <h3 id="breakdown-title">פירוט החישוב</h3>
            <div className="table-scroll">
              <table className="compare" role="table">
                <thead role="rowgroup">
                  <tr role="row">
                    <th scope="col" role="columnheader">פריט</th>
                    <th scope="col" role="columnheader">היום</th>
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
          </section>

          {why.length > 0 && (
            <section className="why-box" aria-labelledby="why-title">
              <h3 id="why-title">למה?</h3>
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

          {optionsField && optionsField.choices.length > 0 && (
            <section className="swap-box" aria-labelledby="swap-title">
              <h3 id="swap-title"><Icon name="swap" size={18} />לשנות בחירה</h3>
              <SelectionCard f={optionsField} fieldParam="options" disabled={disabled} onSend={onSend}
                             initial={shown.map((i) => i.option_id)} onDone={onClose} details={details} />
            </section>
          )}
        </div>

        <footer className="panel-actions">
          <button type="button" className="chip chip-quiet" disabled={disabled} onClick={onCorrect}>
            <Icon name="edit" size={16} />תיקון נתונים
          </button>
        </footer>
      </aside>
    </div>,
    document.body,
  )
}
