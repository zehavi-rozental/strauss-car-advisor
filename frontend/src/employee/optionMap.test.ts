import { describe, expect, it } from 'vitest'
import type { MapEntry, OptionMap } from '../api/types'
import { MAX_COMPARE, asResults, filterValues, leadText, mapSections, toggleCompare } from './optionMap'

const entry = (id: string, delta: string | null, status: MapEntry['status'] = 'catalog_copay',
               extra: Partial<MapEntry> = {}): MapEntry => ({
  option_id: id, label: id, group: 'ג', drive: 'ELECTRIC', status,
  result: { status: status === 'incomplete' ? 'INCOMPLETE' : 'OK', delta_display: delta } as MapEntry['result'],
  ...extra,
})

const map = (entries: MapEntry[], extra: Partial<OptionMap> = {}): OptionMap => ({
  entries, excluded: [], leaders: [], stale: false, group: 'ג',
  counts: { eligible: entries.length, calculated: entries.filter((e) => e.status !== 'incomplete').length,
            waiting: entries.filter((e) => e.status === 'incomplete').length, catalog_copay: 0 },
  ...extra,
})

describe('360° option map', () => {
  const m = map([entry('B', '200'), entry('A', '900'), entry('W', null, 'incomplete'),
                 entry('H', '-50', 'full', { drive: 'HYBRID', group: 'ב' })], { leaders: ['A'] })

  it('ranks only the calculated options, by net, and keeps the waiting ones apart', () => {
    const s = mapSections(m, { drive: null, group: null }, 'net')
    expect(s.ranked.map((e) => e.option_id)).toEqual(['A', 'B', 'H'])
    expect(s.waiting.map((e) => e.option_id)).toEqual(['W'])
  })

  it('every option is listed: more than the three a table shows', () => {
    const many = map(Array.from({ length: 13 }, (_, i) => entry(`C${i}`, String(i * 10))))
    expect(mapSections(many, { drive: null, group: null }, 'net').ranked).toHaveLength(13)
  })

  it('filters only by values the map has, and never hides the waiting section behind a ranking', () => {
    expect(filterValues(m)).toEqual({ drives: ['ELECTRIC', 'HYBRID'], groups: ['ב', 'ג'] })
    const hybrid = mapSections(m, { drive: 'HYBRID', group: null }, 'net')
    expect(hybrid.ranked.map((e) => e.option_id)).toEqual(['H'])
  })

  it('compares at most three calculated options and keeps the order picked', () => {
    let picked: string[] = []
    for (const e of [entry('A', '1'), entry('B', '2'), entry('C', '3'), entry('D', '4')]) picked = toggleCompare(picked, e)
    expect(picked).toEqual(['A', 'B', 'C'])
    expect(picked).toHaveLength(MAX_COMPARE)
    expect(toggleCompare([], entry('W', null, 'incomplete'))).toEqual([])      // not calculated: not compared
    expect(toggleCompare(['A', 'B'], entry('A', '1'))).toEqual(['B'])
    expect(asResults(m, ['B', 'A']).map((r) => r.option_id)).toEqual(['B', 'A'])
  })

  it('says the lead only from the server: one, a tie, none, or out of date', () => {
    expect(leadText(m)).toBe('הנטו החודשי הגבוה ביותר: A')
    expect(leadText({ ...m, leaders: ['A', 'B'] })).toBe('שוויון בנטו המשוער: B, A')
    expect(leadText({ ...m, leaders: [] })).toContain('אין עדיין חלופה שחושבה במלואה')
    expect(leadText({ ...m, stale: true })).toContain('לפני השינוי האחרון')
  })
})
