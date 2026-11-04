// STRAUSS DRIVE at the Cloudflare edge: the built React app is served from static assets (instantly, even while
// the Render service sleeps), and /api/* is passed to that service, so the browser sees one origin: the session
// cookie stays first-party (SameSite=Lax, no CORS) and the client keeps calling relative /api/... paths.
// wrangler.jsonc sends only /api requests here (run_worker_first); everything else never runs this code.
//
// Env: BACKEND_ORIGIN (e.g. https://strauss-drive.onrender.com), EDGE_PROXY_SECRET (a secret shared with the
// backend, which trusts X-Edge-Client-IP only with it: backend/app/api/ratelimit.client_address).

// Headers a visitor could write to choose the address the backend rate-limits on, or to pose as the edge.
const FORGEABLE = ['x-edge-client-ip', 'x-edge-secret', 'x-forwarded-for', 'x-forwarded-host',
  'x-forwarded-proto', 'x-real-ip', 'forwarded', 'true-client-ip']

export function isApiPath(pathname) {
  return pathname === '/api' || pathname.startsWith('/api/')
}

// A JSON answer the client can tell apart from the backend's own (`edge` is set only here). No upstream body,
// header or address is copied into it.
function edgeError(status, code, message_he, extra = {}) {
  return new Response(JSON.stringify({ detail: message_he, message_he, edge: code, ...extra }), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store',
               'x-edge-proxied': '1', 'retry-after': '5' },
  })
}

export async function proxy(request, env, fetchImpl = fetch) {
  const origin = (env.BACKEND_ORIGIN || '').replace(/\/+$/, '')
  if (!origin || !env.EDGE_PROXY_SECRET) {
    return edgeError(503, 'not_configured', 'השירות אינו זמין כרגע. נסו שוב בעוד כמה דקות.')
  }
  const url = new URL(request.url)
  const visitor = request.headers.get('cf-connecting-ip') || ''   // set by Cloudflare, not by the visitor
  const headers = new Headers(request.headers)
  for (const name of FORGEABLE) headers.delete(name)
  for (const name of [...headers.keys()]) if (name.startsWith('cf-')) headers.delete(name)
  headers.delete('host')
  if (visitor) headers.set('x-edge-client-ip', visitor)
  headers.set('x-edge-secret', env.EDGE_PROXY_SECRET)

  const hasBody = request.method !== 'GET' && request.method !== 'HEAD'
  let upstream
  try {
    upstream = await fetchImpl(origin + url.pathname + url.search, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,   // streamed: an 8 MB payslip is never buffered here
      redirect: 'manual',
      ...(hasBody ? { duplex: 'half' } : {}),
    })
  } catch {
    return edgeError(503, 'origin_unreachable', 'השרת עדיין לא זמין. נסו שוב בעוד רגע.')
  }

  // Our backend answers every /api request with JSON (its own 429s and errors included: a rate limit, a Gemini or
  // payslip failure). Anything else is the hosting layer in front of it: Render's 429 or challenge page while the
  // service wakes up, a 502/503 page. That is never passed on as if the backend had said it.
  const type = upstream.headers.get('content-type') || ''
  if (upstream.status >= 200 && !(upstream.status >= 300 && upstream.status < 400) && upstream.status !== 204
      && !type.includes('application/json')) {
    await upstream.body?.cancel()
    return edgeError(503, 'origin_waking', 'השרת מתעורר. נסו שוב בעוד רגע.', { upstream_status: upstream.status })
  }

  const response = new Response(upstream.body, upstream)   // status, headers (Set-Cookie included) and body
  response.headers.set('x-edge-proxied', '1')
  response.headers.set('x-content-type-options', 'nosniff')
  if (!response.headers.has('cache-control')) response.headers.set('cache-control', 'no-store')
  const location = response.headers.get('location')
  if (location && location.startsWith(origin)) {
    response.headers.set('location', location.slice(origin.length) || '/')   // stay on the edge origin
  }
  return response
}

export default {
  async fetch(request, env) {
    if (isApiPath(new URL(request.url).pathname)) return proxy(request, env)
    return env.ASSETS.fetch(request)   // only when called outside run_worker_first (e.g. a misrouted path)
  },
}
