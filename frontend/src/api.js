/** Base URL prefix for all API calls. */
const API_BASE = '/api'

/**
 * Internal fetch wrapper that handles JSON/text response parsing and error normalization.
 * @param {string} path - API path appended to API_BASE.
 * @param {RequestInit} [options={}] - Fetch options (method, body, etc.). Pass
 *   `options.signal` from an AbortController to cancel the request on unmount
 *   or when the user navigates away.
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
export async function getContainerStatus(signal) {
  return request('/container/status', { signal })
}

/** Sends a POST request to start the Docker container. */
export async function startContainer(signal) {
  return request('/container/start', { method: 'POST', signal })
}

/** Sends a POST request to stop the Docker container. */
export async function stopContainer(signal) {
  return request('/container/stop', { method: 'POST', signal })
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
 * @param {AbortSignal} [signal]
 * @returns {Promise<{session_id?: string, error?: string}>}
 */
export async function startAgent(
  { task, apiKey, model, maxSteps, mode, provider, engine = 'computer_use', executionTarget = 'docker', reasoningEffort, useBuiltinSearch = false },
  signal,
) {
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
    if (useBuiltinSearch) body.use_builtin_search = true
    const res = await fetch(`${API_BASE}/agent/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
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
 *
 * Returns a structured result instead of throwing so the caller can
 * distinguish three outcomes:
 *   - ``confirmedStopped: true``        → backend explicitly confirmed ``status: "stopped"``.
 *   - ``sessionGone: true``             → session already gone (positive confirmation, currently 404).
 *   - everything else                   → transient / ambiguous failure; run may still be alive.
 *
 * The caller (see ``useSessionController``) must only clear local
 * ``sessionId`` / ``agentRunning`` state in the first two cases. On a
 * transient failure the session handle must be preserved so the user
 * can retry — otherwise a silent-running agent keeps spending tokens.
 *
 * @param {string} sessionId
 * @param {AbortSignal} [signal]
 * @returns {Promise<{
 *   ok: boolean,
 *   status: number,
 *   data: any,
 *   confirmedStopped: boolean,
 *   sessionGone: boolean,
 *   error?: string,
 * }>}
 */
export async function stopAgent(sessionId, signal) {
  try {
    const res = await fetch(`${API_BASE}/agent/stop/${sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal,
    })
    const ct = res.headers.get('content-type') || ''
    let data = null
    try {
      data = ct.includes('application/json') ? await res.json() : await res.text()
    } catch {
      data = null
    }
    const responseStatus = data && typeof data === 'object' ? data.status : null
    if (!res.ok) {
      const error = (data && typeof data === 'object' && data.error) || (typeof data === 'string' && data) || res.statusText
      return {
        ok: false,
        status: res.status,
        data,
        error,
        confirmedStopped: false,
        sessionGone: res.status === 404,
      }
    }
    return {
      ok: true,
      status: res.status,
      data,
      confirmedStopped: responseStatus === 'stopped',
      sessionGone: false,
    }
  } catch (e) {
    // Network error, aborted request, DNS failure, etc. status=0
    // signals "we could not even talk to the backend" — caller must
    // treat this as ambiguous and preserve sessionId.
    return {
      ok: false,
      status: 0,
      data: null,
      error: String(e?.message || e),
      confirmedStopped: false,
      sessionGone: false,
    }
  }
}

/** Fetches the current or recently-finished status for an agent session. */
export async function getAgentStatus(sessionId, signal) {
  return request(`/agent/status/${sessionId}`, { signal })
}

/** Fetches API key availability/source for each provider. */
export async function getKeyStatuses(signal) {
  return request('/keys/status', { signal })
}

/** Fetches the list of available AI models from the backend. */
export async function getModels(signal) {
  return request('/models', { signal })
}

/**
 * Responds to a safety confirmation prompt for a running session.
 * @param {string} sessionId - Session ID.
 * @param {boolean} confirm - True to approve, false to deny.
 * @param {AbortSignal} [signal]
 */
export async function confirmSafety(sessionId, confirm, signal) {
  return request('/agent/safety-confirm', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, confirm }),
    signal,
  })
}

/**
 * Lightweight API key pre-validation.
 * @param {string} provider - Provider name.
 * @param {string} apiKey - Key to validate.
 * @param {AbortSignal} [signal]
 * @returns {Promise<{valid: boolean, message: string}>}
 */
export async function validateKey(provider, apiKey, signal) {
  return request('/keys/validate', {
    method: 'POST',
    body: JSON.stringify({ provider, api_key: apiKey }),
    signal,
  })
}
