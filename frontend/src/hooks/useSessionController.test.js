import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, beforeEach, vi } from 'vitest'

// Shared, hoisted mock state for the wrapped useWebSocket hook so a test can
// mutate `agentFinished` and re-render to drive the completion path.
const h = vi.hoisted(() => {
  const wsState = {
    connected: true,
    lastScreenshot: null,
    logs: [],
    steps: [],
    agentFinished: null,
    safetyPrompt: null,
  }
  return {
    wsState,
    clearFinished: vi.fn(() => {
      wsState.agentFinished = null
    }),
  }
})

vi.mock('./useWebSocket', () => ({
  default: () => ({
    ...h.wsState,
    clearFinished: h.clearFinished,
    clearLogs: vi.fn(),
    clearSteps: vi.fn(),
    clearSafetyPrompt: vi.fn(),
    setScreenshotMode: vi.fn(),
  }),
}))

vi.mock('../api', () => ({
  startAgent: vi.fn(),
  stopAgent: vi.fn(),
  getAgentStatus: vi.fn(),
}))

import { startAgent, stopAgent } from '../api'
import useSessionController from './useSessionController'

const PAYLOAD = { task: 't', model: 'm', provider: 'p' }
const toastMsgs = (onToast) => onToast.mock.calls.map((c) => c[0])

beforeEach(() => {
  h.wsState.agentFinished = null
  h.clearFinished.mockClear()
  startAgent.mockReset().mockResolvedValue({ session_id: 's1' })
  stopAgent.mockReset()
})

async function startRun(result, rerender) {
  await act(async () => {
    await result.current.start(PAYLOAD)
  })
  rerender()
}

describe('useSessionController', () => {
  it('start() sets sessionId + agentRunning and toasts', async () => {
    const onToast = vi.fn()
    const { result, rerender } = renderHook(() => useSessionController({ onToast }))
    await startRun(result, rerender)
    expect(result.current.sessionId).toBe('s1')
    expect(result.current.agentRunning).toBe(true)
    expect(toastMsgs(onToast)).toContain('Agent started')
  })

  it('finalizes exactly once on WS agent_finished (and a repeat does not re-finalize)', async () => {
    const onToast = vi.fn()
    const onHistoryEntry = vi.fn()
    const { result, rerender } = renderHook(() => useSessionController({ onToast, onHistoryEntry }))
    await startRun(result, rerender)

    act(() => {
      h.wsState.agentFinished = { session_id: 's1', status: 'completed', steps: 3 }
      rerender()
    })
    expect(result.current.agentRunning).toBe(false)
    expect(result.current.completionData).toMatchObject({ session_id: 's1', steps: 3 })
    expect(onHistoryEntry).toHaveBeenCalledTimes(1)
    const completeCount = () => toastMsgs(onToast).filter((m) => m.startsWith('Task complete')).length
    expect(completeCount()).toBe(1)

    // A second delivery of the same finished event must NOT re-finalize.
    act(() => {
      h.wsState.agentFinished = { session_id: 's1', status: 'completed', steps: 3 }
      rerender()
    })
    expect(completeCount()).toBe(1)
  })

  it('ignores an agent_finished for a different session', async () => {
    const onToast = vi.fn()
    const { result, rerender } = renderHook(() => useSessionController({ onToast }))
    await startRun(result, rerender)
    act(() => {
      h.wsState.agentFinished = { session_id: 'OTHER', status: 'completed', steps: 9 }
      rerender()
    })
    expect(result.current.agentRunning).toBe(true)
    expect(result.current.sessionId).toBe('s1')
    expect(toastMsgs(onToast).some((m) => m.startsWith('Task complete'))).toBe(false)
    expect(h.clearFinished).toHaveBeenCalled()  // stale event is cleared
  })

  it('stop() with confirmedStopped clears state', async () => {
    stopAgent.mockResolvedValue({ confirmedStopped: true })
    const onToast = vi.fn()
    const { result, rerender } = renderHook(() => useSessionController({ onToast }))
    await startRun(result, rerender)
    await act(async () => {
      await result.current.stop()
    })
    expect(result.current.sessionId).toBeNull()
    expect(result.current.agentRunning).toBe(false)
    expect(toastMsgs(onToast)).toContain('Agent stopped')
  })

  it('stop() with an ambiguous failure KEEPS the session and warns', async () => {
    stopAgent.mockResolvedValue({ confirmedStopped: false, sessionGone: false, ok: false, status: 0 })
    const onToast = vi.fn()
    const { result, rerender } = renderHook(() => useSessionController({ onToast }))
    await startRun(result, rerender)
    await act(async () => {
      await result.current.stop()
    })
    expect(result.current.sessionId).toBe('s1')       // still tracking — run may be alive
    expect(result.current.agentRunning).toBe(true)
    expect(toastMsgs(onToast).some((m) => m.includes('Could not confirm'))).toBe(true)
  })
})
