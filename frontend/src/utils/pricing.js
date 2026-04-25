/**
 * Approximate model pricing for cost estimation.
 * Values are in USD per 1 million tokens (text input / output, paid tier,
 * standard processing — caching, batch, and data-residency multipliers
 * are NOT modeled here; this is a per-step list-price approximation).
 *
 * Centralized here so updates only need to happen in one place.
 *
 * Last reviewed: 2026-04-25 against the official provider pricing pages.
 *   - Gemini 3 Flash Preview: https://ai.google.dev/gemini-api/docs/pricing
 *   - Claude Opus 4.7 / Sonnet 4.6: https://platform.claude.com/docs/en/about-claude/pricing
 *   - GPT-5.4: https://openai.com/api/pricing/
 */
const MODEL_PRICING = {
  // Google — Gemini 3 Flash Preview (paid tier, text/image/video input).
  // Audio input is $1.00/M (not modeled — CU only sends text + screenshots).
  'gemini-3-flash-preview': { input: 0.50, output: 3.00 },
  // Anthropic — base input / base output (1.25× cache-write and 0.1× cache-hit
  // multipliers from the Anthropic pricing page are not modeled here).
  'claude-opus-4-7': { input: 5.00, output: 25.00 },
  'claude-sonnet-4-6': { input: 3.00, output: 15.00 },
  // OpenAI — GPT-5.4 standard pricing (cached input is $0.25/M, not modeled).
  'gpt-5.4': { input: 2.50, output: 15.00 },
}

// Rough average tokens per CU step.
// Input breakdown (per step, post-prompt-caching):
//   - system prompt / tool schemas (cached)   ~   500 tokens
//   - running conversation context            ~ 1 500 tokens
//   - screenshot (image → model tokens)       ~ 3 000 tokens
// Output breakdown:
//   - reasoning + tool call + text            ~   800 tokens
//
// C18: the previous 3 500 input figure silently ignored screenshot
// token cost, which dominates Computer-Use billing. 5 000 is a
// more honest mid-estimate across Gemini / Claude / GPT.
const AVG_INPUT_TOKENS_PER_STEP = 5000
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
