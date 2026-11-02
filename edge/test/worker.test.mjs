// node --test edge/test   (Node 20+: Request, Response, Headers and fetch are built in; no dependencies)
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import test from 'node:test'

import worker, { isApiPath, proxy } from '../src/worker.js'

const ENV = { BACKEND_ORIGIN: 'https://backend.example', EDGE_PROXY_SECRET: 'edge-secret-0123456789' }

function recorder(response = new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })) {
  const calls = []
  const fetchImpl = async (url, init) => { calls.push({ url, init }); return response }
  return { calls, fetchImpl }
}

test('routes only /api to the proxy', () => {
  assert.equal(isApiPath('/api'), true)
  assert.equal(isApiPath('/api/health'), true)
  assert.equal(isApiPath('/apiary'), false)
  assert.equal(isApiPath('/employee'), false)
})

test('forged address and edge headers are dropped; the trusted ones are set from Cloudflare and the secret', async () => {
  const { calls, fetchImpl } = recorder()
  const request = new Request('https://edge.example/api/conversations?x=1', {
    headers: {
      'cf-connecting-ip': '203.0.113.7', 'cf-ray': 'abc',
      'x-edge-client-ip': '6.6.6.6', 'x-edge-secret': 'a-guess', 'x-forwarded-for': '6.6.6.6',
      'x-real-ip': '6.6.6.6', forwarded: 'for=6.6.6.6', 'true-client-ip': '6.6.6.6',
      cookie: 'sca_session=abc', 'x-rep-token': 'rep', accept: 'application/json',
    },
  })
  await proxy(request, ENV, fetchImpl)
  const [{ url, init }] = calls
  assert.equal(url, 'https://backend.example/api/conversations?x=1')
  const h = init.headers
  assert.equal(h.get('x-edge-client-ip'), '203.0.113.7')
  assert.equal(h.get('x-edge-secret'), ENV.EDGE_PROXY_SECRET)
  for (const name of ['x-forwarded-for', 'x-real-ip', 'forwarded', 'true-client-ip', 'cf-ray', 'cf-connecting-ip']) {
    assert.equal(h.get(name), null, name)
  }
  assert.equal(h.get('cookie'), 'sca_session=abc')          // the session and the rep headers pass unchanged
  assert.equal(h.get('x-rep-token'), 'rep')
  assert.equal(init.redirect, 'manual')
})

test('without a Cloudflare address no visitor address is invented', async () => {
  const { calls, fetchImpl } = recorder()
  await proxy(new Request('https://edge.example/api/health', { headers: { 'x-edge-client-ip': '6.6.6.6' } }), ENV, fetchImpl)
  assert.equal(calls[0].init.headers.get('x-edge-client-ip'), null)
})

test('the response passes through with its cookies, marked as proxied and never cached', async () => {
  const upstream = new Response('{"ok":1}', { status: 201, headers: [
    ['content-type', 'application/json'], ['set-cookie', 'sca_session=new; HttpOnly; Path=/api; SameSite=lax'],
    ['set-cookie', 'other=1; Path=/'], ['retry-after', '7']] })
  const { fetchImpl } = recorder(upstream)
  const response = await proxy(new Request('https://edge.example/api/conversations', { method: 'POST' }), ENV, fetchImpl)
  assert.equal(response.status, 201)
  assert.equal(await response.text(), '{"ok":1}')
  assert.deepEqual(response.headers.getSetCookie(), ['sca_session=new; HttpOnly; Path=/api; SameSite=lax', 'other=1; Path=/'])
  assert.equal(response.headers.get('x-edge-proxied'), '1')
  assert.equal(response.headers.get('cache-control'), 'no-store')
  assert.equal(response.headers.get('retry-after'), '7')
})

test('a backend redirect stays on the edge origin', async () => {
  const { fetchImpl } = recorder(new Response(null, { status: 307, headers: { location: 'https://backend.example/api/x/' } }))
  const response = await proxy(new Request('https://edge.example/api/x'), ENV, fetchImpl)
  assert.equal(response.headers.get('location'), '/api/x/')
})

test('an unreachable backend is a JSON 503 the client can tell apart; nothing is configured: 503 too', async () => {
  const failing = async () => { throw new TypeError('fetch failed') }
  const down = await proxy(new Request('https://edge.example/api/health'), ENV, failing)
  assert.equal(down.status, 503)
  assert.equal(down.headers.get('x-edge-proxied'), '1')
  assert.equal((await down.json()).edge, 'origin_unreachable')
  const unset = await proxy(new Request('https://edge.example/api/health'), { BACKEND_ORIGIN: '' }, failing)
  assert.equal((await unset.json()).edge, 'not_configured')
})

test('a POST body is streamed to the backend byte for byte (8 MB multipart)', async () => {
  const received = []
  const server = createServer((req, res) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      received.push({ headers: req.headers, body: Buffer.concat(chunks) })
      res.writeHead(200, { 'content-type': 'application/json' }).end('{"ok":true}')
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  try {
    const env = { ...ENV, BACKEND_ORIGIN: `http://127.0.0.1:${server.address().port}` }
    const payload = new Uint8Array(8 * 1024 * 1024).map((_, i) => i % 251)
    const form = new FormData()
    form.append('file', new Blob([payload], { type: 'application/pdf' }), 'payslip.pdf')
    const request = new Request('https://edge.example/api/payslip/extract', { method: 'POST', body: form })
    const response = await proxy(request, env)
    assert.equal(response.status, 200)
    const [{ headers, body }] = received
    assert.match(headers['content-type'], /^multipart\/form-data; boundary=/)
    assert.equal(headers['x-edge-secret'], ENV.EDGE_PROXY_SECRET)
    assert.ok(body.length > payload.length)
    assert.ok(body.includes(Buffer.from(payload.subarray(0, 4096))))
  } finally {
    server.close()
  }
})

test('the default handler proxies /api and leaves the rest to the assets', async () => {
  const seen = []
  const env = { BACKEND_ORIGIN: '', ASSETS: { fetch: async (r) => { seen.push(new URL(r.url).pathname); return new Response('asset') } } }
  assert.equal(await (await worker.fetch(new Request('https://edge.example/employee'), env)).text(), 'asset')
  assert.equal((await worker.fetch(new Request('https://edge.example/api/health'), env)).status, 503)
  assert.deepEqual(seen, ['/employee'])
})
