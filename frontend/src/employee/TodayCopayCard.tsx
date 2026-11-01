// Today's car above the comparison (1/11): the car, the copay amount the calculation uses and where it comes from,
// said once for every card below; the one action that fits (ask the representative, the open inquiry, the answer).
import { Money } from '../format/Amount'
import { Icon } from '../ui/Icon'

export const CATALOG_EXPLAIN = 'ההשוואה מחושבת לפי סכום ההשתתפות שבקטלוג. אם הסכום שלכם שונה, אפשר לברר מול נציג ולעדכן את החישוב.'
export const PROVISIONAL_EXPLAIN = 'ההשוואה מחושבת בינתיים לפי סכום ההשתתפות שבקטלוג. כשהנציג יענה, תוכלו לעדכן את החישוב כאן.'

export interface TodayCopayState {
  car: string | null
  copay: string | null             // the employee's own amount (typed, confirmed, or the rep's answer they applied)
  source: string | null            // rep_confirmed / catalog_confirmed / null (typed)
  provisional: string | null       // the catalog amount used while the representative is asked
  inquiryOpen: boolean             // an open inquiry about today's copay
  inquiryHref?: string | null      // its record in the chat
  answerAmount?: string | null     // the representative answered; the update waits for the employee
  answerHref?: string | null
}

/** Where the amount comes from, in plain words: never an impression that a catalog amount was verified personally. */
export function todayCopaySourceText(s: TodayCopayState): string {
  if (s.copay == null && s.provisional != null) return 'סכום הקטלוג, עד לתשובת הנציג'
  if (s.source === 'rep_confirmed') return 'לפי תשובת הנציג שאישרתם'
  if (s.source === 'catalog_confirmed') return 'סכום הקטלוג, שאישרתם שהוא הסכום שלכם'
  return 'הסכום שמסרתם'
}

export function TodayCopayCard({ state, disabled = false, onAsk }: {
  state: TodayCopayState
  disabled?: boolean
  onAsk?: (car?: string, catalogAmount?: string) => void
}) {
  const s = state
  const amount = s.copay ?? s.provisional
  if (amount == null) return null
  const provisional = s.copay == null && s.provisional != null
  const catalogBased = provisional || s.source === 'catalog_confirmed'
  const explain = s.answerAmount ? null : provisional || s.inquiryOpen ? PROVISIONAL_EXPLAIN
    : s.source === 'catalog_confirmed' ? CATALOG_EXPLAIN : null
  return (
    <section className={`today-copay-card${catalogBased && !s.answerAmount ? ' is-provisional' : ''}`}
             aria-label="ההשתתפות ברכב הנוכחי">
      <p className="today-copay-head">
        <span className="today-copay-title">הרכב שלכם היום{s.car ? <>: <bdi>{s.car}</bdi></> : null}</span>
        {provisional && !s.answerAmount && <span className="tag tag-pending">זמני</span>}
        {s.source === 'rep_confirmed' && <span className="tag tag-up">אושר על ידי הנציג</span>}
      </p>
      <p className="today-copay-amount">
        השתתפות חודשית בחישוב: <Money value={amount} /> <span className="today-copay-source">· {todayCopaySourceText(s)}</span>
      </p>
      {explain && <p className="today-copay-explain">{explain}</p>}
      <p className="today-copay-new">ברכבים החדשים: סכום ההשתתפות של כל דגם לפי הקטלוג, בכרטיס של הרכב.</p>
      {s.answerAmount ? (
        <p className="today-copay-status">
          <Icon name="rep" size={16} />התקבלה תשובת הנציג: <Money value={s.answerAmount} />. העדכון ממתין לאישור שלכם.
          {s.answerHref && <> <a className="link-inline" href={s.answerHref}>לתשובת הנציג</a></>}
        </p>
      ) : s.inquiryOpen ? (
        <p className="today-copay-status">
          <Icon name="clock" size={16} />נשלחה פנייה לנציג התחבורה. ההשוואה ממשיכה בינתיים.
          {s.inquiryHref && <> <a className="link-inline" href={s.inquiryHref}>לפנייה</a></>}
        </p>
      ) : catalogBased && onAsk ? (
        <button type="button" className="btn btn-sm btn-ghost today-copay-ask" disabled={disabled}
                onClick={() => onAsk(s.car ?? undefined, amount)}>
          <Icon name="rep" size={16} />בירור השתתפות מול נציג
        </button>
      ) : null}
    </section>
  )
}
