// What the payslip uploader shows after an extraction. A failure, an unreadable file and a file that is not
// a payslip each say so with a way forward; none of them is ever shown as an empty review form.
import type { ExtractionResult, PayslipStatus } from '../api/types'

export type PayslipView = 'failed' | 'review'

export interface PayslipOutcome {
  view: PayslipView
  status: PayslipStatus
  message: string
  title: string
  retry: boolean          // "לנסות שוב" with the same file may help (timeout, quota, a bad answer)
}

export const FAILED_FALLBACK = 'לא הצלחנו לקרוא את התלוש כרגע. אפשר לנסות שוב בעוד רגע, או למלא את הנתונים ידנית.'
export const NOTHING_FALLBACK = 'לא מצאנו בתלוש נתון שאפשר להכניס לחישוב. אפשר למלא ידנית או לנסות קובץ אחר.'
// the per-row choice (מהתלוש / מהשיחה / לא לעדכן) is explained by PayslipUpload's own hint under this one
export const REVIEW_HINT = 'אלה הערכים שזוהו מהתלוש. בדקו כל שורה ותקנו אם צריך, ואז אשרו.'
export const NOT_ON_PAYSLIP = 'לא נמצא בתלוש'

const TITLES: Record<PayslipStatus, string> = {
  failed: 'לא הצלחנו לקרוא את התלוש',
  not_payslip: 'הקובץ לא נראה כמו תלוש שכר',
  unreadable: 'לא הצלחנו לקרוא את הכתוב בקובץ',
  nothing_found: 'בדיקת הנתונים מהתלוש',
  partial: 'בדיקת הנתונים מהתלוש',
  found: 'בדיקת הנתונים מהתלוש',
}

const filled = (v: string | null | undefined) => v !== null && v !== undefined && v !== ''

export function payslipOutcome(result: ExtractionResult): PayslipOutcome {
  const found = result.fields.filter((f) => filled(f.value) && (f.status ?? 'found') === 'found').length
  // an older server sends no status: derive it the same way
  const status: PayslipStatus = !result.ok ? 'failed' : result.status
    ?? (found === 0 ? 'nothing_found' : found < result.fields.length ? 'partial' : 'found')
  const title = TITLES[status]
  if (status === 'failed') {
    return { view: 'failed', status, title, message: result.message_he || FAILED_FALLBACK, retry: result.retry ?? true }
  }
  if (status === 'not_payslip' || status === 'unreadable') {
    return { view: 'failed', status, title, message: result.message_he, retry: false }
  }
  if (status === 'nothing_found') {
    return { view: 'review', status, title, message: result.message_he || NOTHING_FALLBACK, retry: false }
  }
  const message = status === 'partial' && result.message_he ? result.message_he : REVIEW_HINT
  return { view: 'review', status, title, message, retry: false }
}
