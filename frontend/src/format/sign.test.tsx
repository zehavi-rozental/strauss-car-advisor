// One sign style everywhere: the minus sign sits left of the digits inside <bdi>: "−923 ₪".
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { OptionResult, ScenarioResult } from '../api/types'
import { ResultCard } from '../employee/ResultCard'
import { RichText } from '../employee/richText'
import { SummaryCard } from '../employee/SummaryCard'
import { buildRows } from '../employee/comparison'
import { Money } from './Amount'
import { formatMoney } from './money'

const EXPECTED = '−923 ₪'
const result = (delta: string): ScenarioResult => ({
  status: 'OK', eligible: true, reason: '', missing_fields: [], today: null, alternative: null,
  delta_display: delta, lines: [], cost: null, drivers: [], electric_refund: null, copay_pending: false,
  assumptions: [], why: 'שווי השימוש גבוה יותר, ולכן המס יגדל.', rules_version: '', extensions_version: '', engine_version: '',
})
const item = (label: string, delta: string): OptionResult => ({ option_id: label, label, run_id: 'r', result: result(delta) })

describe('sign style', () => {
  it('formats the same string everywhere', () => {
    expect(formatMoney(-923, { signed: true })).toBe(EXPECTED)
    expect(renderToStaticMarkup(<Money value="-923" signed />)).toContain(`>${EXPECTED}</bdi>`)
    expect(renderToStaticMarkup(<RichText text="הפרש -923 ₪ בחודש" />)).toContain(`>${EXPECTED}</bdi>`)
    expect(renderToStaticMarkup(<SummaryCard items={[item('A', '-923'), item('B', '-1200')]} />))
      .toContain(`>${EXPECTED}</bdi>`)
    expect(renderToStaticMarkup(<ResultCard item={item('A', '-923')} />)).toContain(`>${EXPECTED}</bdi>`)
    const cell = buildRows([item('A', '-923')]).flatMap((r) => r.alts).find((c) => c.kind === 'money' && c.signed)
    if (cell?.kind === 'money') expect(formatMoney(cell.value, { signed: true })).toBe(EXPECTED)
  })

  it('never says ירד / עלה in the headline', () => {
    const out = renderToStaticMarkup(<ResultCard item={item('A', '-923')} />)
    expect(out).toContain('יקטן ב-')
    expect(out).not.toMatch(/ירד|עלה/)
    expect(renderToStaticMarkup(<ResultCard item={item('A', '250')} />)).toContain('יגדל ב-')
  })
})
