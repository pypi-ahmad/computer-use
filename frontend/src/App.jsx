import { useState, useEffect, useCallback } from 'react'
import useWebSocket from './hooks/useWebSocket'
import ControlPanel from './components/ControlPanel'
import ScreenView from './components/ScreenView'
import LogPanel from './components/LogPanel'
import Header from './components/Header'
import { getContainerStatus } from './api'

/**
 * Root application component. Renders the main dashboard layout with Header,
 * ControlPanel, ScreenView, and LogPanel. Polls container status with adaptive
 * back-off (5 s when reachable, 15 s when not).
 */
export default function App() {
  const { connected, lastScreenshot, logs, steps, clearLogs, clearSteps } = useWebSocket()

  const [containerRunning, setContainerRunning] = useState(false)
  const [agentServiceUp, setAgentServiceUp] = useState(false)
  const [agentRunning, setAgentRunning] = useState(false)
  const [sessionId, setSessionId] = useState(null)

  const [backendReachable, setBackendReachable] = useState(true)
  /** Returns poll interval in ms — 5 s normally, 15 s when backend is unreachable. */
  const pollIntervalRef = useCallback(() => backendReachable ? 5000 : 15000, [backendReachable])

  /** Fetches container & agent-service status; updates reachability flag. */
  const refreshContainerStatus = useCallback(async () => {
    try {
      const data = await getContainerStatus()
      setContainerRunning(data.running || false)
      setAgentServiceUp(data.agent_service || false)
      setBackendReachable(true)
    } catch {
      setContainerRunning(false)
      setAgentServiceUp(false)
      setBackendReachable(false)
    }
  }, [])

  useEffect(() => {
    refreshContainerStatus()
    const id = setInterval(refreshContainerStatus, pollIntervalRef())
    return () => clearInterval(id)
  }, [refreshContainerStatus, pollIntervalRef])

  return (
    <div className="app">
      <Header
        connected={connected}
        containerRunning={containerRunning}
        agentServiceUp={agentServiceUp}
        agentRunning={agentRunning}
        onRefreshContainer={refreshContainerStatus}
      />
      <div className="main-content">
        <ControlPanel
          containerRunning={containerRunning}
          agentRunning={agentRunning}
          setAgentRunning={setAgentRunning}
          sessionId={sessionId}
          setSessionId={setSessionId}
          steps={steps}
          clearSteps={clearSteps}
          onRefreshContainer={refreshContainerStatus}
        />
        <div className="right-panel">
          <ScreenView screenshot={lastScreenshot} containerRunning={containerRunning} />
          <LogPanel logs={logs} onClear={clearLogs} />
        </div>
      </div>
    </div>
  )
}
