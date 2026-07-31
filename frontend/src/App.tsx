import { Component, type ErrorInfo, type FormEvent, type ReactNode, useEffect, useMemo, useState } from 'react'
import { Activity, BookOpen, CircleStop, History, KeyRound, MonitorPlay, Play, Plus, Radio, ShieldCheck, Timer, Trash2 } from 'lucide-react'
import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import { api } from './api'
import type { Action, Analytics, EventRecord, Model, Route as ProviderRoute, Session, Workflow } from './types'
import { useLiveStream } from './useLiveStream'

const tabs = [
  ['/', 'Live session', MonitorPlay], ['/audit', 'Audit trail', History], ['/workflows', 'Workflow library', BookOpen],
  ['/providers', 'Providers', KeyRound], ['/analytics', 'Analytics', Activity],
] as const

class RouteBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('Route failed', { message: error.message, componentStack: info.componentStack }) }
  render() { return this.state.failed ? <section className="empty"><h2>View unavailable</h2><p>Reload this view. The active agent session was not changed.</p><button onClick={() => location.reload()}>Reload view</button></section> : this.props.children }
}

function useResource<T>(load: () => Promise<T>, deps: readonly unknown[] = []) {
  const [data, setData] = useState<T | null>(null); const [error, setError] = useState('')
  useEffect(() => { let live = true; load().then(value => live && setData(value)).catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e))); return () => { live = false } }, deps)
  return { data, error, refresh: () => load().then(setData).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e))) }
}

function StatusBadge({ status }: { status: string }) { return <span className={`status status-${status.toLowerCase()}`}><span aria-hidden="true" />{status}</span> }
function Header({ eyebrow, title, aside }: { eyebrow: string; title: string; aside?: ReactNode }) { return <header className="page-header"><div><p>{eyebrow}</p><h1>{title}</h1></div>{aside}</header> }

