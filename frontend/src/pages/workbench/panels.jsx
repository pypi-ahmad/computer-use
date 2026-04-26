// === merged from frontend/src/pages/workbench/Timeline.jsx ===
/**
 * Timeline — the scrollable step timeline shown in the right panel.
 *
 * Extracted verbatim from ``Workbench.jsx``. Expansion state is owned
 * by the parent so keyboard-driven navigation across nested panels
 * remains consistent with the pre-PR behaviour.
 */

import { forwardRef } from 'react'
import formatTime from '../../utils'
import { ACTION_LABEL_MAP, ICON_SIZE, getActionIcon } from './ControlPanel'

const Timeline = forwardRef(function Timeline(
  { steps, expandedStep, setExpandedStep },
  ref,
) {
  return (
    <div className="wb-timeline" ref={ref}>
      {steps.length === 0 && <p className="wb-empty">Start a task to see the agent's actions here.</p>}
      {steps.map((step, i) => {
        const Icon = getActionIcon(step.action?.action)
        return (
          <div
            key={i}
            className={`wb-timeline-item ${step.error ? 'has-error' : ''} ${expandedStep === i ? 'expanded' : ''}`}
            onClick={() => setExpandedStep(expandedStep === i ? null : i)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                setExpandedStep(expandedStep === i ? null : i)
              }
            }}
            role="button"
            tabIndex={0}
            aria-expanded={expandedStep === i}
          >
            <div className="wb-timeline-head">
              <span className="wb-step-num">#{step.step_number}</span>
              <span className="wb-action-icon"><Icon size={ICON_SIZE} /></span>
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
        )
      })}
    </div>
  )
})

export default Timeline

// === merged from frontend/src/pages/workbench/LogsPanel.jsx ===
/**
 * LogsPanel — the collapsible logs section at the bottom of the right
 * panel. Renders the header (with the ``ExportMenu``) and, when
 * expanded, the scrollable log list. Extracted verbatim from
 * ``Workbench.jsx``.
 */

import { forwardRef } from 'react'
import formatTime from '../../utils'
import ExportMenu from './panels'

