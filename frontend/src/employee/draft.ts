// An unsent message survives a trip to the representative area and back (this tab only, never sent anywhere).
const DRAFT_KEY = 'sca.draft'

export function readDraft(): string {
  try {
    return sessionStorage.getItem(DRAFT_KEY) ?? ''
  } catch {
    return ''
  }
}

export function writeDraft(value: string): void {
  try {
    if (value) sessionStorage.setItem(DRAFT_KEY, value)
    else sessionStorage.removeItem(DRAFT_KEY)
  } catch {
    // no storage: the draft lives only on screen
  }
}
