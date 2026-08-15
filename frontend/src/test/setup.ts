import '@testing-library/jest-dom/vitest'

class MockWebSocket {
  static OPEN = 1
  url: string
  binaryType = 'blob'
  onopen: ((event?: unknown) => void) | null = null
  onclose: ((event?: unknown) => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  constructor(url: string) {
    this.url = url
  }
  close() {
    this.onclose?.({})
  }
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', MockWebSocket)
})

afterEach(() => { vi.restoreAllMocks() })
