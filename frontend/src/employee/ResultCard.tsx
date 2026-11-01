// The decision screen for one option, in three layers: (1) the answer: the new monthly net, how much it
// changes in words, and whether it is final or a temporary estimate, (2) what moved it, (3) "פירוט מלא" opens
// the transparent breakdown. Built from ScenarioResult only (the server's *_display values). Never from the
// reply text.
import type { CSSProperties } from 'react'
import type { OptionResult } from '../api/types'
import { Money } from '../format/Amount'
import { annualFromMonthly } from '../format/money'
import { Icon } from '../ui/Icon'
import { missingLabel, splitLabel } from './labels'
import { PENDING_COPAY_LABEL, STATE_LABEL, STATE_NOTE, copayInfo, driverBars, resultState, toneOf } from './resultView'
import type { ScenarioResult } from '../api/types'

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

/** The change against today, as one coloured line: "+1,013 ₪ לעומת היום". */
export function NetChange({ value, suffix = 'לעומת היום' }: { value: string; suffix?: string }) {
  const tone = toneOf(value)
  return (
    <span className={`answer-change is-${tone}`}>
      <Icon name={tone === 'up' ? 'trendUp' : tone === 'down' ? 'trendDown' : 'minus'} size={18} />
      <Money value={value} signed className={tone === 'down' ? 'negative' : tone === 'up' ? 'positive' : undefined} />
      {suffix}
    </span>
  )
}

export function StateBadge({ state }: { state: ReturnType<typeof resultState> }) {
  return (
    <span className={`state state-${state}`}>
      <Icon name={state === 'pending' ? 'clock' : state === 'complete' || state === 'approved' ? 'check' : 'info'} size={14} />
      {STATE_LABEL[state]}
    </span>
  )
}

/** The new car's copay for one option: amount, source, approval status, its effect on the net, and the date the
 *  representative's amount applies from. "compact" is the one-line form for the comparison panel. */
export function CopayLine({ result, compact = false, onAskCopay }: {
  result: ScenarioResult
  compact?: boolean
  onAskCopay?: () => void
}) {
  const c = copayInfo(result)
  // today's copay (provisional or not) is said once, in the card above the comparison (1/11), never per car
  if (!c) return null
  const tag = <span className={`tag tag-${c.tone === 'approved' ? 'up' : c.tone === 'final' ? 'plain' : 'pending'}`}>{c.status}</span>
  if (compact) {
    return (
      <span className="compare-line copay-line">
        <span>השתתפות ברכב החדש</span>
        {c.amount != null ? <Money value={c.amount} /> : <span>חסר סכום</span>}
        {tag}
        {c.effective && <span className="copay-date">{c.effective}</span>}
        {onAskCopay && (c.tone === 'pending' || c.tone === 'final') && (
          <button type="button" className="link-inline" onClick={onAskCopay}>בירור אישי מול הנציג</button>
        )}
      </span>
    )
  }
  return (
    <div className={`copay-box is-${c.tone}`} aria-label="ההשתתפות ברכב החדש">
      <p className="copay-head">
        <span className="copay-title">ההשתתפות ברכב החדש</span>
        {c.amount != null && <span className="copay-amount"><Money value={c.amount} /> בחודש</span>}
        {tag}
      </p>
      <p className="copay-meta">
        <span>{c.source}</span>
        {c.effective && <span>{c.effective}</span>}
        {c.effect != null && <span>השפעה על הנטו לעומת היום: <Money value={c.effect} signed /></span>}
      </p>
      {c.stated != null && (
        <p className="copay-meta">
          ציינתם שתשלמו <Money value={c.stated} />.{c.tone === 'pending' && ' שלחתי את זה לנציג התחבורה יחד עם הסכום שבקטלוג.'}
        </p>
      )}
      {c.tone === 'final' && (
        <p className="copay-meta">זה הסכום בקטלוג לרכב הזה. אם לרכב החדש סוכם אתכם סכום אחר, אפשר לעדכן אותי ואפנה לנציג התחבורה לבירור.</p>
      )}
      {c.tone === 'pending' && (
        <p className="copay-meta">הנטו כאן הוא הערכה לפי הסכום שבקטלוג. אם הנציג יקבע סכום אחר, הנטו ישתנה בהתאם.</p>
      )}
      {onAskCopay && (c.tone === 'pending' || c.tone === 'final') && (
        <button type="button" className="link-inline" onClick={onAskCopay}>לבקש בירור אישי מול הנציג</button>
      )}
    </div>
  )
}

export function ResultCard({ item, onOpenTable, onAskCopay }: {
  item: OptionResult
  onOpenTable?: () => void
  onAskCopay?: (item: OptionResult) => void
}) {
  const r = item.result
  const state = resultState(r)
  return (
    <article className={`result-card is-${state}`} aria-label={`תוצאה: ${item.label}`}>
      {r.status === 'OK' && r.delta_display != null ? (
        <OkBody item={item} onAskCopay={onAskCopay} />
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
          {r.copay_source === 'missing' &&           <CopayLine result={r} onAskCopay={onAskCopay ? () => onAskCopay(item) : undefined} />}
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

function OkBody({ item, onAskCopay }: { item: OptionResult; onAskCopay?: (item: OptionResult) => void }) {
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

      {/* layer 1: the answer. The new net first; without it (no alternative net) the change itself */}
      <div className={`card answer-card is-${tone}`}>
        <div className="answer">
          <span className="answer-label">{r.alternative ? 'הנטו החדש שלכם' : 'השינוי בנטו החודשי'}</span>
          <span className="answer-figure">
            {r.alternative ? <Money value={r.alternative.net_display} /> : <Money value={r.delta_display!} signed />}
            <span className="answer-unit">בחודש</span>
            {tone === 'up' && <Confetti />}
          </span>
          <p className={`answer-change is-${tone}`}>
            <Icon name={tone === 'up' ? 'trendUp' : tone === 'down' ? 'trendDown' : 'minus'} size={20} />
            <span><Headline delta={delta} value={r.delta_display!} /></span>
          </p>
          <p className="answer-meta">
            {r.today && <span>היום: <Money value={r.today.net_display} /></span>}
            <span>שינוי שנתי: <Money value={annualFromMonthly(r.delta_display!)} signed /></span>
          </p>
        </div>
        {state === 'pending' && (
          <p className="pending-note">
            <span className="tag tag-pending">{PENDING_COPAY_LABEL}</span>
          </p>
        )}
      </div>
      <CopayLine result={r} onAskCopay={onAskCopay ? () => onAskCopay(item) : undefined} />

      {/* layer 2: what moved it */}
      {bars.length > 0 && (
        <section className="drivers" aria-label="מה השתנה">
          <h4 className="section-title">מה השתנה</h4>
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
