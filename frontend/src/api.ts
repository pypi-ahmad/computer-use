import type { Action, Analytics, EventRecord, Model, Page, Route, Session, Workflow } from './types'

const BASE = '/api/v2'
export class ApiError extends Error { constructor(message: string, readonly status: number, readonly code = 'HTTP_ERROR') { super(message) } }
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init.headers } })
  if (!response.ok) {
    let body: unknown
    try { body = await response.json() } catch { body = null }
    const envelope = body as { error?: { message?: string; code?: string } } | null
    throw new ApiError(envelope?.error?.message ?? response.statusText, response.status, envelope?.error?.code)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
export const api = {
  models: () => request<{ data: Model[]; catalogVersion: string; verifiedAt: string }>('/models'),
  routes: () => request<Page<Route>>('/provider-routes'),
  sessions: () => request<Page<Session>>('/sessions'),
  session: (id: string) => request<Session>(`/sessions/${encodeURIComponent(id)}`),
  createSession: (input: { task: string; model: string; primaryRoute: string; fallbackRoutes: string[]; credentialSessionId?: string; maxSteps: number; reasoningEffort?: string; retainAuditFrames: boolean }) => request<Session>('/sessions', { method: 'POST', body: JSON.stringify(input) }),
  stopSession: (id: string) => request<Session>(`/sessions/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ status: 'STOPPING' }) }),
  actions: (id: string) => request<Page<Action>>(`/sessions/${encodeURIComponent(id)}/actions?limit=100`),
  events: (id: string) => request<Page<EventRecord>>(`/sessions/${encodeURIComponent(id)}/events?limit=100`),
  workflows: () => request<Page<Workflow>>('/workflows'),
  createWorkflow: (input: { slug: string; name: string; variablesSchema: Record<string, unknown>; steps: string[] }) => request<Workflow>('/workflows', { method: 'POST', body: JSON.stringify(input) }),
  credentialSession: (credentials: Record<string, string>) => request<{ id: string; providers: string[]; expiresAt: number }>('/credential-sessions', { method: 'POST', body: JSON.stringify({ credentials }) }),
  deleteCredentialSession: (id: string) => request<void>(`/credential-sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  analytics: (filters: { sessionId?: string; model?: string; route?: string } = {}) => {
    const query = new URLSearchParams(Object.entries(filters).filter((entry): entry is [string, string] => Boolean(entry[1])))
    return request<Analytics>(`/analytics${query.size ? `?${query}` : ''}`)
  },
}