function LivePage({ credentialSessionId, preferredRoute }: { credentialSessionId: string | null; preferredRoute: string }) {
  const models = useResource(() => api.models(), []); const routes = useResource(() => api.routes(), [])
  const [session, setSession] = useState<Session | null>(null); const [task, setTask] = useState(''); const [modelId, setModelId] = useState(''); const [routeId, setRouteId] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  const model = models.data?.data.find(item => item.logicalId === modelId) ?? models.data?.data[0]
  useEffect(() => { if (!modelId && models.data?.data[0]) setModelId(models.data.data[0].logicalId) }, [models.data, modelId])
  useEffect(() => { if (model && !model.routes.some(route => route.id === routeId)) setRouteId(model.routes.find(route => route.id === preferredRoute)?.id ?? model.routes[0]?.id ?? '') }, [model, routeId, preferredRoute])
  const stream = useLiveStream(session?.id ?? null)
  async function start(event: FormEvent) { event.preventDefault(); if (!model || !routeId || !task.trim()) return; setBusy(true); setError(''); try { setSession(await api.createSession({ task: task.trim(), model: model.logicalId, primaryRoute: routeId, fallbackRoutes: [], ...(credentialSessionId ? { credentialSessionId } : {}), maxSteps: 50, retainAuditFrames: true })) } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  async function stop() { if (!session) return; setSession(await api.stopSession(session.id)) }
  const stages = ['capture', 'encode', 'infer', 'validate', 'act'] as const
  const latestStage = [...stream.events].reverse().find(item => item.event === 'PIPELINE_STAGE')
  const safety = [...stream.events].reverse().find(item => item.event === 'SAFETY_CONFIRMATION')
  const terminal = [...stream.events].reverse().find(item => item.event === 'SESSION_TERMINAL')
  return <><Header eyebrow="Execution deck" title="Live agent session" aside={<div className="header-state"><Radio size={16}/><span>{stream.connected ? 'Stream linked' : 'Stream idle'}</span></div>} />
    <div className="execution-rail" aria-label="Execution pipeline">{stages.map((stage, index) => <div className={latestStage?.event === 'PIPELINE_STAGE' && latestStage.stage === stage ? 'active' : ''} key={stage}><span>{String(index + 1).padStart(2, '0')}</span><strong>{stage}</strong></div>)}</div>
    {(stream.error || terminal?.event === 'SESSION_TERMINAL' && terminal.message) && <p className="form-error" role="alert">{stream.error || terminal?.message}</p>}
    {safety?.event === 'SAFETY_CONFIRMATION' && <section className="safety" role="alertdialog" aria-label="Safety confirmation required"><ShieldCheck/><div><strong>Approval required</strong><p>{safety.explanation}</p></div></section>}
    <div className="live-grid"><section className="viewport panel"><div className="panel-head"><span>Viewport / 1440 × 900</span>{session && <StatusBadge status={session.status}/>}</div><div className="screen">{stream.frameUrl ? <img src={stream.frameUrl} alt="Live agent desktop"/> : <div><MonitorPlay size={42}/><strong>{session ? 'Waiting for first frame' : 'No session on the wire'}</strong><span>Start a run to link the visual stream.</span></div>}</div></section>
    <form className="control panel" onSubmit={start}><div className="panel-head"><span>Mission control</span><ShieldCheck size={17}/></div><label>Task<textarea value={task} onChange={e => setTask(e.target.value)} placeholder="Describe the desktop outcome…" rows={6}/></label><label>Computer Use model<select value={model?.logicalId ?? ''} onChange={e => setModelId(e.target.value)}>{models.data?.data.map(item => <option value={item.logicalId} key={item.logicalId}>{item.displayName}</option>)}</select></label><label>Primary route<select value={routeId} onChange={e => setRouteId(e.target.value)}>{model?.routes.map(route => <option value={route.id} key={route.id}>{route.provider} · {route.transport}</option>)}</select></label>{(error || models.error || routes.error) && <p className="form-error" role="alert">{error || models.error || routes.error}</p>}<div className="actions">{session?.status === 'RUNNING' ? <button type="button" className="danger" onClick={stop}><CircleStop size={17}/>Stop run</button> : <button className="primary" disabled={busy || !task.trim() || !routeId}><Play size={17}/>{busy ? 'Starting…' : 'Start run'}</button>}</div></form></div></>
}

function AuditPage() {
  const sessions = useResource(() => api.sessions(), []); const [selected, setSelected] = useState(''); const [actions, setActions] = useState<Action[]>([]); const [events, setEvents] = useState<EventRecord[]>([])
  useEffect(() => { const id = selected || sessions.data?.data[0]?.id; if (!id) return; setSelected(id); Promise.all([api.actions(id), api.events(id)]).then(([a, e]) => { setActions(a.data); setEvents(e.data) }).catch(() => { setActions([]); setEvents([]) }) }, [sessions.data, selected])
  return <><Header eyebrow="Flight recorder" title="Session audit trail" aside={<select aria-label="Session" value={selected} onChange={e => setSelected(e.target.value)}>{sessions.data?.data.map(item => <option value={item.id} key={item.id}>{item.task.slice(0, 42)}</option>)}</select>}/><section className="panel table-panel"><div className="panel-head"><span>Confirmed action journal</span><span>{actions.length} actions</span></div><div className="timeline">{actions.length ? actions.map((action, index) => <article key={action.id ?? index}><span>{String(action.sequence ?? index + 1).padStart(3, '0')}</span><div><strong>{action.actionType ?? action.action ?? 'ACTION'}</strong><code>{JSON.stringify(action.payload ?? {})}</code></div></article>) : <div className="empty"><History/><h2>No recorded actions</h2><p>Confirmed actions appear here in execution order.</p></div>}</div></section><section className="event-strip">{events.slice(0, 8).map((event, i) => <div key={event.id ?? i}><span>{event.createdAt?.slice(11, 19) ?? '—'}</span>{event.eventType ?? event.type}</div>)}</section></>
}

function WorkflowPage() {
  const workflows = useResource(() => api.workflows(), []); const [name, setName] = useState(''); const [steps, setSteps] = useState(''); const [error, setError] = useState('')
  async function create(event: FormEvent) { event.preventDefault(); const clean = steps.split('\n').map(s => s.trim()).filter(Boolean); if (!name.trim() || !clean.length) return; try { await api.createWorkflow({ name: name.trim(), slug: name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''), variablesSchema: { type: 'object' }, steps: clean }); setName(''); setSteps(''); await workflows.refresh() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } }
  return <><Header eyebrow="Reusable procedures" title="Prompt & skill library" aside={<span className="count">{workflows.data?.data.length ?? 0} workflows</span>}/><div className="library-grid"><section className="workflow-list">{workflows.data?.data.map(workflow => <article className="panel workflow" key={workflow.id}><div><BookOpen size={18}/><span>v{workflow.version}</span></div><h2>{workflow.name}</h2><ol>{workflow.steps.slice(0, 3).map(step => <li key={step}>{step}</li>)}</ol></article>)}{workflows.data?.data.length === 0 && <div className="empty"><BookOpen/><h2>No workflows yet</h2><p>Create a declarative procedure for repeatable runs.</p></div>}</section><form className="panel editor" onSubmit={create}><div className="panel-head"><span>New workflow</span><Plus size={17}/></div><label>Name<input value={name} onChange={e => setName(e.target.value)} placeholder="Quarterly access review"/></label><label>Ordered instructions<textarea value={steps} onChange={e => setSteps(e.target.value)} rows={10} placeholder={'Open the admin portal\nExport active accounts\nSave the report'}/></label>{error && <p role="alert" className="form-error">{error}</p>}<button className="primary"><Plus size={17}/>Create workflow</button></form></div></>
}

