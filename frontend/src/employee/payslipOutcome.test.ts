import { describe, expect, it } from 'vitest'
import type { ExtractedField, ExtractionResult } from '../api/types'
import { FAILED_FALLBACK, REVIEW_HINT, payslipOutcome } from './payslipOutcome'

const KEYS = ['commissions', 'bonus', 'pension_percent', 'study_fund_percent', 'credit_points',
  'children_under_23'] as const

function fields(values: Partial<Record<(typeof KEYS)[number], string>>): ExtractedField[] {
  return KEYS.map((k) => ({ field: k, label: k, value: values[k] ?? null, kind: 'amount', confident: true,
    in_range: true, note: '' }))
}

describe('payslipOutcome', () => {
  it('a failure is its own view, never an empty review form', () => {
    const r: ExtractionResult = { ok: false, status: 'failed', fields: [], message_he: 'לא הצלחנו לקרוא את התלוש כרגע.' }
    expect(payslipOutcome(r)).toEqual({ view: 'failed', status: 'failed', message: r.message_he })
  })

  it('a failure from an older server (no status) is still a failure', () => {
    expect(payslipOutcome({ ok: false, fields: [], message_he: '' }))
      .toEqual({ view: 'failed', status: 'failed', message: FAILED_FALLBACK })
  })

  it('nothing found says so with the server message', () => {
    const r: ExtractionResult = { ok: true, status: 'nothing_found', fields: fields({}), message_he: 'לא מצאנו בתלוש…' }
    expect(payslipOutcome(r)).toEqual({ view: 'review', status: 'nothing_found', message: 'לא מצאנו בתלוש…' })
  })

  it('partial and found go to the review form', () => {
    const partial: ExtractionResult = { ok: true, status: 'partial', fields: fields({ bonus: '500' }), message_he: 'זיהינו חלק' }
    expect(payslipOutcome(partial)).toMatchObject({ view: 'review', status: 'partial', message: 'זיהינו חלק' })
    const all = Object.fromEntries(KEYS.map((k) => [k, '1']))
    const found: ExtractionResult = { ok: true, status: 'found', fields: fields(all), message_he: 'x' }
    expect(payslipOutcome(found)).toEqual({ view: 'review', status: 'found', message: REVIEW_HINT })
  })

  it('derives the status when an older server sends none', () => {
    expect(payslipOutcome({ ok: true, fields: fields({}), message_he: '' }).status).toBe('nothing_found')
    expect(payslipOutcome({ ok: true, fields: fields({ bonus: '5' }), message_he: '' }).status).toBe('partial')
  })
})
