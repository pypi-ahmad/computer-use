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
