import { CheckCircle2, XCircle, Square, X } from 'lucide-react'

function normalizeGeminiChunks(grounding) {
  return Array.isArray(grounding?.groundingChunks)
    ? grounding.groundingChunks.filter(chunk => chunk?.web?.uri)
    : []
}

function normalizeGeminiSupports(grounding) {
  return Array.isArray(grounding?.groundingSupports)
    ? grounding.groundingSupports.filter(support => support && typeof support === 'object')
    : []
}

function GeminiCitationMarkers({ chunkIndices, chunks }) {
  const links = chunkIndices
    .map((chunkIndex) => {
      const chunk = chunks[chunkIndex]
      const web = chunk?.web
      if (!web?.uri) return null
      return {
        chunkIndex,
        title: web.title || web.uri,
        uri: web.uri,
      }
    })
    .filter(Boolean)

  if (!links.length) return null

  return (
    <sup className="completion-citations">
      {links.map(({ chunkIndex, title, uri }) => (
        <a
          key={`${uri}-${chunkIndex}`}
          className="completion-citation-link"
          href={uri}
          target="_blank"
          rel="noreferrer noopener"
          title={title}
        >
          [{chunkIndex + 1}]
        </a>
      ))}
    </sup>
  )
}

function GeminiGroundedText({ text, grounding }) {
  if (!text) return null

  const chunks = normalizeGeminiChunks(grounding)
  const supports = normalizeGeminiSupports(grounding)
  const insertions = new Map()

  supports.forEach((support) => {
    const endIndex = Number.isInteger(support?.segment?.endIndex)
      ? Math.max(0, Math.min(text.length, support.segment.endIndex))
      : null
    if (endIndex == null) return

    const indices = Array.isArray(support?.groundingChunkIndices)
      ? support.groundingChunkIndices.filter(idx => Number.isInteger(idx) && idx >= 0)
      : []
    if (!indices.length) return

    const existing = insertions.get(endIndex) || []
    indices.forEach((idx) => {
      if (!existing.includes(idx)) existing.push(idx)
    })
    insertions.set(endIndex, existing)
  })

  if (!insertions.size) {
    return <span>{text}</span>
  }

  const nodes = []
  const breakpoints = [...insertions.keys()].sort((left, right) => left - right)
  let cursor = 0

  breakpoints.forEach((point) => {
    if (point > cursor) {
      nodes.push(
        <span key={`text-${cursor}-${point}`}>
          {text.slice(cursor, point)}
        </span>,
      )
    }
    nodes.push(
      <GeminiCitationMarkers
        key={`cite-${point}`}
        chunkIndices={insertions.get(point) || []}
        chunks={chunks}
      />,
    )
    cursor = Math.max(cursor, point)
  })

  if (cursor < text.length) {
    nodes.push(
      <span key={`text-${cursor}-end`}>
        {text.slice(cursor)}
      </span>,
    )
  }

  return <>{nodes}</>
}

function GeminiGroundingResult({ text, grounding }) {
  const renderedContent = typeof grounding?.renderedContent === 'string'
    ? grounding.renderedContent.trim()
    : ''
  const chunks = normalizeGeminiChunks(grounding)
  const queries = Array.isArray(grounding?.webSearchQueries)
    ? grounding.webSearchQueries.filter(query => typeof query === 'string' && query.trim())
    : []

  return (
    <div className="completion-result completion-result-grounded">
      {text && (
        <div className="completion-grounded-text">
          <GeminiGroundedText text={text} grounding={grounding} />
        </div>
      )}

      {renderedContent && (
        <div className="completion-grounding-card">
          <div className="completion-grounding-label">Google Search Suggestions</div>
          <iframe
            className="completion-grounding-frame"
            title="Google Search suggestions"
            sandbox="allow-popups allow-popups-to-escape-sandbox"
            referrerPolicy="no-referrer"
            srcDoc={renderedContent}
          />
        </div>
      )}

      {queries.length > 0 && (
        <div className="completion-grounding-meta">
          <div className="completion-grounding-label">Search queries</div>
          <div className="completion-grounding-pills">
            {queries.map((query) => (
              <span key={query} className="completion-grounding-pill">{query}</span>
            ))}
          </div>
        </div>
      )}

      {chunks.length > 0 && (
        <div className="completion-grounding-meta">
          <div className="completion-grounding-label">Sources</div>
          <div className="completion-grounding-links">
            {chunks.map((chunk, index) => (
              <a
                key={`${chunk.web.uri}-${index}`}
                className="completion-grounding-source"
                href={chunk.web.uri}
                target="_blank"
                rel="noreferrer noopener"
                title={chunk.web.title || chunk.web.uri}
              >
                [{index + 1}] {chunk.web.title || chunk.web.uri}
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function CompletionBanner({ finishData, stepCount, costEstimate, onDismiss }) {
  if (!finishData) return null

  const status = finishData.status || 'completed'
  const isSuccess = status === 'completed'
  const isError = status === 'error'
  const label = isSuccess ? 'Task Complete' : isError ? 'Task Failed' : 'Task Stopped'
  const Icon = isSuccess ? CheckCircle2 : isError ? XCircle : Square

  const elapsed = finishData.elapsedSeconds
  const durationText = elapsed != null ? (elapsed >= 60 ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : `${elapsed}s`) : null
  const finalText = (finishData.final_text || '').trim()
  const geminiGrounding = finishData.gemini_grounding && typeof finishData.gemini_grounding === 'object'
    ? finishData.gemini_grounding
    : null

  return (
    <div className={`completion-banner ${isSuccess ? 'success' : isError ? 'error' : 'stopped'}`} role="status">
      <div className="completion-content">
        <Icon size={16} />
        <span className="completion-label">{label}</span>
        <span className="completion-detail">
          {finishData.steps ?? stepCount} steps
          {durationText && ` · ${durationText}`}
          {costEstimate && ` · ~$${costEstimate.cost.toFixed(4)}`}
        </span>
        {geminiGrounding
          ? <GeminiGroundingResult text={finalText} grounding={geminiGrounding} />
          : finalText && <div className="completion-result">{finalText}</div>}
      </div>
      <button className="completion-dismiss" onClick={onDismiss} aria-label="Dismiss"><X size={16} /></button>
    </div>
  )
}
