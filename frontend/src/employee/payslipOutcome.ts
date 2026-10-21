// What the payslip uploader shows after an extraction. Four outcomes the employee must tell apart:
// a failure (the model gave no usable answer) is never shown as an empty review form.
import type { ExtractionResult } from '../api/types'

export type PayslipView = 'failed' | 'review'

export interface PayslipOutcome {
  view: PayslipView
  status: 'found' | 'partial' | 'nothing_found' | 'failed'
  message: string
}

export const FAILED_FALLBACK = 'לא הצלחנו לקרוא את התלוש כרגע. אפשר לנסות שוב בעוד רגע, או למלא את הנתונים ידנית.'
export const NOTHING_FALLBACK = 'לא מצאנו בתלוש את הנתונים שהמערכת קוראת. אפשר למלא ידנית או לנסות קובץ אחר.'
export const REVIEW_HINT = 'אלה הערכים שזוהו מהתלוש. בדקו כל שדה, תקנו מה שצריך, ואז אשרו. ' +
  'שדה שמסומן "נא לבדוק" לא זוהה בוודאות.'
export const NOT_ON_PAYSLIP = 'לא נמצא בתלוש'

export function payslipOutcome(result: ExtractionResult): PayslipOutcome {
  const found = result.fields.filter((f) => f.value !== null && f.value !== undefined && f.value !== '').length
  // an older server sends no status: derive it the same way
  const status = result.status
    ?? (!result.ok ? 'failed' : found === 0 ? 'nothing_found' : found < result.fields.length ? 'partial' : 'found')
  if (status === 'failed' || !result.ok) {
    return { view: 'failed', status: 'failed', message: result.message_he || FAILED_FALLBACK }
  }
  if (status === 'nothing_found') {
    return { view: 'review', status, message: result.message_he || NOTHING_FALLBACK }
  }
  return { view: 'review', status, message: status === 'partial' && result.message_he ? result.message_he : REVIEW_HINT }
}
