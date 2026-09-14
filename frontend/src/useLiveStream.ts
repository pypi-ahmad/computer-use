// React hook that maintains a single WebSocket connection to the v2 stream
// endpoint (/api/v2/ws/<streamId>). Binary messages are CUAF frames decoded
// via decodeCuafFrame; each frame is stored as a blob: URL so the <img> src
// updates without re-encoding. The previous blob URL is revoked immediately
// after the next frame arrives to avoid a permanent memory leak — callers
// must not hold references to frameUrl across renders. Text messages are
// accumulated as StreamEvent[] (last 200 kept).

import { useEffect, useState } from 'react'
import { decodeCuafFrame } from './protocol'
import type { StreamEvent } from './types'
import { getAppToken } from './api'

export const DESKTOP_STREAM_ID = 'desktop'

export function useLiveStream(sessionId: string | null) {
  const [frameUrl, setFrameUrl] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [error, setError] = useState('')
  const [activeSession, setActiveSession] = useState<string | null>(null)
  const streamId = sessionId ?? DESKTOP_STREAM_ID
  useEffect(() => {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const token = getAppToken() || String(import.meta.env.VITE_WS_TOKEN ?? '').trim()
    const ws = new WebSocket(`${scheme}://${location.host}/api/v2/ws/${encodeURIComponent(streamId)}${token ? `?token=${encodeURIComponent(token)}` : ''}`)
    ws.binaryType = 'arraybuffer'
    let currentUrl: string | null = null
    ws.onopen = () => {
      setActiveSession(streamId)
      setConnected(true)
      setError('')
    }
    ws.onclose = () => setConnected(false)
    ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        try { const parsed = JSON.parse(event.data) as StreamEvent; if (parsed && typeof parsed.event === 'string') setEvents(previous => [...previous.slice(-199), parsed]) }
        catch { setError('The session stream sent malformed JSON.') }
        return
      }
      if (!(event.data instanceof ArrayBuffer)) return
      try {
        const frame = decodeCuafFrame(event.data)
        const imageBytes = new Uint8Array(frame.payload.byteLength)
        imageBytes.set(frame.payload)
        const next = URL.createObjectURL(new Blob([imageBytes.buffer], { type: frame.codec === 'WEBP' ? 'image/webp' : 'image/jpeg' }))
        if (currentUrl) URL.revokeObjectURL(currentUrl)
        currentUrl = next; setFrameUrl(next)
      } catch { setError('The session stream sent a malformed frame.') }
    }
    return () => {
      ws.close()
      if (currentUrl) URL.revokeObjectURL(currentUrl)
      setFrameUrl(null)
      setConnected(false)
    }
  }, [streamId])
  const live = activeSession === streamId
  return {
    frameUrl: live ? frameUrl : null,
    connected: live && connected,
    events: live ? events : [],
    error: live ? error : '',
  }
}
