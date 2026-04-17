/** Base URL prefix for all API calls. */
const API_BASE = '/api'

/**
 * Internal fetch wrapper that handles JSON/text response parsing and error normalization.
 * @param {string} path - API path appended to API_BASE.
 * @param {RequestInit} [options={}] - Fetch options (method, body, etc.).
 * @returns {Promise<any>} Parsed JSON object or plain text.
 * @throws {Error} On non-2xx responses, with the response body as the message.
 */
async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let message
    try {
      message = await res.text()
    } catch {
      message = res.statusText
    }
    throw new Error(message || res.statusText)
  }
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) {
    return res.json()
  }
  return res.text()
}

/** Fetches the current Docker container status from the backend. */
export async function getContainerStatus() {
  return request('/container/status')
}

/** Sends a POST request to start the Docker container. */
export async function startContainer() {
  return request('/container/start', { method: 'POST' })
}

/** Sends a POST request to stop the Docker container. */
export async function stopContainer() {
  return request('/container/stop', { method: 'POST' })
}

/**
 * Starts a CUA agent session. Bypasses `request()` to handle 400/429 validation
 * errors gracefully — always returns `{ error?, session_id? }` instead of throwing.
 * @param {Object} params
 * @param {string} params.task - Natural-language task description.
 * @param {string} params.apiKey - API key (empty string = backend resolves from env).
 * @param {string} params.model - Model identifier.
 * @param {number} params.maxSteps - Maximum agent steps.
 * @param {string} params.mode - Execution mode ('desktop' only).
 * @param {string} params.provider - AI provider ('google' | 'anthropic' | 'openai').
 * @param {string} [params.engine='computer_use'] - Execution engine.
 * @param {string} [params.executionTarget='docker'] - Execution target.
 * @returns {Promise<{session_id?: string, error?: string}>}
 */
export async function startAgent({ task, apiKey, model, maxSteps, mode, provider, engine = 'computer_use', executionTarget = 'docker', reasoningEffort = null }) {
  try {
    const body = {
      task,
      api_key: apiKey,
      model,
      max_steps: maxSteps,
      mode,
      engine,
      provider,
      execution_target: executionTarget,
    }
    if (reasoningEffort) body.reasoning_effort = reasoningEffort
    const res = await fetch(`${API_BASE}/agent/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    const ct = res.headers.get('content-type') || ''
    let data
    if (ct.includes('application/json')) {
      data = await res.json()
    } else {
      const text = await res.text()
      data = { error: text || res.statusText }
    }

    // Normalize non-2xx into error if backend didn't already provide one
    if (!res.ok && !data?.error) {
      data = { ...data, error: res.statusText }
    }

    return data
  } catch (e) {
    return { error: String(e?.message || e) }
  }
}

/**
 * Stops a running agent session.
 * @param {string} sessionId - The session ID returned by `startAgent`.
 */
export async function stopAgent(sessionId) {
  return request(`/agent/stop/${sessionId}`, { method: 'POST' })
}

/** Fetches the current or recently-finished status for an agent session. */
export async function getAgentStatus(sessionId) {
  return request(`/agent/status/${sessionId}`)
}

/** Fetches API key availability/source for each provider. */
export async function getKeyStatuses() {
  return request('/keys/status')
}

/** Fetches the list of available AI models from the backend. */
export async function getModels() {
  return request('/models')
}

/**
 * Responds to a safety confirmation prompt for a running session.
 * @param {string} sessionId - Session ID.
 * @param {boolean} confirm - True to approve, false to deny.
 */
export async function confirmSafety(sessionId, confirm) {
  return request('/agent/safety-confirm', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, confirm }),
  })
}

/**
 * Lightweight API key pre-validation.
 * @param {string} provider - Provider name.
 * @param {string} apiKey - Key to validate.
 * @returns {Promise<{valid: boolean, message: string}>}
 */
export async function validateKey(provider, apiKey) {
  return request('/keys/validate', {
    method: 'POST',
    body: JSON.stringify({ provider, api_key: apiKey }),
  })
}
