// Which input the current question gets, derived from `expects` only. Pure, so it is unit-tested.
import type { Expects, ExpectsField } from '../api/types'
import { Q2_FIELD_NAMES } from './formFields'
import { fieldLabel } from './labels'

export const SELECT_THRESHOLD = 8
export type WidgetKind = 'profile_form' | 'car_search' | 'selection' | 'buttons' | 'select' | 'quick'

export interface Widget {
  kind: WidgetKind
  field: ExpectsField
}

export function fieldsOf(expects: Expects | null): ExpectsField[] {
  if (!expects) return []
  return expects.fields.length ? expects.fields : expects.type !== 'none' ? [expects] : []
}

const isQ2 = (f: ExpectsField) => f.type === 'number' && f.field !== null && Q2_FIELD_NAMES.has(f.field)

/** Q2 itself: every field on screen is a profile number field (two or more) -> the form. */
export function isQ2Question(expects: Expects | null): boolean {
  const fields = fieldsOf(expects)
  return fields.length >= 2 && fields.every(isQ2)
}

/** Q2 fields still open next to another question (Q3 leftovers): shown as a compact row. */
export function leftoverFields(expects: Expects | null): string[] {
  const fields = fieldsOf(expects)
  if (!fields.some((f) => !isQ2(f))) return []
  return fields.filter(isQ2).map((f) => f.field as string)
}

export const leftoverText = (names: string[]) => `חסר עוד: ${names.map(fieldLabel).join(' · ')}`

/** One primary widget per field on screen. */
export function widgetsFor(expects: Expects | null): Widget[] {
  const fields = fieldsOf(expects)
  if (isQ2Question(expects)) return [{ kind: 'profile_form', field: fields[0] }]
  const out: Widget[] = []
  for (const f of fields) {
    if (isQ2(f)) continue                                   // leftovers: the compact row
    if (f.type === 'multi_choice') out.push({ kind: 'selection', field: f })
    else if (f.type === 'car_search') out.push({ kind: 'car_search', field: f })
    else if (f.type === 'choice' && f.choices.length > SELECT_THRESHOLD) out.push({ kind: 'select', field: f })
    else if (f.type === 'choice' && f.choices.length > 0) out.push({ kind: 'buttons', field: f })
    else if (f.quick_replies.length > 0) out.push({ kind: 'quick', field: f })
  }
  return out
}

// ---------- selection counter ----------

export const counterLabel = (count: number, max: number) => `נבחרו ${count} מתוך ${max}`
export const limitHint = (max: number) => `אפשר עד ${max}`

export function toggleSelection(picked: string[], id: string, max: number): string[] {
  if (picked.includes(id)) return picked.filter((x) => x !== id)
  return picked.length < max ? [...picked, id] : picked
}

/** A card that is not selected is blocked once the limit is reached. */
export const isBlocked = (picked: string[], id: string, max: number) => !picked.includes(id) && picked.length >= max

// ---------- composer placeholder ----------

const EXAMPLES: Record<string, string> = {
  occupation: 'מנהלת מוצר, או: קבוצה ג',
  group: 'ג',
  base: '18500',
  commissions: 'עמלות 1,000, או: אין',
  bonus: 'בונוס 500, או: אין',
  pension_percent: 'פנסיה 6%',
  study_fund_percent: 'קרן השתלמות 2.5%, או: אין',
  credit_points: 'נקודות זיכוי 2.75',
  children: '2, או: אין',
  car: 'קורולה קרוס 2023',
  use_value: '4000, או: לא ידוע',
  copay: '300, או: לא ידוע',
  maintenance: '1500',
  options: '1, 3, או: הכי משתלם',
}

export function placeholderFor(expects: Expects | null): string {
  const fields = fieldsOf(expects)
  if (!fields.length) return 'כתבו הודעה…'
  const example = isQ2Question(expects) ? EXAMPLES.pension_percent
    : fields.map((f) => (f.field ? EXAMPLES[f.field] : undefined)).find((x) => x !== undefined)
  if (!example) return 'כתבו הודעה…'
  return widgetsFor(expects).length || isQ2Question(expects) ? `או כתבו כאן, למשל: ${example}` : `כתבו כאן, למשל: ${example}`
}
