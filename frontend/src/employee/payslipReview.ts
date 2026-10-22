// The payslip review, as pure functions: one row per profile field, what the payslip says, what the
// conversation already knows, and what the employee decided. Only rows chosen "from the payslip" are sent,
// through the same {fields} message as the manual form (source "payslip").
import type { ExtractedField, PayslipInfo, ProfileFields } from '../api/types'
import { formatMoney } from '../format/money'
import { BASE_SALARY_FIELD, FORM_FIELDS, parseNumber } from './formFields'
import type { FormFieldSpec } from './formFields'
import { NOT_ON_PAYSLIP } from './payslipOutcome'

type Key = keyof ProfileFields

export const PAYSLIP_FIELDS: FormFieldSpec[] = [BASE_SALARY_FIELD, ...FORM_FIELDS]

/** What the employee decided for a row. "payslip": update the profile with the payslip value (or what
 *  they typed); "chat": keep the value said in the conversation; "skip": do not update. null: not decided yet
 *  (a conflict, or nothing to compare with) — the review cannot be confirmed until it is. */
export type Choice = 'payslip' | 'chat' | 'skip'

export interface ReviewRow {
  key: Key
  label: string
  unit: '₪' | '%' | ''
  integer: boolean
  value: string            // the input (the payslip's value, editable)
  choice: Choice | null
  status: 'found' | 'missing' | 'ambiguous'
  source: string           // "בתלוש: שכר יסוד"
  note: string
  known: string | null     // what the conversation already has, for display
  differs: boolean         // the payslip says something else than the conversation: the employee chooses
  same: boolean            // the payslip says what the conversation already has
}

export const NOT_COMPARED = 'לא הצלחתי לבדוק מה רשום בשיחה. בחרו אם לעדכן.'
export const CHOOSE_FIRST = 'בחרו איזה ערך נכון לחישוב.'

const asInput = (v: string | null | undefined): string => {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  return Number.isFinite(n) ? String(n) : ''
}

export const show = (spec: { unit: '₪' | '%' | '' }, v: string): string =>
  spec.unit === '₪' ? formatMoney(Number(v)) : spec.unit === '%' ? `${Number(v)}%` : String(Number(v))

/** Rows for the review. profile: what the conversation holds now, or null when it could not be loaded.
 *  Nothing is updated without the employee's decision: a value that contradicts the conversation, or one
 *  that could not be compared, has no default; only a confident value for an empty field is preselected. */
export function reviewRows(fields: ExtractedField[], profile: Record<string, unknown> | null = {}): ReviewRow[] {
  const byField = new Map(fields.map((f) => [f.field, f]))
  return PAYSLIP_FIELDS.map((spec) => {
    const ex = byField.get(spec.key)
    const status = ex ? (ex.status ?? (ex.value === null ? 'missing' : 'found')) : 'missing'
    const value = status === 'found' ? asInput(ex?.value) : ''
    const knownRaw = profile ? profile[spec.profileKey] : undefined
    const known = knownRaw === null || knownRaw === undefined || knownRaw === '' ? null : String(knownRaw)
    const differs = value !== '' && known !== null && Number(known) !== Number(value)
    const same = value !== '' && known !== null && !differs
    let note = ex?.note ?? ''
    if (status === 'missing') note = NOT_ON_PAYSLIP
    if (status === 'ambiguous' && ex?.value !== null && ex?.value !== undefined && ex.value !== '')
      note = `${note} (בתלוש: ${show(spec, ex.value)})`
    if (value !== '' && profile === null) note = NOT_COMPARED
    let choice: Choice | null = 'skip'
    if (value !== '') {
      if (differs || profile === null) choice = null
      else if (same) choice = 'skip'
      else choice = status === 'found' && ex?.confident ? 'payslip' : 'skip'
    }
    return {
      key: spec.key, label: spec.label, unit: spec.unit, integer: Boolean(spec.integer), value, choice,
      status, note,
      source: ex?.source_label ? `בתלוש: ${ex.source_label}` : '',
      known: known === null ? null : show(spec, known),
      differs, same,
    }
  })
}

export type ReviewBuild = { fields: ProfileFields; errors: Partial<Record<Key, string>> }

/** What to send: the rows the employee chose to update from the payslip; each must hold a valid number.
 *  A row still undecided is an error: nothing is sent until every conflict is decided. */
export function reviewFields(rows: ReviewRow[]): ReviewBuild {
  const fields: ProfileFields = {}
  const errors: ReviewBuild['errors'] = {}
  for (const r of rows) {
    if (r.choice === null) {
      errors[r.key] = CHOOSE_FIRST
      continue
    }
    if (r.choice !== 'payslip') continue
    const raw = r.value.trim()
    if (raw === '') {
      errors[r.key] = 'אין ערך: מלאו אותו או בחרו "לא לעדכן"'
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

/** The choices a row offers: a conflict is "from the conversation" or "from the payslip"; anything else
 *  is "update" or "do not update". */
export function choicesFor(r: ReviewRow): { id: Choice; label: string }[] {
  if (r.differs && r.known !== null) {
    return [{ id: 'chat', label: `מהשיחה: ${r.known}` }, { id: 'payslip', label: `מהתלוש: ${show(r, r.value)}` }]
  }
  return [{ id: 'payslip', label: 'לעדכן' }, { id: 'skip', label: 'לא לעדכן' }]
}

/** "תוספת חודשית" can be assigned to commissions or bonus by the employee, only into an empty row:
 *  the amount is copied as printed, never added to another value. Only what the server marks assignable:
 *  a car copay or car maintenance never is, and an older server that does not say is not trusted. */
export function assignable(info: PayslipInfo, rows: ReviewRow[]): Key[] {
  if (info.key !== 'other_addition' || info.assignable !== true || info.suspect) return []
  return (['commissions', 'bonus'] as Key[]).filter((k) => rows.find((r) => r.key === k)?.value === '')
}

export function assign(rows: ReviewRow[], key: Key, info: PayslipInfo): ReviewRow[] {
  return rows.map((r) => (r.key === key
    ? { ...r, value: asInput(info.value), choice: 'payslip' as Choice, source: info.source_label ? `בתלוש: ${info.source_label}` : '',
        note: 'שויך על ידיכם' }
    : r))
}
