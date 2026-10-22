// How the rep screen keeps its two lists fresh without using up the server's rate limit (QA 22/10).
// Before: both lists every 3 s (40 requests a minute) plus 3 per answer, against 60 a minute per address,
// so ~7 actions in a minute got "יותר מדי בקשות". Now: the list on screen every 5 s, the other one every 60 s
// (and when its tab opens); an answer updates both lists from the server's reply (RepPage), so it costs one
// request; a 409 refreshes both; a 429 pauses everything for its Retry-After. Tabs of the same
// browser share what they fetch and their pauses (a channel), so three open tabs cost about as much as one.
import { ApiError } from '../api/client'
import type { RepQuestion } from '../api/types'

export const POLL_MS = 5000          // the list on screen
export const OTHER_MS = 60000        // the other list (its count in the header, "נענו לאחרונה")
export const DEFAULT_PAUSE_S = 60    // a 429 without Retry-After
export const MIN_PAUSE_S = 5         // never back sooner than one poll: a short Retry-After would only bounce
const FRESH_MS = POLL_MS - 1000      // a list another tab fetched this recently is not fetched again

export type RepTab = 'open' | 'answered'

/** What tabs of the same browser tell each other (BroadcastChannel in the page; a fake one in tests). */
export type RepMessage =
  | { type: 'list'; which: RepTab; at: number; questions: RepQuestion[] }
  | { type: 'pause'; until: number }

export interface RepChannel {
  post: (message: RepMessage) => void
  listen: (handler: (message: RepMessage) => void) => () => void
}

export interface RepPollerDeps {
  fetchOpen: () => Promise<RepQuestion[]>
  fetchAnswered: () => Promise<RepQuestion[]>
  onOpen: (questions: RepQuestion[]) => void
  onAnswered: (questions: RepQuestion[]) => void
  onLoaded: () => void                       // a list arrived: the error, if any, is over
  onError: (error: unknown) => void          // anything but 401 / 429
  onUnauthorized: () => void
  onPause: (seconds: number | null) => void  // a 429: polling waits; null when it resumes
  channel?: RepChannel
}

export class RepPoller {
  private tab: RepTab = 'open'
  private timer: ReturnType<typeof setInterval> | undefined
  private resumeTimer: ReturnType<typeof setTimeout> | undefined
  private fetchedAt = { open: -Infinity, answered: -Infinity }     // by this tab or another one
  private inFlight = { open: false, answered: false }
  private paused = false
  private running = false
  private unlisten: (() => void) | undefined
  private readonly deps: RepPollerDeps
  private readonly now: () => number

  constructor(deps: RepPollerDeps, now: () => number = Date.now) {
    this.deps = deps
    this.now = now
    this.unlisten = deps.channel?.listen((m) => this.receive(m))
  }

  /** The screen opened (or became visible again): both lists (unless another tab has them fresh), then polling. */
  start(): void {
    this.running = true
    if (this.paused) return
    void this.load('open')
    void this.load('answered')
    this.schedule()
  }

  /** The tab is hidden: no requests at all. A pause in progress still ends on time (it only clears a flag). */
  stop(): void {
    this.running = false
    this.clear()
  }

  /** The screen closed: nothing is left scheduled or listening. */
  dispose(): void {
    this.stop()
    if (this.resumeTimer !== undefined) clearTimeout(this.resumeTimer)
    this.resumeTimer = undefined
    this.unlisten?.()
    this.unlisten = undefined
  }

  /** The tab on screen changed: its list now, unless it is fresh; then it is the one polled. */
  setTab(tab: RepTab): void {
    if (tab === this.tab) return
    this.tab = tab
    if (this.running && !this.paused) void this.load(tab)
  }

  /** After a 409 (another rep answered first): both lists now, fresh or not. */
  refreshAll(): void {
    if (!this.running || this.paused) return
    void this.load('open', true)
    void this.load('answered', true)
  }

  private other(): RepTab {
    return this.tab === 'open' ? 'answered' : 'open'
  }

  private schedule(): void {
    this.clear()
    this.timer = setInterval(() => this.tick(), POLL_MS)
  }

  private clear(): void {
    if (this.timer !== undefined) clearInterval(this.timer)
    this.timer = undefined
  }

  private tick(): void {
    void this.load(this.tab)
    if (this.now() - this.fetchedAt[this.other()] >= OTHER_MS) void this.load(this.other())
  }

  private receive(m: RepMessage): void {
    if (m.type === 'pause') {
      const seconds = Math.ceil((m.until - this.now()) / 1000)
      if (seconds > 0) this.pause(seconds, false)
      return
    }
    if (m.at <= this.fetchedAt[m.which]) return
    this.fetchedAt[m.which] = m.at
    if (!this.running) return
    if (m.which === 'open') this.deps.onOpen(m.questions)
    else this.deps.onAnswered(m.questions)
    this.deps.onLoaded()
  }

  private async load(which: RepTab, force = false): Promise<void> {
    if (this.inFlight[which] || this.paused) return       // a slow answer is never stacked with the next one
    if (!force && this.now() - this.fetchedAt[which] < FRESH_MS) return     // another tab just fetched it
    this.inFlight[which] = true
    try {
      const questions = await (which === 'open' ? this.deps.fetchOpen() : this.deps.fetchAnswered())
      const at = this.now()
      this.fetchedAt[which] = at
      this.deps.channel?.post({ type: 'list', which, at, questions })
      if (!this.running) return
      if (which === 'open') this.deps.onOpen(questions)
      else this.deps.onAnswered(questions)
      this.deps.onLoaded()
    } catch (e) {
      if (!this.running) return
      if (e instanceof ApiError && e.status === 401) this.deps.onUnauthorized()
      else if (e instanceof ApiError && e.status === 429) this.pause(e.retryAfter ?? DEFAULT_PAUSE_S, true)
      else this.deps.onError(e)
    } finally {
      this.inFlight[which] = false
    }
  }

  private pause(seconds: number, tellOthers: boolean): void {
    if (this.paused) return
    const wait = Math.max(seconds, MIN_PAUSE_S)
    this.paused = true
    this.clear()
    if (tellOthers) this.deps.channel?.post({ type: 'pause', until: this.now() + wait * 1000 })
    this.deps.onPause(wait)
    this.resumeTimer = setTimeout(() => {
      this.resumeTimer = undefined
      this.paused = false
      this.deps.onPause(null)
      if (!this.running) return                  // hidden meanwhile: the next start() (visible again) loads
      void this.load(this.tab, true)             // gently: the list on screen, then the usual polling;
      if (this.fetchedAt[this.other()] === -Infinity) void this.load(this.other(), true)   // and one never loaded
      this.schedule()
    }, wait * 1000)
  }
}

/** The page's channel between tabs; none where BroadcastChannel is missing (each tab then polls on its own). */
export function browserChannel(name = 'sca.rep'): RepChannel | undefined {
  if (typeof BroadcastChannel === 'undefined') return undefined
  const channel = new BroadcastChannel(name)
  return {
    post: (message) => channel.postMessage(message),
    listen: (handler) => {
      const onMessage = (e: MessageEvent<RepMessage>) => handler(e.data)
      channel.addEventListener('message', onMessage)
      return () => { channel.removeEventListener('message', onMessage); channel.close() }
    },
  }
}
