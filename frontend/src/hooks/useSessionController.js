/**
 * useSessionController — owns the agent session lifecycle.
 *
 * Wraps :mod:`useWebSocket` and adds:
 *   - session-scoped state (running / starting / stopping / id / completion)
 *   - reconciliation with WS ``agent_finished`` + a low-rate status poll
 *     (unchanged from the pre-PR behaviour)
 *   - ``start`` / ``stop`` handlers with truthful semantics: clear
 *     local run state only after explicit backend confirmation, keep the
 *     handle on any ambiguous failure, and offer a retry affordance
 *   - pass-through of WS-backed state (screenshot / logs / steps /
 *     safetyPrompt / connected) so the page can consume everything
 *     from a single hook.
 *
 * The hook is deliberately free of DOM concerns. It calls
 * ``onToast(msg, kind, options)`` and ``onHistoryEntry(entry)`` via injectable
 * callbacks so the page controls notification UI and history
 * persistence.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import useWebSocket from './useWebSocket'
import {
  getAgentStatus,
  startAgent,
  stopAgent,
} from '../api'

/**
 * @param {object} opts
 * @param {(message: string, kind?: 'success'|'error'|'info', options?: {
 *   duration?: number,
 *   actionLabel?: string,
 *   onAction?: () => void,
 * }) => void} opts.onToast
 * @param {(entry: object) => void} [opts.onHistoryEntry]
 *        Called with the shape previously passed to
 *        ``addSessionToHistory`` — lets the page persist the entry and
 *        update any local cache.
 */
const AMBIGUOUS_STOP_ERROR =
  'Stop request failed, and the session may still be running. Retry stop or wait for completion.'

