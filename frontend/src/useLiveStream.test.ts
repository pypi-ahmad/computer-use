import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { DESKTOP_STREAM_ID, useLiveStream } from './useLiveStream'

class HookSocket {
  static instances: HookSocket[] = []
  url: string
  binaryType = ''
  onopen: ((event?: unknown) => void) | null = null
  onclose: ((event?: unknown) => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  constructor(url: string) {
    this.url = url
    HookSocket.instances.push(this)
  }
  close() {
    this.onclose?.({})
  }
}

afterEach(() => {
  HookSocket.instances = []
})

it('links the desktop stream before a run starts', async () => {
  vi.stubGlobal('WebSocket', HookSocket)
  const { result } = renderHook(() => useLiveStream(null))
  expect(HookSocket.instances[0]?.url).toContain(`/api/v2/ws/${DESKTOP_STREAM_ID}`)
  act(() => { HookSocket.instances[0]?.onopen?.({}) })
  await waitFor(() => expect(result.current.connected).toBe(true))
  expect(result.current.frameUrl).toBeNull()
})
