import { useState, useEffect, useRef, useCallback } from 'react'
import useWebSocket from '../hooks/useWebSocket'
import { startAgent, stopAgent, getContainerStatus, startContainer, stopContainer, getKeyStatuses, getModels, validateKey } from '../api'
import ScreenView from '../components/ScreenView'
import SafetyModal from '../components/SafetyModal'
import CompletionBanner from '../components/CompletionBanner'
import ToastContainer, { useToasts } from '../components/ToastContainer'
import WelcomeOverlay from '../components/WelcomeOverlay'
import formatTime from '../utils/formatTime'
import { estimateCost } from '../utils/pricing'
import { getSessionHistory, addSessionToHistory, clearSessionHistory } from '../utils/sessionHistory'
import { getTheme, setTheme as applyTheme, initTheme } from '../utils/theme'
import {
  MousePointer2, Keyboard, Type as TypeIcon, ScrollText, Globe, ArrowLeft,
  ArrowRight, Timer, Clipboard, Copy, RefreshCw, Plus, X as XIcon,
  Shuffle, Search, Monitor, Rocket, Camera, CheckCircle2, AlertCircle,
  Zap, Download, Sun, Moon, BookOpen, Check, History, FileJson, FileText, Trash2, HelpCircle
} from 'lucide-react'
import './Workbench.css'

const PROVIDERS = [
  { value: 'google', label: 'Google Gemini', envVar: 'GOOGLE_API_KEY', placeholder: 'Paste your Google API key' },
  { value: 'anthropic', label: 'Anthropic Claude', envVar: 'ANTHROPIC_API_KEY', placeholder: 'Paste your Anthropic API key' },
  { value: 'openai', label: 'OpenAI GPT-5.4', envVar: 'OPENAI_API_KEY', placeholder: 'Paste your OpenAI API key' },
]

const ICON_SIZE = 14
const ACTION_ICON_MAP = {
  click: MousePointer2, double_click: MousePointer2, right_click: MousePointer2, hover: MousePointer2,
  type: Keyboard, fill: TypeIcon, key: Keyboard, hotkey: Keyboard,
  paste: Clipboard, copy: Copy,
  open_url: Globe, reload: RefreshCw, go_back: ArrowLeft, go_forward: ArrowRight,
  new_tab: Plus, close_tab: XIcon, switch_tab: Shuffle,
  scroll: ScrollText, scroll_to: ScrollText,
  get_text: Search, find_element: Search, evaluate_js: Monitor,
  focus_window: Monitor, open_app: Rocket,
  wait: Timer, wait_for: Timer, screenshot_region: Camera,
  done: CheckCircle2, error: AlertCircle,
}

const ACTION_LABEL_MAP = {
  click: 'Clicked', double_click: 'Double-clicked', right_click: 'Right-clicked', hover: 'Hovered',
  type: 'Typed text', fill: 'Filled field', key: 'Pressed key', hotkey: 'Pressed keys',
  paste: 'Pasted', copy: 'Copied',
  open_url: 'Opened URL', reload: 'Reloaded page', go_back: 'Went back', go_forward: 'Went forward',
  new_tab: 'Opened new tab', close_tab: 'Closed tab', switch_tab: 'Switched tab',
  scroll: 'Scrolled', scroll_to: 'Scrolled to',
  get_text: 'Read text', find_element: 'Found element', evaluate_js: 'Ran script',
  focus_window: 'Switched window', open_app: 'Opened app',
  wait: 'Waited', wait_for: 'Waited for', screenshot_region: 'Captured region',
  done: 'Finished', error: 'Error',
}

const MODEL_HINTS = {
  'gemini-3-flash-preview': { hint: 'Fast and affordable — good for simple tasks', tier: 'Budget' },
  'gemini-3.1-pro-preview': { hint: 'Stronger reasoning — computer use support unconfirmed', tier: 'Mid-range' },
  'claude-sonnet-4-6': { hint: 'Balanced speed and capability — recommended for most tasks', tier: 'Mid-range', recommended: true },
  'claude-opus-4-6': { hint: 'Most capable — best for complex multi-step tasks', tier: 'Premium' },
  'gpt-5.4': { hint: 'OpenAI\'s built-in computer use model', tier: 'Mid-range' },
}

