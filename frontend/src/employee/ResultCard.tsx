// The decision screen for one option, in three layers: (1) the monthly change in the net and whether it is
// final or a temporary estimate, (2) today against the new net and what moved it, (3) "פירוט מלא" opens the
// transparent breakdown. Built from ScenarioResult only. Never from the reply text.
import type { CSSProperties } from 'react'
import type { OptionResult } from '../api/types'
import { Money } from '../format/Amount'
import { annualFromMonthly } from '../format/money'
import { Icon } from '../ui/Icon'
import { missingLabel, splitLabel } from './labels'
import { STATE_LABEL, STATE_NOTE, driverBars, resultState, toneOf } from './resultView'

/** "הנטו שלכם יגדל ב-X ₪" / "יקטן ב-X ₪": future tense, readable without niqqud. */
function Headline({ delta, value }: { delta: number; value: string }) {
  if (delta === 0) return <>הנטו שלכם לא ישתנה</>
  return <>הנטו שלכם {delta > 0 ? 'יגדל' : 'יקטן'} ב-<Money value={String(Math.abs(Number(value)))} /> בחודש</>
}

/** A short burst of the Strauss colours when the net grows (decorative; skipped with reduced motion). */
export function Confetti() {
  return (
    <span className="confetti" aria-hidden="true">
      {Array.from({ length: 14 }, (_, i) => <i key={i} style={{ '--i': i } as CSSProperties} />)}
    </span>
  )
}

export function CarName({ label }: { label: string }) {
  const { name, year } = splitLabel(label)
  return (
    <span className="car-title">
      <span className="car-title-name latin">{name}</span>
      {year && <span className="car-title-year num">{year}</span>}
    </span>
  )
}

export function StateBadge({ state }: { state: ReturnType<typeof resultState> }) {
  return (
    <span className={`state state-${state}`}>
      <Icon name={state === 'pending' ? 'clock' : state === 'complete' ? 'check' : 'info'} size={14} />
      {STATE_LABEL[state]}
    </span>
  )
}

export function ResultCard({ item, onOpenTable }: { item: OptionResult; onOpenTable?: () => void }) {
  const r = item.result
  const state = resultState(r)
  return (
    <article className={`result-card is-${state}`} aria-label={`תוצאה: ${item.label}`}>
      {r.status === 'OK' && r.delta_display != null ? (
        <OkBody item={item} />
      ) : (
        <div className="result-empty">
          <header className="result-head">
            <h3 className="result-title"><CarName label={item.label} /></h3>
            <StateBadge state={state} />
          </header>
          <p className="result-reason">
            {r.status === 'INCOMPLETE' ? `${item.label}: חסרים פרטים לחישוב` : `${item.label}: לא בזכאות`}
          </p>
          {r.reason && <p className="result-reason muted">{r.reason}</p>}
          {r.status === 'INCOMPLETE' && r.missing_fields.length > 0 && (
            <ul className="missing-list">
              {r.missing_fields.map((f) => <li key={f}>{missingLabel(f)}</li>)}
            </ul>
          )}
        </div>
      )}
      <footer className="result-foot">
        <span className="disclaimer">הערכה, לא תלוש</span>
        <button type="button" className="table-button" disabled={!onOpenTable} onClick={onOpenTable}>
          <Icon name="table" size={18} />פירוט מלא
        </button>
      </footer>
    </article>
  )
}

function OkBody({ item }: { item: OptionResult }) {
  const r = item.result
  const delta = Number(r.delta_display)
  const tone = toneOf(r.delta_display)
  const state = resultState(r)
  const bars = driverBars(r.drivers)
  return (
    <>
      <header className="result-head">
        <h3 className="result-title"><CarName label={item.label} /></h3>
        <StateBadge state={state} />
      </header>

      {/* layer 1: the change in the net */}
      <div className={`delta-hero tone-${tone}`}>
        <span className="delta-figure" aria-hidden="true">
          <Money value={r.delta_display!} signed />
          {tone === 'up' && <Confetti />}
        </span>
        <p className={`delta-headline ${delta < 0 ? 'negative' : 'positive'}`}><Headline delta={delta} value={r.delta_display!} /></p>
        <p className="annual">
          <Money value={r.delta_display!} signed className={delta < 0 ? 'negative' : 'positive'} /> בחודש,{' '}
          <Money value={annualFromMonthly(r.delta_display!)} signed /> בשנה
        </p>
        {state === 'pending' && (
          <p className="pending-note">
            <span className="tag tag-pending">ההשתתפות לפי הקטלוג, ממתינה לאישור נציג</span>
          </p>
        )}
      </div>

      {/* layer 2: today against the new net, and what moved it */}
      {r.today && r.alternative && (
        <div className="net-row">
          <div className="net-cell">
            <span className="net-label">נטו היום</span>
            <Money value={r.today.net_display} className="net-value" />
          </div>
          <span aria-hidden="true" className="net-arrow"><Icon name="arrowLeft" size={20} /></span>
          <div className="net-cell is-new">
            <span className="net-label">נטו חדש</span>
            <Money value={r.alternative.net_display} className="net-value strong" />
          </div>
        </div>
      )}
      {bars.length > 0 && (
        <section className="drivers" aria-label="מה השתנה">
          <h4>מה השתנה</h4>
          <ul>
            {bars.map((d) => (
              <li key={d.code} className={`driver tone-${d.tone}`} style={{ '--share': d.share } as CSSProperties}>
                <span className="driver-label">{d.label}</span>
                <span className="driver-bar" aria-hidden="true"><i /></span>
                <Money value={d.value} signed className={Number(d.value) < 0 ? 'negative' : 'positive'} />
              </li>
            ))}
          </ul>
          {r.why && <p className="why-line"><Icon name="info" size={16} />{r.why}</p>}
        </section>
      )}
      {state !== 'pending' && <p className="state-note">{STATE_NOTE[state]}</p>}
    </>
  )
}
