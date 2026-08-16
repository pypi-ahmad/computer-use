export interface ModelPrice {
  label: string
  inputPerMillion: number
  outputPerMillion: number
  details: string
}

/** Published list rates (USD per 1M tokens). Batch and cache discounts are not applied. */
export const MODEL_PRICES: Record<string, ModelPrice> = {
  'claude-sonnet-5': {
    label: 'Sonnet 5',
    inputPerMillion: 2,
    outputPerMillion: 10,
    details: 'Anthropic $2 / $10 standard list rate. 50% Batch API discount is not applied here.',
  },
  'gemini-3.7-flash': {
    label: 'Gemini Flash 3.7',
    inputPerMillion: 0.75,
    outputPerMillion: 3.75,
    details: 'Half price of 3.6 Flash under a promo rate through 31 Dec 2026.',
  },
  'gemini-3.5-flash-lite': {
    label: 'Gemini 3.5 Flash Lite',
    inputPerMillion: 0.3,
    outputPerMillion: 2.5,
    details: 'Lowest cost Flash-family tier. Batch $0.15 / $1.25 is not applied here.',
  },
  'gpt-5.6-luna': {
    label: 'GPT 5.6 Luna',
    inputPerMillion: 0.2,
    outputPerMillion: 1.2,
    details: 'OpenAI budget tier. Prompt-cache reads ($0.02) are not metered separately.',
  },
  'gpt-5.6-terra': {
    label: 'GPT 5.6 Terra',
    inputPerMillion: 2,
    outputPerMillion: 12,
    details: 'OpenAI mid-tier. Long-context doubling (>272k input) is not applied automatically.',
  },
}

export function estimateSessionCost(model: string, inputTokens: number, outputTokens: number) {
  const rate = MODEL_PRICES[model]
  const input = Math.max(0, inputTokens)
  const output = Math.max(0, outputTokens)
  if (!rate) {
    return { known: false as const, inputUsd: 0, outputUsd: 0, totalUsd: 0, rate: null }
  }
  const inputUsd = (input / 1_000_000) * rate.inputPerMillion
  const outputUsd = (output / 1_000_000) * rate.outputPerMillion
  return { known: true as const, inputUsd, outputUsd, totalUsd: inputUsd + outputUsd, rate }
}

export function formatUsd(amount: number): string {
  if (amount > 0 && amount < 0.01) return `$${amount.toFixed(6)}`
  return `$${amount.toFixed(4)}`
}
