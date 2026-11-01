import { beforeEach, describe, expect, it } from 'vitest'
import { placeAnchor, readAnchor } from './anchors'

// the unit tests run without a browser: a small stand-in for the browser's storage
const memory = new Map<string, string>()
Object.assign(globalThis, { localStorage: {
  getItem: (k: string) => memory.get(k) ?? null, setItem: (k: string, v: string) => void memory.set(k, v),
  removeItem: (k: string) => void memory.delete(k), clear: () => memory.clear(),
} })

describe('a card keeps its message across a reload (1/11)', () => {
  beforeEach(() => localStorage.clear())
  it('the same content keeps its first message; new content takes the new one; another conversation starts over', () => {
    const first = placeAnchor('results', 'c1', 'run-a', 3, null)
    expect(first.seq).toBe(3)
    expect(placeAnchor('results', 'c1', 'run-a', 7, null).seq).toBe(3)       // a reload after an inquiry (seq 7)
    expect(placeAnchor('results', 'c1', 'run-b', 9, first).seq).toBe(9)      // recalculated: under that message
    expect(readAnchor('results', 'c2')).toBeNull()
  })
})
