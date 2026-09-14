// CUAF (Computer Use Audit Frame) binary framing — decoder for the binary
// frames sent over the v2 WebSocket stream.
//
// Wire layout (all fields big-endian):
//   bytes 0–3   : magic "CUAF"
//   byte  4     : version, must be 1
//   byte  5     : codec — 1 = JPEG, 2 = WEBP
//   bytes 6–13  : sequence number (uint64)
//   bytes 14–17 : width in pixels (uint32)
//   bytes 18–21 : height in pixels (uint32)
//   bytes 22–29 : capture timestamp in milliseconds since epoch (uint64)
//   bytes 30+   : image payload (JPEG or WebP bytes)
//
// The matching Python encoder is backend/v2/frames.py::pack_cuaf_frame.
// HEADER_SIZE must stay in sync with the struct defined there.

export interface CuafFrame { codec: 'WEBP' | 'JPEG'; sequence: number; width: number; height: number; timestampMs: number; payload: Uint8Array }
const HEADER_SIZE = 30
export function decodeCuafFrame(buffer: ArrayBuffer): CuafFrame {
  if (buffer.byteLength < HEADER_SIZE) throw new Error('Invalid CUAF frame: truncated header')
  const bytes = new Uint8Array(buffer)
  if (String.fromCharCode(...bytes.slice(0, 4)) !== 'CUAF' || bytes[4] !== 1) throw new Error('Invalid CUAF frame: unsupported header')
  const view = new DataView(buffer)
  const codecByte = bytes[5]
  if (codecByte !== 1 && codecByte !== 2) throw new Error('Invalid CUAF frame: unknown codec')
  const sequence = Number(view.getBigUint64(6, false))
  const width = view.getUint32(14, false)
  const height = view.getUint32(18, false)
  const timestampMs = Number(view.getBigUint64(22, false))
  return { codec: codecByte === 1 ? 'JPEG' : 'WEBP', sequence, width, height, timestampMs, payload: bytes.slice(HEADER_SIZE) }
}
