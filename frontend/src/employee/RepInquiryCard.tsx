// A question to the transport representative, opened inside the conversation (not a floating button, not a side
// panel — the AI advisor stays the one interface). The employee edits the pre-filled question, confirms, and only
// then is the question saved through the existing cases mechanism (POST /rep-question). The card never claims the
// question was sent before the server confirms it, and tells the employee honestly when an open question already
// exists (status "exists" returns the saved wording, which may differ from what was just typed).
import { useState } from 'react'
import { ApiError, api } from '../api/client'
import type { RepQuestionIntent, RepQuestionResult } from '../api/types'
import { Icon } from '../ui/Icon'

type Step = 'edit' | 'summary' | 'done'

interface Props {
  conversationId: string
  /** the question text the advisor proposed (editable); empty for a blank start */
  initialQuestion: string
  intent: RepQuestionIntent
  /** the car this inquiry is about, when known (shown as context; the server re-validates it) */
  optionId?: string
  carLabel?: string
  catalogAmount?: string
  /** a verified email stored earlier in this conversation; shown pre-filled, editable */
  knownEmail?: string
  disabled?: boolean
  onCancel: () => void
  /** called after a question is saved (status created/exists), so the parent can refresh results/status */
  onSaved: (result: RepQuestionResult) => void
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function RepInquiryCard({
  conversationId, initialQuestion, intent, optionId, carLabel, catalogAmount, knownEmail,
  disabled = false, onCancel, onSaved,
}: Props) {
  const [step, setStep] = useState<Step>('edit')
  const [question, setQuestion] = useState(initialQuestion)
  const [email, setEmail] = useState(knownEmail ?? '')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<RepQuestionResult | null>(null)

  const needsEmail = !knownEmail
  const trimmed = question.trim()
  const emailTyped = email.trim().length > 0
  const emailOk = emailTyped && EMAIL_RE.test(email.trim())

  const toSummary = () => {
    if (!trimmed) { setError('כתבו את השאלה לנציג.'); return }
    if (needsEmail && !emailOk) { setError('כתובת אימייל תקינה נדרשת כדי לשלוח קישור אימות.'); return }
    setError(null)
    setStep('summary')
  }

  const send = async () => {
    if (sending) return                       // guard against a double click
    setSending(true)
    setError(null)
    try {
      const body = { question: trimmed, intent, email: knownEmail ?? email.trim(),
                     ...(optionId ? { option_id: optionId } : {}) }
      const res = await api.repQuestion(conversationId, body)
      setResult(res)
      setStep('done')
      onSaved(res)
    } catch (e) {
      // keep everything the employee typed; let them try again
      setError(e instanceof ApiError ? e.message_he : 'משהו השתבש. נסו שוב בעוד רגע.')
      setStep('edit')
    } finally {
      setSending(false)
    }
  }

  if (step === 'done' && result) {
    return <RepInquiryDone result={result} carLabel={carLabel} />
  }

  return (
    <section className="rep-inquiry-card" aria-label="העברת שאלה לנציג הרכב">
      <header className="rep-inquiry-head">
        <span className="rep-inquiry-icon" aria-hidden="true"><Icon name="rep" size={18} /></span>
        <h3>העברת שאלה לנציג הרכב</h3>
      </header>

      {step === 'edit' && (
        <div className="rep-inquiry-body">
          {carLabel && (
            <p className="rep-inquiry-context">
              <span>הרכב: <bdi>{carLabel}</bdi></span>
              {catalogAmount && <span> · השתתפות לפי הקטלוג: {catalogAmount} ₪</span>}
            </p>
          )}
          <label className="rep-inquiry-label" htmlFor="rep-q">מה תרצו לשאול?</label>
          <textarea id="rep-q" className="rep-inquiry-text" rows={3} value={question} disabled={disabled || sending}
                    onChange={(e) => setQuestion(e.target.value)} maxLength={500}
                    placeholder="כתבו כאן את השאלה לנציג הרכב" />
          {needsEmail && (
            <>
              <label className="rep-inquiry-label" htmlFor="rep-email">כתובת אימייל לעדכונים</label>
              <input id="rep-email" className="rep-inquiry-email" type="email" dir="ltr" value={email}
                     disabled={disabled || sending} onChange={(e) => setEmail(e.target.value)}
                     placeholder="name@example.com" required autoComplete="email" />
              <p className="rep-inquiry-hint">
                נשלח קישור חד־פעמי לאימות הכתובת. לאחר האימות נעדכן אתכם כשהנציג יענה.
              </p>
            </>
          )}
          {error && <p className="rep-inquiry-error" role="alert"><Icon name="alert" size={16} />{error}</p>}
          <div className="rep-inquiry-actions">
            <button type="button" className="btn btn-quiet" disabled={sending} onClick={onCancel}>ביטול</button>
            <button type="button" className="btn btn-primary" disabled={sending || !trimmed || (needsEmail && !emailOk)}
                    onClick={toSummary}>
              המשך
            </button>
          </div>
        </div>
      )}

      {step === 'summary' && (
        <div className="rep-inquiry-body">
          <p className="rep-inquiry-summary-title">סיכום לפני שליחה</p>
          <dl className="rep-inquiry-summary">
            {carLabel && <div><dt>הרכב</dt><dd><bdi>{carLabel}</bdi></dd></div>}
            {catalogAmount && <div><dt>השתתפות לפי הקטלוג</dt><dd>{catalogAmount} ₪</dd></div>}
            <div><dt>השאלה לנציג</dt><dd><bdi>{trimmed}</bdi></dd></div>
            {needsEmail && <div><dt>אימייל לעדכון</dt><dd dir="ltr">{email.trim()}</dd></div>}
          </dl>
          {intent === 'copay_verification' && (
            <p className="rep-inquiry-note">
              עד שהנציג יאמת סכום אישי, החישוב ממשיך להתבסס על סכום ברירת המחדל בקטלוג.
            </p>
          )}
          {error && <p className="rep-inquiry-error" role="alert"><Icon name="alert" size={16} />{error}</p>}
          <div className="rep-inquiry-actions">
            <button type="button" className="btn btn-quiet" disabled={sending}
                    onClick={() => { setError(null); setStep('edit') }}>חזרה לעריכה</button>
            <button type="button" className="btn btn-primary" disabled={sending} onClick={send} aria-busy={sending}>
              {sending ? 'שולח…' : 'שליחת הפנייה לנציג'}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

/** The confirmation shown after the server saved (or found) the question. Honest about created vs exists. */
function RepInquiryDone({ result, carLabel }: { result: RepQuestionResult; carLabel?: string }) {
  const car = result.car_label ?? carLabel
  const emailNotice = result.email_verified
    ? 'כתובת האימייל אומתה. ננסה לעדכן אתכם כשהנציג יענה; התשובה תופיע גם כאן בשיחה.'
    : result.email_status === 'sent'
      ? 'העברנו לספק הדוא״ל בקשה לשלוח קישור לאימות הכתובת. אחרי האימות נוכל לעדכן אתכם כשהנציג יענה.'
      : result.email_status === 'failed'
        ? 'הפנייה נשמרה. שליחת האימייל נכשלה וננסה שוב; התשובה תופיע גם בשיחה.'
        : 'הפנייה נשמרה. קישור האימות ממתין לשליחה; התשובה תופיע גם בשיחה.'
  if (result.status === 'exists') {
    return (
      <section className="rep-inquiry-card is-exists" aria-label="פנייה קיימת">
        <header className="rep-inquiry-head">
          <span className="rep-inquiry-icon" aria-hidden="true"><Icon name="info" size={18} /></span>
          <h3>כבר קיימת פנייה פתוחה{car ? <> בנושא <bdi>{car}</bdi></> : null}</h3>
        </header>
        <div className="rep-inquiry-body">
          <p>השאלה שכבר נשלחה לנציג:</p>
          <blockquote className="rep-inquiry-saved"><bdi>{result.question_text}</bdi></blockquote>
          <p className="rep-inquiry-hint">אפשר להמתין לתשובת הנציג לפני פתיחת בירור נוסף באותו נושא.</p>
        </div>
      </section>
    )
  }
  return (
    <section className="rep-inquiry-card is-sent" aria-label="הפנייה נשלחה">
      <header className="rep-inquiry-head">
        <span className="rep-inquiry-icon" aria-hidden="true"><Icon name="check" size={18} /></span>
        <h3>הפנייה נשלחה לנציג הרכב</h3>
      </header>
      <div className="rep-inquiry-body">
        {car && <p>בנושא <bdi>{car}</bdi>.</p>}
        <p>השאלה שנשלחה:</p>
        <blockquote className="rep-inquiry-saved"><bdi>{result.question_text}</bdi></blockquote>
        <p className="rep-inquiry-hint">{emailNotice}</p>
        <p className="rep-inquiry-hint">תשובת הנציג תופיע כאן בשיחה.</p>
      </div>
    </section>
  )
}
