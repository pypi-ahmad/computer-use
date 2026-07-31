import { useEffect, useState } from 'react'
import { decodeCuafFrame } from './protocol'
import type { StreamEvent } from './types'

export function useLiveStream(sessionId: string | null) {
  const [frameUrl, setFrameUrl] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    if (!sessionId) { setFrameUrl(null); setConnected(false); return }
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const token = String(import.meta.env.VITE_WS_TOKEN ?? '').trim()
    const ws = new WebSocket(`${scheme}://${location.host}/api/v2/ws/${encodeURIComponent(sessionId)}${token ? `?token=${encodeURIComponent(token)}` : ''}`)
    ws.binaryType = 'arraybuffer'
    let currentUrl: string | null = null
    ws.onopen = () => setConnected(true)
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
    return () => { ws.close(); if (currentUrl) URL.revokeObjectURL(currentUrl) }
  }, [sessionId])
  return { frameUrl, connected, events, error }
}
