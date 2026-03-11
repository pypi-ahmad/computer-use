import { startContainer, stopContainer } from '../api'

/**
 * Top-bar header displaying connection status, container controls (start/stop),
 * and agent-service readiness badge.
 */
export default function Header({ connected, containerRunning, agentServiceUp, agentRunning, onRefreshContainer }) {

  /** Starts the Docker container and refreshes status. */
  const handleStartContainer = async () => {
    await startContainer()
    onRefreshContainer()
  }

  /** Stops the Docker container and refreshes status. */
  const handleStopContainer = async () => {
    await stopContainer()
    onRefreshContainer()
  }

  return (
    <header className="header">
      <h1>
        <span>CUA</span> — Computer Using Agent
      </h1>
      <div className="header-status">
        <div className="container-controls">
          <span className={`container-status ${containerRunning ? 'running' : 'stopped'}`}>
            {containerRunning ? 'Container Running' : 'Container Stopped'}
          </span>
          {containerRunning && (
            <span className={`service-badge ${agentServiceUp ? 'up' : 'down'}`}>
              Agent Service {agentServiceUp ? 'Ready' : 'Down'}
            </span>
          )}
          {!containerRunning && (
            <button className="btn-sm" onClick={handleStartContainer}>
              Start Container
            </button>
          )}
          {containerRunning && !agentRunning && (
            <button className="btn-sm" onClick={handleStopContainer}>
              Stop Container
            </button>
          )}
        </div>
        <span className={`status-dot ${connected ? 'connected' : ''} ${agentRunning ? 'running' : ''}`} />
        <span>{connected ? (agentRunning ? 'Agent Running' : 'Connected') : 'Disconnected'}</span>
      </div>
    </header>
  )
}
