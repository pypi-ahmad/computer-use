import { useState, useEffect, useRef, useCallback } from 'react'
import { Link } from 'react-router-dom'
import useWebSocket from '../hooks/useWebSocket'
import { startAgent, stopAgent, getContainerStatus, startContainer, getKeyStatuses, getModels } from '../api'
import ScreenView from '../components/ScreenView'
import formatTime from '../utils/formatTime'
import './Workbench.css'

const PROVIDERS = [
  { value: 'google', label: 'Google Gemini', envVar: 'GOOGLE_API_KEY', placeholder: 'AI...' },
  { value: 'anthropic', label: 'Anthropic Claude', envVar: 'ANTHROPIC_API_KEY', placeholder: 'sk-ant-...' },
  { value: 'openai', label: 'OpenAI GPT-5.4', envVar: 'OPENAI_API_KEY', placeholder: 'sk-...' },
]

/** Maps action names to emoji icons for the step timeline. */
const ACTION_ICONS = {
  click: '🖱️', double_click: '🖱️', right_click: '🖱️', hover: '👆',
  type: '⌨️', fill: '📝', key: '⌨️', hotkey: '⌨️', paste: '📋', copy: '📋',
  open_url: '🌐', reload: '🔄', go_back: '◀', go_forward: '▶',
  new_tab: '➕', close_tab: '✖', switch_tab: '🔀',
  scroll: '📜', scroll_to: '📜',
  get_text: '📖', find_element: '🔍', evaluate_js: '💻',
  focus_window: '🪟', open_app: '🚀',
  wait: '⏳', wait_for: '⏳', screenshot_region: '📸',
  done: '✅', error: '❌',
}

// No hardcoded fallback — models come exclusively from GET /api/models.

/**
 * Full-featured workbench page with sidebar config, live screen view,
 * step timeline, and log panel. Runs through the desktop-only backend mode.
 */
