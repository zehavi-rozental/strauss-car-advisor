// The payslip review, as pure functions: one row per profile field, what the payslip says, what the
// conversation already knows, and whether the employee keeps it. Only checked rows are sent, through the
// same {fields} message as the manual form.
import type { ExtractedField, PayslipInfo, ProfileFields } from '../api/types'
import { formatMoney } from '../format/money'
import { BASE_SALARY_FIELD, FORM_FIELDS, parseNumber } from './formFields'
import type { FormFieldSpec } from './formFields'
import { NOT_ON_PAYSLIP } from './payslipOutcome'

type Key = keyof ProfileFields

export const PAYSLIP_FIELDS: FormFieldSpec[] = [BASE_SALARY_FIELD, ...FORM_FIELDS]

export interface ReviewRow {
  key: Key
  label: string
  unit: '₪' | '%' | ''
  integer: boolean
  value: string            // the input
  keep: boolean            // checked: sent on confirm
  status: 'found' | 'missing' | 'ambiguous'
  source: string           // "בתלוש: שכר יסוד"
  note: string
  known: string | null     // what the conversation already has, for display
  differs: boolean         // the payslip says something else than the conversation
}

const asInput = (v: string | null | undefined): string => {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  return Number.isFinite(n) ? String(n) : ''
}

const show = (spec: FormFieldSpec, v: string): string =>
  spec.unit === '₪' ? formatMoney(Number(v)) : spec.unit === '%' ? `${Number(v)}%` : String(Number(v))

/** Rows for the review. A value is kept by default only when it was found with confidence and does not
 *  contradict what the employee already said; anything else waits for the employee's decision. */
export function reviewRows(fields: ExtractedField[], profile: Record<string, unknown> = {}): ReviewRow[] {
  const byField = new Map(fields.map((f) => [f.field, f]))
  return PAYSLIP_FIELDS.map((spec) => {
    const ex = byField.get(spec.key)
    const status = ex ? (ex.status ?? (ex.value === null ? 'missing' : 'found')) : 'missing'
    const value = status === 'found' ? asInput(ex?.value) : ''
    const knownRaw = profile[spec.profileKey]
    const known = knownRaw === null || knownRaw === undefined || knownRaw === '' ? null : String(knownRaw)
    const differs = value !== '' && known !== null && Number(known) !== Number(value)
    let note = ex?.note ?? ''
    if (status === 'missing') note = NOT_ON_PAYSLIP
    if (status === 'ambiguous' && ex?.value !== null && ex?.value !== undefined && ex.value !== '')
      note = `${note} (בתלוש: ${show(spec, ex.value)})`
    return {
      key: spec.key, label: spec.label, unit: spec.unit, integer: Boolean(spec.integer), value,
      keep: status === 'found' && Boolean(ex?.confident) && !differs,
      status, note,
      source: ex?.source_label ? `בתלוש: ${ex.source_label}` : '',
      known: known === null ? null : show(spec, known),
      differs,
    }
  })
}

export type ReviewBuild = { fields: ProfileFields; errors: Partial<Record<Key, string>> }

/** What to send: the checked rows only; a checked row must hold a valid number. */
export function reviewFields(rows: ReviewRow[]): ReviewBuild {
  const fields: ProfileFields = {}
  const errors: ReviewBuild['errors'] = {}
  for (const r of rows) {
    if (!r.keep) continue
    const raw = r.value.trim()
    if (raw === '') {
      errors[r.key] = 'אין ערך: מלאו או בטלו את הסימון'
      continue
    }
    const n = parseNumber(raw, r.integer)
    if (n === null) {
      errors[r.key] = r.integer ? 'מספר שלם, 0 או יותר' : 'מספר, 0 או יותר'
      continue
    }
    fields[r.key] = n
  }
  return { fields, errors }
}

/** "תוספת חודשית" can be assigned to commissions or bonus by the employee, only into an empty row:
 *  the amount is copied as printed, never added to another value. */
export function assignable(info: PayslipInfo, rows: ReviewRow[]): Key[] {
  if (info.key !== 'other_addition') return []
  return (['commissions', 'bonus'] as Key[]).filter((k) => rows.find((r) => r.key === k)?.value === '')
}

export function assign(rows: ReviewRow[], key: Key, info: PayslipInfo): ReviewRow[] {
  return rows.map((r) => (r.key === key
    ? { ...r, value: asInput(info.value), keep: true, source: info.source_label ? `בתלוש: ${info.source_label}` : '',
        note: 'שויך על ידיכם' }
    : r))
}
