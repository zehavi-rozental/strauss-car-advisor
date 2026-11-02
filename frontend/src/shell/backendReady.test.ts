import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { ReadinessProbe, type Health, type Readiness } from './backendReady'

const OK: Health = { status: 'ok', db: true, engine: 'untried' }

function probe(answers: Array<Health | Error | 'hang'>, extra: Partial<ConstructorParameters<typeof ReadinessProbe>[0]> = {}) {
  let calls = 0
  let concurrent = 0
  let maxConcurrent = 0
  const states: Readiness[] = []
  const p = new ReadinessProbe({
    fetchHealth: (signal) => {
      const answer = answers[Math.min(calls, answers.length - 1)]
      calls += 1
      concurrent += 1
      maxConcurrent = Math.max(maxConcurrent, concurrent)
      const done = () => { concurrent -= 1 }
      if (answer === 'hang') {
        return new Promise((_, reject) => signal.addEventListener('abort', () => { done(); reject(new ApiError(0, 'aborted')) }))
      }
      done()
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer)
    },
    visible: () => true,
    now: () => Date.now(),
    ...extra,
  })
  p.subscribe((s) => states.push(s))
  return { p, states, calls: () => calls, maxConcurrent: () => maxConcurrent }
}

const waking = () => new ApiError(503, 'not ready', {}, null, true)

describe('the readiness probe (the static page waits for the server and its database)', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('an awake server: ready at once, no notice ever shown', async () => {
    const t = probe([OK])
    t.p.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(t.states).toEqual(['ready'])
    await vi.advanceTimersByTimeAsync(5_000)
    expect(t.calls()).toBe(1)
  })

  it('a sleeping server: "waking", one request at a time with growing pauses, then ready by itself', async () => {
    const t = probe([waking(), waking(), waking(), OK])
    t.p.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(t.p.state).toBe('waking')
    await vi.advanceTimersByTimeAsync(999)
    expect(t.calls()).toBe(1)                     // the first pause is 1 s
    await vi.advanceTimersByTimeAsync(1)
    expect(t.calls()).toBe(2)
    await vi.advanceTimersByTimeAsync(2_000)      // then 2 s
    expect(t.calls()).toBe(3)
    await vi.advanceTimersByTimeAsync(4_000)      // then 4 s: the fourth answer is ok
    expect(t.calls()).toBe(4)
    expect(t.states).toEqual(['waking', 'ready'])
    expect(t.maxConcurrent()).toBe(1)
  })

  it('a slow first answer shows the notice only after the quiet time', async () => {
    const t = probe(['hang', OK], { attemptTimeoutMs: 15_000 })
    t.p.start()
    await vi.advanceTimersByTimeAsync(1_499)
    expect(t.p.state).toBe('checking')
    await vi.advanceTimersByTimeAsync(1)
    expect(t.p.state).toBe('waking')
    await vi.advanceTimersByTimeAsync(15_000 + 1_000)   // the hung request is aborted, the next one answers
    expect(t.p.state).toBe('ready')
    expect(t.maxConcurrent()).toBe(1)
  })

  it('the server answers but its database does not: unavailable at once, never "waking"', async () => {
    const t = probe([{ status: 'degraded', db: false }])
    t.p.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(t.states).toEqual(['unavailable'])
    await vi.advanceTimersByTimeAsync(60_000)
    expect(t.calls()).toBe(1)                     // no automatic retries: the button decides
  })

  it('a platform 429 (the edge daily limit) is "busy" and stops; retry() starts again', async () => {
    const t = probe([new ApiError(429, 'busy', {}, null, true), OK])
    t.p.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(t.p.state).toBe('busy')
    await vi.advanceTimersByTimeAsync(60_000)
    expect(t.calls()).toBe(1)
    t.p.retry()
    await vi.advanceTimersByTimeAsync(0)
    expect(t.p.state).toBe('ready')
  })

  it('nothing answers within the budget: unavailable, and "לנסות שוב" gets a new budget', async () => {
    const t = probe([waking()], { budgetMs: 20_000 })
    t.p.start()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(t.p.state).toBe('unavailable')
    const before = t.calls()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(t.calls()).toBe(before)                // stopped
    t.p.retry()
    await vi.advanceTimersByTimeAsync(0)
    expect(t.calls()).toBe(before + 1)
    expect(t.p.state).toBe('waking')              // tried again at once, still asleep: the notice is back
  })

  it('start() twice never sends two requests; a hidden tab sends nothing until it is visible', async () => {
    let visible = false
    const t = probe([OK], { visible: () => visible })
    t.p.start()
    t.p.start()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(t.calls()).toBe(0)
    visible = true
    t.p.resume()
    await vi.advanceTimersByTimeAsync(0)
    expect(t.calls()).toBe(1)
    expect(t.p.state).toBe('ready')
  })
})
