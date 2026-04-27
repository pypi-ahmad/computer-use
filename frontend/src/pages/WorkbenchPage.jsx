// === merged from frontend/src/pages/Workbench.jsx ===
/**
 * Workbench — the single-page computer-use agent UI.
 *
 * After PR <frontend-workbench-split>, this file is a thin
 * composition layer. Session lifecycle lives in
 * :mod:`useSessionController`; each visual region is its own panel
 * component under ``./workbench/``. The page keeps only:
 *
 *   - config form state (provider / model / API key / task /
 *     advanced settings)
 *   - environment (container) state
 *   - theme, onboarding, and UI-ephemeral state (logs expanded,
 *     history drawer open, expanded-step)
 *   - wiring between those concerns and the controller hook
 *
 * Visual design, keyboard shortcuts, export behaviour, and every
 * WS/REST contract are unchanged.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { BookOpen, Copy, HelpCircle, History, Moon, Sun } from 'lucide-react'

import {
  getContainerStatus, getKeyStatuses, getModels,
  startContainer, stopContainer, validateKey,
} from '../api'
import useSessionController from '../hooks/useSessionController'
import ScreenView from '../components/ScreenView'
import SafetyModal from '../components/SafetyModal'
import CompletionBanner from '../components/CompletionBanner'
import ToastContainer, { useToasts } from '../components/ToastContainer'
import WelcomeOverlay from '../components/WelcomeOverlay'

import { estimateCost } from '../utils'
import {
  addSessionToHistory, clearSessionHistory, getSessionHistory,
} from '../utils'
import { getTheme, initTheme, setTheme as applyTheme } from '../utils'

import ControlPanel from './workbench/ControlPanelView.jsx'
import GraphRunPanel from './workbench/GraphRunPanel.jsx'
import Timeline from './workbench/Timeline.jsx'
import HistoryDrawer from './workbench/HistoryDrawer.jsx'
import LogsPanel from './workbench/LogsPanel.jsx'
import { PROVIDERS, SETTINGS_KEY } from './workbench/constants.js'
import {
  downloadLogsTxt,
  exportSessionHTML,
  exportSessionJSON,
  formatLogsForClipboard,
  formatTimelineForClipboard,
} from './workbench/exporters.js'

import './Workbench.css'

// ---------------------------------------------------------------------------
// Settings persistence (localStorage) — unchanged from pre-PR.
// ---------------------------------------------------------------------------

function loadSettings() {
  try { return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {} } catch { return {} }
}
function saveSettings(s) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)) } catch { /* ignore */ }
}

function getOpenAIReasoningDefault(modelId) {
  return modelId === 'gpt-5.4' ? 'none' : 'medium'
}

function isOpenAIReasoningModel(modelId) {
  return modelId === 'gpt-5.4' || modelId === 'gpt-5.5'
}

