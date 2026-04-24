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
 * viewer currently needs the periodic screenshot stream. The backend
 * runs a single capture publisher per process; opting out while on
 * noVNC means the publisher stops capturing entirely when every viewer
 * is on noVNC, which is the common case.
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
