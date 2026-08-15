import { expect, it } from 'vitest'
import { estimateSessionCost, formatUsd } from './pricing'

it('prices Luna tokens at $0.20 / $1.20 per million', () => {
  const cost = estimateSessionCost('gpt-5.6-luna', 1_000_000, 1_000_000)
  expect(cost.known).toBe(true)
  expect(cost.inputUsd).toBeCloseTo(0.2)
  expect(cost.outputUsd).toBeCloseTo(1.2)
  expect(cost.totalUsd).toBeCloseTo(1.4)
})

it('prices Gemini 3.7 Flash at the promo $0.75 / $3.75 rate', () => {
  const cost = estimateSessionCost('gemini-3.7-flash', 2_000_000, 500_000)
  expect(cost.totalUsd).toBeCloseTo(0.75 * 2 + 3.75 * 0.5)
})

it('prices Gemini 3.5 Flash Lite and Sonnet 5 list rates', () => {
  expect(estimateSessionCost('gemini-3.5-flash-lite', 1_000_000, 0).inputUsd).toBeCloseTo(0.3)
  expect(estimateSessionCost('claude-sonnet-5', 0, 1_000_000).outputUsd).toBeCloseTo(10)
})

it('marks unknown models as unpriced', () => {
  const cost = estimateSessionCost('not-a-model', 100, 100)
  expect(cost.known).toBe(false)
  expect(cost.totalUsd).toBe(0)
})

it('formats tiny USD amounts with extra digits', () => {
  expect(formatUsd(0)).toBe('$0.0000')
  expect(formatUsd(0.0008)).toBe('$0.000800')
  expect(formatUsd(1.25)).toBe('$1.2500')
})
