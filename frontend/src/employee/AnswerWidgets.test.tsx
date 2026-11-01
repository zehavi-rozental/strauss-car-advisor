import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Expects } from '../api/types'
import { AnswerWidgets, RepCopayUpdate, copayAmountValid } from './AnswerWidgets'

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

describe("the representative's copay (1/11)", () => {
  const offer = {
    field: 'rep_copay', type: 'choice', quick_replies: [], max: null, closed: true,
    choices: [{ id: 'apply:q1', label: 'עדכון החישוב לפי תשובת הנציג' }],
    fields: [{ field: 'rep_copay', type: 'choice', quick_replies: [], max: null, closed: true,
               choices: [{ id: 'apply:q1', label: 'עדכון החישוב לפי תשובת הנציג' }],
               context: { car: 'COROLLA (2026)', amount: '230', previous: '200' } }],
  } as unknown as Expects

  it('is drawn under the answer, never in the bottom area', () => {
    expect(renderToStaticMarkup(<AnswerWidgets expects={offer} disabled={false} onSend={async () => true} />)).toBe('')
  })

  it('shows one update button, and no confirmation until it is pressed', () => {
    const field = (offer as unknown as { fields: Parameters<typeof RepCopayUpdate>[0]['field'][] }).fields[0]
    const out = renderToStaticMarkup(<RepCopayUpdate field={field} disabled={false} onSend={async () => true} />)
    expect(out.match(/<button/g)).toHaveLength(1)
    expect(out).toContain('עדכון החישוב לפי תשובת הנציג')
    expect(out).not.toContain('אישור העדכון')
  })
})
