/**
 * Approximate model pricing for cost estimation.
 * Values are in USD per 1 million tokens. Clearly approximate.
 * Centralized here so updates only need to happen in one place.
 * Last reviewed: 2026-04 (approximate — check provider pricing pages for current rates).
 */
const MODEL_PRICING = {
  // Google Gemini
  'gemini-2.5-flash-preview-04-17': { input: 0.15, output: 0.60 },
  'gemini-2.5-pro-preview-03-25': { input: 1.25, output: 5.00 },
  'gemini-2.0-flash': { input: 0.10, output: 0.40 },
  'gemini-2.0-flash-lite': { input: 0.075, output: 0.30 },
  // Anthropic Claude
  'claude-sonnet-4-20250514': { input: 3.00, output: 15.00 },
  'claude-3-7-sonnet-20250219': { input: 3.00, output: 15.00 },
  // OpenAI
  'computer-use-preview': { input: 3.00, output: 12.00 },
}

// Rough average tokens per CU step (screenshot + prompt + response)
const AVG_INPUT_TOKENS_PER_STEP = 3500
const AVG_OUTPUT_TOKENS_PER_STEP = 800

/**
 * Estimate approximate session cost.
 * @param {string} modelId - Model identifier from allowed_models.json.
 * @param {number} steps - Number of completed steps.
 * @returns {{ cost: number, note: string } | null} Null if model pricing unknown.
 */
export function estimateCost(modelId, steps) {
  const pricing = MODEL_PRICING[modelId]
  if (!pricing || steps <= 0) return null
  const inputCost = (steps * AVG_INPUT_TOKENS_PER_STEP / 1_000_000) * pricing.input
  const outputCost = (steps * AVG_OUTPUT_TOKENS_PER_STEP / 1_000_000) * pricing.output
  return {
    cost: inputCost + outputCost,
    note: 'Approximate — actual cost depends on token usage',
  }
}
