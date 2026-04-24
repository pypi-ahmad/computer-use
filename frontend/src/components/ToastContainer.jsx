import { useState, useEffect, useCallback, useRef } from 'react'

/**
 * Lightweight toast notification system.
 * Usage: const { toasts, addToast } = useToasts()
 */
export function useToasts() {
  const [toasts, setToasts] = useState([])
  const timersRef = useRef(new Map())

  const clearToast = useCallback((id) => {
    const timer = timersRef.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timersRef.current.delete(id)
    }
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  useEffect(() => () => {
    timersRef.current.forEach(timer => clearTimeout(timer))
    timersRef.current.clear()
  }, [])

  const addToast = useCallback((message, type = 'info', options = {}) => {
    const id = Date.now() + Math.random()
    const duration = Number.isFinite(options.duration) ? options.duration : 4000
    setToasts(prev => [
      ...prev,
      {
        id,
        message,
        type,
        actionLabel: options.actionLabel || '',
        onAction: typeof options.onAction === 'function' ? options.onAction : null,
      },
    ])
    if (duration > 0) {
      const timer = window.setTimeout(() => clearToast(id), duration)
      timersRef.current.set(id, timer)
    }
  }, [clearToast])

  return { toasts, addToast }
}

/**
 * Renders toast notifications in the top-right corner.
 */
export default function ToastContainer({ toasts }) {
  if (!toasts || toasts.length === 0) return null
  return (
    <div className="toast-container" aria-live="polite">
      {toasts.map(t => (
        <div key={t.id} className={`toast toast-${t.type}`}>
          <span className="toast-message">{t.message}</span>
          {t.actionLabel && t.onAction && (
            <button type="button" className="toast-action" onClick={t.onAction}>
              {t.actionLabel}
            </button>
          )}
        </div>
      ))}
    </div>
  )
}
