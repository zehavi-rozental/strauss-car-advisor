// What the current question shows, derived from `expects` only. Pure, so it is unit-tested.
// Free text is the primary input: open questions (group, salary, profile numbers, the car, the copay)
// get no widget at all — the bot asks in words and the employee types. Buttons appear only for closed
// questions (`closed` from the server: confirmations, clarifications, the car version, the drive,
// "לשאול את נציג התחבורה"). The car selection and the profile form open only from the reply's one link.
import type { Expects, ExpectsField, ExpectsLink, Stage } from '../api/types'

export const SELECT_THRESHOLD = 8
export type WidgetKind = 'buttons' | 'select'

export interface Widget {
  kind: WidgetKind
  field: ExpectsField
}

export function fieldsOf(expects: Expects | null): ExpectsField[] {
  if (!expects) return []
  return expects.fields.length ? expects.fields : expects.type !== 'none' ? [expects] : []
}

/** Buttons only for closed questions; an open question is answered by typing. */
export function widgetsFor(expects: Expects | null): Widget[] {
  return fieldsOf(expects)
    .filter((f) => f.closed && f.type === 'choice' && f.choices.length > 0)
    .map((f) => ({ kind: f.choices.length > SELECT_THRESHOLD ? 'select' : 'buttons', field: f }))
}

/** The one secondary link under the reply ("או למלא בטופס", "להציג את הרכבים בקבוצה שלך"), or null. */
export function linkFor(expects: Expects | null): ExpectsLink | null {
  return expects?.link ?? null
}

/** The options field, for the selection card the link opens. */
export function optionsField(expects: Expects | null): ExpectsField | null {
  return fieldsOf(expects).find((f) => f.field === 'options' && f.type === 'multi_choice') ?? null
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

/** The server mirrors the question on screen with an example ("למשל: קורולה קרוס 2025, משלמת 300"). */
export function placeholderFor(expects: Expects | null): string {
  return expects?.placeholder || 'כתבו הודעה…'
}

// ---------- progress ----------

export const PROGRESS_STEPS = ['פרטים', 'רכב היום', 'חלופות', 'תוצאה'] as const

/** The highlighted step, from the conversation stage. Waiting for the representative stays on the step
 *  it waits in: the result (a new car's copay) when results exist, otherwise the car today. */
export function progressStep(stage: Stage | null, hasResults = false): number {
  switch (stage) {
    case 'COLLECT_CURRENT_CAR':
      return 1
    case 'CHOOSE_OPTIONS':
    case 'CALCULATING':
      return 2
    case 'PRESENTED':
      return 3
    case 'WAITING_FOR_REP':
      return hasResults ? 3 : 1
    default:
      return 0
  }
}
