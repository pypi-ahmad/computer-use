// === merged from frontend/src/pages/workbench/ControlPanel.jsx ===
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
import { useRef, useState } from 'react'
import { MAX_TASK_LENGTH, PROVIDERS, TASK_EXAMPLES } from './constants.js'
import { uploadFile, deleteFile } from '../../api'

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
  openAIReasoningDefault = 'medium',
  useBuiltinSearch, setUseBuiltinSearch,
  // Attached files (provider file-search / Anthropic Files API / OpenAI vector store)
  attachedFiles = [], setAttachedFiles = () => {},
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
  const showOpenAIReasoningDropdown = provider === 'openai' && (model === 'gpt-5.4' || model === 'gpt-5.5')

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
            {showOpenAIReasoningDropdown && (
              <>
                <label className="wb-label">Reasoning Effort</label>
                <select className="wb-input" value={reasoningEffort} onChange={(e) => setReasoningEffort(e.target.value)} disabled={agentRunning}>
                  <option value="">Default — {openAIReasoningDefault === 'none' ? 'None' : 'Medium'} per OpenAI docs</option>
                  <option value="none">None — no reasoning</option>
                  <option value="low">Low — efficient reasoning</option>
                  <option value="medium">Medium — balanced</option>
                  <option value="high">High — more thorough reasoning</option>
                  <option value="xhigh">XHigh — deepest reasoning</option>
                </select>
                <span style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginTop: 2 }}>
                  GPT-5.4 defaults to none; GPT-5.5 defaults to medium, per OpenAI's model pages.
                </span>
              </>
            )}
          </div>
        )}
      </div>

      {/* Web search toggle — when ON, the request advertises each
          provider's official first-party search tool alongside the
          computer-use tool. When OFF, only computer use is available. */}
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
            ? 'Web search is ON — the model may call its provider\'s official search tool during the computer-use run.'
            : 'Web search is OFF — only the provider\'s computer-use tool is available.'}
        >
          {useBuiltinSearch ? '● Web Search: ON' : '○ Web Search: OFF'}
        </button>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'block', marginTop: 4 }}>
          {useBuiltinSearch
            ? 'ON: provider-native search is advertised in the same request as Computer Use (OpenAI web_search, Anthropic web_search_20250305, Gemini google_search).'
            : 'OFF: only Computer Use is advertised. The model can still use native browser actions inside the sandbox.'}
        </span>
      </div>

      {/* Document Attachments — uploaded once, referenced by file_id in startAgent.
          Routed per-provider:
            - OpenAI: vector store + file_search tool
            - Anthropic: Files API (PDF/TXT) or inline (MD/DOCX) document blocks
            - Gemini: rejected for Computer Use runs
          Backend caps: 10 files per session, 1 GB per file. */}
      <FileAttachments
        attachedFiles={attachedFiles}
        setAttachedFiles={setAttachedFiles}
        agentRunning={agentRunning}
      />

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

/**
 * FileAttachments — multi-file uploader for the next agent run.
 *
 * Files are POSTed to /api/files/upload immediately on selection so the
 * upload latency is paid before "Start Agent" is clicked, not during.
 * The returned file_id is then forwarded as ``attached_files`` in the
 * startAgent payload, where each provider's adapter consumes it
 * according to its own document protocol.
 */
function FileAttachments({ attachedFiles, setAttachedFiles, agentRunning }) {
  const inputRef = useRef(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')

  // Hard cap per backend models.py (max_length=10). Block before the
  // request goes out so the user gets a clear message instead of 422.
  const MAX_FILES = 10

  const handleFiles = async (fileList) => {
    if (!fileList || fileList.length === 0) return
    setUploadError('')
    if (attachedFiles.length + fileList.length > MAX_FILES) {
      setUploadError(`Maximum ${MAX_FILES} files per session.`)
      return
    }
    setUploading(true)
    const newRecords = []
    for (const file of Array.from(fileList)) {
      try {
        const rec = await uploadFile(file)
        newRecords.push(rec)
      } catch (e) {
        setUploadError(`Upload failed: ${e.message || 'unknown error'}`)
      }
    }
    if (newRecords.length > 0) {
      setAttachedFiles([...attachedFiles, ...newRecords])
    }
    setUploading(false)
    if (inputRef.current) inputRef.current.value = ''
  }

  const handleRemove = async (fileId) => {
    try {
      await deleteFile(fileId)
    } catch {
      // Even if backend deletion fails, drop it from local state so
      // the user can move on; orphan cleanup is handled server-side.
    }
    setAttachedFiles(attachedFiles.filter(f => f.file_id !== fileId))
  }

  const formatBytes = (n) => {
    if (n < 1024) return `${n} B`
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
    return `${(n / (1024 * 1024)).toFixed(1)} MB`
  }

  return (
    <div className="wb-section">
      <label className="wb-label">Document Attachments ({attachedFiles.length}/{MAX_FILES})</label>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf,.txt,.md,.docx"
        onChange={(e) => handleFiles(e.target.files)}
        disabled={agentRunning || uploading || attachedFiles.length >= MAX_FILES}
        style={{ display: 'block', width: '100%', fontSize: 12, marginBottom: 6 }}
      />
      {uploading && (
        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Uploading…</span>
      )}
      {uploadError && (
        <span style={{ fontSize: 11, color: 'var(--error)', display: 'block', marginTop: 4 }}>
          {uploadError}
        </span>
      )}
      {attachedFiles.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '6px 0 0 0' }}>
          {attachedFiles.map(f => (
            <li
              key={f.file_id}
              style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '4px 6px', marginBottom: 2,
                background: 'var(--bg-secondary)', borderRadius: 4,
                fontSize: 11,
              }}
            >
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {f.filename} <span style={{ color: 'var(--text-secondary)' }}>({formatBytes(f.size_bytes)})</span>
              </span>
              <button
                type="button"
                onClick={() => handleRemove(f.file_id)}
                disabled={agentRunning}
                aria-label={`Remove ${f.filename}`}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--text-secondary)', fontSize: 14, padding: '0 4px',
                  marginLeft: 6,
                }}
                title="Remove"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <span style={{ fontSize: 10, color: 'var(--text-secondary)', display: 'block', marginTop: 4 }}>
        PDF, TXT, MD, DOCX. Documents are routed to the provider's official
        retrieval contract (OpenAI vector store, Anthropic Files API). Gemini
        Computer Use runs do not accept document attachments.
      </span>
    </div>
  )
}
