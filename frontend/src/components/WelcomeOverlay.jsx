import { useState, useEffect, useRef } from 'react'
import useFocusTrap from '../hooks/useFocusTrap'

const WELCOME_KEY = 'cua_welcomed'

function readWelcomed() {
  // U9: never read localStorage during render, and don't throw if it's
  // blocked (private mode / disabled storage).
  try {
    return !!localStorage.getItem(WELCOME_KEY)
  } catch {
    return true  // can't persist → don't nag on every load
  }
}

export default function WelcomeOverlay({ show, onDismiss }) {
  const [visible, setVisible] = useState(false)
  const modalRef = useRef(null)
  const dismissBtnRef = useRef(null)

  // U9: first-run check runs in an effect, not the render-time initializer.
  useEffect(() => {
    if (!readWelcomed()) setVisible(true)
  }, [])

  useEffect(() => {
    if (show) setVisible(true)
  }, [show])

  const dismiss = () => {
    try {
      localStorage.setItem(WELCOME_KEY, '1')
    } catch {
      /* storage blocked — still dismiss for this session */
    }
    setVisible(false)
    if (onDismiss) onDismiss()
  }

  // U2: trap focus, Escape dismisses (the only action), restore focus on close.
  useFocusTrap(visible, {
    onEscape: dismiss,
    initialFocusRef: dismissBtnRef,
    containerRef: modalRef,
  })

  if (!visible) return null

  return (
    <div ref={modalRef} className="welcome-overlay" role="dialog" aria-modal="true" aria-label="Welcome to CUA">
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
        <button ref={dismissBtnRef} className="welcome-dismiss" onClick={dismiss}>Get Started</button>
      </div>
    </div>
  )
}