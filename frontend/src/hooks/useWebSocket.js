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
  const reconnectTimer = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
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
            setLogs((prev) => [...prev.slice(-200), msg.log])
            break
          case 'step':
            setSteps((prev) => [...prev, msg.step])
            break
          case 'agent_finished':
            setAgentFinished(msg)
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
      // Reconnect after 2s
      reconnectTimer.current = setTimeout(connect, 2000)
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

  return { connected, lastScreenshot, logs, steps, agentFinished, clearLogs, clearSteps, clearFinished }
}
