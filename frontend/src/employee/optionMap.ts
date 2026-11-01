// STRAUSS DRIVE 360°: what the option map shows, as pure functions. Every number comes from the server (the
// engine's result per option); nothing here calculates money. Sorting and filtering only reorder what it sent.
import type { MapEntry, MapStatus, OptionMap, OptionResult } from '../api/types'

export const MAP_REQUEST = 'השווה את כל האפשרויות שלי'

/** What makes the map a new one (1/11): its options and their numbers. Being marked out of date is not: the card
 *  stays where it was, with its "לא מעודכן" note, until the rebuild moves it to the message that says so. */
export const mapKey = (map: OptionMap | null | undefined): string | null =>
  map ? JSON.stringify(map.entries.map((e) => [e.option_id, e.status, e.result.delta_display ?? null])) : null

export const NET_ONLY_NOTE = 'הדירוג מבוסס על הנטו החודשי המשוער בלבד. התאמת הרכב לצרכים שלכם עשויה להיות שונה.'
export const MAX_COMPARE = 3          // a display limit for the side-by-side table, never a limit on the list

export const STATUS_LABEL: Record<MapStatus, string> = {
  full: 'חושב במלואו',
  catalog_copay: 'חושב, ההשתתפות לפי הקטלוג',
  incomplete: 'ממתין לנתון',
  not_eligible: 'לא זמין לכם',
}

export type MapSort = 'net' | 'name'
export interface MapFilter { drive: string | null; group: string | null }

export const isRanked = (e: MapEntry) => e.status === 'full' || e.status === 'catalog_copay'

/** The calculated options (sorted by net or by name) and, separately, the ones waiting for a value. */
export function mapSections(map: OptionMap, filter: MapFilter, sort: MapSort):
  { ranked: MapEntry[]; waiting: MapEntry[]; notEligible: MapEntry[] } {
  const keep = (e: MapEntry) => (!filter.drive || e.drive === filter.drive) && (!filter.group || e.group === filter.group)
  const shown = map.entries.filter(keep)
  const ranked = shown.filter(isRanked)
  if (sort === 'name') ranked.sort((a, b) => a.label.localeCompare(b.label, 'he'))
  else ranked.sort((a, b) => Number(b.result.delta_display) - Number(a.result.delta_display))
  return {
    ranked,
    waiting: shown.filter((e) => e.status === 'incomplete'),
    notEligible: shown.filter((e) => e.status === 'not_eligible'),
  }
}

/** Filter values that exist in this map (no filter without data behind it). */
export function filterValues(map: OptionMap): { drives: string[]; groups: string[] } {
  const uniq = (xs: (string | null)[]) => [...new Set(xs.filter((x): x is string => Boolean(x)))]
  return { drives: uniq(map.entries.map((e) => e.drive)), groups: uniq(map.entries.map((e) => e.group)).sort() }
}

/** Toggle an option for the detailed comparison: at most MAX_COMPARE, only calculated ones. */
export function toggleCompare(picked: string[], entry: MapEntry): string[] {
  if (picked.includes(entry.option_id)) return picked.filter((id) => id !== entry.option_id)
  if (!isRanked(entry) || picked.length >= MAX_COMPARE) return picked
  return [...picked, entry.option_id]
}

/** The chosen entries in the shape the comparison table already takes. */
export function asResults(map: OptionMap, ids: string[]): OptionResult[] {
  return ids.map((id) => map.entries.find((e) => e.option_id === id))
    .filter((e): e is MapEntry => Boolean(e))
    .map((e) => ({ option_id: e.option_id, label: e.label, run_id: null, result: e.result }))
}

/** "מוביל בנטו המשוער" / a tie / nothing to rank: from the server's leaders only. */
export function leadText(map: OptionMap): string {
  if (map.stale) return 'המפה חושבה לפני השינוי האחרון ולכן אין בה דירוג עדכני.'
  const names = map.entries.filter((e) => map.leaders.includes(e.option_id)).map((e) => e.label)
  if (names.length === 1) return `הנטו החודשי הגבוה ביותר: ${names[0]}`
  if (names.length > 1) return `שוויון בנטו המשוער: ${names.join(', ')}`
  return 'אין עדיין חלופה שחושבה במלואה, ולכן אין דירוג.'
}
