import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Expects } from '../api/types'
import { AnswerWidgets, copayAmountValid } from './AnswerWidgets'

const copay: Expects = {
  field: 'copay', type: 'choice', quick_replies: [], max: null, closed: true,
  choices: [{ id: 'catalog:CAR-07', label: 'זה הסכום שלי' }, { id: 'other', label: 'הסכום שונה' },
            { id: 'rep', label: 'לברר מול נציג' }],
  fields: [{ field: 'copay', type: 'choice', quick_replies: [], max: null, closed: true,
             choices: [{ id: 'catalog:CAR-07', label: 'זה הסכום שלי' }, { id: 'other', label: 'הסכום שונה' },
                       { id: 'rep', label: 'לברר מול נציג' }],
             context: { car: 'COROLLA (2026)', catalog_amount: '250' } }],
} as unknown as Expects

describe("today's copay (31/10)", () => {
  it('shows exactly three choices, and no amount field until "הסכום שונה" is chosen', () => {
    const out = renderToStaticMarkup(
      <AnswerWidgets expects={copay} disabled={false} onSend={async () => true} onCopayRep={() => {}} />)
    expect(out.match(/<button/g)).toHaveLength(3)
    for (const label of ['זה הסכום שלי', 'הסכום שונה', 'לברר מול נציג']) expect(out).toContain(label)
    expect(out).not.toContain('copay-amount')
  })

  it('accepts an amount and refuses text', () => {
    for (const ok of ['280', ' 280 ', '250.5', '1200']) expect(copayAmountValid(ok)).toBe(true)
    for (const bad of ['', 'd', 'abc', '280₪', '-5', '123456']) expect(copayAmountValid(bad)).toBe(false)
  })
})
