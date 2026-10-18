// One compact card ranks the calculated options. Built from ScenarioResult only (delta_display, why).
import type { OptionResult } from '../api/types'
import { Money } from '../format/Amount'

export function rankResults(items: OptionResult[]): OptionResult[] {
  const ok = (i: OptionResult) => i.result.status === 'OK' && i.result.delta_display != null
  const delta = (i: OptionResult) => Number(i.result.delta_display)
  return [...items.filter(ok).sort((a, b) => delta(b) - delta(a)), ...items.filter((i) => !ok(i))]
}

export function SummaryCard({ items, onOpenTable }: { items: OptionResult[]; onOpenTable?: () => void }) {
  return (
    <article className="summary-card" aria-label="סיכום ההשוואה">
      <ol className="summary-rows">
        {rankResults(items).map((i) => {
          const r = i.result
          const ok = r.status === 'OK' && r.delta_display != null
          const tone = ok && Number(r.delta_display) < 0 ? 'negative' : 'positive'
          return (
            <li key={`${i.option_id}:${i.run_id}`} className="summary-row">
              <span className="summary-car">{i.label}</span>
              {ok ? (
                <>
                  <Money value={r.delta_display!} signed className={`summary-delta ${tone}`} />
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
      <button type="button" className="table-button" disabled={!onOpenTable} onClick={onOpenTable}>פירוט מלא</button>
    </article>
  )
}

/** Once results are shown the selection collapses into this line. */
export function SelectedBar({ items, disabled, onChange }:
  { items: OptionResult[]; disabled: boolean; onChange: () => void }) {
  return (
    <div className="selected-bar">
      <span>הרכבים שנבחרו: {items.map((i) => i.label).join(', ')}</span>
      <button type="button" className="chip chip-quiet" disabled={disabled} onClick={onChange}>לשנות בחירה</button>
    </div>
  )
}
