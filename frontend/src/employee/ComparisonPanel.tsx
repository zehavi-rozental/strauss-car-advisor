// Comparison table: side panel on desktop, full screen on a phone. Built from the same results the
// card uses, so it updates with the same sync.
import { useEffect, useState } from 'react'
import type { Expects, MessageIn, OptionResult } from '../api/types'
import { Money } from '../format/Amount'
import { OPT_OUT_ID, buildRows, optionIds } from './comparison'
import type { Cell } from './comparison'
import { driverLabel } from './labels'

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
  const ids = optionIds(expects)
  const canOptOut = ids.includes(OPT_OUT_ID)
  const assumptions = [...new Set(shown.flatMap((i) => i.result.assumptions))]
  const optionsField = expects?.fields.find((f) => f.field === 'options') ?? (expects?.field === 'options' ? expects : null)
  const choices = (optionsField?.choices ?? []).filter((c) => c.id !== OPT_OUT_ID)
  const max = optionsField?.max ?? 3
  const alreadyOptOut = shown.some((i) => i.option_id === OPT_OUT_ID)
  const [picked, setPicked] = useState<string[]>([])
  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < max ? [...p, id] : p))
  const labelOf = (id: string) => optionsField?.choices.find((c) => c.id === id)?.label ?? id
  const recalc = async (selection: string[]) => {
    const ok = await onSend({ choices: selection, field: 'options' }, selection.map(labelOf).join(', '))
    if (ok) onClose()
  }
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
          {choices.length > 0 && (
            <div className="swap-box">
              <div className="reply-hint">להחלפה: בוחרים עד {max} רכבים</div>
              <div className="reply-row">
                {choices.map((c) => (
                  <button key={c.id} type="button" className={picked.includes(c.id) ? 'chip chip-on' : 'chip'}
                          aria-pressed={picked.includes(c.id)} disabled={disabled} onClick={() => toggle(c.id)}>
                    {c.label}
                  </button>
                ))}
              </div>
              <div className="reply-row">
                <button type="button" className="chip chip-primary" disabled={disabled || !picked.length}
                        onClick={() => recalc(picked)}>
                  החלפת רכב וחישוב מחדש
                </button>
                {canOptOut && !alreadyOptOut && (
                  <button type="button" className="chip" disabled={disabled}
                          onClick={() => recalc([...shown.map((i) => i.option_id).slice(0, max - 1), OPT_OUT_ID])}>
                    להוסיף: ויתור על רכב
                  </button>
                )}
              </div>
            </div>
          )}
          <button type="button" className="chip chip-quiet" disabled={disabled} onClick={onCorrect}>תיקון נתונים</button>
        </footer>
      </aside>
    </div>
  )
}
