// === merged from frontend/src/components/CompletionBanner.jsx ===
import { CheckCircle2, XCircle, Square, X } from 'lucide-react'

function normalizeGeminiChunks(grounding) {
  return Array.isArray(grounding?.groundingChunks)
    ? grounding.groundingChunks.filter(chunk => chunk?.web?.uri)
    : []
}

function normalizeGeminiSupports(grounding) {
  return Array.isArray(grounding?.groundingSupports)
    ? grounding.groundingSupports.filter(support => support && typeof support === 'object')
    : []
}

function GeminiCitationMarkers({ chunkIndices, chunks }) {
  const links = chunkIndices
    .map((chunkIndex) => {
      const chunk = chunks[chunkIndex]
      const web = chunk?.web
      if (!web?.uri) return null
      return {
        chunkIndex,
        title: web.title || web.uri,
        uri: web.uri,
      }
    })
    .filter(Boolean)

  if (!links.length) return null

  return (
    <sup className="completion-citations">
      {links.map(({ chunkIndex, title, uri }) => (
        <a
          key={`${uri}-${chunkIndex}`}
          className="completion-citation-link"
          href={uri}
          target="_blank"
          rel="noreferrer noopener"
          title={title}
        >
          [{chunkIndex + 1}]
        </a>
      ))}
    </sup>
  )
}

function GeminiGroundedText({ text, grounding }) {
  if (!text) return null

  const chunks = normalizeGeminiChunks(grounding)
  const supports = normalizeGeminiSupports(grounding)
  const insertions = new Map()

  supports.forEach((support) => {
    const endIndex = Number.isInteger(support?.segment?.endIndex)
      ? Math.max(0, Math.min(text.length, support.segment.endIndex))
      : null
    if (endIndex == null) return

    const indices = Array.isArray(support?.groundingChunkIndices)
      ? support.groundingChunkIndices.filter(idx => Number.isInteger(idx) && idx >= 0)
      : []
    if (!indices.length) return

    const existing = insertions.get(endIndex) || []
    indices.forEach((idx) => {
      if (!existing.includes(idx)) existing.push(idx)
    })
    insertions.set(endIndex, existing)
  })

  if (!insertions.size) {
    return <span>{text}</span>
  }

  const nodes = []
  const breakpoints = [...insertions.keys()].sort((left, right) => left - right)
  let cursor = 0

  breakpoints.forEach((point) => {
    if (point > cursor) {
      nodes.push(
        <span key={`text-${cursor}-${point}`}>
          {text.slice(cursor, point)}
        </span>,
      )
    }
    nodes.push(
      <GeminiCitationMarkers
        key={`cite-${point}`}
        chunkIndices={insertions.get(point) || []}
        chunks={chunks}
      />,
    )
    cursor = Math.max(cursor, point)
  })

  if (cursor < text.length) {
    nodes.push(
      <span key={`text-${cursor}-end`}>
        {text.slice(cursor)}
      </span>,
    )
  }

  return <>{nodes}</>
}

