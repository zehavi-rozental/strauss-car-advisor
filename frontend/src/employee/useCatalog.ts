// The company catalog by code, loaded once when the options question appears. Display details only (use value,
// catalog copay); nothing is calculated from it, and the cards work without it if the request fails.
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { CatalogItem } from '../api/types'

export function useCatalog(needed: boolean): Record<string, CatalogItem> {
  const [catalog, setCatalog] = useState<Record<string, CatalogItem>>({})
  const load = needed && Object.keys(catalog).length === 0
  useEffect(() => {
    if (!load) return
    let live = true
    api.catalog().then((rows) => { if (live) setCatalog(Object.fromEntries(rows.map((r) => [r.code, r]))) }).catch(() => undefined)
    return () => { live = false }
  }, [load])
  return catalog
}

/** A conversation stored in this browser opens straight into the consultation (never flashing the opening). */
export function hasStoredConversation(): boolean {
  try {
    return Boolean(localStorage.getItem('sca.conversationId'))
  } catch {
    return false
  }
}
