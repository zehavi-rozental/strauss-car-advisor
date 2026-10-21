import { describe, expect, it } from 'vitest'
import type { ExtractedField, ExtractionResult } from '../api/types'
import { FAILED_FALLBACK, REVIEW_HINT, payslipOutcome } from './payslipOutcome'

const KEYS = ['base_salary', 'commissions', 'bonus', 'pension_percent', 'study_fund_percent', 'credit_points',
  'children_under_23'] as const

function fields(values: Partial<Record<(typeof KEYS)[number], string>>): ExtractedField[] {
  return KEYS.map((k) => ({ field: k, label: k, value: values[k] ?? null, kind: 'amount', confident: true,
    in_range: true, note: '', status: values[k] ? 'found' : 'missing' }))
}

describe('payslipOutcome', () => {
  it('a failure is its own view, never an empty review form', () => {
    const r: ExtractionResult = { ok: false, status: 'failed', reason: 'quota', retry: true, fields: [],
      message_he: 'שירות הקריאה עמוס כרגע.' }
    expect(payslipOutcome(r)).toMatchObject({ view: 'failed', status: 'failed', retry: true,
      title: 'לא הצלחנו לקרוא את התלוש', message: r.message_he })
  })

  it('a failure that a retry will not fix offers another file instead', () => {
    const r: ExtractionResult = { ok: false, status: 'failed', reason: 'rejected', retry: false, fields: [], message_he: 'x' }
    expect(payslipOutcome(r).retry).toBe(false)
  })

  it('a failure from an older server (no status) is still a failure', () => {
    expect(payslipOutcome({ ok: false, fields: [], message_he: '' }))
      .toMatchObject({ view: 'failed', status: 'failed', message: FAILED_FALLBACK, retry: true })
  })

  it('not a payslip and unreadable are not shown as a review', () => {
    for (const status of ['not_payslip', 'unreadable'] as const) {
      const out = payslipOutcome({ ok: true, status, fields: [], message_he: 'm' })
      expect(out).toMatchObject({ view: 'failed', status, retry: false, message: 'm' })
    }
  })

  it('nothing found says so with the server message', () => {
    const r: ExtractionResult = { ok: true, status: 'nothing_found', fields: fields({}), message_he: 'לא מצאנו בתלוש…' }
    expect(payslipOutcome(r)).toMatchObject({ view: 'review', status: 'nothing_found', message: 'לא מצאנו בתלוש…' })
  })

  it('partial and found go to the review form', () => {
    const partial: ExtractionResult = { ok: true, status: 'partial', fields: fields({ bonus: '500' }), message_he: 'זיהינו חלק' }
    expect(payslipOutcome(partial)).toMatchObject({ view: 'review', status: 'partial', message: 'זיהינו חלק' })
    const all = Object.fromEntries(KEYS.map((k) => [k, '1']))
    const found: ExtractionResult = { ok: true, status: 'found', fields: fields(all), message_he: 'x' }
    expect(payslipOutcome(found)).toMatchObject({ view: 'review', status: 'found', message: REVIEW_HINT })
  })

  it('derives the status when an older server sends none', () => {
    expect(payslipOutcome({ ok: true, fields: fields({}), message_he: '' }).status).toBe('nothing_found')
    expect(payslipOutcome({ ok: true, fields: fields({ bonus: '5' }), message_he: '' }).status).toBe('partial')
  })
})
