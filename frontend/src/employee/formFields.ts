// The Q2 / correction form, as pure functions. The server validates ranges and saves; the client only
// parses what was typed and writes the display text of the user's bubble (never sent as the message).
import type { ProfileFields } from '../api/types'
import { formatMoney } from '../format/money'

export type FormKey = keyof ProfileFields

export interface FormFieldSpec {
  key: FormKey
  label: string
  unit: '₪' | '%' | ''
  integer?: boolean
  /** the key in GET /conversations/{id} profile */
  profileKey: string
}

export const FORM_FIELDS: FormFieldSpec[] = [
  { key: 'commissions', label: 'עמלות', unit: '₪', profileKey: 'commissions' },
  { key: 'bonus', label: 'בונוס', unit: '₪', profileKey: 'bonus' },
  { key: 'pension_percent', label: 'פנסיה', unit: '%', profileKey: 'pension_percent' },
  { key: 'study_fund_percent', label: 'קרן השתלמות', unit: '%', profileKey: 'study_fund_percent' },
  { key: 'credit_points', label: 'נקודות זיכוי', unit: '', profileKey: 'credit_points' },
  { key: 'children_under_23', label: 'ילדים עד 23', unit: '', integer: true, profileKey: 'children' },
]

/** The field names the server uses in `expects` for these questions (same as the profile keys). */
export const Q2_FIELD_NAMES = new Set<string>(FORM_FIELDS.map((f) => f.profileKey))

export type FormValues = Record<FormKey, string>

export const emptyValues = (): FormValues =>
  Object.fromEntries(FORM_FIELDS.map((f) => [f.key, ''])) as FormValues

/** "2.5" / "2,5" → 2.5. Empty or not a non-negative number → null. */
export function parseNumber(text: string, integer = false): number | null {
  const t = text.trim().replace(',', '.')
  if (!/^\d+(\.\d+)?$/.test(t)) return null
  const n = Number(t)
  return integer && !Number.isInteger(n) ? null : n
}

/** Values already known to the server, as input strings (unknown → empty). */
export function prefill(profile: Record<string, unknown>): FormValues {
  const out = emptyValues()
  for (const f of FORM_FIELDS) {
    const v = profile[f.profileKey]
    if (v !== null && v !== undefined && v !== '') out[f.key] = String(Number(v))
  }
  return out
}

export type BuildResult = { fields: ProfileFields; errors: Partial<Record<FormKey, string>> }

/** What to send: empty fields are skipped; with `original` (correction) only changed fields are sent. */
export function buildFields(values: FormValues, original?: FormValues): BuildResult {
  const fields: ProfileFields = {}
  const errors: BuildResult['errors'] = {}
  for (const f of FORM_FIELDS) {
    const raw = values[f.key].trim()
    if (raw === '') continue
    const n = parseNumber(raw, f.integer)
    if (n === null) {
      errors[f.key] = f.integer ? 'מספר שלם, 0 או יותר' : 'מספר, 0 או יותר'
      continue
    }
    const before = original ? parseNumber(original[f.key], f.integer) : null
    if (original && before !== null && before === n) continue
    fields[f.key] = n
  }
  return { fields, errors }
}

const pct = (n: number) => `${n}%`

/** Display text for the user's bubble: "אין עמלות, בונוס 500 ₪, פנסיה 6%". Display only. */
export function profileFormText(fields: ProfileFields): string {
  const parts: string[] = []
  const f = fields
  if (f.base_salary !== undefined) parts.push(`שכר בסיס ${formatMoney(f.base_salary)}`)
  if (f.commissions !== undefined) parts.push(f.commissions === 0 ? 'אין עמלות' : `עמלות ${formatMoney(f.commissions)}`)
  if (f.bonus !== undefined) parts.push(f.bonus === 0 ? 'אין בונוס' : `בונוס ${formatMoney(f.bonus)}`)
  if (f.pension_percent !== undefined) parts.push(`פנסיה ${pct(f.pension_percent)}`)
  if (f.study_fund_percent !== undefined)
    parts.push(f.study_fund_percent === 0 ? 'אין קרן השתלמות' : `קרן השתלמות ${pct(f.study_fund_percent)}`)
  if (f.credit_points !== undefined) parts.push(`${f.credit_points} נקודות זיכוי`)
  if (f.children_under_23 !== undefined)
    parts.push(f.children_under_23 === 0 ? 'אין ילדים' : f.children_under_23 === 1 ? 'ילד אחד' : `${f.children_under_23} ילדים`)
  return parts.join(', ')
}
