import { beforeEach, expect, it, vi } from 'vitest'
import { api } from './api'

beforeEach(() => vi.stubGlobal('fetch', vi.fn()))

it('waits for noVNC 200 before returning the viewer URL', async () => {
  const { waitForNovnc } = await import('./api')
  let calls = 0
  const fetchImpl = (() => {
    calls += 1
    return Promise.resolve(new Response(calls === 1 ? 'noVNC not available yet' : '<html></html>', { status: calls === 1 ? 502 : 200 }))
  }) as typeof fetch
  const sleeps: number[] = []
  await expect(waitForNovnc('/vnc/vnc.html?autoconnect=1', {
    fetchImpl,
    sleep: (ms) => { sleeps.push(ms); return Promise.resolve() },
  })).resolves.toBe('/vnc/vnc.html?autoconnect=1')
  expect(calls).toBe(2)
  expect(sleeps).toEqual([500])
})

it('puts the workbench token on the noVNC websockify path', async () => {
  const { desktopViewerSrc } = await import('./api')
  expect(desktopViewerSrc('/vnc/vnc.html?autoconnect=1', 'secret')).toContain('path=vnc%2Fwebsockify%3Ftoken%3Dsecret')
})

it('strips VNC password from the noVNC viewer URL', async () => {
  const { desktopViewerSrc } = await import('./api')
  expect(desktopViewerSrc('/vnc/vnc.html?autoconnect=1&password=desk-secret')).not.toContain('password=')
})

it('points noVNC at the proxied /vnc/websockify socket', async () => {
  const { desktopViewerSrc } = await import('./api')
  expect(desktopViewerSrc('/vnc/vnc.html?autoconnect=1&path=websockify')).toContain('path=vnc%2Fwebsockify')
  expect(desktopViewerSrc('/vnc/vnc.html?autoconnect=1&path=websockify')).not.toMatch(/path=websockify(?!\?)/)
})

it('creates a v2 session with credential and routing state', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ id: 's1', status: 'RUNNING' }), { status: 201, headers: { 'content-type': 'application/json' } }))
  await api.createSession({ task: 'Run audit', model: 'gpt-5.6-luna', primaryRoute: 'openai-direct', fallbackRoutes: [], credentialSessionId: 'cred-1', maxSteps: 25, reasoningEffort: 'medium', safetyPolicy: 'provider_default', useBuiltinSearch: false, attachedFiles: [], retainAuditFrames: true })
  const init = vi.mocked(fetch).mock.calls[0]?.[1]
  const body = init?.body
  expect(fetch).toHaveBeenCalledWith('/api/v2/sessions', expect.objectContaining({ method: 'POST' }))
  if (typeof body !== 'string') throw new Error('expected JSON string body')
  expect(JSON.parse(body)).toMatchObject({ credentialSessionId: 'cred-1', primaryRoute: 'openai-direct', fallbackRoutes: [] })
})

it('surfaces the structured v2 error envelope', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ error: { code: 'RATE_LIMITED', message: 'Try later' } }), { status: 429, headers: { 'content-type': 'application/json' } }))
  await expect(api.sessions()).rejects.toMatchObject({ message: 'Try later', status: 429, code: 'RATE_LIMITED' })
})

it('passes analytics filters as query parameters', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response('{}', { headers: { 'content-type': 'application/json' } }))
  await api.analytics({ model: 'claude-sonnet-5', route: 'anthropic-direct' })
  expect(fetch).toHaveBeenCalledWith('/api/v2/analytics?model=claude-sonnet-5&route=anthropic-direct', expect.any(Object))
})
