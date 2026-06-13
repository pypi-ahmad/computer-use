import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// RTL 16 does not auto-cleanup under Vitest globals — wire it here so DOM
// state doesn't bleed between tests.
afterEach(() => cleanup())
