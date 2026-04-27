import {
  CheckCircle2,
  GitBranch,
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

export default function GraphRunPanel({ graphRun }) {
  const pendingApproval = graphRun?.pending_approval
  const approvalValue = pendingApproval
    ? titleize(pendingApproval.origin || 'safety')
    : 'None'
  const retryCount = Number(graphRun?.retry_count || 0)
  const replanCount = Number(graphRun?.replan_count || 0)
  const phase = graphRun ? compact(graphRun.phase) : 'Idle'

  return (
    <div className="wb-graph-section">
      <div className="wb-panel-header">
        <h3>Graph Run</h3>
        <span className={`wb-graph-phase ${phase.toLowerCase()}`}>
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
            <span className={`wb-graph-status ${compact(graphRun.status).toLowerCase()}`}>
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
        </div>
      )}
    </div>
  )
}