const TASK_EXAMPLES = [
  'Open Chrome and search for "weather in New York"',
  'Open LibreOffice Writer and type a short letter',
  'Open the file manager and create a folder called "Projects"',
  'Open the terminal and check the current date',
  'Open Chrome and navigate to wikipedia.org',
]

const SETTINGS_KEY = 'cua_settings_v1'
const MAX_TASK_LENGTH = 10000

function loadSettings() {
  try { return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {} } catch { return {} }
}
function saveSettings(s) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)) } catch { /* ignore */ }
}
export default function Workbench() {
  const { connected, lastScreenshot, logs, steps, agentFinished, safetyPrompt, clearLogs, clearSteps, clearFinished, clearSafetyPrompt } = useWebSocket()
  const { toasts, addToast } = useToasts()

  // Container state
  const [containerRunning, setContainerRunning] = useState(false)
  const [containerLoading, setContainerLoading] = useState(false)
  const [containerError, setContainerError] = useState('')

  // Agent state
  const [agentRunning, setAgentRunning] = useState(false)
  const [sessionId, setSessionId] = useState(null)
  const [starting, setStarting] = useState(false)
  const [completionData, setCompletionData] = useState(null)

  // Config
  const saved = loadSettings()
  const [provider, setProvider] = useState(saved.provider || 'google')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [keySource, setKeySource] = useState('ui')
  const [keyStatuses, setKeyStatuses] = useState({})
  const [task, setTask] = useState('')
  const [maxSteps, setMaxSteps] = useState(saved.maxSteps || 50)
  const [reasoningEffort, setReasoningEffort] = useState(saved.reasoningEffort || 'low')
  const [error, setError] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)

  // Theme
  const [theme, setThemeState] = useState(getTheme)

  // Key validation
  const [keyValidation, setKeyValidation] = useState(null)

  // Session history
  const [showHistory, setShowHistory] = useState(false)
  const [sessionHistory, setSessionHistoryState] = useState(getSessionHistory)

  // Logs panel
  const [logsExpanded, setLogsExpanded] = useState(false)

  // Welcome/help overlay
  const [showWelcome, setShowWelcome] = useState(false)

  // Timeline expansion
  const [expandedStep, setExpandedStep] = useState(null)

  // Refs
  const timelineRef = useRef(null)
  const logRef = useRef(null)
  const sessionStartTime = useRef(null)

  // Dynamic model list
  const [fetchedModels, setFetchedModels] = useState([])
  const [modelsLoaded, setModelsLoaded] = useState(false)
  const [modelsLoading, setModelsLoading] = useState(true)
  const toModelOption = (m) => ({ value: m.model_id, label: m.display_name })
  const modelsByProvider = fetchedModels.reduce((acc, item) => {
    acc[item.provider] = (acc[item.provider] || []).concat(toModelOption(item))
    return acc
  }, {})
  const models = modelsByProvider[provider] || []
  const providerMeta = PROVIDERS.find(item => item.value === provider) || PROVIDERS[0]

  // Init theme on mount
  useEffect(() => { initTheme() }, [])

  // Persist settings
  useEffect(() => {
    saveSettings({ provider, maxSteps, reasoningEffort })
  }, [provider, maxSteps, reasoningEffort])

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
      setModelsLoading(true)
      try {
        const data = await getModels()
        if (data.models?.length) {
          setFetchedModels(data.models)
          setModelsLoaded(true)
          const firstForProvider = data.models.find(m => m.provider === provider)
          if (firstForProvider) setModel(firstForProvider.model_id)
        }
      } catch { /* backend not ready */ }
      setModelsLoading(false)
    }
    fetchKeys()
    fetchModelList()
  }, [provider])

  // Auto-stop frontend when agent finishes
  useEffect(() => {
    if (agentFinished && agentRunning) {
      setAgentRunning(false)
      setSessionId(null)
      const elapsed = sessionStartTime.current ? Math.round((Date.now() - sessionStartTime.current) / 1000) : null
      sessionStartTime.current = null
      setCompletionData({ ...agentFinished, elapsedSeconds: elapsed })
      const status = agentFinished.status || 'completed'
      addToast(
        status === 'completed' ? `Task complete — ${agentFinished.steps ?? steps.length} steps` : `Task ${status}`,
        status === 'completed' ? 'success' : 'error'
      )
      addSessionToHistory({
        task: task.slice(0, 100),
        model,
        modelDisplayName: (fetchedModels.find(m => m.model_id === model))?.display_name || model,
        provider,
        steps: agentFinished.steps ?? steps.length,
        status,
        timestamp: new Date().toISOString(),
      })
      setSessionHistoryState(getSessionHistory())
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

  const handleStartContainer = async () => {
    setContainerLoading(true)
    setContainerError('')
    try {
      await startContainer()
      await refreshContainer()
    } catch {
      setContainerError('Could not start the environment. Please ensure the system is set up correctly.')
    } finally {
      setContainerLoading(false)
    }
  }

  const handleStopContainer = async () => {
    setContainerLoading(true)
    setContainerError('')
    try {
      await stopContainer()
      await refreshContainer()
    } catch {
      setContainerError('Could not stop environment.')
    } finally {
      setContainerLoading(false)
    }
  }

  const handleStart = async () => {
    if (keySource === 'ui' && !apiKey.trim()) return setError('Please enter your API key above to continue.')
    if (!task.trim()) return setError('Task description is required')
    setError('')
    clearSteps()
    clearLogs()
    setCompletionData(null)
    setStarting(true)

    try {
      if (!containerRunning) {
        setContainerLoading(true)
        setContainerError('')
        try {
          await startContainer()
          await refreshContainer()
        } catch {
          setContainerError('Could not start the environment. Please ensure the system is set up correctly.')
          setStarting(false)
          setContainerLoading(false)
          return
        }
        setContainerLoading(false)
      }

      const res = await startAgent({
        task: task.trim(),
        apiKey: keySource === 'ui' ? apiKey.trim() : '',
        model,
        maxSteps: Number(maxSteps),
        mode: 'desktop',
        provider,
        reasoningEffort: provider === 'openai' ? reasoningEffort : null,
      })
      if (res.error) {
        setStarting(false)
        return setError(res.error)
      }
      setSessionId(res.session_id)
      setAgentRunning(true)
      sessionStartTime.current = Date.now()
      addToast('Agent started', 'success')
    } catch (e) {
      setError(`Failed to start: ${e.message}`)
    }
    setStarting(false)
  }

  const handleStop = async () => {
    if (!sessionId) return
    try {
      await stopAgent(sessionId)
      addToast('Agent stopped', 'info')
    } catch {
      setError('Could not stop the agent. Try again or restart the environment.')
    }
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

  /** Copies current logs as plain text to the clipboard. */
  const handleCopyLogs = async () => {
    if (logs.length === 0) return
    const lines = logs.map(log => {
      const ts = formatTime(log.timestamp)
      return `[${ts}] [${(log.level || '').toUpperCase()}] ${log.message}`
    })
    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      addToast('Logs copied to clipboard', 'success')
    } catch {
      addToast('Failed to copy logs', 'error')
    }
  }

  /** Returns the SVG icon for a given action name. */
  const getActionIcon = (action) => { const Icon = ACTION_ICON_MAP[action] || Zap; return <Icon size={ICON_SIZE} /> }

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setThemeState(next)
    applyTheme(next)
  }

  const handleValidateKey = async () => {
    if (!apiKey.trim() || apiKey.length < 8) return
    setKeyValidation('checking')
    try {
      const res = await validateKey(provider, apiKey.trim())
      setKeyValidation(res)
    } catch {
      setKeyValidation({ valid: false, message: 'Validation failed' })
    }
  }

  const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

  const handleExportJSON = () => {
    const modelMeta = fetchedModels.find(m => m.model_id === model)
    const cost = estimateCost(model, steps.length)
    const data = {
      task, model, provider,
      modelDisplayName: modelMeta?.display_name || model,
      steps: steps.map(s => ({ step_number: s.step_number, action: s.action, actionLabel: ACTION_LABEL_MAP[s.action?.action] || s.action?.action || 'Unknown', error: s.error, timestamp: s.timestamp })),
      logs: logs.map(l => ({ timestamp: l.timestamp, level: l.level, message: l.message })),
      summary: {
        totalSteps: steps.length,
        estimatedCost: cost ? `~$${cost.cost.toFixed(4)}` : null,
        costNote: cost?.note || null,
      },
      exportedAt: new Date().toISOString(),
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `cua_session_${new Date().toISOString().replace(/[:.]/g, '-')}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleExportHTML = () => {
    const modelMeta = fetchedModels.find(m => m.model_id === model)
    const displayModel = modelMeta?.display_name || model
    const cost = estimateCost(model, steps.length)
    const html = `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>CUA Session Report</title>
<style>body{font-family:system-ui;max-width:800px;margin:40px auto;padding:0 20px;color:#333}h1{color:#6c63ff}.summary{background:#f5f5ff;border:1px solid #e0e0ff;border-radius:8px;padding:16px;margin:16px 0}.summary p{margin:4px 0}.summary strong{display:inline-block;min-width:120px}table{width:100%;border-collapse:collapse;margin:16px 0}th,td{padding:8px;border:1px solid #ddd;text-align:left}th{background:#f5f5f5}.step{margin:8px 0;padding:10px 12px;background:#f9f9f9;border-radius:6px;border-left:3px solid #6c63ff}.step-label{font-weight:600;color:#333}.reasoning{color:#666;font-style:italic;margin-top:4px}.error{color:#dc3545}h2{margin-top:32px;border-bottom:1px solid #eee;padding-bottom:8px}.footer{margin-top:40px;padding-top:16px;border-top:1px solid #eee;font-size:12px;color:#999}</style></head><body>
<h1>Session Report</h1>
<div class="summary">
<p><strong>Task:</strong> ${esc(task)}</p>
<p><strong>Model:</strong> ${esc(displayModel)} (${esc(provider)})</p>
<p><strong>Steps:</strong> ${steps.length}</p>
${cost ? `<p><strong>Estimated Cost:</strong> ~$${cost.cost.toFixed(4)} <span style="color:#999;font-size:12px">(${esc(cost.note)})</span></p>` : ''}
<p><strong>Generated:</strong> ${new Date().toLocaleString()}</p>
</div>
<h2>Timeline</h2>
${steps.map(s => {
  const label = ACTION_LABEL_MAP[s.action?.action] || s.action?.action || 'Unknown'
  return `<div class="step"><span class="step-label">#${s.step_number} &mdash; ${esc(label)}</span>${s.action?.reasoning ? `<div class="reasoning">${esc(s.action.reasoning)}</div>` : ''}${s.error ? `<div class="error">${esc(s.error)}</div>` : ''}</div>`
}).join('')}
<h2>Logs</h2>
<table><tr><th>Time</th><th>Level</th><th>Message</th></tr>
${logs.map(l => `<tr><td>${formatTime(l.timestamp)}</td><td>${esc(l.level)}</td><td>${esc(l.message)}</td></tr>`).join('')}
</table>
<div class="footer">Generated by CUA — Computer Using Agent</div>
</body></html>`
    const blob = new Blob([html], { type: 'text/html' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `cua_session_${new Date().toISOString().replace(/[:.]/g, '-')}.html`
    a.click()
    URL.revokeObjectURL(url)
  }

  const costEstimate = estimateCost(model, steps.length)

  const hasKey = keySource === 'ui' ? apiKey.trim().length >= 8 : !!keyStatuses[provider]?.available
  const canStart = models.length > 0 && !agentRunning && !starting && hasKey

  return (
    <div className="wb">
      {/* Header */}
      <header className="wb-header">
        <div className="wb-header-left">
          <h1><span style={{ color: 'var(--accent)' }}>CUA</span> <span style={{ fontWeight: 400, color: 'var(--text-secondary)', fontSize: 13 }}>Computer Using Agent</span></h1>
          <span style={{ fontSize: 11, color: 'var(--text-secondary)', opacity: 0.6 }}>v1.0.0</span>
          <span className={`wb-status-pill ${containerRunning ? 'up' : 'down'}`}>
            {containerRunning ? 'Environment Ready' : 'Not Started'}
          </span>
          {!containerRunning && !containerLoading && (
            <button className="wb-btn wb-btn-secondary wb-header-btn" onClick={handleStartContainer} disabled={containerLoading}>
              Start Environment
            </button>
          )}
          {containerRunning && !agentRunning && (
            <button className="wb-btn wb-btn-secondary wb-header-btn" onClick={handleStopContainer} disabled={containerLoading}>
              Stop Environment
            </button>
          )}
          {containerLoading && <span className="wb-status-pill running">Starting…</span>}
          {containerError && <span style={{ fontSize: 12, color: 'var(--error)' }}>{containerError}</span>}
          {!connected && <span className="wb-status-pill down">Reconnecting…</span>}
          {agentRunning && <span className="wb-status-pill running">Agent Running</span>}
        </div>
        <div className="wb-header-right">
          {costEstimate && steps.length > 0 && (
            <span className="wb-cost" title={costEstimate.note}>
              ~${costEstimate.cost.toFixed(4)}
            </span>
          )}
          <span className="wb-step-counter">Steps: {steps.length}/{maxSteps}</span>
          <a href="/docs" target="_blank" rel="noopener noreferrer" className="wb-header-link" title="API Documentation" aria-label="API Documentation">
            <BookOpen size={16} />
          </a>
          <button onClick={() => setShowWelcome(true)} className="wb-theme-toggle" title="How it works" aria-label="How it works">
            <HelpCircle size={16} />
          </button>
          <button onClick={toggleTheme} className="wb-theme-toggle" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </div>
      </header>

      <CompletionBanner
        finishData={completionData}
        stepCount={steps.length}
        costEstimate={completionData ? estimateCost(model, completionData.steps ?? steps.length) : null}
        onDismiss={() => setCompletionData(null)}
      />

      <div className="wb-body">
        {/* Left: Config */}
        <aside className="wb-sidebar">
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
              {modelsLoading ? (
                <option value="">Loading models…</option>
              ) : models.length > 0 ? models.map(m => <option key={m.value} value={m.value}>{m.label}</option>) : (
                <option value="">No models available</option>
              )}
            </select>
            {model && MODEL_HINTS[model] && (
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '4px 0 0', display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ padding: '1px 6px', borderRadius: 8, fontSize: 11, background: MODEL_HINTS[model].recommended ? 'var(--accent)' : 'var(--bg-primary)', color: MODEL_HINTS[model].recommended ? '#fff' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                  {MODEL_HINTS[model].tier}
                </span>
                <span>{MODEL_HINTS[model].hint}</span>
              </div>
            )}
            {modelsLoaded && models.length === 0 && (
              <p className="wb-error" style={{ margin: '4px 0 0' }}>No models available for this provider.</p>
            )}
            <label className="wb-label">API Key Source</label>
            <div className="wb-key-source-group">
              <button className={`wb-key-src-btn ${keySource === 'ui' ? 'active' : ''}`} onClick={() => setKeySource('ui')} disabled={agentRunning} title="Enter key manually" aria-label="Enter API key manually">
                Manual
              </button>
              {keyStatuses[provider]?.source === 'dotenv' && (
              <button
                className={`wb-key-src-btn ${keySource === 'dotenv' ? 'active' : ''} available`}
                onClick={() => setKeySource('dotenv')}
                disabled={agentRunning}
                title={`Found in config file (${keyStatuses[provider]?.masked_key})`}
                aria-label="Use API key from config file"
              >
                Config File ✓
              </button>
              )}
              {keyStatuses[provider]?.source === 'env' && (
              <button
                className={`wb-key-src-btn ${keySource === 'env' ? 'active' : ''} available`}
                onClick={() => setKeySource('env')}
                disabled={agentRunning}
                title={`Pre-configured (${keyStatuses[provider]?.masked_key})`}
                aria-label="Use pre-configured API key"
              >
                Pre-configured ✓
              </button>
              )}
            </div>
            {keySource !== 'ui' && keyStatuses[provider]?.available && (
              <div className="wb-key-status">
                <span className="wb-key-badge ok">{keyStatuses[provider]?.masked_key}</span>
                <span className="wb-key-source-label">from {keySource === 'env' ? 'pre-configured source' : 'config file'}</span>
              </div>
            )}
            {keySource !== 'ui' && !keyStatuses[provider]?.available && (
              <div className="wb-key-status">
                <span className="wb-key-badge missing">No key found</span>
                <span className="wb-key-source-label">
                  Set {providerMeta.envVar}
                </span>
              </div>
            )}
            {keySource === 'ui' && (
              <>
                <label className="wb-label">API Key</label>
                <div style={{ display: 'flex', gap: 4 }}>
                  <input type="password" className="wb-input" style={{ flex: 1 }} placeholder={providerMeta.placeholder} value={apiKey} onChange={(e) => { setApiKey(e.target.value); setKeyValidation(null) }} autoComplete="off" />
                  <button className="wb-btn wb-btn-secondary" style={{ padding: '6px 10px', flex: 'none' }} onClick={handleValidateKey} disabled={!apiKey.trim() || apiKey.length < 8 || keyValidation === 'checking'} title="Validate API key" aria-label="Validate API key">
                    {keyValidation === 'checking' ? '…' : <Check size={14} />}
                  </button>
                </div>
                {keyValidation && keyValidation !== 'checking' && (
                  <span style={{ fontSize: 12, color: keyValidation.valid ? 'var(--success)' : 'var(--error)', marginTop: 2, display: 'block' }}>
                    {keyValidation.message}
                  </span>
                )}
              </>
            )}
          </div>

          {/* Advanced Settings */}
          <div className="wb-section">
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              style={{
                width: '100%', padding: '4px 0', fontSize: 12,
                background: 'none', border: 'none', color: 'var(--text-secondary)',
                cursor: 'pointer', textAlign: 'left',
              }}
            >
              {showAdvanced ? '▾' : '▸'} Advanced Settings
            </button>
            {showAdvanced && (
              <div style={{ paddingLeft: 8, borderLeft: '2px solid var(--border)', marginTop: 4 }}>
                <label className="wb-label">Step Limit</label>
                <input type="number" className="wb-input wb-input-sm" min={1} max={200} value={maxSteps} onChange={(e) => setMaxSteps(e.target.value)} disabled={agentRunning} />
                <span style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginTop: 2 }}>Maximum actions the agent can take before stopping</span>
                {provider === 'openai' && (
                  <>
                    <label className="wb-label">Thinking Depth</label>
                    <select className="wb-input" value={reasoningEffort} onChange={(e) => setReasoningEffort(e.target.value)} disabled={agentRunning}>
                      <option value="none">None — fastest, minimal reasoning</option>
                      <option value="low">Low — quick decisions</option>
                      <option value="medium">Medium — balanced</option>
                      <option value="high">High — thorough reasoning</option>
                      <option value="xhigh">Extra High — deepest analysis</option>
                    </select>
                  </>
                )}
              </div>
            )}
          </div>

          {/* Task */}
          <div className="wb-section wb-section-grow">
            <label className="wb-label">Task</label>
            <textarea className="wb-textarea" placeholder="Describe what the agent should do..." value={task}
              onChange={(e) => setTask(e.target.value.slice(0, MAX_TASK_LENGTH))} disabled={agentRunning}
              onKeyDown={(e) => { if (e.key === 'Enter' && e.ctrlKey && !agentRunning && !starting) handleStart() }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 2 }}>
              <span style={{ fontSize: 12, color: task.length > MAX_TASK_LENGTH * 0.9 ? 'var(--warning)' : 'var(--text-secondary)' }}>
                {task.length.toLocaleString()} / {MAX_TASK_LENGTH.toLocaleString()}
              </span>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Ctrl+Enter to start</span>
            </div>
            {/* Task examples */}
            {!task && !agentRunning && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                {TASK_EXAMPLES.slice(0, 3).map((ex, i) => (
                  <button
                    key={i}
                    onClick={() => setTask(ex)}
                    style={{
                      fontSize: 12, padding: '3px 8px', borderRadius: 12, cursor: 'pointer',
                      background: 'var(--bg-primary)', color: 'var(--text-secondary)',
                      border: '1px solid var(--border)', whiteSpace: 'nowrap', overflow: 'hidden',
                      textOverflow: 'ellipsis', maxWidth: '100%',
                    }}
                  >
                    {ex}
                  </button>
                ))}
              </div>
            )}
            {error && <p className="wb-error">{error}</p>}
            <div className="wb-btn-row">
              <button className="wb-btn wb-btn-primary" onClick={handleStart} disabled={!canStart}>
                {starting ? 'Starting…' : agentRunning ? 'Running…' : modelsLoading ? 'Loading Models…' : models.length === 0 ? 'No Models for This Provider' : !hasKey ? 'Enter API Key to Start' : 'Start Agent'}
              </button>
              <button className="wb-btn wb-btn-danger" onClick={handleStop} disabled={!agentRunning}>Stop</button>
              <button className="wb-btn wb-btn-secondary" onClick={() => { clearSteps(); clearLogs(); setCompletionData(null) }} disabled={agentRunning} aria-label="Clear steps and logs">Clear</button>
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
              <h3>{showHistory ? 'Session History' : `Timeline (${steps.length})`}</h3>
              <button onClick={() => setShowHistory(!showHistory)} className="wb-clear-btn" aria-label={showHistory ? 'Show timeline' : 'Show session history'}>
                {showHistory ? 'Timeline' : <History size={14} />}
              </button>
            </div>
            {showHistory ? (
              <div className="wb-timeline">
                {sessionHistory.length === 0 && <p className="wb-empty">Complete a task to see session history here.</p>}
                {sessionHistory.map((s, i) => (
                  <div key={i} className="wb-timeline-item" style={{ cursor: 'default' }}>
                    <div className="wb-timeline-head">
                      <span className={`wb-log-level ${s.status === 'completed' ? 'info' : 'error'}`}>{s.status}</span>
                      <span className="wb-action-name" style={{ flex: 1, fontWeight: 400 }}>{s.task}</span>
                      <span className="wb-step-time">{s.steps} steps</span>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', paddingLeft: 4, marginTop: 2 }}>
                      {s.modelDisplayName || s.model} · {new Date(s.timestamp).toLocaleString()}
                    </div>
                  </div>
                ))}
                {sessionHistory.length > 0 && (
                  <button onClick={() => { clearSessionHistory(); setSessionHistoryState([]) }} className="wb-clear-btn" style={{ margin: '8px auto', display: 'block' }}>Clear History</button>
                )}
              </div>
            ) : (
            <div className="wb-timeline" ref={timelineRef}>
              {steps.length === 0 && <p className="wb-empty">Start a task to see the agent's actions here.</p>}
              {steps.map((step, i) => (
                <div key={i} className={`wb-timeline-item ${step.error ? 'has-error' : ''} ${expandedStep === i ? 'expanded' : ''}`}
                  onClick={() => setExpandedStep(expandedStep === i ? null : i)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpandedStep(expandedStep === i ? null : i) } }}
                  role="button" tabIndex={0} aria-expanded={expandedStep === i}
                >
                  <div className="wb-timeline-head">
                    <span className="wb-step-num">#{step.step_number}</span>
                    <span className="wb-action-icon">{getActionIcon(step.action?.action)}</span>
                    <span className="wb-action-name">{ACTION_LABEL_MAP[step.action?.action] || step.action?.action || 'Unknown'}</span>
                    {step.action?.target && <span className="wb-action-target" title={step.action.target}>{step.action.target.length > 20 ? step.action.target.slice(0, 20) + '…' : step.action.target}</span>}
                    {step.action?.text && step.action.action !== 'done' && (
                      <span className="wb-action-text" title={step.action.text}>"{step.action.text.length > 20 ? step.action.text.slice(0, 20) + '…' : step.action.text}"</span>
                    )}
                    <span className="wb-step-time">{formatTime(step.timestamp)}</span>
                  </div>
                  {expandedStep === i && (
                    <div className="wb-timeline-detail">
                      {step.action?.reasoning && <p className="wb-reasoning">{step.action.reasoning}</p>}
                      {!step.action?.reasoning && <p className="wb-reasoning" style={{ fontStyle: 'italic', opacity: 0.6 }}>No explanation provided</p>}
                      {step.error && <p className="wb-step-error">Error: {step.error}</p>}
                      <details className="wb-raw-details" onClick={(e) => e.stopPropagation()}>
                        <summary style={{ fontSize: 12, color: 'var(--text-secondary)', cursor: 'pointer', userSelect: 'none' }}>Show raw data</summary>
                        {step.action?.coordinates && <p className="wb-coords">Coords: [{step.action.coordinates.join(', ')}]</p>}
                        <pre className="wb-json">{JSON.stringify(step.action, null, 2)}</pre>
                      </details>
                    </div>
                  )}
                </div>
              ))}
            </div>
            )}
          </div>

          {/* Logs */}
          <div className={`wb-log-section ${logsExpanded ? 'expanded' : 'collapsed'}`}>
            <div className="wb-panel-header" onClick={() => setLogsExpanded(!logsExpanded)} style={{ cursor: 'pointer', userSelect: 'none' }}>
              <h3>{logsExpanded ? '▾' : '▸'} Logs {logs.length > 0 ? `(${logs.length})` : ''}</h3>
              <div className="wb-log-actions" onClick={(e) => e.stopPropagation()}>
                <button className="wb-download-btn" onClick={handleExportJSON} disabled={steps.length === 0 && logs.length === 0} title="Export session as JSON" aria-label="Export as JSON"><FileJson size={14} /></button>
                <button className="wb-download-btn" onClick={handleExportHTML} disabled={steps.length === 0 && logs.length === 0} title="Export session as HTML report" aria-label="Export as HTML"><FileText size={14} /></button>
                <button className="wb-download-btn" onClick={handleDownloadLogs} disabled={logs.length === 0} title="Download logs as .txt" aria-label="Download logs"><Download size={14} /></button>
                <button className="wb-download-btn" onClick={handleCopyLogs} disabled={logs.length === 0} title="Copy logs to clipboard" aria-label="Copy logs"><Copy size={14} /></button>
                <button className="wb-clear-btn" onClick={clearLogs} aria-label="Clear logs"><Trash2 size={14} /></button>
              </div>
            </div>
            {logsExpanded && (
            <div className="wb-logs" ref={logRef}>
              {logs.length === 0 && <p className="wb-empty">Logs will appear here once a task is running.</p>}
              {logs.map((log, i) => (
                <div key={i} className="wb-log-entry">
                  <span className="wb-log-time">{formatTime(log.timestamp)}</span>
                  <span className={`wb-log-level ${log.level}`}>{log.level === 'info' ? 'Info' : log.level === 'error' ? 'Error' : log.level === 'warning' ? 'Warning' : log.level === 'debug' ? 'Debug' : log.level}</span>
                  <span className="wb-log-msg">{log.message}</span>
                </div>
              ))}
            </div>
            )}
          </div>
        </aside>
      </div>
      <SafetyModal prompt={safetyPrompt} onDismiss={clearSafetyPrompt} />
      <ToastContainer toasts={toasts} />
      <WelcomeOverlay show={showWelcome} onDismiss={() => setShowWelcome(false)} />
    </div>
  )
}