function GeminiGroundingResult({ text, grounding }) {
  const renderedContent = typeof grounding?.renderedContent === 'string'
    ? grounding.renderedContent.trim()
    : ''
  const chunks = normalizeGeminiChunks(grounding)
  const queries = Array.isArray(grounding?.webSearchQueries)
    ? grounding.webSearchQueries.filter(query => typeof query === 'string' && query.trim())
    : []

  return (
    <div className="completion-result completion-result-grounded">
      {text && (
        <div className="completion-grounded-text">
          <GeminiGroundedText text={text} grounding={grounding} />
        </div>
      )}

      {renderedContent && (
        <div className="completion-grounding-card">
          <div className="completion-grounding-label">Google Search Suggestions</div>
          <iframe
            className="completion-grounding-frame"
            title="Google Search suggestions"
            sandbox="allow-popups allow-popups-to-escape-sandbox"
            referrerPolicy="no-referrer"
            srcDoc={renderedContent}
          />
        </div>
      )}

      {queries.length > 0 && (
        <div className="completion-grounding-meta">
          <div className="completion-grounding-label">Search queries</div>
          <div className="completion-grounding-pills">
            {queries.map((query) => (
              <span key={query} className="completion-grounding-pill">{query}</span>
            ))}
          </div>
        </div>
      )}

      {chunks.length > 0 && (
        <div className="completion-grounding-meta">
          <div className="completion-grounding-label">Sources</div>
          <div className="completion-grounding-links">
            {chunks.map((chunk, index) => (
              <a
                key={`${chunk.web.uri}-${index}`}
                className="completion-grounding-source"
                href={chunk.web.uri}
                target="_blank"
                rel="noreferrer noopener"
                title={chunk.web.title || chunk.web.uri}
              >
                [{index + 1}] {chunk.web.title || chunk.web.uri}
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * Banner shown when an agent session completes. Displays outcome, step count, and duration.
 */
export default function CompletionBanner({ finishData, stepCount, costEstimate, onDismiss }) {
  if (!finishData) return null

  const status = finishData.status || 'completed'
  const isSuccess = status === 'completed'
  const isError = status === 'error'
  const label = isSuccess ? 'Task Complete' : isError ? 'Task Failed' : 'Task Stopped'
  const Icon = isSuccess ? CheckCircle2 : isError ? XCircle : Square

  const elapsed = finishData.elapsedSeconds
  const durationText = elapsed != null ? (elapsed >= 60 ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : `${elapsed}s`) : null
  const finalText = (finishData.final_text || '').trim()
  const geminiGrounding = finishData.gemini_grounding && typeof finishData.gemini_grounding === 'object'
    ? finishData.gemini_grounding
    : null

  return (
    <div className={`completion-banner ${isSuccess ? 'success' : isError ? 'error' : 'stopped'}`} role="status">
      <div className="completion-content">
        <Icon size={16} />
        <span className="completion-label">{label}</span>
        <span className="completion-detail">
          {finishData.steps ?? stepCount} steps
          {durationText && ` · ${durationText}`}
          {costEstimate && ` · ~$${costEstimate.cost.toFixed(4)}`}
        </span>
        {geminiGrounding
          ? <GeminiGroundingResult text={finalText} grounding={geminiGrounding} />
          : finalText && <div className="completion-result">{finalText}</div>}
      </div>
      <button className="completion-dismiss" onClick={onDismiss} aria-label="Dismiss"><X size={16} /></button>
    </div>
  )
}

// === merged from frontend/src/components/ErrorBoundary.jsx ===
import { Component } from 'react'

/**
 * React error boundary — catches unhandled exceptions in the component tree
 * and renders a recovery UI instead of a blank page.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          height: '100vh', background: 'var(--bg-primary, #0f1117)', color: 'var(--text-primary, #e4e6ed)',
          fontFamily: 'Inter, -apple-system, sans-serif', gap: 16, padding: 24, textAlign: 'center',
        }}>
          <h1 style={{ fontSize: 20, fontWeight: 700 }}>Something went wrong</h1>
          <p style={{ fontSize: 14, color: 'var(--text-secondary, #9499ad)', maxWidth: 400 }}>
            An unexpected error occurred. Please reload the page to continue.
          </p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '10px 24px', fontSize: 14, fontWeight: 600, border: 'none', borderRadius: 8,
              background: 'var(--accent, #6c63ff)', color: '#fff', cursor: 'pointer',
            }}
          >
            Reload Page
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

// === merged from frontend/src/components/SafetyModal.jsx ===
import { useState, useEffect, useCallback } from 'react'
import { AlertTriangle } from 'lucide-react'
import { confirmSafety } from '../api'

const TIMEOUT_SECONDS = 60

/**
 * Modal dialog for CU safety confirmation prompts.
 * Shows the action explanation, Approve/Deny buttons, and a visible countdown.
 * Auto-denies after 60 seconds.
 */
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

  // Auto-deny on timeout
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

// === merged from frontend/src/components/ScreenView.jsx ===
import { useState, useEffect } from 'react'

/**
 * Optional shared secret matching the backend's ``CUA_WS_TOKEN`` env var.
 * Must match the value used by ``useWebSocket.js`` for /ws — the backend
 * enforces the same token on /vnc/websockify and closes mismatched
 * upgrades with code 4401. Set ``VITE_WS_TOKEN`` in the frontend env
 * when the backend has the token configured.
 */
const WS_TOKEN = (import.meta.env?.VITE_WS_TOKEN || '').trim()

/**
 * Displays the remote desktop view. Prefers an interactive noVNC iframe when the
 * container is running; falls back to a static base64 screenshot otherwise.
 *
 * P-PUB — tells the backend via ``setScreenshotMode`` whether this
 * viewer currently needs the periodic screenshot stream. The callback
 * is session-bound by the controller, so opting out while on noVNC
 * lets the backend stop capturing entirely when every viewer is on
 * noVNC or the active session has finished.
 *
 * @param {{screenshot: string|null, containerRunning: boolean, setScreenshotMode?: (mode: 'on'|'off') => void}} props
 */
export default function ScreenView({ screenshot, containerRunning, setScreenshotMode }) {
  // Default to VNC (interactive) when container is running
  const [useVnc, setUseVnc] = useState(true)

  // The *effective* surface the user is seeing right now. Must mirror
  // the render branches below — keep this derivation in one place so
  // the backend subscription matches what we actually painted.
  const showingVnc = containerRunning && useVnc

  // Backend subscribe/unsubscribe. Runs on every toggle AND on unmount
  // so tearing down the Workbench also drops the subscription.
  useEffect(() => {
    if (!setScreenshotMode) return
    setScreenshotMode(showingVnc ? 'off' : 'on')
    return () => {
      // On unmount the ws may still be open (e.g. user navigated to
      // another page). Tell the backend to stop capturing for us.
      setScreenshotMode('off')
    }
  }, [showingVnc, setScreenshotMode])

  // Route noVNC through the backend reverse proxy (same origin) so the
  // browser never needs direct access to Docker-mapped port 6080.
  // noVNC turns the ``path`` parameter into the websocket URL verbatim,
  // so we append ``?token=<value>`` (URL-encoded) to the path value
  // whenever the backend has ``CUA_WS_TOKEN`` configured. If the token
  // is empty, the path stays unchanged so the default-open behaviour is
  // preserved for local dev.
  const vncPath = WS_TOKEN
    ? `vnc/websockify?token=${encodeURIComponent(WS_TOKEN)}`
    : 'vnc/websockify'
  const vncUrl = `/vnc/vnc.html?autoconnect=true&resize=scale&path=${encodeURIComponent(vncPath)}`

  // When container is running and VNC mode enabled, show interactive desktop
  if (containerRunning && useVnc) {
    return (
      <div className="screen-container" style={{ position: 'relative' }}>
        <iframe
          src={vncUrl}
          title="Live Desktop (noVNC)"
          style={{ width: '100%', height: '100%', border: 'none' }}
          allow="clipboard-read; clipboard-write"
          onError={() => {
            console.warn("VNC iframe failed to load, falling back to screenshot")
            setUseVnc(false)
          }}
        />
        <div className="screen-overlay">
          <span className="screen-badge">Interactive</span>
        </div>
      </div>
    )
  }

  // Screenshot fallback view
  return (
    <div className="screen-container" style={{ position: 'relative' }}>
        {/* Screenshot layer */}
        {screenshot && (
            <img
            src={`data:image/png;base64,${screenshot}`}
            alt="Agent screen"
            draggable={false}
            style={{
                width: '100%', height: '100%', objectFit: 'contain',
                display: 'block'
            }}
            />
        )}

        {/* Empty state */}
        {!screenshot && (
            <div className="screen-placeholder">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="2" y="3" width="20" height="14" rx="2" />
                <path d="M8 21h8M12 17v4" />
            </svg>
            <span>Start the environment to see the desktop here</span>
            <span style={{ fontSize: 12 }}>The live desktop will appear once the environment is running</span>
            </div>
        )}

        {/* Overlay */}
        {screenshot && (
            <div className="screen-overlay">
                <span className="screen-badge">Screenshot</span>
                {containerRunning && (
                    <button
                        onClick={() => setUseVnc(true)}
                        style={{
                            marginLeft: 8, padding: '2px 8px', fontSize: 12,
                            background: 'rgba(0,0,0,0.5)', color: '#fff', border: '1px solid rgba(255,255,255,0.3)',
                            borderRadius: 4, cursor: 'pointer',
                        }}
                    >
                        Interactive View
                    </button>
                )}
            </div>
        )}
    </div>
  )
}

// === merged from frontend/src/components/ToastContainer.jsx ===
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

// === merged from frontend/src/components/WelcomeOverlay.jsx ===
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

