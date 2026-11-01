// A question to the transport representative, opened inside the conversation (not a floating button, not a side
// panel — the AI advisor stays the one interface). The employee edits the pre-filled question, confirms, and only
// then is the question saved through the existing cases mechanism (POST /rep-question). The card never claims the
// question was sent before the server confirms it, and tells the employee honestly when an open question already
// exists (status "exists" returns the saved wording, which may differ from what was just typed).
import { useEffect, useRef, useState } from 'react'
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
  /** a ready subject ("בירור סכום ההשתתפות ברכב הנוכחי"), shown above the editable question */
  subject?: string
  /** a verified email stored earlier in this conversation; shown pre-filled, editable */
  knownEmail?: string
  /** mail delivery is configured; false: no address is asked and nothing mentions email */
  emailEnabled?: boolean
  /** a local test server's fake mail: no address is asked, and the card says no real email is sent */
  emailSimulated?: boolean
  disabled?: boolean
  onCancel: () => void
  /** called after a question is saved (status created/exists), so the parent can refresh results/status */
  onSaved: (result: RepQuestionResult) => void
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function RepInquiryCard({
  conversationId, initialQuestion, intent, optionId, carLabel, catalogAmount, subject, knownEmail, emailEnabled = true,
  emailSimulated = false, disabled = false, onCancel, onSaved,
}: Props) {
  const [step, setStep] = useState<Step>('edit')
  const [question, setQuestion] = useState(initialQuestion)
  const [email, setEmail] = useState(knownEmail ?? '')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // errors next to their own field (1/11); `error` is the server's, above the buttons
  const [fieldErrors, setFieldErrors] = useState<{ question?: string; email?: string }>({})
  const emailField = useRef<HTMLInputElement>(null)
  const [result, setResult] = useState<RepQuestionResult | null>(null)
  const section = useRef<HTMLElement>(null)
  const field = useRef<HTMLTextAreaElement>(null)
  // opened (the card is remounted on every opening, even from a card high up in the chat): brought into view and
  // the question field focused, so the employee sees where to write
  useEffect(() => {
    section.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
    // the address comes first when one is needed (visible at once, never behind "המשך")
    ;(emailField.current ?? field.current)?.focus({ preventScroll: true })
  }, [])

  // an address is asked only when mail is on and none is verified for this conversation yet
  const needsEmail = emailEnabled && !emailSimulated && !knownEmail
  const trimmed = question.trim()
  const emailTyped = email.trim().length > 0
  const emailOk = emailTyped && EMAIL_RE.test(email.trim())

  const toSummary = () => {
    const errors: { question?: string; email?: string } = {}
    if (needsEmail && !emailTyped) errors.email = 'כתבו כתובת אימייל, כדי שנוכל לעדכן אתכם כשהנציג יענה.'
    else if (needsEmail && !emailOk) errors.email = 'הכתובת לא נראית תקינה. בדקו אותה, למשל name@example.com.'
    if (!trimmed) errors.question = 'כתבו את השאלה לנציג.'
    setFieldErrors(errors)
    if (errors.email || errors.question) {
      ;(errors.email ? emailField.current : field.current)?.focus()
      return
    }
    setError(null)
    setStep('summary')
  }

  const send = async () => {
    if (sending) return                       // guard against a double click
    setSending(true)
    setError(null)
    try {
      const address = needsEmail ? email.trim() : undefined
      const body = { question: trimmed, intent, ...(address ? { email: address } : {}),
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
    return <RepInquiryDone result={result} carLabel={carLabel} onClose={onCancel} />
  }

  return (
    <section ref={section} className="rep-inquiry-card" aria-label="העברת שאלה לנציג הרכב">
      <header className="rep-inquiry-head">
        <span className="rep-inquiry-icon" aria-hidden="true"><Icon name="rep" size={18} /></span>
        <h3>העברת שאלה לנציג הרכב</h3>
      </header>

      {step === 'edit' && (
        <div className="rep-inquiry-body">
          {subject && <p className="rep-inquiry-subject"><strong>נושא:</strong> {subject}</p>}
          {carLabel && (
            <p className="rep-inquiry-context">
              <span>הרכב: <bdi>{carLabel}</bdi></span>
              {catalogAmount && <span> · השתתפות לפי הקטלוג: {catalogAmount} ₪</span>}
            </p>
          )}
          {emailSimulated && (
            <p className="rep-inquiry-hint" role="note">
              סביבת בדיקות: לא נשלח מייל אמיתי. התשובה של הנציג תופיע כאן בשיחה.
            </p>
          )}
          {needsEmail && (
            <>
              <label className="rep-inquiry-label" htmlFor="rep-email">כתובת אימייל לעדכון כשהנציג יענה</label>
              <input ref={emailField} id="rep-email" className="rep-inquiry-email" type="email" dir="ltr" value={email}
                     disabled={disabled || sending} placeholder="name@example.com" required autoComplete="email"
                     aria-invalid={fieldErrors.email ? true : undefined}
                     aria-describedby={fieldErrors.email ? 'rep-email-error' : 'rep-email-hint'}
                     onChange={(e) => { setEmail(e.target.value); setFieldErrors((f) => ({ ...f, email: undefined })) }} />
              {fieldErrors.email
                ? <p id="rep-email-error" className="rep-inquiry-field-error" role="alert">{fieldErrors.email}</p>
                : <p id="rep-email-hint" className="rep-inquiry-hint">
                    נשלח קישור חד־פעמי לאימות הכתובת. התשובה תופיע גם כאן בשיחה.
                  </p>}
            </>
          )}
          <label className="rep-inquiry-label" htmlFor="rep-q">מה תרצו לשאול?</label>
          <textarea ref={field} id="rep-q" className="rep-inquiry-text" rows={3} value={question} disabled={disabled || sending}
                    maxLength={500} placeholder="כתבו כאן את השאלה לנציג הרכב"
                    aria-invalid={fieldErrors.question ? true : undefined}
                    aria-describedby={fieldErrors.question ? 'rep-q-error' : undefined}
                    onChange={(e) => { setQuestion(e.target.value); setFieldErrors((f) => ({ ...f, question: undefined })) }} />
          {fieldErrors.question && <p id="rep-q-error" className="rep-inquiry-field-error" role="alert">{fieldErrors.question}</p>}
          {error && <p className="rep-inquiry-error" role="alert"><Icon name="alert" size={16} />{error}</p>}
          <div className="rep-inquiry-actions">
            <button type="button" className="btn btn-quiet" disabled={sending} onClick={onCancel}>ביטול</button>
            <button type="button" className="btn btn-primary" disabled={sending} onClick={toSummary}>
              המשך
            </button>
          </div>
        </div>
      )}

      {step === 'summary' && (
        <div className="rep-inquiry-body">
          <p className="rep-inquiry-summary-title">סיכום לפני שליחה</p>
          <dl className="rep-inquiry-summary">
            {subject && <div><dt>נושא</dt><dd>{subject}</dd></div>}
            {carLabel && <div><dt>הרכב</dt><dd><bdi>{carLabel}</bdi></dd></div>}
            {catalogAmount && <div><dt>השתתפות לפי הקטלוג</dt><dd>{catalogAmount} ₪</dd></div>}
            <div><dt>השאלה לנציג</dt><dd><bdi>{trimmed}</bdi></dd></div>
            {needsEmail && <div><dt>אימייל לעדכון</dt><dd dir="ltr">{email.trim()}</dd></div>}
          </dl>
          {(intent === 'copay_verification' || intent === 'current_copay') && (
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

/** After the server answered: an inquiry that already exists is said plainly, with a link to its record in the
 *  chat (nothing new was sent). A created one needs nothing here: its record stands in the chat. */
function RepInquiryDone({ result, carLabel, onClose }: { result: RepQuestionResult; carLabel?: string;
                                                         onClose: () => void }) {
  if (result.status !== 'exists') return null
  const car = result.car_label ?? carLabel
  return (
    <section className="rep-inquiry-card is-exists" aria-label="פנייה קיימת" role="status">
      <header className="rep-inquiry-head">
        <span className="rep-inquiry-icon" aria-hidden="true"><Icon name="info" size={18} /></span>
        <h3>כבר קיימת פנייה פתוחה{car ? <> בנושא <bdi>{car}</bdi></> : null}</h3>
      </header>
      <div className="rep-inquiry-body">
        <p>לא שלחנו פנייה נוספת. הפנייה הקיימת ממתינה לתשובת הנציג, והתשובה תופיע כאן בשיחה.</p>
        {result.question_text && <blockquote className="rep-inquiry-saved"><bdi>{result.question_text}</bdi></blockquote>}
        <div className="rep-inquiry-actions">
          {result.question_id && (
            <a className="btn btn-quiet" href={`#rep-inquiry-${result.question_id}`}>לפנייה הקיימת</a>
          )}
          <button type="button" className="btn btn-primary" onClick={onClose}>סגירה</button>
        </div>
      </div>
    </section>
  )
}
