import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  GitBranch,
  ListChecks,
  RefreshCw,
  Route,
  ShieldAlert,
  Target,
} from 'lucide-react'

const ICON_SIZE = 13

function titleize(value) {
  const text = String(value || '').replace(/_/g, ' ').trim()
  if (!text) return 'None'
  return text.replace(/\b\w/g, (char) => char.toUpperCase())
}

function compact(value) {
  const text = String(value || '').trim()
  return text || 'None'
}

function cssToken(value) {
  return String(value || 'none').trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_')
}

function stringList(value) {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item || '').trim()).filter(Boolean)
}

function GraphField({ icon: Icon, label, value, title }) {
  return (
    <div className="wb-graph-field" title={title || compact(value)}>
      <span className="wb-graph-field-label">
        <Icon size={ICON_SIZE} />
        {label}
      </span>
      <span className="wb-graph-field-value">{value}</span>
    </div>
  )
}

function CriteriaList({ criteria, unmet }) {
  const remaining = new Set(unmet)
  const visible = criteria.length > 0 ? criteria : unmet
  if (visible.length === 0) {
    return <p className="wb-graph-muted">None</p>
  }
  return (
    <ul className="wb-graph-criteria">
      {visible.map((item) => {
        const isUnmet = remaining.has(item)
        return (
          <li key={item} className={isUnmet ? 'unmet' : 'met'}>
            <span>{isUnmet ? 'Open' : 'Met'}</span>
            <p>{item}</p>
          </li>
        )
      })}
    </ul>
  )
}

function InsightBlock({ icon: Icon, label, children }) {
  return (
    <section className="wb-graph-insight">
      <div className="wb-graph-insight-title">
        <Icon size={ICON_SIZE} />
        <span>{label}</span>
      </div>
      {children}
    </section>
  )
}

export default function GraphRunPanel({ graphRun }) {
  const pendingApproval = graphRun?.pending_approval
  const approvalValue = pendingApproval
    ? titleize(pendingApproval.origin || 'safety')
    : 'None'
  const retryCount = Number(graphRun?.retry_count || 0)
  const replanCount = Number(graphRun?.replan_count || 0)
  const phase = graphRun ? compact(graphRun.phase) : 'Idle'
  const verifierVerdict = compact(graphRun?.verifier_verdict)
  const verificationRationale = compact(graphRun?.verification_rationale)
  const completionCriteria = stringList(graphRun?.completion_criteria)
  const unmetCriteria = stringList(graphRun?.unmet_completion_criteria)
  const recovery = graphRun?.recovery || null
  const replanReason = compact(graphRun?.replan_reason || recovery?.replan_reason)
  const hasVerifierInsight = (
    verifierVerdict !== 'None'
    || verificationRationale !== 'None'
    || completionCriteria.length > 0
    || unmetCriteria.length > 0
  )
  const hasRecoveryInsight = recovery || replanReason !== 'None' || replanCount > 0

  return (
    <div className="wb-graph-section">
      <div className="wb-panel-header">
        <h3>Graph Run</h3>
        <span className={`wb-graph-phase ${cssToken(phase)}`}>
          {titleize(phase)}
        </span>
      </div>

      {!graphRun ? (
        <div className="wb-graph-empty">No active graph.</div>
      ) : (
        <div className="wb-graph-run">
          <div className="wb-graph-node-row">
            <GitBranch size={14} />
            <span className="wb-graph-node">{titleize(graphRun.current_node || graphRun.node)}</span>
            <span className={`wb-graph-status ${cssToken(graphRun.status)}`}>
              {titleize(graphRun.status)}
            </span>
          </div>

          <div className="wb-graph-grid">
            <GraphField icon={Route} label="Route" value={titleize(graphRun.route)} />
            <GraphField icon={RefreshCw} label="Retry/Replan" value={`${retryCount}/${replanCount}`} />
            <GraphField icon={CheckCircle2} label="Verifier" value={titleize(graphRun.verifier_verdict)} />
            <GraphField
              icon={ShieldAlert}
              label="Approval"
              value={approvalValue}
              title={pendingApproval?.explanation || approvalValue}
            />
          </div>

          <GraphField
            icon={Target}
            label="Active Subgoal"
            value={compact(graphRun.active_subgoal)}
          />

          {hasVerifierInsight && (
            <InsightBlock
              icon={FileText}
              label={verifierVerdict === 'complete' ? 'Why Done' : 'Verifier Rationale'}
            >
              <p className="wb-graph-prose">{verificationRationale}</p>
              <div className="wb-graph-subhead">
                <ListChecks size={ICON_SIZE} />
                <span>Criteria Remaining</span>
              </div>
              <CriteriaList criteria={completionCriteria} unmet={unmetCriteria} />
            </InsightBlock>
          )}

          {hasRecoveryInsight && (
            <InsightBlock icon={AlertTriangle} label="Recovery">
              <div className="wb-graph-recovery-row">
                <span>Class</span>
                <strong>{titleize(recovery?.classification)}</strong>
              </div>
              <div className="wb-graph-recovery-row">
                <span>Cause</span>
                <strong>{titleize(recovery?.retry_reason || recovery?.verification_status)}</strong>
              </div>
              <p className="wb-graph-prose">{replanReason}</p>
              {compact(recovery?.evidence_brief) !== 'None' && (
                <p className="wb-graph-muted">{recovery.evidence_brief}</p>
              )}
            </InsightBlock>
          )}
        </div>
      )}
    </div>
  )
}
