import { describe, expect, it } from 'vitest'
import type { Expects, ExpectsField, Stage } from '../api/types'
import {
  PROGRESS_STEPS, counterLabel, isBlocked, linkFor, optionsField, placeholderFor, progressStep, toggleSelection,
  widgetsFor,
} from './widgets'

const f = (field: string, type: ExpectsField['type'], extra: Partial<ExpectsField> = {}): ExpectsField =>
  ({ field, type, choices: [], quick_replies: [], max: null, ...extra })
const ex = (fields: ExpectsField[], extra: Partial<Expects> = {}): Expects => ({ ...fields[0], fields, ...extra })
const cars = Array.from({ length: 5 }, (_, i) => ({ id: `C${i}`, label: `Car ${i}` }))
const kinds = (e: Expects | null) => widgetsFor(e).map((w) => w.kind)

describe('widgetsFor: free text first', () => {
  it('shows no widget for open questions', () => {
    expect(kinds(ex([f('occupation', 'choice', { choices: [...cars, ...cars] }), f('group', 'choice', { choices: cars }),
                     f('base', 'number')]))).toEqual([])
    expect(kinds(ex([f('commissions', 'number'), f('bonus', 'number'), f('pension_percent', 'number')]))).toEqual([])
    expect(kinds(ex([f('car', 'car_search', { quick_replies: [{ id: 'no_car', label: 'אין רכב' }] }),
                     f('copay', 'number')]))).toEqual([])
    expect(kinds(ex([f('copay', 'number', { quick_replies: [{ id: 'unknown', label: 'לא ידוע' }] })]))).toEqual([])
    expect(kinds(ex([f('options', 'multi_choice', { choices: cars, max: 3 })]))).toEqual([])
    expect(kinds(null)).toEqual([])
  })

  it('shows buttons for closed questions only', () => {
    const yesNo = [{ id: 'yes', label: 'לאשר' }, { id: 'no', label: 'לתקן' }]
    expect(kinds(ex([f('confirmation', 'choice', { choices: yesNo, closed: true })]))).toEqual(['buttons'])
    expect(kinds(ex([f('car_choice', 'choice', { choices: [...cars, ...cars], closed: true })]))).toEqual(['select'])
  })

  it('adds the representative button next to an open question', () => {
    const e = ex([f('base', 'number'),
                  f('ask_rep', 'choice', { choices: [{ id: 'ask_rep', label: 'לשאול את נציג התחבורה' }], closed: true })])
    expect(widgetsFor(e).map((w) => w.field.field)).toEqual(['ask_rep'])
  })
})

describe('links, selection and placeholder', () => {
  it('takes the one link from the server', () => {
    const e = ex([f('commissions', 'number'), f('bonus', 'number')], { link: { kind: 'profile_form', label: 'למלא בטופס' } })
    expect(linkFor(e)).toEqual({ kind: 'profile_form', label: 'למלא בטופס' })
    expect(linkFor(ex([f('base', 'number')]))).toBeNull()
  })

  it('finds the options for the selection card the link opens', () => {
    const e = ex([f('options', 'multi_choice', { choices: cars, max: 3 })], { link: { kind: 'selection', label: 'x' } })
    expect(optionsField(e)?.choices).toHaveLength(5)
    expect(optionsField(ex([f('car', 'car_search')]))).toBeNull()
  })

  it('mirrors the question with the server example', () => {
    expect(placeholderFor(ex([f('car', 'car_search')], { placeholder: 'למשל: קורולה קרוס 2025, משלמת 300' })))
      .toBe('למשל: קורולה קרוס 2025, משלמת 300')
    expect(placeholderFor(null)).toBe('כתבו הודעה…')
  })
})

describe('progress', () => {
  it('maps every stage to a step', () => {
    const cases: [Stage | null, boolean, number][] = [
      [null, false, 0], ['COLLECT_PROFILE', false, 0], ['COLLECT_CURRENT_CAR', false, 1], ['CHOOSE_OPTIONS', false, 2],
      ['CALCULATING', false, 2], ['PRESENTED', true, 3], ['WAITING_FOR_REP', false, 1], ['WAITING_FOR_REP', true, 3],
    ]
    for (const [stage, results, step] of cases) expect(progressStep(stage, results)).toBe(step)
    expect(PROGRESS_STEPS).toEqual(['פרטים', 'רכב היום', 'חלופות', 'תוצאה'])
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
