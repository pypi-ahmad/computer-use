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

