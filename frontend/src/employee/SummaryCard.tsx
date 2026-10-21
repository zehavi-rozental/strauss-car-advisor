// Several calculated options side by side, ranked by the monthly change (the server's delta_display, highest
// first). Each option: its rank, name, the change with its sign and word, the status, the one-line why, and an
// abstract petal whose size is the change relative to the largest one. Built from ScenarioResult only.
import type { CSSProperties } from 'react'
import type { OptionResult } from '../api/types'
import { Money } from '../format/Amount'
import { Icon } from '../ui/Icon'
import { CarName, StateBadge } from './ResultCard'
import { deltaShares, resultState, toneOf } from './resultView'

export function rankResults(items: OptionResult[]): OptionResult[] {
  const ok = (i: OptionResult) => i.result.status === 'OK' && i.result.delta_display != null
  const delta = (i: OptionResult) => Number(i.result.delta_display)
  return [...items.filter(ok).sort((a, b) => delta(b) - delta(a)), ...items.filter((i) => !ok(i))]
}

export function SummaryCard({ items, onOpenTable }: { items: OptionResult[]; onOpenTable?: () => void }) {
  const ranked = rankResults(items)
  const shares = deltaShares(items)
  return (
    <article className="summary-card" aria-label="סיכום ההשוואה">
      <ol className="summary-rows">
        {ranked.map((i, n) => {
          const r = i.result
          const ok = r.status === 'OK' && r.delta_display != null
          const tone = ok ? toneOf(r.delta_display) : 'flat'
          const best = ok && n === 0 && ranked.length > 1
          return (
            <li key={`${i.option_id}:${i.run_id}`} className={`summary-row tone-${tone}${best ? ' is-best' : ''}`}
                style={{ '--share': shares[i.option_id] ?? 0 } as CSSProperties}>
              <span className="summary-rank num" aria-hidden="true">{n + 1}</span>
              <span className="summary-shape" aria-hidden="true" />
              <span className="summary-car">
                <CarName label={i.label} />
                {best && <span className="tag tag-brand tag-plain">הכי משתלם</span>}
              </span>
              {ok ? (
                <>
                  <span className="summary-figure">
                    <Money value={r.delta_display!} signed className={`summary-delta ${Number(r.delta_display) < 0 ? 'negative' : 'positive'}`} />
                    <span className="summary-unit">בחודש</span>
                  </span>
                  <span className="summary-status"><StateBadge state={resultState(r)} /></span>
                  {r.why && <span className="summary-why">{r.why}</span>}
                </>
              ) : (
                <span className="summary-why">
                  {r.status === 'NOT_ELIGIBLE' ? 'לא זמין לכם' : 'חסרים פרטים לחישוב'}. {r.reason}
                </span>
              )}
            </li>
          )
        })}
      </ol>
      <footer className="result-foot">
        <span className="disclaimer">הערכה, לא תלוש</span>
        <button type="button" className="table-button" disabled={!onOpenTable} onClick={onOpenTable}>
          <Icon name="table" size={18} />פירוט מלא
        </button>
      </footer>
    </article>
  )
}

/** Once results are shown the selection collapses into this line. */
export function SelectedBar({ items, disabled, onChange }:
  { items: OptionResult[]; disabled: boolean; onChange: () => void }) {
  return (
    <div className="selected-bar">
      <span className="selected-text"><Icon name="grid" size={16} />הרכבים שנבחרו: {items.map((i) => i.label).join(', ')}</span>
      <button type="button" className="chip chip-quiet btn-sm" disabled={disabled} onClick={onChange}>
        <Icon name="swap" size={16} />לשנות בחירה
      </button>
    </div>
  )
}
