import { useEffect, useRef } from 'react'
import formatTime from '../utils/formatTime'

/**
 * Scrollable log viewer that auto-scrolls to the latest entry.
 * Displays timestamped, level-colored log messages with a Clear button.
 * @param {{logs: Array, onClear: Function}} props
 */
export default function LogPanel({ logs, onClear }) {
  const scrollRef = useRef(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logs])

  return (
    <div className="bottom-panel">
      <div className="bottom-panel-header">
        <h3>Logs ({logs.length})</h3>
        <button className="clear-logs-btn" onClick={onClear}>
          Clear
        </button>
      </div>
      <div className="log-container" ref={scrollRef}>
        {logs.length === 0 && (
          <div className="log-entry">
            <span className="log-message" style={{ color: 'var(--text-secondary)' }}>
              Waiting for logs...
            </span>
          </div>
        )}
        {logs.map((log, i) => (
          <div key={i} className="log-entry">
            <span className="log-time">{formatTime(log.timestamp)}</span>
            <span className={`log-level ${log.level}`}>{log.level}</span>
            <span className="log-message">{log.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
