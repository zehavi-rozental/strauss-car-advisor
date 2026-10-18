// Built from ScenarioResult only. Never from the reply text.
import type { OptionResult } from '../api/types'
import { Money } from '../format/Amount'
import { annualFromMonthly } from '../format/money'
import { driverLabel, missingLabel } from './labels'

/** "הנטו שלך יגדל ב-X ₪" / "יקטן ב-X ₪": future tense, readable without niqqud. */
function Headline({ delta, value }: { delta: number; value: string }) {
  if (delta === 0) return <>הנטו שלך לא ישתנה</>
  return <>הנטו שלך {delta > 0 ? 'יגדל' : 'יקטן'} ב-<Money value={String(Math.abs(Number(value)))} /> בחודש</>
}

export function ResultCard({ item, onOpenTable }: { item: OptionResult; onOpenTable?: () => void }) {
  const r = item.result
  return (
    <article className="result-card" aria-label={`תוצאה: ${item.label}`}>
      {r.status === 'OK' && r.delta_display != null ? (
        <OkBody item={item} />
      ) : r.status === 'INCOMPLETE' ? (
        <>
          <h3 className="result-title">{item.label}: חסרים פרטים לחישוב</h3>
          <p className="result-reason">{r.reason}</p>
          {r.missing_fields.length > 0 && (
            <ul className="missing-list">
              {r.missing_fields.map((f) => <li key={f}>{missingLabel(f)}</li>)}
            </ul>
          )}
        </>
      ) : (
        <>
          <h3 className="result-title">{item.label}: לא בזכאות</h3>
          <p className="result-reason">{r.reason}</p>
        </>
      )}
      <button type="button" className="table-button" disabled={!onOpenTable} onClick={onOpenTable}>פירוט מלא</button>
    </article>
  )
}

function OkBody({ item }: { item: OptionResult }) {
  const r = item.result
  const delta = Number(r.delta_display)
  const tone = delta < 0 ? 'negative' : 'positive'
  const drivers = [...r.drivers].sort(
    (a, b) => Math.abs(Number(b.net_effect_display)) - Math.abs(Number(a.net_effect_display)))
  return (
    <>
      <h3 className="result-title">{item.label}</h3>
      <p className={`delta-headline ${tone}`}><Headline delta={delta} value={r.delta_display!} /></p>
      <p className="annual">
        <Money value={r.delta_display!} signed className={tone} /> בחודש,{' '}
        <Money value={annualFromMonthly(r.delta_display!)} signed /> בשנה
      </p>
      {r.today && r.alternative && (
        <p className="net-row">
          <span>נטו היום <Money value={r.today.net_display} /></span>
          <span aria-hidden="true" className="arrow">←</span>
          <span>נטו חדש <Money value={r.alternative.net_display} className="strong" /></span>
        </p>
      )}
      {drivers.length > 0 && (
        <section className="drivers">
          <h4>מה השתנה</h4>
          <ul>
            {drivers.map((d) => (
              <li key={d.code}>
                <span>{driverLabel(d.code)}</span>
                <Money value={d.net_effect_display} signed
                       className={Number(d.net_effect_display) < 0 ? 'negative' : 'positive'} />
              </li>
            ))}
          </ul>
        </section>
      )}
      {r.copay_pending && (
        <div className="tags"><span className="tag tag-pending">ההשתתפות לפי הקטלוג, ממתינה לאישור נציג</span></div>
      )}
      <p className="disclaimer">הערכה, לא תלוש</p>
    </>
  )
}
