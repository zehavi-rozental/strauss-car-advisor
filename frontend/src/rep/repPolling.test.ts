import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, retryAfterSeconds } from '../api/client'
import type { RepQuestion } from '../api/types'
import { OTHER_MS, POLL_MS, RepPoller, type RepChannel, type RepMessage } from './repPolling'

const q = (id: string) => ({ id }) as unknown as RepQuestion

/** Tabs of one browser: what one posts, the others receive (never the sender, as with BroadcastChannel). */
function bus() {
  const handlers = new Set<(m: RepMessage) => void>()
  return (): RepChannel => {
    let mine: ((m: RepMessage) => void) | undefined
    return {
      post: (m) => { for (const h of handlers) if (h !== mine) h(m) },
      listen: (h) => { mine = h; handlers.add(h); return () => handlers.delete(h) },
    }
  }
}

function setup(overrides: Partial<{ open: () => Promise<RepQuestion[]>; answered: () => Promise<RepQuestion[]>;
                                    channel: RepChannel }> = {}) {
  const calls = { open: 0, answered: 0 }
  const seen = { open: [] as RepQuestion[][], answered: [] as RepQuestion[][] }
  const onPause = vi.fn()
  const onUnauthorized = vi.fn()
  const onError = vi.fn()
  const poller = new RepPoller({
    fetchOpen: () => { calls.open++; return overrides.open ? overrides.open() : Promise.resolve([q('o1')]) },
    fetchAnswered: () => { calls.answered++; return overrides.answered ? overrides.answered() : Promise.resolve([q('a1')]) },
    onOpen: (x) => seen.open.push(x),
    onAnswered: (x) => seen.answered.push(x),
    onLoaded: () => {},
    onError, onUnauthorized, onPause,
    channel: overrides.channel,
  })
  return { poller, calls, seen, onPause, onUnauthorized, onError }
}

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

describe('the rep screen asks the server for little (QA 22/10: 40 requests a minute used up the limit)', () => {
  it('one minute on the open tab: the open list every 5 s, the answered one once a minute', async () => {
    const { poller, calls } = setup()
    poller.start()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(calls).toEqual({ open: 1 + 60_000 / POLL_MS, answered: 2 })    // 13 + 2, was 20 + 20
    poller.dispose()
  })

  it('ten minutes stay at about 13 requests a minute, far from the 45 the server allows for reads', async () => {
    const { poller, calls } = setup()
    poller.start()
    await vi.advanceTimersByTimeAsync(10 * 60_000)
    const perMinute = (calls.open + calls.answered) / 10
    expect(perMinute).toBeLessThanOrEqual(13.5)
    expect(perMinute * 3).toBeLessThan(45)                                 // three tabs still fit
    poller.dispose()
  })

  it('a 409 (another rep answered first) refreshes both lists once; an answer itself asks nothing', async () => {
    const { poller, calls } = setup()
    poller.start()
    await vi.advanceTimersByTimeAsync(1_000)
    const before = { ...calls }
    poller.refreshAll()
    await vi.advanceTimersByTimeAsync(0)
    expect(calls).toEqual({ open: before.open + 1, answered: before.answered + 1 })
    poller.dispose()
  })
})

describe('answered questions stay correct', () => {
  it('opening the answered tab loads it at once, and then it is the list polled', async () => {
    const { poller, calls } = setup()
    poller.start()
    await vi.advanceTimersByTimeAsync(30_000)
    poller.setTab('answered')
    await vi.advanceTimersByTimeAsync(0)
    expect(calls.answered).toBe(2)                                          // start + the switch
    await vi.advanceTimersByTimeAsync(3 * POLL_MS)
    expect(calls.answered).toBe(5)
    poller.dispose()
  })

  it("another rep's answer leaves the open list within one poll", async () => {
    let open = [q('o1'), q('o2')]
    const { poller, seen } = setup({ open: () => Promise.resolve(open) })
    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    open = [q('o2')]                                                        // answered elsewhere
    await vi.advanceTimersByTimeAsync(POLL_MS)
    expect(seen.open.at(-1)).toEqual([q('o2')])
    poller.dispose()
  })

  it('the other list is refreshed at least once a minute (the header counts)', async () => {
    let answered = [q('a1')]
    const { poller, seen } = setup({ answered: () => Promise.resolve(answered) })
    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    answered = [q('a1'), q('a2')]
    await vi.advanceTimersByTimeAsync(OTHER_MS)
    expect(seen.answered.at(-1)).toEqual([q('a1'), q('a2')])
    poller.dispose()
  })

  it('a page reload loads both lists again', async () => {
    const first = setup()
    first.poller.start()
    first.poller.dispose()
    const again = setup()
    again.poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(again.calls).toEqual({ open: 1, answered: 1 })
    again.poller.dispose()
  })
})

