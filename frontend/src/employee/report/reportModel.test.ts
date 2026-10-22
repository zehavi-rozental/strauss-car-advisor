import { describe, expect, it } from 'vitest'
import { breakdownRows, rankResults } from '../resultView'
import { MANY, THREE, item } from './fixtures.test-data'
import { buildReport, canReport, generatedText, reportFileName } from './reportModel'

const AT = new Date(2026, 9, 22, 14, 32)

describe('when the report is offered', () => {
  it('only for current results with at least one calculated option', () => {
    expect(canReport([], false)).toBe(false)
    expect(canReport([item('X', 'X (2025)', null)], false)).toBe(false)                 // nothing calculated
    expect(canReport(THREE, true)).toBe(false)                                          // out of date
    expect(canReport(THREE, false)).toBe(true)
    expect(canReport([item('OPT_OUT', 'ויתור על הרכב', 14594)], false)).toBe(true)      // a waiver alone
  })
})

describe('the report model', () => {
  it('holds every result, ranked as the screen ranks them, never cut to 3', () => {
    const m = buildReport(MANY, AT)
    expect(m.options.map((o) => o.id)).toEqual(rankResults(MANY).map((r) => r.option_id))
    expect(m.options).toHaveLength(6)
    expect(m.options.map((o) => o.rank)).toEqual([1, 2, 3, 4, 5, 6])
  })

  it('takes every amount from the server, as sent', () => {
    const m = buildReport(THREE, AT)
    for (const r of THREE) {
      const o = m.options.find((x) => x.id === r.option_id)!
      expect(o.net).toBe(r.result.alternative!.net_display)
      expect(o.delta).toBe(r.result.delta_display)
      expect(o.copay).toBe(r.result.alternative!.copay)
      expect(o.useValue).toBe(r.result.alternative!.use_value)
      expect(o.annual).toBe(Number(r.result.delta_display) * 12)
      expect(o.copayPending).toBe(true)
    }
    expect(m.today).toEqual({ label: 'COROLLA CROSS (2025)', net: '11032', copay: '300', useValue: '4070' })
  })

  it('never turns a missing result into 0', () => {
    const o = buildReport(MANY, AT).options.find((x) => x.id === 'CAR-99')!
    expect(o).toMatchObject({ calculated: false, net: null, delta: null, annual: null, copay: null, useValue: null, gapToHighest: null })
    expect(o.reason).toBe('הרכב לא בקבוצת הזכאות שלכם')
  })

  it('marks the highest monthly net, a waiver included, and the gap of each other option', () => {
    const m = buildReport(MANY, AT)
    const by = (id: string) => m.options.find((o) => o.id === id)!
    expect(by('OPT_OUT').highest).toBe(true)
    expect(m.highestCount).toBe(1)
    expect(by('CAR-04').gapToHighest).toBe(14594 - 12045)
    expect(by('CAR-13')).toMatchObject({ tone: 'down', delta: String(10109 - 11032), gapToHighest: 14594 - 10109 })
    expect(by('OPT_OUT').gapToHighest).toBeNull()
  })

  it('names every option that ties for the highest net', () => {
    const m = buildReport([item('A', 'A (2025)', 12045), item('B', 'B (2025)', 12045), item('C', 'C (2025)', 11000)], AT)
    expect(m.options.filter((o) => o.highest).map((o) => o.id).sort()).toEqual(['A', 'B'])
    expect(m.highestCount).toBe(2)
  })

  it('uses the comparison screen rows for the breakdown, in the report order', () => {
    expect(buildReport(MANY, AT).rows).toEqual(breakdownRows(rankResults(MANY)))
  })

  it('says when the report was made, and names the file safely', () => {
    expect(generatedText(AT)).toBe('22.10.2026, 14:32')
    expect(reportFileName(AT)).toBe('STRAUSS-DRIVE-comparison-2026-10-22-1432.pdf')
  })
})