export default function Workbench() {
  const { toasts, addToast } = useToasts()

  // --- Session controller -------------------------------------------------

  const handleToast = useCallback((message, type = 'info', options) => {
    addToast(message, type, options)
  }, [addToast])

  const onHistoryEntry = useCallback((entry) => {
    addSessionToHistory(entry)
    setSessionHistoryState(getSessionHistory())
  }, [])

  const session = useSessionController({
    onToast: handleToast,
    onHistoryEntry,
  })
  const {
    connected, lastScreenshot, logs, steps, graphRun, safetyPrompt,
    clearLogs, clearSafetyPrompt, setScreenshotMode,
    agentRunning, starting, stopping, completionData, error,
    setError, start, stop, dismissCompletion, clearAll,
  } = session

  // --- Container state (page-scoped) --------------------------------------

  const [containerRunning, setContainerRunning] = useState(false)
  const [containerLoading, setContainerLoading] = useState(false)
  const [containerError, setContainerError] = useState('')

  /** Polls container running status from the backend. */
  const refreshContainer = useCallback(async (signal) => {
    try {
      const data = await getContainerStatus(signal)
      setContainerRunning(data.running || false)
    } catch (e) {
      // U1 — AbortError on unmount is expected; don't flip state.
      if (e?.name === 'AbortError') return
      setContainerRunning(false)
    }
  }, [])

  useEffect(() => {
    // U1/U3 — cancel in-flight polls on unmount so setState never fires on
    // a dead component. refreshContainer is memoized with stable deps so
    // this effect re-runs only when the callback identity actually changes.
    const controller = new AbortController()
    refreshContainer(controller.signal)
    const id = setInterval(() => refreshContainer(controller.signal), 5000)
    return () => {
      clearInterval(id)
      controller.abort()
    }
  }, [refreshContainer])

  // --- Config form state --------------------------------------------------

  const saved = loadSettings()
  const [provider, setProvider] = useState(saved.provider || 'google')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [keySource, setKeySource] = useState('ui')
  const [keyStatuses, setKeyStatuses] = useState({})
  const [task, setTask] = useState('')
  const [maxSteps, setMaxSteps] = useState(saved.maxSteps || 50)
  const [reasoningEffort, setReasoningEffort] = useState(saved.reasoningEffort || '')
  const [useBuiltinSearch, setUseBuiltinSearch] = useState(
    typeof saved.useBuiltinSearch === 'boolean' ? saved.useBuiltinSearch : false,
  )
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [keyValidation, setKeyValidation] = useState(null)
  // Files attached to the next agent run. Each entry: { file_id, filename, size_bytes }.
  // Not persisted across reloads — uploads survive on the backend store but the
  // selection is fresh per session to avoid stale references.
  const [attachedFiles, setAttachedFiles] = useState([])

  // --- Theme / onboarding / drawers (page-scoped) -------------------------

  const [theme, setThemeState] = useState(getTheme)
  const [showHistory, setShowHistory] = useState(false)
  const [sessionHistory, setSessionHistoryState] = useState(getSessionHistory)
  const [logsExpanded, setLogsExpanded] = useState(false)
  const [showWelcome, setShowWelcome] = useState(false)
  const [expandedStep, setExpandedStep] = useState(null)

  // --- Model list ---------------------------------------------------------

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

  // --- Scroll refs --------------------------------------------------------

  const timelineRef = useRef(null)
  const logRef = useRef(null)

  // --- Init + persist -----------------------------------------------------

  useEffect(() => { initTheme() }, [])
  useEffect(() => {
    saveSettings({ provider, maxSteps, reasoningEffort, useBuiltinSearch })
  }, [provider, maxSteps, reasoningEffort, useBuiltinSearch])

  useEffect(() => {
    if (provider !== 'openai') return
    if (isOpenAIReasoningModel(model)) return
    setReasoningEffort('')
  }, [provider, model])

  // C16: model list doesn't depend on provider — fetch once on mount.
  useEffect(() => {
    let cancelled = false
    const fetchModelList = async () => {
      setModelsLoading(true)
      try {
        const data = await getModels()
        if (!cancelled && data.models?.length) {
          setFetchedModels(data.models)
          setModelsLoaded(true)
        }
      } catch { /* backend not ready */ }
      if (!cancelled) setModelsLoading(false)
    }
    fetchModelList()
    return () => { cancelled = true }
  }, [])

  // Fetch API key statuses on provider change.
  useEffect(() => {
    const fetchKeys = async () => {
      try {
        const data = await getKeyStatuses()
        if (data.keys) {
          const map = {}
          data.keys.forEach(k => { map[k.provider] = k })
          setKeyStatuses(map)
          const current = map[provider]
          if (current?.available) {
            setKeySource(current.source)
          }
        }
      } catch { /* backend not ready yet */ }
    }
    fetchKeys()
  }, [provider])

  // Auto-scroll timeline / logs.
  useEffect(() => {
    if (timelineRef.current) timelineRef.current.scrollTop = timelineRef.current.scrollHeight
  }, [steps])
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [logs])

  // Sync model when provider changes or when the fetched model list arrives.
  useEffect(() => {
    const providerModels = fetchedModels.filter(item => item.provider === provider)
    if (providerModels.length === 0) {
      if (model) setModel('')
      return
    }

    const currentStillAllowed = providerModels.some(item => item.model_id === model)
    if (!currentStillAllowed) {
      setModel(providerModels[0].model_id)
    }
  }, [provider, fetchedModels, model])

  useEffect(() => {
    const status = keyStatuses[provider]
    if (status?.available) setKeySource(status.source)
    else setKeySource('ui')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, keyStatuses])

  // --- Handlers -----------------------------------------------------------

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
    if (!model || !models.some(item => item.value === model)) {
      return setError('Select an allowed model before starting the agent.')
    }

    // Start the environment first if it isn't already running. Keeping
    // this logic on the page preserves the container lifecycle as a
    // page-level concern and avoids coupling the session controller to
    // container REST endpoints.
    if (!containerRunning) {
      setContainerLoading(true)
      setContainerError('')
      try {
        await startContainer()
        await refreshContainer()
      } catch {
        setContainerError('Could not start the environment. Please ensure the system is set up correctly.')
        setContainerLoading(false)
        return
      }
      setContainerLoading(false)
    }

    const modelMeta = fetchedModels.find(m => m.model_id === model)
    await start(
      {
        task: task.trim(),
        apiKey: keySource === 'ui' ? apiKey.trim() : '',
        model,
        maxSteps: Number(maxSteps),
        provider,
        reasoningEffort: provider === 'openai' && isOpenAIReasoningModel(model)
          ? (reasoningEffort || getOpenAIReasoningDefault(model))
          : null,
        useBuiltinSearch,
        attachedFiles: attachedFiles.map(f => f.file_id),
      },
      { modelDisplayName: modelMeta?.display_name || model },
    )
  }

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

  const handleDownloadLogs = () => downloadLogsTxt(logs)

  const handleCopyLogs = async () => {
    if (logs.length === 0) return
    try {
      await navigator.clipboard.writeText(formatLogsForClipboard(logs))
      addToast('Logs copied to clipboard', 'success')
    } catch {
      addToast('Failed to copy logs', 'error')
    }
  }

  const handleCopyTimeline = async () => {
    if (steps.length === 0) return
    try {
      await navigator.clipboard.writeText(formatTimelineForClipboard(steps))
      addToast('Timeline copied to clipboard', 'success')
    } catch {
      addToast('Failed to copy timeline', 'error')
    }
  }

  const handleExportJSON = () =>
    exportSessionJSON({ task, model, provider, fetchedModels, steps, logs })

  const handleExportHTML = () =>
    exportSessionHTML({ task, model, provider, fetchedModels, steps, logs })

  // --- Derived ------------------------------------------------------------

  const costEstimate = estimateCost(model, steps.length)
  const hasKey = keySource === 'ui' ? apiKey.trim().length >= 8 : !!keyStatuses[provider]?.available
  const hasAllowedModel = !!model && models.some(item => item.value === model)
  const canStart = models.length > 0 && hasAllowedModel && !agentRunning && !starting && hasKey

  // --- Render -------------------------------------------------------------

  return (
    <div className="wb">
      <header className="wb-header">
        <div className="wb-header-left">
          <h1>
            <span style={{ color: 'var(--accent)' }}>CUA</span>{' '}
            <span style={{ fontWeight: 400, color: 'var(--text-secondary)', fontSize: 13 }}>Computer Using Agent</span>
          </h1>
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
        onDismiss={dismissCompletion}
      />

      <div className="wb-body">
        <ControlPanel
          provider={provider} setProvider={setProvider}
          model={model} setModel={setModel}
          models={models}
          modelsLoading={modelsLoading}
          modelsLoaded={modelsLoaded}
          providerMeta={providerMeta}
          apiKey={apiKey} setApiKey={setApiKey}
          keySource={keySource} setKeySource={setKeySource}
          keyStatuses={keyStatuses}
          keyValidation={keyValidation} setKeyValidation={setKeyValidation}
          onValidateKey={handleValidateKey}
          showAdvanced={showAdvanced} setShowAdvanced={setShowAdvanced}
          maxSteps={maxSteps} setMaxSteps={setMaxSteps}
          reasoningEffort={reasoningEffort} setReasoningEffort={setReasoningEffort}
          openAIReasoningDefault={getOpenAIReasoningDefault(model)}
          useBuiltinSearch={useBuiltinSearch} setUseBuiltinSearch={setUseBuiltinSearch}
          attachedFiles={attachedFiles} setAttachedFiles={setAttachedFiles}
          task={task} setTask={setTask}
          error={error}
          agentRunning={agentRunning}
          starting={starting}
          stopping={stopping}
          canStart={canStart}
          hasKey={hasKey}
          onStart={handleStart}
          onStop={stop}
          onClear={clearAll}
        />

        <main className="wb-screen-area">
          <ScreenView screenshot={lastScreenshot} containerRunning={containerRunning} setScreenshotMode={setScreenshotMode} />
          {agentRunning && steps.length > 0 && (
            <div className="wb-progress">
              <div className="wb-progress-fill" style={{ width: `${Math.min((steps.length / maxSteps) * 100, 100)}%` }} />
            </div>
          )}
        </main>

        <aside className="wb-right-panel">
          <GraphRunPanel graphRun={graphRun} />

          <div className="wb-timeline-section">
            <div className="wb-panel-header">
              <h3>{showHistory ? 'Session History' : `Timeline (${steps.length})`}</h3>
              {!showHistory && (
                <button
                  className="wb-download-btn"
                  onClick={handleCopyTimeline}
                  disabled={steps.length === 0}
                  title="Copy timeline to clipboard"
                  aria-label="Copy timeline"
                >
                  <Copy size={14} />
                </button>
              )}
              <button
                onClick={() => setShowHistory(!showHistory)}
                className="wb-clear-btn"
                aria-label={showHistory ? 'Show timeline' : 'Show session history'}
              >
                {showHistory ? 'Timeline' : <History size={14} />}
              </button>
            </div>
            {showHistory ? (
              <HistoryDrawer
                sessionHistory={sessionHistory}
                onClearHistory={() => { clearSessionHistory(); setSessionHistoryState([]) }}
              />
            ) : (
              <Timeline
                ref={timelineRef}
                steps={steps}
                expandedStep={expandedStep}
                setExpandedStep={setExpandedStep}
              />
            )}
          </div>

          <LogsPanel
            ref={logRef}
            logs={logs}
            steps={steps}
            logsExpanded={logsExpanded}
            setLogsExpanded={setLogsExpanded}
            onExportJSON={handleExportJSON}
            onExportHTML={handleExportHTML}
            onDownloadLogs={handleDownloadLogs}
            onCopyLogs={handleCopyLogs}
            onClearLogs={clearLogs}
          />
        </aside>
      </div>

      <SafetyModal prompt={safetyPrompt} onDismiss={clearSafetyPrompt} />
      <ToastContainer toasts={toasts} />
      <WelcomeOverlay show={showWelcome} onDismiss={() => setShowWelcome(false)} />
    </div>
  )
}
