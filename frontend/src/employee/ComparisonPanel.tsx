// Comparison table: side panel on desktop, full screen on a phone. Built from the same results the
// card uses, so it updates with the same sync.
import { useEffect } from 'react'
import type { Expects, MessageIn, OptionResult } from '../api/types'
import { Money } from '../format/Amount'
import { buildRows } from './comparison'
import type { Cell } from './comparison'
import { driverLabel } from './labels'
import { SelectionCard } from './SelectionCard'

interface Props {
  results: OptionResult[]
  expects: Expects | null
  disabled: boolean
  onClose: () => void
  onSend: (body: MessageIn, display: string) => Promise<boolean>
  onCorrect: () => void
}

function CellView({ cell }: { cell: Cell }) {
  if (cell.kind === 'money') return <Money value={cell.value} signed={cell.signed} />
  if (cell.kind === 'text') return <>{cell.text}</>
  return <span className="muted">—</span>
}

export function ComparisonPanel({ results, expects, disabled, onClose, onSend, onCorrect }: Props) {
  const shown = results.slice(0, 3)
  const rows = buildRows(shown)
  // the pending-copay assumption is already the "ממתין לאישור" tag in the table
  const assumptions = [...new Set(shown.flatMap((i) => i.result.assumptions))].filter((x) => !x.includes('ממתינה לאישור נציג'))
  const optionsField = expects?.fields.find((f) => f.field === 'options') ?? (expects?.field === 'options' ? expects : null)
  const why = shown.filter((i) => i.result.status === 'OK')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="panel-backdrop" onClick={onClose}>
      <aside className="comparison-panel" role="dialog" aria-modal="true" aria-label="טבלת השוואה"
             onClick={(e) => e.stopPropagation()}>
        <header className="panel-header">
          <h2>טבלת השוואה</h2>
          <button type="button" className="panel-close" onClick={onClose} aria-label="סגירה">×</button>
        </header>

        <div className="panel-body">
          <div className="table-scroll">
            <table className="compare">
              <thead>
                <tr>
                  <th scope="col">פריט</th>
                  <th scope="col">היום</th>
                  {shown.map((i) => <th key={i.option_id} scope="col" className="alt-col">{i.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className={r.strong ? 'row-strong' : undefined}>
                    <th scope="row">{r.label}</th>
                    <td><CellView cell={r.today} /></td>
                    {r.alts.map((c, n) => (
                      <td key={shown[n].option_id} className="alt-col">
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
            <div className="tags">{assumptions.map((a) => <span key={a} className="tag">{a}</span>)}</div>
          )}

          {why.length > 0 && (
            <section className="why-box">
              <h3>למה?</h3>
              {why.map((i) => (
                <div key={i.option_id}>
                  <strong>{i.label}</strong>
                  {i.result.reason && <p>{i.result.reason}</p>}
                  <ul>
                    {i.result.drivers.map((d) => (
                      <li key={d.code}>
                        <span>{driverLabel(d.code)}</span>
                        <Money value={d.net_effect_display} signed
                               className={Number(d.net_effect_display) < 0 ? 'negative' : 'positive'} />
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </section>
          )}
        </div>

        <footer className="panel-actions">
          {optionsField && optionsField.choices.length > 0 && (
            <div className="swap-box">
              <h4>לשנות בחירה</h4>
              <SelectionCard f={optionsField} fieldParam="options" disabled={disabled} onSend={onSend}
                             initial={shown.map((i) => i.option_id)} onDone={onClose} />
            </div>
          )}
          <button type="button" className="chip chip-quiet" disabled={disabled} onClick={onCorrect}>תיקון נתונים</button>
        </footer>
      </aside>
    </div>
  )
}
