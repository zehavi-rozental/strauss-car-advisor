import { describe, expect, it } from 'vitest'
import type { Expects, ExpectsField } from '../api/types'
import { counterLabel, isBlocked, leftoverFields, placeholderFor, toggleSelection, widgetsFor } from './widgets'

const f = (field: string, type: ExpectsField['type'], extra: Partial<ExpectsField> = {}): ExpectsField =>
  ({ field, type, choices: [], quick_replies: [], max: null, ...extra })
const ex = (...fields: ExpectsField[]): Expects => ({ ...fields[0], fields })
const cars = Array.from({ length: 5 }, (_, i) => ({ id: `C${i}`, label: `Car ${i}` }))
const kinds = (e: Expects | null) => widgetsFor(e).map((w) => w.kind)

describe('widgetsFor', () => {
  it('chooses the primary widget by expects', () => {
    expect(kinds(ex(f('group', 'choice', { choices: cars })))).toEqual(['buttons'])
    expect(kinds(ex(f('occupation', 'choice', { choices: [...cars, ...cars] })))).toEqual(['select'])
    expect(kinds(ex(f('car', 'car_search')))).toEqual(['car_search'])
    expect(kinds(ex(f('options', 'multi_choice', { choices: cars, max: 3 })))).toEqual(['selection'])
    expect(kinds(ex(f('copay', 'number', { quick_replies: [{ id: 'unknown', label: 'לא ידוע' }] })))).toEqual(['quick'])
    expect(kinds(null)).toEqual([])
  })

  it('Q2 (only profile numbers) is the form', () => {
    const q2 = ex(f('commissions', 'number'), f('bonus', 'number'), f('pension_percent', 'number'))
    expect(kinds(q2)).toEqual(['profile_form'])
  })

  it('Q3 shows the car search and the selection together, with leftovers as a row', () => {
    const q3 = ex(f('car', 'car_search'), f('options', 'multi_choice', { choices: cars, max: 3 }),
                  f('bonus', 'number'), f('children', 'number'))
    expect(kinds(q3)).toEqual(['car_search', 'selection'])
    expect(leftoverFields(q3)).toEqual(['bonus', 'children'])
  })

  it('a partial Q3 shows only the open part', () => {
    expect(kinds(ex(f('options', 'multi_choice', { choices: cars, max: 3 })))).toEqual(['selection'])
    expect(kinds(ex(f('car', 'car_search'), f('bonus', 'number')))).toEqual(['car_search'])
  })
})

describe('selection counter', () => {
  it('toggles and stops at the limit', () => {
    let p: string[] = []
    p = toggleSelection(p, 'a', 3)
    p = toggleSelection(p, 'b', 3)
    p = toggleSelection(p, 'c', 3)
    expect(p).toEqual(['a', 'b', 'c'])
    expect(toggleSelection(p, 'd', 3)).toEqual(['a', 'b', 'c'])
    expect(toggleSelection(p, 'b', 3)).toEqual(['a', 'c'])
  })

  it('blocks only unselected cards once full', () => {
    expect(isBlocked(['a', 'b', 'c'], 'd', 3)).toBe(true)
    expect(isBlocked(['a', 'b', 'c'], 'a', 3)).toBe(false)
    expect(isBlocked(['a'], 'd', 3)).toBe(false)
  })

  it('writes the live counter', () => {
    expect(counterLabel(2, 3)).toBe('נבחרו 2 מתוך 3')
  })
})

describe('placeholderFor', () => {
  it('gives a short per-question hint', () => {
    expect(placeholderFor(ex(f('pension_percent', 'number')))).toContain('פנסיה 6%')
    expect(placeholderFor(ex(f('car', 'car_search')))).toMatch(/^או כתבו כאן/)
    expect(placeholderFor(null)).toBe('כתבו הודעה…')
  })
})
