// === merged from frontend/src/pages/workbench/Timeline.jsx ===
/**
 * Timeline — the scrollable step timeline shown in the right panel.
 *
 * Extracted verbatim from ``Workbench.jsx``. Expansion state is owned
 * by the parent so keyboard-driven navigation across nested panels
 * remains consistent with the pre-PR behaviour.
 */

import { forwardRef } from 'react'
import formatTime from '../../utils'
import { ACTION_LABEL_MAP, ICON_SIZE, getActionIcon } from './constants.js'

const Timeline = forwardRef(function Timeline(
  { steps, expandedStep, setExpandedStep },
  ref,
) {
  return (
    <div className="wb-timeline" ref={ref}>
      {steps.length === 0 && <p className="wb-empty">Start a task to see the agent's actions here.</p>}
      {steps.map((step, i) => {
        const Icon = getActionIcon(step.action?.action)
        return (
          <div
            key={i}
            className={`wb-timeline-item ${step.error ? 'has-error' : ''} ${expandedStep === i ? 'expanded' : ''}`}
            onClick={() => setExpandedStep(expandedStep === i ? null : i)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                setExpandedStep(expandedStep === i ? null : i)
              }
            }}
            role="button"
            tabIndex={0}
            aria-expanded={expandedStep === i}
          >
            <div className="wb-timeline-head">
              <span className="wb-step-num">#{step.step_number}</span>
              <span className="wb-action-icon"><Icon size={ICON_SIZE} /></span>
              <span className="wb-action-name">{ACTION_LABEL_MAP[step.action?.action] || step.action?.action || 'Unknown'}</span>
              {step.action?.target && <span className="wb-action-target" title={step.action.target}>{step.action.target.length > 20 ? step.action.target.slice(0, 20) + '…' : step.action.target}</span>}
              {step.action?.text && step.action.action !== 'done' && (
                <span className="wb-action-text" title={step.action.text}>"{step.action.text.length > 20 ? step.action.text.slice(0, 20) + '…' : step.action.text}"</span>
              )}
              <span className="wb-step-time">{formatTime(step.timestamp)}</span>
            </div>
            {expandedStep === i && (
              <div className="wb-timeline-detail">
                {step.action?.reasoning && <p className="wb-reasoning">{step.action.reasoning}</p>}
                {!step.action?.reasoning && <p className="wb-reasoning" style={{ fontStyle: 'italic', opacity: 0.6 }}>No explanation provided</p>}
                {step.error && <p className="wb-step-error">Error: {step.error}</p>}
                <details className="wb-raw-details" onClick={(e) => e.stopPropagation()}>
                  <summary style={{ fontSize: 12, color: 'var(--text-secondary)', cursor: 'pointer', userSelect: 'none' }}>Show raw data</summary>
                  {step.action?.coordinates && <p className="wb-coords">Coords: [{step.action.coordinates.join(', ')}]</p>}
                  <pre className="wb-json">{JSON.stringify(step.action, null, 2)}</pre>
                </details>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
})

export default Timeline

