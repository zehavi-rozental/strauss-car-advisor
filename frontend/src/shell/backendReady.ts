// Is the server ready? The page itself is static (served instantly, also while the Render service sleeps), so
// the chat waits for this: one /api/health request at a time, with a timeout and a growing pause between tries,
// until the server answers AND its database is reachable. Nothing is promised about how long a wake-up takes.
import { useEffect, useState } from 'react'

import { ApiError, api } from '../api/client'
import type { EngineState } from '../api/types'

/** checking: the first answer is awaited (nothing is shown yet, so an awake server never flashes a notice).
 *  waking: no usable answer yet (the server is starting). ready: the API and the database answer.
 *  unavailable: the database is not reachable, or nothing answered within the budget. busy: a platform limit. */
export type Readiness = 'checking' | 'waking' | 'ready' | 'unavailable' | 'busy'

export interface Health { status?: string; db?: boolean; engine?: EngineState }

export interface ProbeOptions {
  fetchHealth: (signal: AbortSignal) => Promise<Health>
  visible?: () => boolean
  now?: () => number
  attemptTimeoutMs?: number   // one request
  quietMs?: number            // how long "checking" may last before the notice appears
  budgetMs?: number           // then "unavailable" and a button to try again
  delaysMs?: number[]         // between tries; the last one repeats
}

const DEFAULTS = { attemptTimeoutMs: 15_000, quietMs: 1_500, budgetMs: 180_000, delaysMs: [1_000, 2_000, 4_000, 8_000, 10_000] }

export class ReadinessProbe {
  state: Readiness = 'checking'
  health: Health | null = null
  private readonly o: Required<ProbeOptions>
  private listeners = new Set<(s: Readiness) => void>()
  private inFlight = false
  private attempt = 0
  private startedAt = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private quietTimer: ReturnType<typeof setTimeout> | null = null
  private waitingForVisible = false

  constructor(options: ProbeOptions) {
    this.o = {
      visible: () => typeof document === 'undefined' || document.visibilityState === 'visible',
      now: () => Date.now(),
      ...DEFAULTS,
      ...options,
    } as Required<ProbeOptions>
  }

  subscribe(fn: (s: Readiness) => void): () => void {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }

  /** Starts probing once; later calls while it runs (or once ready) do nothing. */
  start(): void {
    if (this.state === 'ready' || this.inFlight || this.timer || this.waitingForVisible) return
    if (this.state === 'unavailable' || this.state === 'busy') return    // only retry() starts again
    this.begin()
  }

  /** "לנסות שוב": a new budget, starting now. */
  retry(): void {
    if (this.state === 'ready' || this.inFlight) return
    this.clearTimers()
    this.set('checking')
    this.begin()
  }

  /** The tab became visible again: a probe waiting for it continues. */
  resume(): void {
    if (!this.waitingForVisible) return
    this.waitingForVisible = false
    void this.tick()
  }

  dispose(): void {
    this.clearTimers()
    this.listeners.clear()
  }

  private begin(): void {
    this.attempt = 0
    this.startedAt = this.o.now()
    this.quietTimer = setTimeout(() => { if (this.state === 'checking') this.set('waking') }, this.o.quietMs)
    void this.tick()
  }

  private async tick(): Promise<void> {
    this.timer = null
    if (!this.o.visible()) { this.waitingForVisible = true; return }   // nothing is sent from a hidden tab
    this.inFlight = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), this.o.attemptTimeoutMs)
    let outcome: 'ready' | 'db_down' | 'busy' | 'again'
    try {
      const h = await this.o.fetchHealth(controller.signal)
      this.health = h
      outcome = h.status === 'ok' && h.db === true ? 'ready' : 'db_down'
    } catch (e) {
      // a platform 429 (the edge's daily limit) is not a wake-up: waiting seconds would not help
      outcome = e instanceof ApiError && e.status === 429 ? 'busy' : 'again'
    } finally {
      clearTimeout(timeout)
      this.inFlight = false
    }
    if (outcome === 'ready') { this.clearTimers(); this.set('ready'); return }
    if (outcome === 'busy') { this.clearTimers(); this.set('busy'); return }
    if (outcome === 'db_down') { this.clearTimers(); this.set('unavailable'); return }   // the server answers: no wake-up to wait for
    if (this.state === 'checking') this.set('waking')
    const delay = this.o.delaysMs[Math.min(this.attempt, this.o.delaysMs.length - 1)]
    this.attempt += 1
    if (this.o.now() - this.startedAt + delay > this.o.budgetMs) { this.clearTimers(); this.set('unavailable'); return }
    this.timer = setTimeout(() => void this.tick(), delay)
  }

  private clearTimers(): void {
    if (this.timer) clearTimeout(this.timer)
    if (this.quietTimer) clearTimeout(this.quietTimer)
    this.timer = null
    this.quietTimer = null
    this.waitingForVisible = false
  }

  private set(state: Readiness): void {
    if (this.state === state) return
    this.state = state
    for (const fn of this.listeners) fn(state)
  }
}

// one probe for the whole page: the employee chat and the rep screen share it (never two health requests at once)
let shared: ReadinessProbe | null = null

function sharedProbe(): ReadinessProbe {
  if (!shared) {
    shared = new ReadinessProbe({ fetchHealth: (signal) => api.probeHealth(signal) })
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') shared?.resume() })
    }
  }
  return shared
}

export function useBackendReady(): { state: Readiness; ready: boolean; health: Health | null; retry: () => void } {
  const probe = sharedProbe()
  const [state, setState] = useState<Readiness>(probe.state)
  useEffect(() => {
    const unsubscribe = probe.subscribe(setState)   // the first state came from useState(probe.state)
    probe.start()
    return unsubscribe
  }, [probe])
  return { state, ready: state === 'ready', health: probe.health, retry: () => probe.retry() }
}
