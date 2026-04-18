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
