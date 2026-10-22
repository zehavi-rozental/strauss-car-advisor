import { afterEach, describe, expect, it, vi } from 'vitest'
import { repApi, repSessionId, resetRepSession } from './client'

// The server accepts a session id of 16-64 letters, digits or "-" (rep_routes._SESSION_RE); anything else is
// ignored and the answers are counted for the address.
const SERVER_FORMAT = /^[A-Za-z0-9-]{16,64}$/

function storage() {
  const data = new Map<string, string>()
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => { data.set(k, v) },
    removeItem: (k: string) => { data.delete(k) },
  }
}

function captureHeaders() {
  const seen: Record<string, string>[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
    seen.push(init.headers as Record<string, string>)
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
  return seen
}

afterEach(() => {
  vi.unstubAllGlobals()
  resetRepSession()
})

describe('the rep screen sends its session id (each rep gets their own answer budget)', () => {
  it('every rep request carries the same id, in the format the server accepts', async () => {
    vi.stubGlobal('sessionStorage', storage())
    const seen = captureHeaders()
    await repApi.listQuestions('tok', 'OPEN')
    await repApi.answer('tok', 'q1', { value: '300' })
    expect(seen.map((h) => h['X-Rep-Token'])).toEqual(['tok', 'tok'])
    expect(seen[0]['X-Rep-Session']).toMatch(SERVER_FORMAT)
    expect(seen[1]['X-Rep-Session']).toBe(seen[0]['X-Rep-Session'])
  })

  it('a login or a logout starts a new session', () => {
    vi.stubGlobal('sessionStorage', storage())
    const first = repSessionId()
    expect(repSessionId()).toBe(first)
    resetRepSession()
    const second = repSessionId()
    expect(second).not.toBe(first)
    expect(second).toMatch(SERVER_FORMAT)
  })

  it('two tabs (two storages) are two sessions', () => {
    vi.stubGlobal('sessionStorage', storage())
    const tabA = repSessionId()
    vi.stubGlobal('sessionStorage', storage())
    const tabB = repSessionId()
    expect(tabB).not.toBe(tabA)
  })

  it('without storage the page keeps one id in memory', () => {
    vi.stubGlobal('sessionStorage', undefined)
    const id = repSessionId()
    expect(repSessionId()).toBe(id)
    expect(id).toMatch(SERVER_FORMAT)
  })
})