describe('a 429 pauses the polling for its Retry-After', () => {
  it('nothing is asked while paused, then it resumes and says so', async () => {
    let refuse = true
    const { poller, calls, onPause } = setup({
      open: () => (refuse ? Promise.reject(new ApiError(429, 'יותר מדי בקשות', {}, 20)) : Promise.resolve([q('o1')])),
    })
    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(onPause).toHaveBeenLastCalledWith(20)
    const during = { ...calls }
    refuse = false
    await vi.advanceTimersByTimeAsync(19_000)
    expect(calls).toEqual(during)                                           // silent for the whole pause
    poller.refreshAll()                                                     // not even after an answer
    await vi.advanceTimersByTimeAsync(0)
    expect(calls).toEqual(during)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(onPause).toHaveBeenLastCalledWith(null)
    expect(calls.open).toBe(during.open + 1)                                // resumed with a fresh load
    poller.dispose()
  })

  it('a 429 with no Retry-After waits a minute', async () => {
    const { poller, onPause } = setup({ open: () => Promise.reject(new ApiError(429, 'יותר מדי בקשות')) })
    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(onPause).toHaveBeenLastCalledWith(60)
    poller.dispose()
  })

  it('a pause that ends while the tab is hidden resumes when it is shown again', async () => {
    let refuse = true
    const { poller, calls, onPause } = setup({
      open: () => (refuse ? Promise.reject(new ApiError(429, 'x', {}, 5)) : Promise.resolve([])),
    })
    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    refuse = false
    poller.stop()                                                           // hidden
    await vi.advanceTimersByTimeAsync(10_000)
    expect(onPause).toHaveBeenLastCalledWith(null)
    const hidden = { ...calls }
    poller.start()                                                          // shown again
    await vi.advanceTimersByTimeAsync(0)
    expect(calls.open).toBe(hidden.open + 1)
    poller.dispose()
  })
})

describe('tabs of the same browser share the work', () => {
  it('three tabs for a minute cost about what one tab costs', async () => {
    const channel = bus()
    const tabs = [setup({ channel: channel() }), setup({ channel: channel() }), setup({ channel: channel() })]
    for (const [i, t] of tabs.entries()) {
      t.poller.start()
      await vi.advanceTimersByTimeAsync(1_000 + i * 700)                    // opened one after the other
    }
    await vi.advanceTimersByTimeAsync(60_000)
    const total = tabs.reduce((n, t) => n + t.calls.open + t.calls.answered, 0)
    expect(total).toBeLessThanOrEqual(18)                                   // one tab: ~15; three alone: ~45
    for (const t of tabs) expect(t.seen.open.length).toBeGreaterThanOrEqual(12)    // every tab stays fresh
    for (const t of tabs) t.poller.dispose()
  })

  it('a 429 in one tab pauses the others too', async () => {
    const channel = bus()
    let refuse = false
    const a = setup({ channel: channel(),
                      open: () => (refuse ? Promise.reject(new ApiError(429, 'x', {}, 20)) : Promise.resolve([])) })
    const b = setup({ channel: channel() })
    b.poller.start()
    await vi.advanceTimersByTimeAsync(14_600)              // b fetched at 10 s: 4.6 s ago, so a fetches itself
    refuse = true
    a.poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(b.onPause).toHaveBeenLastCalledWith(20)
    const quiet = { ...b.calls }
    await vi.advanceTimersByTimeAsync(15_000)
    expect(b.calls).toEqual(quiet)
    a.poller.dispose()
    b.poller.dispose()
  })

  it('after a pause a tab comes back gently: the list on screen only, never sooner than 5 s', async () => {
    let refuse = true
    const { poller, calls, onPause } = setup({
      open: () => (refuse ? Promise.reject(new ApiError(429, 'x', {}, 1)) : Promise.resolve([])),
    })
    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(onPause).toHaveBeenLastCalledWith(5)                             // Retry-After 1 is stretched to 5
    refuse = false
    const during = { ...calls }
    await vi.advanceTimersByTimeAsync(5_000)
    expect(calls).toEqual({ open: during.open + 1, answered: during.answered })
    poller.dispose()
  })
})

describe('after a pause', () => {
  it('a list that was never loaded is loaded together with the one on screen (the header is never left at "—")',
     async () => {
    let refuse = true
    const { poller, calls, seen } = setup({
      answered: () => (refuse ? Promise.reject(new ApiError(429, 'x', {}, 10)) : Promise.resolve([q('a1')])),
    })
    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(seen.answered).toHaveLength(0)
    refuse = false
    await vi.advanceTimersByTimeAsync(10_000)
    expect(seen.answered).toHaveLength(1)
    expect(calls.answered).toBe(2)
    poller.dispose()
  })
})

describe('Retry-After from the server', () => {
  it('is read as whole seconds, and ignored when it is not a positive number', () => {
    expect([retryAfterSeconds('20'), retryAfterSeconds('1.2'), retryAfterSeconds('0'), retryAfterSeconds('abc'),
            retryAfterSeconds(null)]).toEqual([20, 2, null, null, null])
  })
})

describe('other cases', () => {
  it('a hidden tab asks nothing', async () => {
    const { poller, calls } = setup()
    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    poller.stop()
    const hidden = { ...calls }
    await vi.advanceTimersByTimeAsync(5 * 60_000)
    expect(calls).toEqual(hidden)
    poller.dispose()
  })

  it('a slow answer is never stacked with the next poll', async () => {
    const { poller, calls } = setup({ open: () => new Promise((resolve) => setTimeout(() => resolve([]), 12_000)) })
    poller.start()
    await vi.advanceTimersByTimeAsync(11_000)
    expect(calls.open).toBe(1)                                              // the 5 s and 10 s ticks waited
    poller.dispose()
  })

  it('a 401 logs out, any other error is shown', async () => {
    const a = setup({ open: () => Promise.reject(new ApiError(401, 'x')) })
    a.poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(a.onUnauthorized).toHaveBeenCalled()
    a.poller.dispose()
    const b = setup({ open: () => Promise.reject(new ApiError(500, 'x')) })
    b.poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(b.onError).toHaveBeenCalled()
    b.poller.dispose()
  })
})
