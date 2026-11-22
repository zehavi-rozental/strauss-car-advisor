import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Expects, PayslipInfo } from '../api/types'
import { AnswerWidgets } from './AnswerWidgets'
import { payslipMaintenance } from './payslipReview'

// No company car (19/11): the car-maintenance allowance is a closed question, the same shape as today's copay.
const maintenance = (choices: { id: string; label: string }[], context: Record<string, unknown>) => ({
  field: 'maintenance', type: 'choice', quick_replies: [], max: null, closed: true, choices,
  fields: [{ field: 'maintenance', type: 'choice', quick_replies: [], max: null, closed: true, choices, context }],
}) as unknown as Expects

const render = (expects: Expects) => renderToStaticMarkup(
  <AnswerWidgets expects={expects} disabled={false} onSend={async () => true} onMaintenanceRep={() => {}} />)

describe('the car-maintenance allowance with no company car (19/11)', () => {
  it("offers the payslip's amount to confirm, change or check with the representative", () => {
    const out = render(maintenance([{ id: 'payslip', label: 'זה הסכום שלי' }, { id: 'other', label: 'הסכום שונה' },
                                    { id: 'rep', label: 'לברר מול נציג' }], { payslip_amount: '1200', with_rep: false }))
    expect(out.match(/<button/g)).toHaveLength(3)
    for (const label of ['זה הסכום שלי', 'הסכום שונה', 'לברר מול נציג']) expect(out).toContain(label)
    expect(out).not.toContain('maintenance-amount')                 // no amount field until "הסכום שונה"
  })

  it('suggests nothing without a payslip amount: an amount, none, or the representative', () => {
    const out = render(maintenance([{ id: 'other', label: 'להזין סכום' }, { id: 'none', label: 'אין אחזקת רכב' },
                                    { id: 'rep', label: 'לברר מול נציג' }], { payslip_amount: null, with_rep: false }))
    expect(out.match(/<button/g)).toHaveLength(3)
    expect(out).not.toContain('זה הסכום שלי')
    expect(out).toContain('אין אחזקת רכב')
  })

  it('never offers the representative again once asked (the server leaves it out)', () => {
    const out = render(maintenance([{ id: 'other', label: 'להזין סכום' }, { id: 'none', label: 'אין אחזקת רכב' }],
                                   { payslip_amount: null, with_rep: true }))
    expect(out).not.toContain('לברר מול נציג')
  })
})

describe('the payslip maintenance line (19/11)', () => {
  const item = (key: string, value: string, suspect = false) =>
    ({ key, value, label: '', kind: 'amount', source_label: '', note: '', suspect }) as PayslipInfo
  it('is sent only when a sure car-maintenance line was read', () => {
    expect(payslipMaintenance([item('net_pay', '9000'), item('car_maintenance', '1200')])).toBe('1200')
    expect(payslipMaintenance([item('car_maintenance', '3.4', true)])).toBeUndefined()
    expect(payslipMaintenance([item('car_copay', '300')])).toBeUndefined()
    expect(payslipMaintenance([])).toBeUndefined()
  })
})
