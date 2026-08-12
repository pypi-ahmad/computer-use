export type Status = 'PENDING' | 'RUNNING' | 'STOPPING' | 'STOPPED' | 'COMPLETED' | 'ERROR'
export interface Route { id: string; provider: string; transport: string; isConfigured: boolean; isExecutable: boolean; authMode: string; circuitState: string }
export interface ModelRoute { id: string; provider: string; transport: string; modelId: string }
export interface Model { logicalId: string; displayName: string; family: string; lifecycle: string; contextWindow: number; maxOutputTokens: number; reasoningEfforts: string[]; routes: ModelRoute[] }
export interface Session { id: string; task: string; model: string; primaryRoute: string; status: Status; createdAt: string; activeRoute?: string }
export interface Workflow { id: string; slug: string; name: string; version: number; variablesSchema: Record<string, unknown>; steps: string[]; createdAt: string }
export interface Action { id?: number; sequence: number; type?: string; actionType?: string; action?: string; payload?: Record<string, unknown>; confirmed?: boolean; createdAt?: string }
export interface EventRecord { id?: number; eventType?: string; type?: string; payload?: Record<string, unknown>; createdAt?: string }
export interface Analytics { sampleCount?: number; totalDurationMs?: number; inputTokens?: number; outputTokens?: number; [key: string]: unknown }
export type PipelineStage = 'capture' | 'encode' | 'infer' | 'validate' | 'act'
export type StreamEvent =
  | { event: 'SESSION_STREAM_READY'; sessionId: string }
  | { event: 'FRAME'; sessionId: string; sequence: number; codec: string; width: number; height: number; timestampMs: number }
  | { event: 'PIPELINE_STAGE'; stage: PipelineStage; status: 'ACTIVE' | 'COMPLETED' | 'ERROR' }
  | { event: 'ACTION'; action: Action }
  | { event: 'LOG'; level: string; message: string }
  | { event: 'SAFETY_CONFIRMATION'; nonce: string; explanation: string }
  | { event: 'SESSION_TERMINAL'; status: Status; message?: string }
export interface Page<T> { data: T[]; nextCursor?: number | null }
export interface ApiErrorBody { error: { code: string; message: string; details?: unknown; isRetryable: boolean; requestId: string } }
