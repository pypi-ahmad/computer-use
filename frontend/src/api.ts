import type { Action, Analytics, EventRecord, Model, Page, Route, Session, Workflow } from './types'

const BASE = '/api/v2'
const TOKEN_KEY = 'cua-api-token'
export const getAppToken = () => sessionStorage.getItem(TOKEN_KEY)?.trim() ?? ''
export const setAppToken = (token: string) => token.trim() ? sessionStorage.setItem(TOKEN_KEY, token.trim()) : sessionStorage.removeItem(TOKEN_KEY)
export class ApiError extends Error { constructor(message: string, readonly status: number, readonly code = 'HTTP_ERROR') { super(message) } }
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getAppToken()
  const response = await fetch(`${BASE}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(token ? { 'X-CUA-Token': token } : {}), ...init.headers } })
  if (!response.ok) {
    let body: unknown
    try { body = await response.json() } catch { body = null }
    const envelope = body as { error?: { message?: string; code?: string } } | null
    throw new ApiError(envelope?.error?.message ?? response.statusText, response.status, envelope?.error?.code)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
export function desktopViewerSrc(viewerUrl: string, token = getAppToken()): string {
  const url = new URL(viewerUrl, 'http://127.0.0.1')
  // noVNC builds ws://host:port/<path>. A bare "websockify" hits the
  // Vite origin at /websockify, which is not proxied. The workbench
  // websocket lives at /vnc/websockify.
  url.searchParams.delete('password')
  url.searchParams.delete('token')
  url.searchParams.set('path', token ? `vnc/websockify?token=${token}` : 'vnc/websockify')
  return `${url.pathname}?${url.searchParams.toString()}`
}

export async function waitForNovnc(
  viewerUrl: string,
  {
    attempts = 40,
    delayMs = 500,
    fetchImpl = fetch,
    sleep = (ms: number) => new Promise<void>(resolve => { window.setTimeout(resolve, ms) }),
  }: {
    attempts?: number
    delayMs?: number
    fetchImpl?: typeof fetch
    sleep?: (ms: number) => Promise<void>
  } = {},
): Promise<string> {
  const probe = viewerUrl.split('?')[0] || viewerUrl
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const response = await fetchImpl(probe)
      if (response.ok) return viewerUrl
    } catch {
      // sandbox still coming up
    }
    if (attempt + 1 < attempts) await sleep(delayMs)
  }
  return viewerUrl
}

export const api = {
  desktop: () => request<{ viewerUrl: string }>('/desktop'),
  models: () => request<{ data: Model[]; catalogVersion: string; verifiedAt: string }>('/models'),
  routes: () => request<Page<Route>>('/provider-routes'),
  sessions: () => request<Page<Session>>('/sessions'),
  session: (id: string) => request<Session>(`/sessions/${encodeURIComponent(id)}`),
  createSession: (input: { task: string; model: string; primaryRoute: string; fallbackRoutes: Array<string | { model: string; route: string }>; credentialSessionId?: string; maxSteps: number; reasoningEffort?: string; safetyPolicy: string; useBuiltinSearch: boolean; attachedFiles: string[]; retainAuditFrames: boolean }) => request<Session>('/sessions', { method: 'POST', body: JSON.stringify(input) }),
  stopSession: (id: string) => request<Session>(`/sessions/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ status: 'STOPPING' }) }),
  safetyDecision: (id: string, nonce: string, confirm: boolean) => request<{ sessionId: string; confirmed: boolean }>(`/sessions/${encodeURIComponent(id)}/safety-decisions`, { method: 'POST', body: JSON.stringify({ nonce, confirm }) }),
  actions: (id: string) => request<Page<Action>>(`/sessions/${encodeURIComponent(id)}/actions?limit=100`),
  events: (id: string) => request<Page<EventRecord>>(`/sessions/${encodeURIComponent(id)}/events?limit=100`),
  exportSession: async (id: string, includeFrames = false) => {
    const token = getAppToken(); const response = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}/export?include_frames=${includeFrames}`, { headers: token ? { 'X-CUA-Token': token } : {} })
    if (!response.ok) throw new ApiError(response.statusText, response.status)
    return response.blob()
  },
  workflows: () => request<Page<Workflow>>('/workflows'),
  createWorkflow: (input: { slug: string; name: string; variablesSchema: Record<string, unknown>; steps: string[] }) => request<Workflow>('/workflows', { method: 'POST', body: JSON.stringify(input) }),
  compileWorkflow: (id: string, variables: Record<string, unknown> = {}) => request<{ workflowId: string; version: number; instructions: string[] }>(`/workflows/${encodeURIComponent(id)}/compile`, { method: 'POST', body: JSON.stringify({ variables }) }),
  credentialSession: (credentials: Record<string, string>) => request<{ id: string; providers: string[]; expiresAt: number }>('/credential-sessions', { method: 'POST', body: JSON.stringify({ credentials }) }),
  credentialSessionStatus: (id: string) => request<{ id: string; providers: string[]; expiresAt: number }>(`/credential-sessions/${encodeURIComponent(id)}`),
  startGoogleOAuth: (quotaProjectId?: string) => request<{ credentialSessionId: string; expiresAt: number; authorizationUrl: string }>('/credential-sessions/google/oauth/start', { method: 'POST', body: JSON.stringify({ ...(quotaProjectId?.trim() ? { quotaProjectId: quotaProjectId.trim() } : {}) }) }),
  uploadFile: async (file: File) => {
    const body = new FormData(); body.append('file', file)
    const token = getAppToken()
    const response = await fetch('/api/files/upload', { method: 'POST', headers: token ? { 'X-CUA-Token': token } : {}, body })
    if (!response.ok) throw new ApiError(response.statusText, response.status)
    return response.json() as Promise<{ file_id: string; filename: string; size_bytes: number; mime_type: string }>
  },
  deleteCredentialSession: (id: string) => request<void>(`/credential-sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  analytics: (filters: { sessionId?: string; model?: string; route?: string } = {}) => {
    const query = new URLSearchParams(Object.entries(filters).filter((entry): entry is [string, string] => Boolean(entry[1])))
    return request<Analytics>(`/analytics${query.size ? `?${query}` : ''}`)
  },
  diagnostics: () => request<Record<string, unknown>>('/diagnostics'),
  retentionPreview: () => request<{ fileCount: number; totalBytes: number; expiredFileCount: number; expiredBytes: number; maxBytes: number; maxAgeSeconds: number }>('/retention/preview'),
  pruneRetention: () => request<{ removedFileCount: number; reclaimedBytes: number }>('/retention/prune', { method: 'POST', body: '{}' }),
  shutdown: () => request<{ status: 'stopping' }>('/system/shutdown', { method: 'POST', body: '{}' }),
}
