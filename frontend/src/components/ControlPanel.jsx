import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { startAgent, stopAgent, getKeyStatuses, getEngines, getModels } from '../api'

const PROVIDERS = [
  { value: 'google', label: 'Google Gemini', envVar: 'GOOGLE_API_KEY', placeholder: 'AI...' },
  { value: 'anthropic', label: 'Anthropic Claude', envVar: 'ANTHROPIC_API_KEY', placeholder: 'sk-ant-...' },
  { value: 'openai', label: 'OpenAI GPT-5.4', envVar: 'OPENAI_API_KEY', placeholder: 'sk-...' },
]

/**
 * Left-panel form for configuring and controlling the CUA agent.
 * Handles provider/model selection, API key source, task input, and start/stop actions.
 */
export default function ControlPanel({
  containerRunning,
  agentRunning,
  setAgentRunning,
  sessionId,
  setSessionId,
  steps,
  clearSteps,
  onRefreshContainer,
}) {
  const [apiKey, setApiKey] = useState('')
  const [keySource, setKeySource] = useState('ui')
  const [keyStatuses, setKeyStatuses] = useState({})
  const [provider, setProvider] = useState('google')
  const [model, setModel] = useState('')
  const [task, setTask] = useState('')
  const [maxSteps, setMaxSteps] = useState(50)
  const [engine, setEngine] = useState('computer_use')
  const [engineList, setEngineList] = useState([])
  const [executionTarget, setExecutionTarget] = useState('docker')
  const [reasoningEffort, setReasoningEffort] = useState('low')
  const [error, setError] = useState('')
  const runtimeMode = 'desktop'

  // computer_use always runs in Docker
  const ENGINES_WITH_TARGET = ['computer_use']
  const getDefaultTarget = () => 'docker'

  // Model lists — fetched exclusively from /api/models (no hardcoded fallback)
  const [fetchedModels, setFetchedModels] = useState([])
  const [modelsLoaded, setModelsLoaded] = useState(false)

  // Derive per-provider lists from fetched data only
  /** Maps a model record to a { value, label } option for the select dropdown. */
  const toOption = (m) => ({ value: m.model_id, label: `${m.display_name} (${m.model_id})` })
  const modelsByProvider = fetchedModels.reduce((acc, item) => {
    acc[item.provider] = (acc[item.provider] || []).concat(toOption(item))
    return acc
  }, {})
  const models = modelsByProvider[provider] || []
  const providerMeta = PROVIDERS.find(item => item.value === provider) || PROVIDERS[0]

  // Fetch API key statuses, engines, and models on mount
  useEffect(() => {
    const fetchKeys = async () => {
      try {
        const data = await getKeyStatuses()
        if (data.keys) {
          const map = {}
          data.keys.forEach(k => { map[k.provider] = k })
          setKeyStatuses(map)
          const current = map[provider]
          if (current?.available) setKeySource(current.source)
        }
      } catch { /* backend not ready */ }
    }
    const fetchEngines = async () => {
      try {
        const data = await getEngines()
        if (data.engines?.length) setEngineList(data.engines)
      } catch { /* backend not ready */ }
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
      } catch { /* backend not ready — models will stay empty */ }
    }
    fetchKeys()
    fetchEngines()
    fetchModelList()
  }, [provider])

  /** Switches the active provider, resets the selected model, and auto-selects key source. */
  const handleProviderChange = (newProvider) => {
    setProvider(newProvider)
    const list = modelsByProvider[newProvider] || []
    setModel(list.length > 0 ? list[0].value : '')
    // Auto-select key source
    const status = keyStatuses[newProvider]
    if (status?.available) {
      setKeySource(status.source)
    } else {
      setKeySource('ui')
    }
  }

  /** Validates inputs and sends a start-agent request to the backend. */
  const handleStart = async () => {
    if (keySource === 'ui' && !apiKey.trim()) {
      setError('API key is required')
      return
    }
    if (!task.trim()) {
      setError('Task description is required')
      return
    }
    setError('')
    clearSteps()

    try {
      const res = await startAgent({
        task: task.trim(),
        apiKey: keySource === 'ui' ? apiKey.trim() : '',
        model,
        maxSteps: Number(maxSteps),
        mode: runtimeMode,
        engine,
        provider,
        executionTarget,
        reasoningEffort: provider === 'openai' ? reasoningEffort : null,
      })
      if (res.error) {
        setError(res.error)
        return
      }
      setSessionId(res.session_id)
      setAgentRunning(true)
    } catch (e) {
      setError(`Failed to start agent: ${e.message}`)
    }
  }

  /** Stops the currently running agent session. */
  const handleStop = async () => {
    if (!sessionId) return
    try {
      await stopAgent(sessionId)
    } catch {
      // ignore
    }
    setAgentRunning(false)
  }

  /** Triggers handleStart on Ctrl+Enter when agent is not running. */
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && e.ctrlKey && !agentRunning) {
      handleStart()
    }
  }

  return (
    <div className="left-panel">
      {/* API Config */}
      <div className="panel-section">
        <h3>API Configuration</h3>
        <select className="model-select" value={provider} onChange={(e) => handleProviderChange(e.target.value)} disabled={agentRunning}>
          {PROVIDERS.map(item => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </select>

        {/* Key Source Toggle */}
        <div className="key-source-row" style={{ display: 'flex', gap: 4, marginBottom: 6 }}>
          <button
            className={`key-src-btn ${keySource === 'ui' ? 'active' : ''}`}
            onClick={() => setKeySource('ui')}
            disabled={agentRunning}
            style={{ flex: 1, padding: '4px 6px', fontSize: 11, borderRadius: 4, border: '1px solid var(--border)', cursor: 'pointer', background: keySource === 'ui' ? 'var(--accent)' : 'var(--bg-secondary)', color: keySource === 'ui' ? '#fff' : 'var(--text-primary)' }}
          >
            ✏️ Manual
          </button>
          <button
            className={`key-src-btn ${keySource === 'dotenv' ? 'active' : ''}`}
            onClick={() => setKeySource('dotenv')}
            disabled={agentRunning || keyStatuses[provider]?.source !== 'dotenv'}
            style={{ flex: 1, padding: '4px 6px', fontSize: 11, borderRadius: 4, border: '1px solid var(--border)', cursor: 'pointer', background: keySource === 'dotenv' ? 'var(--accent)' : 'var(--bg-secondary)', color: keySource === 'dotenv' ? '#fff' : 'var(--text-primary)', opacity: keyStatuses[provider]?.source === 'dotenv' ? 1 : 0.4 }}
            title={keyStatuses[provider]?.source === 'dotenv' ? `Found (${keyStatuses[provider]?.masked_key})` : 'No key in .env'}
          >
            📄 .env {keyStatuses[provider]?.source === 'dotenv' ? '✓' : ''}
          </button>
          <button
            className={`key-src-btn ${keySource === 'env' ? 'active' : ''}`}
            onClick={() => setKeySource('env')}
            disabled={agentRunning || keyStatuses[provider]?.source !== 'env'}
            style={{ flex: 1, padding: '4px 6px', fontSize: 11, borderRadius: 4, border: '1px solid var(--border)', cursor: 'pointer', background: keySource === 'env' ? 'var(--accent)' : 'var(--bg-secondary)', color: keySource === 'env' ? '#fff' : 'var(--text-primary)', opacity: keyStatuses[provider]?.source === 'env' ? 1 : 0.4 }}
            title={keyStatuses[provider]?.source === 'env' ? `Found (${keyStatuses[provider]?.masked_key})` : 'No system env var'}
          >
            💻 System {keyStatuses[provider]?.source === 'env' ? '✓' : ''}
          </button>
        </div>

        {keySource !== 'ui' && keyStatuses[provider]?.available && (
          <div style={{ fontSize: 11, color: 'var(--success, #4caf50)', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 4 }}>
            🔑 <span>{keyStatuses[provider]?.masked_key}</span>
            <span style={{ color: 'var(--text-secondary)' }}>({keySource === 'env' ? 'system variable' : '.env file'})</span>
          </div>
        )}
        {keySource !== 'ui' && !keyStatuses[provider]?.available && (
          <div style={{ fontSize: 11, color: 'var(--error, #f44336)', marginBottom: 6 }}>
            ⚠️ No key found — set {providerMeta.envVar}
          </div>
        )}

        {keySource === 'ui' && (
          <input
            type="password"
            className="api-key-input"
            placeholder={`${providerMeta.label} API Key`}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="off"
          />
        )}

        <select className="model-select" value={model} onChange={(e) => setModel(e.target.value)} disabled={models.length === 0}>
          {models.length > 0 ? models.map((m) => (
            <option key={m.value} value={m.value}>{m.label}</option>
          )) : (
            <option value="">Loading models…</option>
          )}
        </select>
        {modelsLoaded && models.length === 0 && (
          <p style={{ color: 'var(--error)', fontSize: 11, margin: '4px 0 0' }}>No models available for this provider.</p>
        )}
        <select className="model-select" value={engine} onChange={(e) => { setEngine(e.target.value); setExecutionTarget(getDefaultTarget(e.target.value)) }} disabled={agentRunning}>
          {engineList.length > 0 ? engineList.map(e => (
            <option key={e.value} value={e.value}>{e.label}</option>
          )) : (
            <>
              <option value="computer_use">🖥️ Computer Use (Native CU Protocol)</option>
            </>
          )}
        </select>
        {ENGINES_WITH_TARGET.includes(engine) && (
          <select
            className="model-select"
            value={executionTarget}
            onChange={(e) => setExecutionTarget(e.target.value)}
            disabled={agentRunning}
            title="Where to run the engine — Docker (Ubuntu container)"
          >
            <option value="docker">🐳 Run in Docker (Ubuntu Container)</option>
          </select>
        )}
        <p style={{ color: 'var(--text-secondary)', fontSize: 11, margin: '4px 0 0' }}>
          Runtime: desktop mode only.
        </p>
        {provider === 'openai' && (
          <select
            className="model-select"
            value={reasoningEffort}
            onChange={(e) => setReasoningEffort(e.target.value)}
            disabled={agentRunning}
            title="OpenAI reasoning effort — higher values improve accuracy but increase cost and latency"
          >
            <option value="none">🧠 Reasoning: None</option>
            <option value="low">🧠 Reasoning: Low</option>
            <option value="medium">🧠 Reasoning: Medium</option>
            <option value="high">🧠 Reasoning: High</option>
            <option value="xhigh">🧠 Reasoning: X-High</option>
          </select>
        )}
        <Link to="/workbench" className="btn btn-secondary" style={{ textAlign: 'center', marginTop: 6, display: 'block', textDecoration: 'none' }}>
          Open Workbench →
        </Link>
      </div>

      {/* Task Input */}
      <div className="panel-section">
        <h3>Task</h3>
        <textarea
          className="task-input"
          placeholder="Describe what the agent should do...&#10;&#10;e.g., Open Chrome and search for 'weather in New York'"
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={agentRunning}
        />
        <div className="step-info" style={{ marginTop: 8 }}>
          <span>
            Max steps: <strong>{maxSteps}</strong>
          </span>
          <input
            type="number"
            className="max-steps-input"
            min={1}
            max={200}
            value={maxSteps}
            onChange={(e) => setMaxSteps(e.target.value)}
            disabled={agentRunning}
          />
        </div>
        {error && <p style={{ color: 'var(--error)', fontSize: 12, marginTop: 6 }}>{error}</p>}
        <div className="btn-row">
          <button
            className="btn btn-primary"
            disabled={agentRunning || !containerRunning || models.length === 0}
            onClick={handleStart}
          >
            {!containerRunning ? 'Start Container First' : models.length === 0 ? 'No Models Loaded' : 'Start Agent'}
          </button>
          <button className="btn btn-danger" disabled={!agentRunning} onClick={handleStop}>
            Stop
          </button>
        </div>
      </div>

      {/* Action History */}
      <div className="panel-section" style={{ flexShrink: 0 }}>
        <h3>Action History ({steps.length}){maxSteps > 0 && agentRunning && ` / ${maxSteps}`}</h3>
        {agentRunning && steps.length > 0 && (
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${Math.min((steps.length / maxSteps) * 100, 100)}%` }}
            />
          </div>
        )}
      </div>
      <div className="action-list">
        {steps.length === 0 && (
          <p style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '8px 0' }}>
            No actions yet. Start the agent to begin.
          </p>
        )}
        {steps.map((step, i) => (
          <div key={i} className={`action-item ${step.error ? 'has-error' : ''}`}>
            <span className="action-step">#{step.step_number}</span>
            <div className="action-details">
              <div className="action-header">
                <span className={`action-badge action-badge--${step.action?.action || 'unknown'}`}>
                  {step.action?.action || 'unknown'}
                </span>
                {step.action?.coordinates && (
                  <span className="action-coords">
                    ({step.action.coordinates[0]}, {step.action.coordinates[1]})
                  </span>
                )}
                {step.action?.text && step.action.action !== 'done' && (
                  <span className="action-text" title={step.action.text}>
                    &ldquo;{step.action.text.length > 30 ? step.action.text.slice(0, 30) + '...' : step.action.text}&rdquo;
                  </span>
                )}
              </div>
              {step.action?.reasoning && (
                <p className="action-reasoning">{step.action.reasoning}</p>
              )}
              {step.error && (
                <p className="action-reasoning" style={{ color: 'var(--error)' }}>
                  {step.error}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