const LogsPanel = forwardRef(function LogsPanel(
  {
    logs,
    steps,
    logsExpanded,
    setLogsExpanded,
    onExportJSON,
    onExportHTML,
    onDownloadLogs,
    onCopyLogs,
    onClearLogs,
  },
  ref,
) {
  return (
    <div className={`wb-log-section ${logsExpanded ? 'expanded' : 'collapsed'}`}>
      <div
        className="wb-panel-header"
        onClick={() => setLogsExpanded(!logsExpanded)}
        style={{ cursor: 'pointer', userSelect: 'none' }}
      >
        <h3>{logsExpanded ? '▾' : '▸'} Logs {logs.length > 0 ? `(${logs.length})` : ''}</h3>
        <ExportMenu
          stepsEmpty={steps.length === 0}
          logsEmpty={logs.length === 0}
          onExportJSON={onExportJSON}
          onExportHTML={onExportHTML}
          onDownloadLogs={onDownloadLogs}
          onCopyLogs={onCopyLogs}
          onClearLogs={onClearLogs}
        />
      </div>
      {logsExpanded && (
        <div className="wb-logs" ref={ref}>
          {logs.length === 0 && <p className="wb-empty">Logs will appear here once a task is running.</p>}
          {logs.map((log, i) => (
            <div key={i} className="wb-log-entry">
              <span className="wb-log-time">{formatTime(log.timestamp)}</span>
              <span className={`wb-log-level ${log.level}`}>
                {log.level === 'info' ? 'Info' : log.level === 'error' ? 'Error' : log.level === 'warning' ? 'Warning' : log.level === 'debug' ? 'Debug' : log.level}
              </span>
              <span className="wb-log-msg">{log.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
})

export default LogsPanel

// === merged from frontend/src/pages/workbench/HistoryDrawer.jsx ===
/**
 * HistoryDrawer — the session-history list rendered in place of the
 * timeline when the user toggles the history view. Extracted verbatim
 * from ``Workbench.jsx``.
 */

export default function HistoryDrawer({ sessionHistory, onClearHistory }) {
  return (
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
        <button onClick={onClearHistory} className="wb-clear-btn" style={{ margin: '8px auto', display: 'block' }}>
          Clear History
        </button>
      )}
    </div>
  )
}

// === merged from frontend/src/pages/workbench/ExportMenu.jsx ===
/**
 * ExportMenu — the row of export / download / copy / clear buttons
 * that lives in the LogsPanel header. Extracted verbatim from
 * ``Workbench.jsx``. Its disabled rules (Export disabled only when
 * both steps AND logs are empty; the download/copy/clear log buttons
 * disabled only on empty logs) are preserved unchanged.
 */

import { Copy, Download, FileJson, FileText, Trash2 } from 'lucide-react'

export default function ExportMenu({
  stepsEmpty,
  logsEmpty,
  onExportJSON,
  onExportHTML,
  onDownloadLogs,
  onCopyLogs,
  onClearLogs,
}) {
  const sessionEmpty = stepsEmpty && logsEmpty
  return (
    <div className="wb-log-actions" onClick={(e) => e.stopPropagation()}>
      <button className="wb-download-btn" onClick={onExportJSON} disabled={sessionEmpty} title="Export session as JSON" aria-label="Export as JSON"><FileJson size={14} /></button>
      <button className="wb-download-btn" onClick={onExportHTML} disabled={sessionEmpty} title="Export session as HTML report" aria-label="Export as HTML"><FileText size={14} /></button>
      <button className="wb-download-btn" onClick={onDownloadLogs} disabled={logsEmpty} title="Download logs as .txt" aria-label="Download logs"><Download size={14} /></button>
      <button className="wb-download-btn" onClick={onCopyLogs} disabled={logsEmpty} title="Copy logs to clipboard" aria-label="Copy logs"><Copy size={14} /></button>
      <button className="wb-clear-btn" onClick={onClearLogs} aria-label="Clear logs"><Trash2 size={14} /></button>
    </div>
  )
}

// === merged from frontend/src/pages/workbench/exporters.js ===
/**
 * Workbench export helpers.
 *
 * Pure functions extracted verbatim from ``Workbench.jsx``. Each one
 * builds a file in memory and triggers a download via a temporary
 * anchor click — no React state involved, which makes them easy to
 * test and reason about independently of the page.
 */

import formatTime from '../../utils'
import { escapeHtml as esc } from '../../utils'
import { estimateCost } from '../../utils'
import { ACTION_LABEL_MAP } from './ControlPanel'

function _triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** Download the full session (task + steps + logs + summary) as JSON. */
export function exportSessionJSON({ task, model, provider, fetchedModels, steps, logs }) {
  const modelMeta = fetchedModels.find(m => m.model_id === model)
  const cost = estimateCost(model, steps.length)
  const data = {
    task, model, provider,
    modelDisplayName: modelMeta?.display_name || model,
    steps: steps.map(s => ({
      step_number: s.step_number,
      action: s.action,
      actionLabel: ACTION_LABEL_MAP[s.action?.action] || s.action?.action || 'Unknown',
      error: s.error,
      timestamp: s.timestamp,
    })),
    logs: logs.map(l => ({ timestamp: l.timestamp, level: l.level, message: l.message })),
    summary: {
      totalSteps: steps.length,
      estimatedCost: cost ? `~$${cost.cost.toFixed(4)}` : null,
      costNote: cost?.note || null,
    },
    exportedAt: new Date().toISOString(),
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  _triggerDownload(blob, `cua_session_${new Date().toISOString().replace(/[:.]/g, '-')}.json`)
}

/** Download a self-contained styled HTML session report. */
export function exportSessionHTML({ task, model, provider, fetchedModels, steps, logs }) {
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
  _triggerDownload(blob, `cua_session_${new Date().toISOString().replace(/[:.]/g, '-')}.html`)
}

/** Download the current logs as a timestamped plain-text file. */
export function downloadLogsTxt(logs) {
  if (logs.length === 0) return
  const now = new Date()
  const pad = (n, w = 2) => String(n).padStart(w, '0')
  const filename = `CUA_logs_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.txt`
  const lines = logs.map(log => {
    const ts = formatTime(log.timestamp)
    return `[${ts}] [${(log.level || '').toUpperCase()}] ${log.message}`
  })
  const blob = new Blob([lines.join('\n')], { type: 'text/plain' })
  _triggerDownload(blob, filename)
}

/** Format logs into a plain-text block suitable for clipboard pasting. */
export function formatLogsForClipboard(logs) {
  return logs.map(log => {
    const ts = formatTime(log.timestamp)
    return `[${ts}] [${(log.level || '').toUpperCase()}] ${log.message}`
  }).join('\n')
}

/** Format the step timeline into a plain-text block suitable for clipboard pasting. */
export function formatTimelineForClipboard(steps) {
  return steps.map(step => {
    const ts = formatTime(step.timestamp)
    const action = ACTION_LABEL_MAP[step.action?.action] || step.action?.action || 'Unknown'
    const parts = [`#${step.step_number}`, `[${ts}]`, action]
    if (step.action?.target) parts.push(`→ ${step.action.target}`)
    if (step.action?.text && step.action.action !== 'done') parts.push(`"${step.action.text}"`)
    if (step.action?.coordinates) parts.push(`@[${step.action.coordinates.join(', ')}]`)
    const head = parts.join(' ')
    const detail = []
    if (step.action?.reasoning) detail.push(`  ${step.action.reasoning}`)
    if (step.error) detail.push(`  Error: ${step.error}`)
    return detail.length ? `${head}\n${detail.join('\n')}` : head
  }).join('\n')
}