function ProvidersPage({ onCredential }: { onCredential: (id: string | null) => void }) {
  const routes = useResource(() => api.routes(), []); const [provider, setProvider] = useState('OPENAI'); const [key, setKey] = useState(''); const [credential, setCredential] = useState<{ id: string; providers: string[]; expiresAt: number } | null>(null); const [error, setError] = useState('')
  async function save(event: FormEvent) { event.preventDefault(); try { const created = await api.credentialSession({ [provider]: key }); setCredential(created); onCredential(created.id); setKey(''); setError('') } catch (e) { setError(e instanceof Error ? e.message : String(e)) } }
  async function remove() { if (!credential) return; await api.deleteCredentialSession(credential.id); setCredential(null); onCredential(null) }
  const providers = Array.from(new Set(routes.data?.data.map(route => route.provider) ?? ['OPENAI']))
  return <><Header eyebrow="Secrets stay in memory" title="Provider access" aside={credential ? <StatusBadge status="RUNNING"/> : <StatusBadge status="STOPPED"/>}/><div className="provider-grid"><section className="route-list">{routes.data?.data.map(route => <article className="panel route" key={route.id}><div><strong>{route.provider}</strong><StatusBadge status={route.isConfigured ? 'COMPLETED' : 'STOPPED'}/></div><p>{route.transport}</p><dl><div><dt>Auth</dt><dd>{route.authMode}</dd></div><div><dt>Circuit</dt><dd>{route.circuitState}</dd></div></dl></article>)}</section><form className="panel credential" onSubmit={save}><div className="panel-head"><span>Ephemeral credential session</span><KeyRound size={17}/></div><p>Keys remain process-local, are never written to the audit store, and expire within eight hours.</p><label>Provider<select value={provider} onChange={e => setProvider(e.target.value)}>{providers.map(item => <option key={item}>{item}</option>)}</select></label><label>API key<input aria-label="API key" type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)}/></label>{error && <p role="alert" className="form-error">{error}</p>}{credential ? <div className="credential-active"><ShieldCheck/><div><strong>Credential session active</strong><span>{credential.providers.join(', ')}</span></div><button type="button" className="icon-button" onClick={remove} aria-label="Delete credential session"><Trash2/></button></div> : <button className="primary" disabled={!key.trim()}><KeyRound size={17}/>Create credential session</button>}</form></div></>
}

function AnalyticsPage() {
  const analytics = useResource(() => api.analytics(), []); const values = analytics.data ?? {} as Analytics
  const metrics = [['Sessions', values.sessionCount ?? 0], ['Actions', values.actionCount ?? 0], ['Latency samples', values.metricCount ?? 0], ['Average latency', `${Math.round(Number(values.averageDurationMs ?? 0))} ms`]]
  const max = Math.max(1, ...metrics.slice(0, 3).map(([, value]) => Number(value)))
  return <><Header eyebrow="System telemetry" title="Latency & token analytics" aside={<span className="header-state"><Timer size={16}/>Live store</span>}/><section className="metrics">{metrics.map(([label, value]) => <article className="panel metric" key={label}><span>{label}</span><strong>{value}</strong><div><i style={{ width: `${Math.max(4, Number(value) / max * 100)}%` }}/></div></article>)}</section><section className="panel telemetry"><div className="panel-head"><span>Operational totals</span><Activity size={17}/></div><pre>{JSON.stringify(values, null, 2)}</pre></section></>
}

export default function App() {
  const [credentialSessionId, setCredentialSessionId] = useState<string | null>(null)
  const [preferredRoute] = useState('')
  return <div className="shell"><aside className="sidebar"><div className="brand"><div>CU</div><span><strong>CONTROL</strong><small>Computer use operations</small></span></div><nav aria-label="Primary">{tabs.map(([to, label, Icon]) => <NavLink end={to === '/'} to={to} key={to}><Icon/><span>{label}</span></NavLink>)}</nav><footer><span className="pulse"/>v2.0 / local</footer></aside><main><Routes>{tabs.map(([to]) => <Route key={to} path={to} element={<RouteBoundary>{to === '/' ? <LivePage credentialSessionId={credentialSessionId} preferredRoute={preferredRoute}/> : to === '/audit' ? <AuditPage/> : to === '/workflows' ? <WorkflowPage/> : to === '/providers' ? <ProvidersPage onCredential={setCredentialSessionId}/> : <AnalyticsPage/>}</RouteBoundary>}/>) }<Route path="*" element={<Navigate to="/" replace/>}/></Routes></main></div>
}
