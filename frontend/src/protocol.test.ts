import { describe, expect, it } from 'vitest'
import { decodeCuafFrame } from './protocol'

describe('decodeCuafFrame', () => {
  it('decodes the binary CUAF header and image payload', () => {
    const bytes = new Uint8Array(31)
    bytes.set([67, 85, 65, 70, 1, 2])
    const view = new DataView(bytes.buffer)
    view.setBigUint64(6, 9n, false)
    view.setUint32(14, 1440, false)
    view.setUint32(18, 900, false)
    view.setBigUint64(22, 1234n, false)
    bytes[30] = 255
    expect(decodeCuafFrame(bytes.buffer)).toMatchObject({ codec: 'JPEG', sequence: 9, width: 1440, height: 900 })
  })

  it('rejects malformed frames', () => {
    expect(() => decodeCuafFrame(new ArrayBuffer(10))).toThrow('Invalid CUAF frame')
  })
})
