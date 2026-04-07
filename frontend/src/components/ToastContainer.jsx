import { useState, useEffect, useCallback } from 'react'

/**
 * Lightweight toast notification system.
 * Usage: const { toasts, addToast } = useToasts()
 */
export function useToasts() {
  const [toasts, setToasts] = useState([])

  const addToast = useCallback((message, type = 'info') => {
    const id = Date.now() + Math.random()
    setToasts(prev => [...prev, { id, message, type }])
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, 4000)
  }, [])

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
          {t.message}
        </div>
      ))}
    </div>
  )
}
