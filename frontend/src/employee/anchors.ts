// Where a card belongs in the chat (1/11): the message that presented the results, or the option map. Kept on this
// device per conversation, so a reload (or the reload after an inquiry is sent) puts each card back under its own
// message instead of under whatever message came last — the inquiry that "jumped back to the comparisons".
export type CardAnchor = { key: string; seq: number }
type Kind = 'results' | 'map'

const STORE = 'sca.anchors'

function readAll(): { id?: string } & Partial<Record<Kind, CardAnchor>> {
  try {
    return JSON.parse(localStorage.getItem(STORE) ?? '{}')
  } catch {
    return {}
  }
}

export function readAnchor(kind: Kind, conversationId: string): CardAnchor | null {
  const all = readAll()
  return all.id === conversationId ? all[kind] ?? null : null
}

export function storeAnchor(kind: Kind, conversationId: string, anchor: CardAnchor): void {
  try {
    const all = readAll()
    const next = all.id === conversationId ? { ...all, [kind]: anchor } : { id: conversationId, [kind]: anchor }
    localStorage.setItem(STORE, JSON.stringify(next))        // the current conversation only
  } catch {
    // private mode: after a reload the card sits under the latest message
  }
}

/** The anchor for a card whose content has `key`: the one it already has, the stored one after a reload, or a new
 *  one at `seq` (the message that brought this content). */
export function placeAnchor(kind: Kind, conversationId: string, key: string, seq: number,
                            current: CardAnchor | null): CardAnchor {
  if (current?.key === key) return current
  const stored = readAnchor(kind, conversationId)
  const anchor = stored?.key === key ? stored : { key, seq }
  storeAnchor(kind, conversationId, anchor)
  return anchor
}
