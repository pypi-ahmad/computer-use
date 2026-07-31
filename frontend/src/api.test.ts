import { beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from './api'

beforeEach(() => vi.stubGlobal('fetch', vi.fn()))

it('creates a v2 session with credential and routing state', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ id: 's1', status: 'RUNNING' }), { status: 201, headers: { 'content-type': 'application/json' } }))
  await api.createSession({ task: 'Run audit', model: 'gpt-5.6-terra', primaryRoute: 'openai', fallbackRoutes: ['azure'], credentialSessionId: 'cred-1', maxSteps: 25, reasoningEffort: 'medium', retainAuditFrames: true })
  const init = vi.mocked(fetch).mock.calls[0]?.[1]
  expect(fetch).toHaveBeenCalledWith('/api/v2/sessions', expect.objectContaining({ method: 'POST' }))
  expect(JSON.parse(String(init?.body))).toMatchObject({ credentialSessionId: 'cred-1', primaryRoute: 'openai', fallbackRoutes: ['azure'] })
})

it('surfaces the structured v2 error envelope', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ error: { code: 'RATE_LIMITED', message: 'Try later' } }), { status: 429, headers: { 'content-type': 'application/json' } }))
  await expect(api.sessions()).rejects.toMatchObject({ message: 'Try later', status: 429, code: 'RATE_LIMITED' })
})

it('passes analytics filters as query parameters', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response('{}', { headers: { 'content-type': 'application/json' } }))
  await api.analytics({ model: 'sonnet-5', route: 'bedrock' })
  expect(fetch).toHaveBeenCalledWith('/api/v2/analytics?model=sonnet-5&route=bedrock', expect.any(Object))
})
