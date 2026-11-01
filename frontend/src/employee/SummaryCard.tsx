// Several calculated options, ranked by the monthly change (the server's delta_display, highest first). The card
// opens with today's net; each option then answers the one question: the new monthly net in large type, and the
// change against today in words and colour. Then its name, status and the one-line why. Built from
// ScenarioResult only (the server's *_display values).
import type { OptionResult } from '../api/types'
import { Money } from '../format/Amount'
import { Icon } from '../ui/Icon'
import { CarName, CopayLine, NetChange, StateBadge } from './ResultCard'
import { resultState, toneOf } from './resultView'

export function rankResults(items: OptionResult[]): OptionResult[] {
  const ok = (i: OptionResult) => i.result.status === 'OK' && i.result.delta_display != null
  const delta = (i: OptionResult) => Number(i.result.delta_display)
  return [...items.filter(ok).sort((a, b) => delta(b) - delta(a)), ...items.filter((i) => !ok(i))]
}

export function SummaryCard({ items, onOpenTable, current = true, onAskCopay, canAskCopay = () => true }:
  { items: OptionResult[]; onOpenTable?: () => void; current?: boolean;
    onAskCopay?: (item: OptionResult) => void
    /** false for a car whose copay inquiry is already open: the row keeps its status tag, no second "ask" */
    canAskCopay?: (item: OptionResult) => boolean }) {
  // an out-of-date result (current=false) is never ranked and has no leader: shown in the order chosen, as history
  const ranked = current ? rankResults(items) : items
  const today = ranked.find((i) => i.result.today)?.result.today ?? null
  return (
    <article className="summary-card" aria-label="סיכום ההשוואה">
      <header className="summary-head">
        <h3 className="section-title">הנטו החודשי שלכם עם כל רכב</h3>
        {today && (
          <p className="summary-today">
            <span>הנטו שלכם היום</span>
            <Money value={today.net_display} />
          </p>
        )}
      </header>
      <ol className="summary-rows">
        {ranked.map((i, n) => {
          const r = i.result
          const ok = r.status === 'OK' && r.delta_display != null
          const tone = ok ? toneOf(r.delta_display) : 'flat'
          const best = current && ok && n === 0 && ranked.length > 1
          return (
            <li key={`${i.option_id}:${i.run_id}`} className={`summary-row tone-${tone}${best ? ' is-best' : ''}`}>
              <span className="summary-rank num" aria-hidden="true">{n + 1}</span>
              <span className="summary-car">
                <CarName label={i.label} />
                {best && <span className="tag tag-brand tag-plain">הנטו הגבוה ביותר</span>}
              </span>
              {ok ? (
                <>
                  <span className="summary-figure answer is-compact">
                    {r.alternative && (
                      <>
                        <span className="answer-label">הנטו החדש</span>
                        <span className="answer-figure"><Money value={r.alternative.net_display} /></span>
                      </>
                    )}
                    <NetChange value={r.delta_display!} suffix={r.alternative ? 'לעומת היום' : 'בחודש'} />
                  </span>
                  <span className="summary-status"><StateBadge state={resultState(r)} /></span>
                  {r.why && <span className="summary-why">{r.why}</span>}
                  {/* the new car's copay stays on every current row; its inquiry link only where a page passes one
                      (the conversation does not since 1/11: one place, today's car card above the comparison) */}
                  {current && (
                    <span className="summary-copay">
                      <CopayLine result={r} compact onAskCopay={onAskCopay && canAskCopay(i) ? () => onAskCopay(i) : undefined} />
                    </span>
                  )}
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
