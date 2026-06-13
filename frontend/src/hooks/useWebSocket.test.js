import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import MockWebSocket from '../test/mockWebSocket'
import useWebSocket from './useWebSocket'

const sock = () => MockWebSocket.instances[MockWebSocket.instances.length - 1]

describe('useWebSocket', () => {
  beforeEach(() => {
    MockWebSocket.reset()
    vi.stubGlobal('WebSocket', MockWebSocket)
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('connects to /ws and flips connected on open', () => {
    const { result } = renderHook(() => useWebSocket())
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(sock().url).toContain('/ws')
    act(() => sock().simulateOpen())
    expect(result.current.connected).toBe(true)
  })

  it('sends a heartbeat ping every 15s while open', () => {
    renderHook(() => useWebSocket())
    act(() => sock().simulateOpen())
    act(() => vi.advanceTimersByTime(15000))
    expect(sock().sent.filter((m) => m.type === 'ping')).toHaveLength(1)
    act(() => vi.advanceTimersByTime(15000))
    expect(sock().sent.filter((m) => m.type === 'ping')).toHaveLength(2)
  })

  it('routes screenshot / log / step / agent_finished messages', () => {
    const { result } = renderHook(() => useWebSocket())
    act(() => sock().simulateOpen())
    act(() => sock().simulateMessage({ event: 'screenshot_stream', screenshot: 'AAAA', format: 'jpeg' }))
    expect(result.current.lastScreenshot).toBe('data:image/jpeg;base64,AAAA')
    act(() => sock().simulateMessage({ event: 'log', log: { level: 'info', message: 'hi' } }))
    expect(result.current.logs).toHaveLength(1)
    act(() => sock().simulateMessage({ event: 'step', step: { step_number: 1 } }))
    expect(result.current.steps).toHaveLength(1)
    act(() => sock().simulateMessage({ event: 'agent_finished', status: 'completed' }))
    expect(result.current.agentFinished).toMatchObject({ status: 'completed' })
  })

  it('surfaces a safety_confirmation payload (incl. nonce)', () => {
    const { result } = renderHook(() => useWebSocket())
    act(() => sock().simulateOpen())
    act(() => sock().simulateMessage({
      event: 'log',
      log: { level: 'warning', data: { type: 'safety_confirmation', session_id: 's1', explanation: 'why', nonce: 'n1' } },
    }))
    expect(result.current.safetyPrompt).toMatchObject({ sessionId: 's1', nonce: 'n1' })
  })

  it('U5: drops malformed / oversized frames without throwing', () => {
    const { result } = renderHook(() => useWebSocket())
    act(() => sock().simulateOpen())
    act(() => sock().simulateMessage('not json'))           // parse error
    act(() => sock().simulateMessage({ noEvent: true }))     // missing event
    act(() => sock().simulateMessage({ event: 'screenshot_stream', screenshot: 'x'.repeat(9_000_000) }))
    expect(result.current.logs).toHaveLength(0)
    expect(result.current.lastScreenshot).toBeNull()
  })

  it('U3: schedules a reconnect on close and a new socket connects after the delay', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5)  // jitter factor 0.75
    renderHook(() => useWebSocket())
    act(() => sock().simulateOpen())
    expect(MockWebSocket.instances).toHaveLength(1)
    act(() => sock().simulateClose())
    // attempt 0: base 2000 * 0.75 = 1500ms. Nothing before; new socket after.
    act(() => vi.advanceTimersByTime(1499))
    expect(MockWebSocket.instances).toHaveLength(1)
    act(() => vi.advanceTimersByTime(2))
    expect(MockWebSocket.instances).toHaveLength(2)
  })

  it('U3: does NOT reconnect after unmount', () => {
    const { unmount } = renderHook(() => useWebSocket())
    act(() => sock().simulateOpen())
    unmount()
    act(() => vi.advanceTimersByTime(60000))
    expect(MockWebSocket.instances).toHaveLength(1)  // no reconnect storm
  })
})
