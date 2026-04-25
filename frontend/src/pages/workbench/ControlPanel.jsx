/**
 * ControlPanel — the left sidebar of the Workbench.
 *
 * Renders provider/model selectors, API-key source toggle, advanced
 * settings, task textarea, and start/stop/clear buttons. Every piece
 * of state is owned by the page; this component is pure presentation
 * plus event dispatch.
 *
 * Extracted verbatim from ``Workbench.jsx`` (pre-PR). Behaviour —
 * including the Ctrl+Enter start shortcut and every disabled-state
 * rule — is preserved.
 */

import { Check } from 'lucide-react'
import { MAX_TASK_LENGTH, MODEL_HINTS, PROVIDERS, TASK_EXAMPLES } from './constants'

export default function ControlPanel({
  // Provider/model
  provider, setProvider,
  model, setModel,
  models,
  modelsLoading,
  modelsLoaded,
  providerMeta,
  // API key
  apiKey, setApiKey,
  keySource, setKeySource,
  keyStatuses,
  keyValidation, setKeyValidation,
  onValidateKey,
  // Advanced
  showAdvanced, setShowAdvanced,
  maxSteps, setMaxSteps,
  reasoningEffort, setReasoningEffort,
  useBuiltinSearch, setUseBuiltinSearch,
  // Automation mode
  automationMode, setAutomationMode,
  // Task
  task, setTask,
  error,
  // Lifecycle
  agentRunning,
  starting,
  stopping,
  canStart,
  hasKey,
  onStart,
  onStop,
  onClear,
}) {
  return (
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
              <button className="wb-btn wb-btn-secondary" style={{ padding: '6px 10px', flex: 'none' }} onClick={onValidateKey} disabled={!apiKey.trim() || apiKey.length < 8 || keyValidation === 'checking'} title="Validate API key" aria-label="Validate API key">
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

      {/* Automation mode — prominent, outside Advanced Settings.
          Desktop = full X11 sandbox (any app); Browser = Chromium-only,
          activates Gemini's ENVIRONMENT_BROWSER hint and a browser-
          focused system prompt for all three providers. */}
      <div className="wb-section">
        <label className="wb-label">Automation Mode</label>
        <select
          className="wb-select"
          value={automationMode}
          onChange={(e) => setAutomationMode(e.target.value)}
          disabled={agentRunning}
          aria-label="Automation mode"
        >
          <option value="desktop">Desktop — full X11 sandbox (any app)</option>
          <option value="browser">Browser — Chromium-focused web tasks</option>
        </select>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginTop: 4 }}>
          {automationMode === 'browser'
            ? 'Gemini receives ENVIRONMENT_BROWSER per Google\'s docs; all providers get a browser-focused system prompt.'
            : 'Full desktop environment — any application available inside the sandbox.'}
        </span>
      </div>

      {/* Web search toggle — prominent, outside Advanced Settings.
          Off by default; when on, the model gets its provider's
          official web_search / google_search tool. */}
      <div className="wb-section">
        <label className="wb-label">Web Search</label>
        <button
          type="button"
          role="switch"
          aria-checked={!!useBuiltinSearch}
          onClick={() => setUseBuiltinSearch(!useBuiltinSearch)}
          disabled={agentRunning}
          className={`wb-btn ${useBuiltinSearch ? 'wb-btn-primary' : 'wb-btn-secondary'}`}
          style={{ width: '100%', justifyContent: 'center' }}
          title={useBuiltinSearch
            ? 'Web search is ON — the model can call its provider\'s official search tool.'
            : 'Web search is OFF — the model cannot search the internet.'}
        >
          {useBuiltinSearch ? '● Web Search: ON' : '○ Web Search: OFF'}
        </button>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginTop: 4 }}>
          {useBuiltinSearch
            ? 'Model may call its provider\'s official search tool (OpenAI web_search, Anthropic web_search_20250305, Gemini google_search).'
            : 'Model cannot search the internet. Same behaviour as before web search support was added.'}
        </span>
      </div>

      {/* Task */}
      <div className="wb-section wb-section-grow">
        <label className="wb-label">Task</label>
        <textarea className="wb-textarea" placeholder="Describe what the agent should do..." value={task}
          onChange={(e) => setTask(e.target.value.slice(0, MAX_TASK_LENGTH))} disabled={agentRunning}
          onKeyDown={(e) => { if (e.key === 'Enter' && e.ctrlKey && !agentRunning && !starting) onStart() }}
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
          <button className="wb-btn wb-btn-primary" onClick={onStart} disabled={!canStart}>
            {starting ? 'Starting…' : agentRunning ? 'Running…' : modelsLoading ? 'Loading Models…' : models.length === 0 ? 'No Models for This Provider' : !hasKey ? 'Enter API Key to Start' : 'Start Agent'}
          </button>
          <button className="wb-btn wb-btn-danger" onClick={onStop} disabled={!agentRunning || stopping}>{stopping ? 'Stopping…' : 'Stop'}</button>
          <button className="wb-btn wb-btn-secondary" onClick={onClear} disabled={agentRunning} aria-label="Clear steps and logs">Clear</button>
        </div>
      </div>
    </aside>
  )
}
