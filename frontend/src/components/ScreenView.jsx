import { useState } from 'react'

/**
 * Displays the remote desktop view. Prefers an interactive noVNC iframe when the
 * container is running; falls back to a static base64 screenshot otherwise.
 * @param {{screenshot: string|null, containerRunning: boolean}} props
 */
export default function ScreenView({ screenshot, containerRunning }) {
  // Default to VNC (interactive) when container is running
  const [useVnc, setUseVnc] = useState(true)

  // Route noVNC through the backend reverse proxy (same origin) so the
  // browser never needs direct access to Docker-mapped port 6080.
  const vncUrl = `/vnc/vnc.html?autoconnect=true&resize=scale&path=vnc/websockify`

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
            <span>No screen capture available</span>
            <span style={{ fontSize: 12 }}>Start the environment to see the live desktop</span>
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
