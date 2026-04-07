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