export default function useSessionController({ onToast, onHistoryEntry } = {}) {
  const ws = useWebSocket()
  const {
    connected,
    lastScreenshot,
    logs,
    steps,
    agentFinished,
    safetyPrompt,
    clearLogs,
    clearSteps,
    clearFinished,
    clearSafetyPrompt,
    setScreenshotMode: setWsScreenshotMode,
  } = ws

  // Session-scoped state.
  const [agentRunning, setAgentRunning] = useState(false)
  const [sessionId, setSessionId] = useState(null)
  const [starting, setStarting] = useState(false)
  // True while a POST /agent/stop is in flight. Gates the Stop button
  // against duplicate clicks and prevents a second (wasted) cancel
  // request racing the first. Reset in ``stop``'s ``finally``.
  const [stopping, setStopping] = useState(false)
  const [completionData, setCompletionData] = useState(null)
  const [error, setError] = useState('')

  // Refs used by the completion-reconciliation path.
  const sessionStartTime = useRef(null)
  const handledCompletionSession = useRef(null)
  const agentRunningRef = useRef(agentRunning)
  const sessionIdRef = useRef(sessionId)
  const stoppingRef = useRef(stopping)

  useEffect(() => {
    agentRunningRef.current = agentRunning
  }, [agentRunning])

  useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])

  useEffect(() => {
    stoppingRef.current = stopping
  }, [stopping])

  // --- Completion reconciliation ----------------------------------------

  const clearTrackedRun = useCallback((targetSessionId) => {
    if (!targetSessionId || sessionIdRef.current !== targetSessionId) return false

    agentRunningRef.current = false
    sessionIdRef.current = null
    setAgentRunning(false)
    setSessionId(null)
    setError('')
    sessionStartTime.current = null
    return true
  }, [])

  const finalizeAgentRun = useCallback((finishEvent, context = {}) => {
    const finishedSessionId = finishEvent?.session_id || sessionId
    if (!finishedSessionId || !sessionId || finishedSessionId !== sessionId) return
    if (handledCompletionSession.current === finishedSessionId) return

    handledCompletionSession.current = finishedSessionId

    const status = finishEvent.status || 'completed'
    const completedSteps = finishEvent.steps ?? finishEvent.current_step ?? 0
    const elapsed = sessionStartTime.current
      ? Math.round((Date.now() - sessionStartTime.current) / 1000)
      : null

    agentRunningRef.current = false
    sessionIdRef.current = null
    setAgentRunning(false)
    setSessionId(null)
    setError('')
    sessionStartTime.current = null
    setCompletionData({
      ...finishEvent,
      session_id: finishedSessionId,
      steps: completedSteps,
      elapsedSeconds: elapsed,
    })

    if (onToast) {
      onToast(
        status === 'completed'
          ? `Task complete — ${completedSteps} steps`
          : `Task ${status}`,
        status === 'completed' ? 'success' : 'error',
      )
    }
    if (onHistoryEntry && context) {
      onHistoryEntry({
        task: (context.task || '').slice(0, 100),
        model: context.model,
        modelDisplayName: context.modelDisplayName || context.model,
        provider: context.provider,
        steps: completedSteps,
        status,
        timestamp: new Date().toISOString(),
      })
    }
  }, [onHistoryEntry, onToast, sessionId])

  // Most-recent context (task/model/provider/modelDisplayName) — refreshed
  // on every ``start`` so the finalize callback can log an accurate
  // history entry even if the page's form state has changed by the
  // time the run ends.
  const runContextRef = useRef(null)

  // WS ``agent_finished`` is the primary completion path.
  useEffect(() => {
    if (!agentFinished) return
    if (!agentRunning || !sessionId || agentFinished.session_id !== sessionId) {
      clearFinished()
      return
    }
    finalizeAgentRun(agentFinished, runContextRef.current || {})
    clearFinished()
  }, [agentFinished, agentRunning, sessionId, finalizeAgentRun, clearFinished])

  // C14: low-rate (10 s) poll as a safety net against a missed WS event.
  useEffect(() => {
    if (!agentRunning || !sessionId) return

    let cancelled = false
    const pollStatus = async () => {
      try {
        const data = await getAgentStatus(sessionId)
        if (cancelled || data?.error) return
        if (data.status === 'completed' || data.status === 'error') {
          finalizeAgentRun(data, runContextRef.current || {})
        }
      } catch {
        // Transient polling failures are ignored — WS is primary.
      }
    }
    const id = window.setInterval(pollStatus, 10000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [agentRunning, sessionId, finalizeAgentRun])

  // --- Start / Stop -----------------------------------------------------

  /**
   * Start an agent run.
   *
   * ``payload`` is the exact body previously assembled inline in
   * ``Workbench.jsx``'s ``handleStart``; ``context`` carries
   * display-only fields (modelDisplayName) needed at completion time.
   * Returns the backend response so the caller can surface
   * provider-specific errors in its UI.
   */
  const start = useCallback(async (payload, context = {}) => {
    setError('')
    clearSteps()
    clearLogs()
    clearFinished()
    setCompletionData(null)
    setStarting(true)
    handledCompletionSession.current = null

    runContextRef.current = {
      task: payload.task,
      model: payload.model,
      provider: payload.provider,
      modelDisplayName: context.modelDisplayName,
    }

    try {
      const res = await startAgent(payload)
      if (res.error) {
        setStarting(false)
        setError(res.error)
        return res
      }
      setSessionId(res.session_id)
      setAgentRunning(true)
      sessionIdRef.current = res.session_id
      agentRunningRef.current = true
      sessionStartTime.current = Date.now()
      if (onToast) onToast('Agent started', 'success')
      setStarting(false)
      return res
    } catch (e) {
      setStarting(false)
      setError(`Failed to start: ${e.message}`)
      return { error: e.message }
    }
  }, [clearSteps, clearLogs, clearFinished, onToast])

  /**
   * Stop the currently running agent session with positive-confirmation
   * semantics.
   *
   *   1. Success (2xx with explicit ``status: "stopped"``) → clear local state.
   *   2. 404 "Session not found"                  → clear local state.
   *      The backend has no record of this session so there is nothing
   *      to keep a handle to; treating 404 as confirmation prevents a
   *      stuck UI if the session ended between two clicks.
   *   3. Any other failure (network, 5xx, timeout) → KEEP ``sessionId``
   *      and ``agentRunning``. The backend run may still be alive and
   *      still spending tokens. Surface a toast, re-enable the button,
   *      and let the user retry.
   */
  const requestStop = useCallback(async (targetSessionId) => {
    if (!targetSessionId || stoppingRef.current) return
    stoppingRef.current = true
    setError('')
    setStopping(true)
    try {
      const res = await stopAgent(targetSessionId)
      const stillTrackingSession = sessionIdRef.current === targetSessionId
      const runStillActive = stillTrackingSession && agentRunningRef.current

      if (res.confirmedStopped || res.sessionGone) {
        if (clearTrackedRun(targetSessionId) && onToast) {
          onToast(
            res.sessionGone ? 'Session already ended' : 'Agent stopped',
            'info',
          )
        }
        return
      }

      if (!runStillActive) return

      setError(AMBIGUOUS_STOP_ERROR)
      const detail = res.error ? ` ${res.error}` : ''
      if (onToast) {
        onToast(
          `Could not confirm that the agent stopped.${detail}`,
          'error',
          {
            duration: 10000,
            actionLabel: 'Retry stop',
            onAction: () => {
              if (
                stoppingRef.current
                || sessionIdRef.current !== targetSessionId
                || !agentRunningRef.current
              ) {
                return
              }
              void requestStop(targetSessionId)
            },
          },
        )
      }
    } finally {
      stoppingRef.current = false
      setStopping(false)
    }
  }, [clearTrackedRun, onToast])

  const stop = useCallback(async () => {
    if (!sessionId) return
    await requestStop(sessionId)
  }, [requestStop, sessionId])

  // Keep the screen-view subscription session-bound without making the
  // screen component care about session ids directly. The callback
  // identity intentionally changes with ``sessionId`` so ScreenView's
  // effect re-sends on start/finish transitions as well as surface
  // toggles.
  const setScreenshotMode = useCallback((mode) => {
    setWsScreenshotMode(mode, sessionId)
  }, [setWsScreenshotMode, sessionId])

  /** Dismiss the completion banner. */
  const dismissCompletion = useCallback(() => setCompletionData(null), [])

  /** Clear both timeline + logs (convenience wrapper used by the Clear button). */
  const clearAll = useCallback(() => {
    clearSteps()
    clearLogs()
    setCompletionData(null)
  }, [clearLogs, clearSteps])

  return {
    // WS-backed pass-throughs
    connected,
    lastScreenshot,
    logs,
    steps,
    safetyPrompt,
    clearLogs,
    clearSafetyPrompt,
    setScreenshotMode,

    // Session lifecycle
    agentRunning,
    sessionId,
    starting,
    stopping,
    completionData,
    error,
    setError,
    start,
    stop,
    dismissCompletion,
    clearAll,
  }
}
