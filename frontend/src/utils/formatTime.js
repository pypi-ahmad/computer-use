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
