import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { RepQuestion } from '../api/types'
import { AnswerForm } from './AnswerForm'

const base: RepQuestion = {
  id: 'q1', missing_field: 'NEW_CAR_COPAY', mode: 'CONFIRM', option_id: 'CAR-05',
  question_text: 'מה ההשתתפות החודשית של העובד ב-NIRO (2025)? לפי הקטלוג: 250 ₪', answer_type: 'MONEY',
  suggested_value: '250', context: { car_label: 'NIRO (2025)' }, status: 'OPEN',
  created_at: '2026-10-29T10:00:00Z', answered_at: null, answer_value: null, effective_date: null,
  answer_note: null, answered_by: null,
} as RepQuestion

const html = (q: RepQuestion) => renderToStaticMarkup(
  <AnswerForm question={q} token="t" onAnswered={() => {}} onUnauthorized={() => {}} onConflict={() => {}} />)

describe('AnswerForm', () => {
  it('a new car copay: one-click approval of the catalog amount, an optional note, no date', () => {
    const out = html(base)
    expect(out).toContain('אישור סכום ברירת המחדל')
    expect(out).toContain('סכום אחר')
    expect(out).toContain('הערה לעובד (לא חובה)')
    expect(out).not.toContain('required')               // the note is never mandatory
    expect(out).not.toContain('הסבר לעובד')
    expect(out).not.toContain('type="date"')             // the effective date is not asked
    expect(out).not.toContain('תאריך')
  })

  it('a general question: a single answer field, no note', () => {
    const out = html({ ...base, missing_field: 'GENERAL_QUESTION', answer_type: 'TEXT', suggested_value: null,
                       question_text: 'באיזה צבעים יש את NIRO?' })
    expect(out.match(/<textarea/g)).toHaveLength(1)
    expect(out).toContain('>תשובה<')
    expect(out).not.toContain('הערה לעובד')
    expect(out).not.toContain('<input')
  })
})
