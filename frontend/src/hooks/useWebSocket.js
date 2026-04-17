import { useEffect, useRef, useState, useCallback } from 'react'

/** WebSocket protocol derived from current page (wss: for HTTPS, ws: for HTTP). */
const WS_PROTOCOL = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
/** Full WebSocket URL pointing to the backend /ws endpoint. */
const WS_URL = `${WS_PROTOCOL}//${window.location.host}/ws`

/**
 * React hook that maintains a persistent WebSocket connection to the backend.
 * Provides real-time agent screenshots, logs, step timeline, and finish events.
 * Includes automatic reconnection (2 s delay) and a 15 s heartbeat ping.
 * @returns {{connected: boolean, lastScreenshot: string|null, logs: Array, steps: Array, agentFinished: Object|null, clearLogs: Function, clearSteps: Function, clearFinished: Function}}
 */
export default function useWebSocket() {
  const wsRef = useRef(null)
  const [connected, setConnected] = useState(false)
  const [lastScreenshot, setLastScreenshot] = useState(null)
  const [logs, setLogs] = useState([])
  const [steps, setSteps] = useState([])
  const [agentFinished, setAgentFinished] = useState(null)
  const [safetyPrompt, setSafetyPrompt] = useState(null)
  const reconnectTimer = useRef(null)
  const reconnectAttempts = useRef(0)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      reconnectAttempts.current = 0
      // Heartbeat
      const ping = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, 15000)
      ws._pingInterval = ping
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)

        switch (msg.event) {
          case 'screenshot':
          case 'screenshot_stream':
            setLastScreenshot(msg.screenshot)
            break
          case 'log':
            // Check if this log carries a safety_confirmation payload
            if (msg.log?.data?.type === 'safety_confirmation') {
              setSafetyPrompt({
                sessionId: msg.log.data.session_id,
                explanation: msg.log.data.explanation,
                timestamp: Date.now(),
              })
            }
            setLogs((prev) => [...prev.slice(-200), msg.log])
            break
          case 'step':
            setSteps((prev) => [...prev, msg.step])
            break
          case 'agent_finished':
            setAgentFinished(msg)
            setSafetyPrompt(null)
            break
          case 'pong':
            break
          default:
            break
        }
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = () => {
      setConnected(false)
      clearInterval(ws._pingInterval)
      // Exponential backoff capped at 30s (2s, 4s, 8s, 16s, 30s, 30s, …)
      const attempt = reconnectAttempts.current++
      const delay = Math.min(2000 * Math.pow(2, attempt), 30000)
      reconnectTimer.current = setTimeout(connect, delay)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      if (wsRef.current) {
        wsRef.current.close()
      }
    }
  }, [connect])

  const clearLogs = useCallback(() => setLogs([]), [])
  const clearSteps = useCallback(() => setSteps([]), [])
  const clearFinished = useCallback(() => setAgentFinished(null), [])
  const clearSafetyPrompt = useCallback(() => setSafetyPrompt(null), [])

  return { connected, lastScreenshot, logs, steps, agentFinished, safetyPrompt, clearLogs, clearSteps, clearFinished, clearSafetyPrompt }
}
