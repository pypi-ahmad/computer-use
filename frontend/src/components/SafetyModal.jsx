import { useState, useEffect, useCallback } from 'react'
import { AlertTriangle } from 'lucide-react'

import { confirmSafety } from '../api'

const TIMEOUT_SECONDS = 60

export default function SafetyModal({ prompt, onDismiss }) {
  const [remaining, setRemaining] = useState(TIMEOUT_SECONDS)
  const [responding, setResponding] = useState(false)

  useEffect(() => {
    if (!prompt) return
    setRemaining(TIMEOUT_SECONDS)
    const interval = setInterval(() => {
      setRemaining(prev => {
        if (prev <= 1) {
          clearInterval(interval)
          return 0
        }
        return prev - 1
      })
    }, 1000)
    return () => clearInterval(interval)
  }, [prompt])

  useEffect(() => {
    if (remaining === 0 && prompt && !responding) {
      handleRespond(false)
    }
  }, [remaining, prompt, responding])

  const handleRespond = useCallback(async (confirm) => {
    if (!prompt || responding) return
    setResponding(true)
    try {
      await confirmSafety(prompt.sessionId, confirm)
    } catch {
      // Best effort — server may have already timed out
    }
    onDismiss()
    setResponding(false)
  }, [prompt, responding, onDismiss])

  if (!prompt) return null

  return (
    <div className="safety-overlay" role="dialog" aria-modal="true" aria-label="Safety confirmation required">
      <div className="safety-modal">
        <div className="safety-header">
          <AlertTriangle size={24} className="safety-icon" />
          <h2>Safety Confirmation Required</h2>
        </div>
        <p className="safety-explanation">{prompt.explanation || 'The agent wants to perform an action that requires your approval.'}</p>
        <div className="safety-timer">
          <div className="safety-timer-bar">
            <div className="safety-timer-fill" style={{ width: `${(remaining / TIMEOUT_SECONDS) * 100}%` }} />
          </div>
          <span className="safety-timer-text">{remaining}s — auto-deny if no response</span>
        </div>
        <div className="safety-actions">
          <button className="safety-btn safety-btn-deny" onClick={() => handleRespond(false)} disabled={responding}>
            Deny
          </button>
          <button className="safety-btn safety-btn-approve" onClick={() => handleRespond(true)} disabled={responding}>
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}