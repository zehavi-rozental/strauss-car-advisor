// One inquiry to the transport representative, once, in the chat: the car, what the employee asked, and its status.
// The representative's answer is its own message (linked from here, never copied); a car card shows only a small
// status tag. The same record for an inquiry opened from a car card, from the chat, or by the system.
import { useState } from 'react'
import type { RepInquiry } from '../api/types'
import { Icon } from '../ui/Icon'

const STATUS: Record<RepInquiry['status'], string> = {
  OPEN: 'ממתינה לתשובת הנציג', ANSWERED: 'נענתה', CANCELLED: 'בוטלה',
}

const RESEND_TEXT: Record<string, string> = {
  queued: 'שלחנו קישור אימות חדש לכתובת שמסרתם.',
  valid: 'הקישור הקודם עדיין בתוקף. בדקו את תיבת הדואר, גם בתיקיית הספאם.',
  limit: 'נשלחו כבר כמה קישורי אימות. אפשר לנסות שוב מאוחר יותר; התשובה תופיע כאן בכל מקרה.',
  verified: 'כתובת האימייל כבר אומתה.',
}

/** The record message's own text: "פנייה לנציג הרכב בנושא X:\n<question>\n\n<what happens next>". */
export function parseRecord(content: string): { question: string; next: string } {
  const [head, ...rest] = content.split('\n\n')
  return { question: head.split('\n').slice(1).join('\n').trim(), next: rest.join('\n\n').trim() }
}

const fullTime = (date: Date) => new Intl.DateTimeFormat('he-IL', { dateStyle: 'short', timeStyle: 'short' })
  .format(date)

interface Props {
  question?: RepInquiry
  /** the record message's text (for its question and next step); absent for an older conversation's question */
  content?: string
  answerSeq?: number
  /** a new verification link, offered on the latest open inquiry only while the address waits for it */
  onResend?: () => Promise<string>
}

export function InquiryRecord({ question, content, answerSeq, onResend }: Props) {
  const [resent, setResent] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const parsed = content ? parseRecord(content) : { question: '', next: '' }
  const status = question ? STATUS[question.status] : STATUS.OPEN
  const asked = question?.employee_text || parsed.question
  const open = !question || question.status === 'OPEN'
  const createdAt = question ? new Date(question.created_at) : null
  const answeredAt = question?.answered_at ? new Date(question.answered_at) : null

  const resend = async () => {
    if (!onResend || sending) return
    setSending(true)
    try {
      setResent(RESEND_TEXT[await onResend()] ?? RESEND_TEXT.queued)
    } catch (e) {
      setResent(e instanceof Error && e.message ? e.message : 'לא הצלחנו לשלוח קישור חדש. נסו שוב בעוד רגע.')
    } finally {
      setSending(false)
    }
  }

  return (
    <section id={question ? `rep-inquiry-${question.id}` : undefined}
             className={`rep-inquiry-history is-${(question?.status ?? 'OPEN').toLowerCase()}`}
             data-testid="rep-inquiry-history" aria-label={`פנייה לנציג הרכב: ${status}`}>
      <header>
        <Icon name="rep" size={16} />
        <strong>פנייה לנציג הרכב</strong>
        <span className="rep-inquiry-status" role="status">{status}</span>
      </header>
      {question?.car_label && <p className="rep-inquiry-car">הרכב: <bdi>{question.car_label}</bdi></p>}
      {asked && <p className="rep-inquiry-history-question"><bdi>{asked}</bdi></p>}
      {open && parsed.next && <p className="rep-inquiry-next">{parsed.next}</p>}
      {createdAt && (
        <dl>
          <div><dt>נפתחה</dt><dd>{fullTime(createdAt)}</dd></div>
          {answeredAt && <div><dt>נענתה</dt><dd>{fullTime(answeredAt)}</dd></div>}
        </dl>
      )}
      {answerSeq != null && <a href={`#message-${answerSeq}`}>לתשובת הנציג</a>}
      {open && onResend && (
        <div className="rep-inquiry-resend">
          <span>כתובת האימייל עדיין לא אומתה.</span>
          <button type="button" className="link-inline" disabled={sending} onClick={() => void resend()}>
            {sending ? 'שולח…' : 'שליחת קישור אימות מחדש'}
          </button>
          {resent && <p className="rep-inquiry-hint" role="status">{resent}</p>}
        </div>
      )}
    </section>
  )
}
