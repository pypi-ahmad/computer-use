// === merged from frontend/src/pages/workbench/LogsPanel.jsx ===
/**
 * LogsPanel — the collapsible logs section at the bottom of the right
 * panel. Renders the header (with the ``ExportMenu``) and, when
 * expanded, the scrollable log list. Extracted verbatim from
 * ``Workbench.jsx``.
 */

import { forwardRef } from 'react'
import formatTime from '../../utils'
import ExportMenu from './ExportMenu.jsx'

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

