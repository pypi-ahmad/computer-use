import { useState, useEffect } from 'react'

const WELCOME_KEY = 'cua_welcomed'

/**
 * First-run welcome overlay. Shows once on first visit, and can be re-opened via the help button.
 */
export default function WelcomeOverlay({ show, onDismiss }) {
  const [visible, setVisible] = useState(() => !localStorage.getItem(WELCOME_KEY))

  useEffect(() => {
    if (show) setVisible(true)
  }, [show])

  if (!visible) return null

  const dismiss = () => {
    localStorage.setItem(WELCOME_KEY, '1')
    setVisible(false)
    if (onDismiss) onDismiss()
  }

  return (
    <div className="welcome-overlay" role="dialog" aria-modal="true" aria-label="Welcome to CUA">
      <div className="welcome-modal">
        <h2>Welcome to CUA</h2>
        <p>CUA gives you a virtual desktop and lets AI automate tasks on it.</p>
        <div className="welcome-steps">
          <div className="welcome-step">
            <span className="welcome-num">1</span>
            <span>Choose your AI provider and enter an API key</span>
          </div>
          <div className="welcome-step">
            <span className="welcome-num">2</span>
            <span>Describe a task for the agent to perform</span>
          </div>
          <div className="welcome-step">
            <span className="welcome-num">3</span>
            <span>Watch the agent work in real time on the live desktop</span>
          </div>
        </div>
        <button className="welcome-dismiss" onClick={dismiss}>Get Started</button>
      </div>
    </div>
  )
}
