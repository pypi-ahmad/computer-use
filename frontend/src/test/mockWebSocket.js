/**
 * Deterministic WebSocket test double. jsdom ships a WebSocket that attempts a
 * real network connect, so the useWebSocket suite stubs this in via
 * `vi.stubGlobal('WebSocket', MockWebSocket)`.
 */
export default class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  static instances = []
  static reset() {
    MockWebSocket.instances = []
  }

  constructor(url) {
    this.url = url
    this.readyState = MockWebSocket.CONNECTING
    this.sent = []
    this.onopen = null
    this.onclose = null
    this.onmessage = null
    this.onerror = null
    MockWebSocket.instances.push(this)
  }

  send(data) {
    try {
      this.sent.push(JSON.parse(data))
    } catch {
      this.sent.push(data)
    }
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.({})
  }

  // ── test helpers ──────────────────────────────────────────────────────
  simulateOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.({})
  }

  simulateMessage(obj) {
    this.onmessage?.({ data: typeof obj === 'string' ? obj : JSON.stringify(obj) })
  }

  simulateClose() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.({})
  }

  simulateError() {
    this.onerror?.({})
  }
}
