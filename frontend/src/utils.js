// === merged from frontend/src/utils/escapeHtml.js ===
// Q-7: shared HTML-entity escaper used by the Workbench HTML exporter.
// Escapes all 5 entities so callers placing data inside attributes
// (`"`) or single-quoted content (`'`) can't XSS the exported file
// when it's opened locally.
export function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// === merged from frontend/src/utils/formatTime.js ===
/**
 * Formats a timestamp into a locale time string (HH:MM:SS, 24-hour).
 * Returns '--:--:--' if the timestamp is invalid.
 * @param {string|number|Date} ts - Timestamp to format.
 * @returns {string} Formatted time string.
 */
export default function formatTime(ts) {
  try {
    return new Date(ts).toLocaleTimeString('en-US', { hour12: false })
  } catch {
    return '--:--:--'
  }
}

// === merged from frontend/src/utils/pricing.js ===
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
 *   - GPT-5.5 / GPT-5.4: https://developers.openai.com/api/docs/models
 */
const MODEL_PRICING = {
  // Google — Gemini 3 Flash Preview (paid tier, text/image/video input).
  // Audio input is $1.00/M (not modeled — CU only sends text + screenshots).
  'gemini-3-flash-preview': { input: 0.50, output: 3.00 },
  // Anthropic — base input / base output (1.25× cache-write and 0.1× cache-hit
  // multipliers from the Anthropic pricing page are not modeled here).
  'claude-opus-4-7': { input: 5.00, output: 25.00 },
  'claude-sonnet-4-6': { input: 3.00, output: 15.00 },
  // OpenAI — standard pricing (cached input/tool charges are not modeled).
  'gpt-5.5': { input: 5.00, output: 30.00 },
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

// === merged from frontend/src/utils/sessionHistory.js ===
/**
 * Bounded session history stored in localStorage.
 * Never stores API keys or sensitive data.
 */
const HISTORY_KEY = 'cua_session_history_v1'
const MAX_SESSIONS = 50

export function getSessionHistory() {
  try {
    const data = JSON.parse(localStorage.getItem(HISTORY_KEY))
    return Array.isArray(data) ? data : []
  } catch {
    return []
  }
}

export function addSessionToHistory(entry) {
  const history = getSessionHistory()
  history.unshift(entry)
  if (history.length > MAX_SESSIONS) history.length = MAX_SESSIONS
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history))
  } catch {
    // Storage full — silently drop
  }
}

export function clearSessionHistory() {
  localStorage.removeItem(HISTORY_KEY)
}

// === merged from frontend/src/utils/theme.js ===
/**
 * Theme management — persists preference in localStorage and
 * applies it via a data-theme attribute on <html>.
 */
const THEME_KEY = 'cua_theme'

export function getTheme() {
  return localStorage.getItem(THEME_KEY) || 'dark'
}

export function setTheme(theme) {
  localStorage.setItem(THEME_KEY, theme)
  document.documentElement.setAttribute('data-theme', theme)
}

export function initTheme() {
  setTheme(getTheme())
}

