import { useState, useEffect } from 'react'

const WS_TOKEN = (import.meta.env?.VITE_WS_TOKEN || '').trim()

export default function ScreenView({ screenshot, containerRunning, setScreenshotMode }) {
  const [useVnc, setUseVnc] = useState(true)
  const showingVnc = containerRunning && useVnc

  useEffect(() => {
    if (!setScreenshotMode) return
    setScreenshotMode(showingVnc ? 'off' : 'on')
    return () => {
      setScreenshotMode('off')
    }
  }, [showingVnc, setScreenshotMode])

  const vncPath = WS_TOKEN
    ? `vnc/websockify?token=${encodeURIComponent(WS_TOKEN)}`
    : 'vnc/websockify'
  const vncUrl = `/vnc/vnc.html?autoconnect=true&resize=scale&path=${encodeURIComponent(vncPath)}`

  if (containerRunning && useVnc) {
    return (
      <div className="screen-container" style={{ position: 'relative' }}>
        <iframe
          src={vncUrl}
          title="Live Desktop (noVNC)"
          style={{ width: '100%', height: '100%', border: 'none' }}
          allow="clipboard-read; clipboard-write"
          onError={() => {
            console.warn('VNC iframe failed to load, falling back to screenshot')
            setUseVnc(false)
          }}
        />
        <div className="screen-overlay">
          <span className="screen-badge">Interactive</span>
        </div>
      </div>
    )
  }

  return (
    <div className="screen-container" style={{ position: 'relative' }}>
      {screenshot && (
        <img
          src={`data:image/png;base64,${screenshot}`}
          alt="Agent screen"
          draggable={false}
          style={{
            width: '100%', height: '100%', objectFit: 'contain',
            display: 'block',
          }}
        />
      )}

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