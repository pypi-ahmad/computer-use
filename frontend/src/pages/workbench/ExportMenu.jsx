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
