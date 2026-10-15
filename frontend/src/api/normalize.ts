import type { FlatOptionResult, OptionResult } from './types'

/** POST responses spread the ScenarioResult into the item; GET responses nest it under "result". */
export function normalizeResults(items: (OptionResult | FlatOptionResult)[]): OptionResult[] {
  return items.map((item) => {
    if ('result' in item && item.result) return item as OptionResult
    const { option_id, label, run_id, ...result } = item as FlatOptionResult
    return { option_id, label, run_id, result }
  })
}
