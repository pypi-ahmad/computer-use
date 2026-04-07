import { CheckCircle2, XCircle, Square, X } from 'lucide-react'

/**
 * Banner shown when an agent session completes. Displays outcome, step count, and duration.
 */
export default function CompletionBanner({ finishData, stepCount, onDismiss }) {
  if (!finishData) return null

  const status = finishData.status || 'completed'
  const isSuccess = status === 'completed'
  const isError = status === 'error'
  const label = isSuccess ? 'Task Complete' : isError ? 'Task Failed' : 'Task Stopped'
  const Icon = isSuccess ? CheckCircle2 : isError ? XCircle : Square

  return (
    <div className={`completion-banner ${isSuccess ? 'success' : isError ? 'error' : 'stopped'}`} role="status">
      <div className="completion-content">
        <Icon size={16} />
        <span className="completion-label">{label}</span>
        <span className="completion-detail">{finishData.steps ?? stepCount} steps</span>
      </div>
      <button className="completion-dismiss" onClick={onDismiss} aria-label="Dismiss"><X size={16} /></button>
    </div>
  )
}
