/**
 * WebSocket event schema — mirrors backend/ws_schema.py.
 * Keep in sync when adding or changing backend events.
 *
 * All inbound messages are one of the union members of {@link WSEvent}
 * discriminated by the `event` field. Frontend consumers should narrow
 * with a `switch (msg.event)` and treat the default branch as a
 * forward-compat pass-through.
 */

export interface ScreenshotEvent {
  event: 'screenshot'
  /** base64 PNG */
  screenshot: string
}

export interface ScreenshotStreamEvent {
  event: 'screenshot_stream'
  screenshot: string
}

export interface LogEntryPayload {
  /** log level: 'info' | 'warning' | 'error' | 'debug' */
  level?: string
  message?: string
  /** optional structured payload (e.g. safety_confirmation) */
  data?: {
    type?: string
    session_id?: string
    explanation?: string
    [k: string]: unknown
  }
  [k: string]: unknown
}

export interface LogEvent {
  event: 'log'
  log: LogEntryPayload
}

export interface StepEvent {
  event: 'step'
  step: Record<string, unknown>
}

export interface AgentFinishedEvent {
  event: 'agent_finished'
  session_id: string
  status: string
  steps: number
}

export interface AuthFailedEvent {
  event: 'auth_failed'
  status: number
  message: string
}

export interface PongEvent {
  event: 'pong'
}

export interface GenericWSEvent {
  event: string
  [k: string]: unknown
}

export type WSEvent =
  | ScreenshotEvent
  | ScreenshotStreamEvent
  | LogEvent
  | StepEvent
  | AgentFinishedEvent
  | AuthFailedEvent
  | PongEvent
  | GenericWSEvent

/**
 * Runtime guard — returns true if `msg` has a recognised `event` field.
 * Does not do deep validation; treat as a forward-compat filter.
 */
export function isWSEvent(msg: unknown): msg is WSEvent {
  return (
    typeof msg === 'object' &&
    msg !== null &&
    'event' in msg &&
    typeof (msg as { event: unknown }).event === 'string'
  )
}