export default function Workbench() {
  const { connected, lastScreenshot, logs, steps, agentFinished, clearLogs, clearSteps, clearFinished } = useWebSocket()

  // Container state
  const [containerRunning, setContainerRunning] = useState(false)

  // Agent state
  const [agentRunning, setAgentRunning] = useState(false)
  const [sessionId, setSessionId] = useState(null)

  // Config
  const runMode = 'desktop'
  const [provider, setProvider] = useState('google')
  const [model, setModel] = useState('')
  // Engine and execution target are fixed for computer-use-only mode
  const [apiKey, setApiKey] = useState('')
  const [keySource, setKeySource] = useState('ui') // 'ui' | 'env' | 'dotenv'
  const [keyStatuses, setKeyStatuses] = useState({}) // { google: {...}, anthropic: {...} }
  const [task, setTask] = useState('')
  const [maxSteps, setMaxSteps] = useState(50)
  const [reasoningEffort, setReasoningEffort] = useState('low')
  const [error, setError] = useState('')

  // Timeline expansion
  const [expandedStep, setExpandedStep] = useState(null)

  // Refs
  const timelineRef = useRef(null)
  const logRef = useRef(null)

  // Dynamic model list — fetched exclusively from GET /api/models (no fallback)
  const [fetchedModels, setFetchedModels] = useState([])
  const [modelsLoaded, setModelsLoaded] = useState(false)
  /** Maps a model record to a { value, label } select option. */
  const toModelOption = (m) => ({ value: m.model_id, label: `${m.display_name} (${m.model_id})` })
  const modelsByProvider = fetchedModels.reduce((acc, item) => {
    acc[item.provider] = (acc[item.provider] || []).concat(toModelOption(item))
    return acc
  }, {})
  const models = modelsByProvider[provider] || []
  const providerMeta = PROVIDERS.find(item => item.value === provider) || PROVIDERS[0]



  // Poll container
  /** Polls container running status from the backend. */
  const refreshContainer = useCallback(async () => {
    try {
      const data = await getContainerStatus()
      setContainerRunning(data.running || false)
    } catch {
      setContainerRunning(false)
    }
  }, [])

  useEffect(() => {
    refreshContainer()
    const id = setInterval(refreshContainer, 5000)
    return () => clearInterval(id)
  }, [refreshContainer])

  // Fetch API key statuses, engines, and models on mount
  useEffect(() => {
    const fetchKeys = async () => {
      try {
        const data = await getKeyStatuses()
        if (data.keys) {
          const map = {}
          data.keys.forEach(k => { map[k.provider] = k })
          setKeyStatuses(map)
          // Auto-select best source for current provider
          const current = map[provider]
          if (current?.available) {
            setKeySource(current.source) // 'env' or 'dotenv'
          }
        }
      } catch { /* backend not ready yet */ }
    }

    const fetchModelList = async () => {
      try {
        const data = await getModels()
        if (data.models?.length) {
          setFetchedModels(data.models)
          setModelsLoaded(true)
          // Auto-select first model for the default provider
          const firstForProvider = data.models.find(m => m.provider === provider)
          if (firstForProvider) setModel(firstForProvider.model_id)
        }
      } catch { /* backend not ready — models stay empty, Start disabled */ }
    }
    fetchKeys()
    fetchModelList()
  }, [provider])

  // Auto-stop frontend when agent finishes (done/error/max-steps)
  useEffect(() => {
    if (agentFinished && agentRunning) {
      setAgentRunning(false)
      setSessionId(null)
      clearFinished()
    }
  }, [agentFinished, agentRunning, clearFinished])

  // Auto-scroll timeline
  useEffect(() => {
    if (timelineRef.current) {
      timelineRef.current.scrollTop = timelineRef.current.scrollHeight
    }
  }, [steps])

  // Auto-scroll logs
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [logs])

  // Sync model when provider changes
  useEffect(() => {
    const list = modelsByProvider[provider] || []
    setModel(list.length > 0 ? list[0].value : '')
    // Auto-select key source based on availability
    const status = keyStatuses[provider]
    if (status?.available) {
      setKeySource(status.source)
    } else {
      setKeySource('ui')
    }
  }, [provider, keyStatuses])

  /** Validates inputs, auto-starts container if needed, and launches the agent. */
  const handleStart = async () => {
    if (keySource === 'ui' && !apiKey.trim()) return setError('API key is required')
    if (!task.trim()) return setError('Task description is required')
    setError('')
    clearSteps()
    clearLogs()

    try {
      if (!containerRunning) {
        await startContainer()
        await refreshContainer()
      }

      const res = await startAgent({
        task: task.trim(),
        apiKey: keySource === 'ui' ? apiKey.trim() : '', // empty = backend resolves from env
        model,
        maxSteps: Number(maxSteps),
        mode: runMode,
        provider,
        reasoningEffort: provider === 'openai' ? reasoningEffort : null,
      })
      if (res.error) return setError(res.error)
      setSessionId(res.session_id)
      setAgentRunning(true)
    } catch (e) {
      setError(`Failed to start: ${e.message}`)
    }
  }

  /** Stops the running agent session and clears the session ID. */
  const handleStop = async () => {
    if (!sessionId) return
    try { await stopAgent(sessionId) } catch { /* ignore */ }
    setAgentRunning(false)
    setSessionId(null)
  }

  /** Downloads current logs as a timestamped .txt file via a temporary Blob URL. */
  const handleDownloadLogs = () => {
    if (logs.length === 0) return
    const now = new Date()
    const pad = (n, w = 2) => String(n).padStart(w, '0')
    const filename = `CUA_logs_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.txt`
    const lines = logs.map(log => {
      const ts = formatTime(log.timestamp)
      return `[${ts}] [${(log.level || '').toUpperCase()}] ${log.message}`
    })
    const blob = new Blob([lines.join('\n')], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  /** Returns the emoji icon for a given action name, defaulting to ⚡. */
  const getActionIcon = (action) => ACTION_ICONS[action] || '⚡'

  return (
    <div className="wb">
      {/* Header */}
      <header className="wb-header">
        <div className="wb-header-left">
          <Link to="/" className="wb-back">← Back</Link>
          <h1>CUA Workbench</h1>
          <span className={`wb-status-pill ${containerRunning ? 'up' : 'down'}`}>
            {containerRunning ? 'Container Up' : 'Container Down'}
          </span>
          <span className={`wb-status-pill ${connected ? 'up' : 'down'}`}>
            {connected ? 'WS Connected' : 'WS Disconnected'}
          </span>
          {agentRunning && <span className="wb-status-pill running">Agent Running</span>}
        </div>
        <div className="wb-header-right">
          <span className="wb-step-counter">Steps: {steps.length}/{maxSteps}</span>
        </div>
      </header>

      <div className="wb-body">
        {/* Left: Config */}
        <aside className="wb-sidebar">
          {/* Runtime Mode */}
          <div className="wb-section">
            <label className="wb-label">Runtime Mode</label>
            <div className="wb-toggle-group">
              <button className="wb-toggle active" disabled title="Browser mode was removed from the backend runtime">
                🖥️ Desktop Only
              </button>
            </div>
            <p className="wb-key-source-label">Browser mode is no longer available in this build.</p>
          </div>

          {/* Provider & Model */}
          <div className="wb-section">
            <label className="wb-label">Provider</label>
            <select className="wb-select" value={provider} onChange={(e) => setProvider(e.target.value)} disabled={agentRunning}>
              {PROVIDERS.map(item => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
            <label className="wb-label">Model</label>
            <select className="wb-select" value={model} onChange={(e) => setModel(e.target.value)} disabled={agentRunning || models.length === 0}>
              {models.length > 0 ? models.map(m => <option key={m.value} value={m.value}>{m.label}</option>) : (
                <option value="">Loading models…</option>
              )}
            </select>
            {modelsLoaded && models.length === 0 && (
              <p className="wb-error" style={{ margin: '4px 0 0', fontSize: 11 }}>No models available for this provider.</p>
            )}
            <label className="wb-label">API Key Source</label>
            <div className="wb-key-source-group">
              <button className={`wb-key-src-btn ${keySource === 'ui' ? 'active' : ''}`} onClick={() => setKeySource('ui')} disabled={agentRunning} title="Enter key manually">
                ✏️ Manual
              </button>
              <button
                className={`wb-key-src-btn ${keySource === 'dotenv' ? 'active' : ''} ${keyStatuses[provider]?.source === 'dotenv' ? 'available' : ''}`}
                onClick={() => setKeySource('dotenv')}
                disabled={agentRunning || keyStatuses[provider]?.source !== 'dotenv'}
                title={keyStatuses[provider]?.source === 'dotenv' ? `Found in .env (${keyStatuses[provider]?.masked_key})` : 'No key in .env file'}
              >
                📄 .env {keyStatuses[provider]?.source === 'dotenv' && '✓'}
              </button>
              <button
                className={`wb-key-src-btn ${keySource === 'env' ? 'active' : ''} ${keyStatuses[provider]?.source === 'env' ? 'available' : ''}`}
                onClick={() => setKeySource('env')}
                disabled={agentRunning || keyStatuses[provider]?.source !== 'env'}
                title={keyStatuses[provider]?.source === 'env' ? `Found in system env (${keyStatuses[provider]?.masked_key})` : 'No system env variable set'}
              >
                💻 System {keyStatuses[provider]?.source === 'env' && '✓'}
              </button>
            </div>
            {keySource !== 'ui' && keyStatuses[provider]?.available && (
              <div className="wb-key-status">
                <span className="wb-key-badge ok">🔑 {keyStatuses[provider]?.masked_key}</span>
                <span className="wb-key-source-label">from {keySource === 'env' ? 'system variable' : '.env file'}</span>
              </div>
            )}
            {keySource !== 'ui' && !keyStatuses[provider]?.available && (
              <div className="wb-key-status">
                <span className="wb-key-badge missing">⚠️ No key found</span>
                <span className="wb-key-source-label">
                  Set {providerMeta.envVar}
                </span>
              </div>
            )}
            {keySource === 'ui' && (
              <>
                <label className="wb-label">API Key</label>
                <input type="password" className="wb-input" placeholder={providerMeta.placeholder} value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off" />
              </>
            )}
          </div>

          {/* Max Steps */}
          <div className="wb-section">
            <label className="wb-label">Max Steps</label>
            <input type="number" className="wb-input wb-input-sm" min={1} max={200} value={maxSteps} onChange={(e) => setMaxSteps(e.target.value)} disabled={agentRunning} />
          </div>

          {/* OpenAI Reasoning Effort */}
          {provider === 'openai' && (
            <div className="wb-section">
              <label className="wb-label">Reasoning Effort</label>
              <select className="wb-input" value={reasoningEffort} onChange={(e) => setReasoningEffort(e.target.value)} disabled={agentRunning}>
                <option value="none">None</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="xhigh">X-High</option>
              </select>
            </div>
          )}

          {/* Task */}
          <div className="wb-section wb-section-grow">
            <label className="wb-label">Task</label>
            <textarea className="wb-textarea" placeholder="Describe what the agent should do..." value={task} onChange={(e) => setTask(e.target.value)} disabled={agentRunning}
              onKeyDown={(e) => { if (e.key === 'Enter' && e.ctrlKey && !agentRunning) handleStart() }}
            />
            {error && <p className="wb-error">{error}</p>}
            <div className="wb-btn-row">
              <button className="wb-btn wb-btn-primary" onClick={handleStart} disabled={agentRunning || models.length === 0}>
                {agentRunning ? 'Running...' : models.length === 0 ? 'No Models Loaded' : 'Start Agent'}
              </button>
              <button className="wb-btn wb-btn-danger" onClick={handleStop} disabled={!agentRunning}>Stop</button>
              <button className="wb-btn wb-btn-secondary" onClick={() => { clearSteps(); clearLogs() }} disabled={agentRunning}>Clear</button>
            </div>
          </div>
        </aside>

        {/* Center: Live Screen */}
        <main className="wb-screen-area">
          <ScreenView screenshot={lastScreenshot} containerRunning={containerRunning} />

          {/* Progress bar */}
          {agentRunning && steps.length > 0 && (
            <div className="wb-progress">
              <div className="wb-progress-fill" style={{ width: `${Math.min((steps.length / maxSteps) * 100, 100)}%` }} />
            </div>
          )}
        </main>

        {/* Right: Timeline + Logs */}
        <aside className="wb-right-panel">
          {/* Timeline */}
          <div className="wb-timeline-section">
            <div className="wb-panel-header">
              <h3>Timeline ({steps.length})</h3>
            </div>
            <div className="wb-timeline" ref={timelineRef}>
              {steps.length === 0 && <p className="wb-empty">No steps yet.</p>}
              {steps.map((step, i) => (
                <div key={i} className={`wb-timeline-item ${step.error ? 'has-error' : ''} ${expandedStep === i ? 'expanded' : ''}`} onClick={() => setExpandedStep(expandedStep === i ? null : i)}>
                  <div className="wb-timeline-head">
                    <span className="wb-step-num">#{step.step_number}</span>
                    <span className="wb-action-icon">{getActionIcon(step.action?.action)}</span>
                    <span className="wb-action-name">{step.action?.action || 'unknown'}</span>
                    {step.action?.target && <span className="wb-action-target" title={step.action.target}>{step.action.target.length > 20 ? step.action.target.slice(0, 20) + '…' : step.action.target}</span>}
                    {step.action?.text && step.action.action !== 'done' && (
                      <span className="wb-action-text" title={step.action.text}>"{step.action.text.length > 20 ? step.action.text.slice(0, 20) + '…' : step.action.text}"</span>
                    )}
                    <span className="wb-step-time">{formatTime(step.timestamp)}</span>
                  </div>
                  {expandedStep === i && (
                    <div className="wb-timeline-detail">
                      {step.action?.reasoning && <p className="wb-reasoning">{step.action.reasoning}</p>}
                      {step.action?.coordinates && <p className="wb-coords">Coords: [{step.action.coordinates.join(', ')}]</p>}
                      {step.error && <p className="wb-step-error">Error: {step.error}</p>}
                      <pre className="wb-json">{JSON.stringify(step.action, null, 2)}</pre>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Logs */}
          <div className="wb-log-section">
            <div className="wb-panel-header">
              <h3>Logs ({logs.length})</h3>
              <div className="wb-log-actions">
                <button className="wb-download-btn" onClick={handleDownloadLogs} disabled={logs.length === 0} title="Download logs as .txt">⬇ Download</button>
                <button className="wb-clear-btn" onClick={clearLogs}>Clear</button>
              </div>
            </div>
            <div className="wb-logs" ref={logRef}>
              {logs.length === 0 && <p className="wb-empty">Waiting for logs...</p>}
              {logs.map((log, i) => (
                <div key={i} className="wb-log-entry">
                  <span className="wb-log-time">{formatTime(log.timestamp)}</span>
                  <span className={`wb-log-level ${log.level}`}>{log.level}</span>
                  <span className="wb-log-msg">{log.message}</span>
                </div>
              ))}
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
