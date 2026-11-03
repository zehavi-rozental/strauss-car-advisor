import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api/client'
import { ServiceStatus } from './ServiceStatus'

const html = (state: Parameters<typeof ServiceStatus>[0]['state']) =>
  renderToStaticMarkup(<ServiceStatus state={state} onRetry={() => {}} />)

describe('the readiness line', () => {
  it('says nothing while checking or once ready', () => {
    expect(html('checking')).toBe('')
    expect(html('ready')).toBe('')
  })

  it('while the server starts: a quiet notice, no countdown and no button', () => {
    const out = html('waking')
    expect(out).toContain('מכינים את יועץ הרכב. אפשר בינתיים להכיר את המסך.')
    expect(out).toContain('role="status"')
    expect(out).not.toMatch(/\d/)                // no time is promised
    expect(out).not.toContain('<button')
  })

  it('unavailable or busy: a friendly line and "לנסות שוב"', () => {
    expect(html('unavailable')).toContain('השירות אינו זמין כרגע')
    expect(html('busy')).toContain('יש כרגע עומס על השירות')
    expect(html('unavailable')).toContain('לנסות שוב')
  })
})

describe('an answer that is not JSON is never a success (a wake-up page or a platform error)', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('a 200 HTML page is a "not ready" error from the platform', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>waking up</html>', { status: 200, headers: { 'content-type': 'text/html' } })))
    const e = await api.health().catch((x) => x)
    expect(e).toBeInstanceOf(ApiError)
    expect(e.status).toBe(503)
    expect(e.fromPlatform).toBe(true)
  })

  it("the edge's JSON 503 carries its code and Hebrew message; it is not mistaken for the platform", async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'השרת מתעורר. נסו שוב בעוד רגע.', message_he: 'השרת מתעורר. נסו שוב בעוד רגע.', edge: 'origin_waking', upstream_status: 429 }),
                                                       { status: 503, headers: { 'content-type': 'application/json', 'retry-after': '5' } })))
    const e = await api.health().catch((x) => x)
    expect(e.edge).toBe('origin_waking')
    expect(e.fromPlatform).toBe(false)
    expect(e.retryAfter).toBe(5)
    expect(e.message_he).toBe('השרת מתעורר. נסו שוב בעוד רגע.')
  })

  it("a platform 429 without JSON is 'busy'; the app's own 429 keeps its Hebrew message", async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('error code: 1027', { status: 429 })))
    const platform = await api.health().catch((x) => x)
    expect(platform.status).toBe(429)
    expect(platform.fromPlatform).toBe(true)
    expect(platform.message_he).toContain('עומס')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'יותר מדי שיחות חדשות. נסו שוב בעוד דקה.' }),
                                                       { status: 429, headers: { 'content-type': 'application/json' } })))
    const app = await api.createConversation().catch((x) => x)
    expect(app.fromPlatform).toBe(false)
    expect(app.message_he).toBe('יותר מדי שיחות חדשות. נסו שוב בעוד דקה.')
  })
})
