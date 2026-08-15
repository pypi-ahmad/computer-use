import { Component, type ChangeEvent, type ErrorInfo, type FormEvent, type ReactNode, useEffect, useState } from 'react'
import { Activity, BookOpen, CircleDollarSign, CircleStop, History, KeyRound, MonitorPlay, Play, Plus, Power, Radio, ShieldCheck, Timer, Trash2 } from 'lucide-react'
import { Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api, desktopViewerSrc, getAppToken, setAppToken, waitForNovnc } from './api'
import type { Action, Analytics, EventRecord, Session, Workflow } from './types'
import { estimateSessionCost, formatUsd } from './pricing'
import { useLiveStream } from './useLiveStream'

const tabs = [
  ['/', 'Live session', MonitorPlay], ['/audit', 'Audit trail', History], ['/cost', 'Session cost', CircleDollarSign],
  ['/workflows', 'Workflow library', BookOpen], ['/providers', 'Providers', KeyRound], ['/analytics', 'Analytics', Activity],
] as const

class RouteBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('Route failed', { message: error.message, componentStack: info.componentStack }) }
  render() { return this.state.failed ? <section className="empty"><h2>View unavailable</h2><p>Reload this view. The active agent session was not changed.</p><button onClick={() => location.reload()}>Reload view</button></section> : this.props.children }
}

function useResource<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null); const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    void load().then(value => { if (live) setData(value) }).catch((e: unknown) => { if (live) setError(e instanceof Error ? e.message : String(e)) })
    return () => { live = false }
    // load is a mount-time API fetch at every call site
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return { data, error, refresh: () => load().then(setData).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e))) }
}

function StatusBadge({ status }: { status: string }) { return <span className={`status status-${status.toLowerCase()}`}><span aria-hidden="true" />{status}</span> }
function Header({ eyebrow, title, aside }: { eyebrow: string; title: string; aside?: ReactNode }) { return <header className="page-header"><div><p>{eyebrow}</p><h1>{title}</h1></div>{aside}</header> }

function LivePage({ credentialSessionId, preferredRoute, initialTask, session, onSession }: { credentialSessionId: string | null; preferredRoute: string; initialTask: string; session: Session | null; onSession: (session: Session | null) => void }) {
  const models = useResource(() => api.models()); const routes = useResource(() => api.routes())
  const [task, setTask] = useState(initialTask); const [modelId, setModelId] = useState(''); const [routeId, setRouteId] = useState(''); const [fallback, setFallback] = useState(''); const [safetyPolicy, setSafetyPolicy] = useState('provider_default'); const [reasoning, setReasoning] = useState(''); const [search, setSearch] = useState(false); const [files, setFiles] = useState<Array<{ id: string; name: string }>>([]); const [handledNonce, setHandledNonce] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [viewerUrl, setViewerUrl] = useState('')
  useEffect(() => { let live = true; void api.desktop().then(desktop => waitForNovnc(desktopViewerSrc(desktop.viewerUrl))).then(src => { if (live) setViewerUrl(src) }).catch((e: unknown) => { if (live) setError(e instanceof Error ? e.message : String(e)) }); return () => { live = false } }, [])
  const model = models.data?.data.find(item => item.logicalId === (modelId || models.data?.data[0]?.logicalId)) ?? models.data?.data[0]
  const selectedRouteId = model?.routes.some(route => route.id === routeId) ? routeId : (model?.routes.find(route => route.id === preferredRoute)?.id ?? model?.routes[0]?.id ?? '')
  const stream = useLiveStream(session?.id ?? null)
  async function start(event: FormEvent) { event.preventDefault(); if (!model || !selectedRouteId || !task.trim()) return; const [fallbackModel, fallbackRoute] = fallback.split('@'); setBusy(true); setError(''); try { onSession(await api.createSession({ task: task.trim(), model: model.logicalId, primaryRoute: selectedRouteId, fallbackRoutes: fallbackModel && fallbackRoute ? [{ model: fallbackModel, route: fallbackRoute }] : [], ...(credentialSessionId ? { credentialSessionId } : {}), maxSteps: 50, ...(reasoning ? { reasoningEffort: reasoning } : {}), safetyPolicy, useBuiltinSearch: search, attachedFiles: files.map(file => file.id), retainAuditFrames: true })) } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  async function attach(event: ChangeEvent<HTMLInputElement>) { const selected = Array.from(event.target.files ?? []); try { const uploaded = await Promise.all(selected.map(api.uploadFile)); setFiles(previous => [...previous, ...uploaded.map(file => ({ id: file.file_id, name: file.filename }))]) } catch (e) { setError(e instanceof Error ? e.message : String(e)) } event.target.value = '' }
  async function stop() { if (!session) return; onSession(await api.stopSession(session.id)) }
  const stages = ['capture', 'encode', 'infer', 'validate', 'act'] as const
  const latestStage = [...stream.events].reverse().find(item => item.event === 'PIPELINE_STAGE')
  const safety = [...stream.events].reverse().find(item => item.event === 'SAFETY_CONFIRMATION')
  async function decide(confirm: boolean) { if (!session || safety?.event !== 'SAFETY_CONFIRMATION') return; await api.safetyDecision(session.id, safety.nonce, confirm); setHandledNonce(safety.nonce) }
  const terminal = [...stream.events].reverse().find(item => item.event === 'SESSION_TERMINAL')
  return <><Header eyebrow="Execution deck" title="Live agent session" aside={<div className="header-state"><Radio size={16}/><span>{stream.connected ? 'Stream linked' : 'Stream idle'}</span></div>} />
    <div className="execution-rail" aria-label="Execution pipeline">{stages.map((stage, index) => <div className={latestStage?.event === 'PIPELINE_STAGE' && latestStage.stage === stage ? 'active' : ''} key={stage}><span>{String(index + 1).padStart(2, '0')}</span><strong>{stage}</strong></div>)}</div>
    {(stream.error || terminal?.event === 'SESSION_TERMINAL' && terminal.message) && <p className="form-error" role="alert">{stream.error || terminal?.message}</p>}
    {safety?.event === 'SAFETY_CONFIRMATION' && safety.nonce !== handledNonce && <section className="safety" role="alertdialog" aria-label="Safety confirmation required"><ShieldCheck/><div><strong>Approval required</strong><p>{safety.explanation}</p><button type="button" onClick={() => { void decide(false) }}>Deny</button><button type="button" className="primary" onClick={() => { void decide(true) }}>Approve</button></div></section>}
    <div className="live-grid"><section className="viewport panel"><div className="panel-head"><span>Viewport / 1440 × 900</span>{session && <StatusBadge status={session.status}/>}</div><div className="screen">{viewerUrl ? <iframe title="Sandbox desktop" src={viewerUrl} allow="clipboard-read; clipboard-write"/> : <div><MonitorPlay size={42}/><strong>Connecting to sandbox</strong><span>The interactive desktop appears here when noVNC is ready.</span></div>}</div></section>
    <form className="control panel" onSubmit={event => { void start(event) }}><div className="panel-head"><span>Mission control</span><ShieldCheck size={17}/></div><label>Task<textarea value={task} onChange={e => setTask(e.target.value)} placeholder="Describe the desktop outcome…" rows={6}/></label><label>Computer Use model<select value={model?.logicalId ?? ''} onChange={e => { setModelId(e.target.value); setReasoning(''); setFiles([]) }}>{models.data?.data.map(item => <option value={item.logicalId} key={item.logicalId}>{item.displayName}</option>)}</select></label><label>Primary route<select value={selectedRouteId} onChange={e => setRouteId(e.target.value)}>{model?.routes.map(route => <option value={route.id} key={route.id}>{route.provider} · {route.transport}</option>)}</select></label><label>Fallback model<select value={fallback} onChange={e => setFallback(e.target.value)}><option value="">No fallback</option>{models.data?.data.filter(item => item.logicalId !== model?.logicalId).flatMap(item => item.routes.map(route => <option value={`${item.logicalId}@${route.id}`} key={`${item.logicalId}@${route.id}`}>{item.displayName} · {route.provider}</option>))}</select></label>{Boolean(model?.reasoningEfforts?.length) && <label>Reasoning<select value={reasoning} onChange={e => setReasoning(e.target.value)}><option value="">Model default</option>{model?.reasoningEfforts.map(effort => <option key={effort}>{effort}</option>)}</select></label>}<label>Safety policy<select value={safetyPolicy} onChange={e => setSafetyPolicy(e.target.value)}><option value="provider_default">Provider default</option><option value="confirm_mutating">Confirm mutating actions</option><option value="read_only">Read only</option></select></label><label className="toggle-field">Provider web search planning<button type="button" className={search ? 'toggle on' : 'toggle'} role="switch" aria-checked={search} aria-label="Provider web search planning" onClick={() => { setSearch(on => !on) }}><span className="toggle-knob" /><span>{search ? 'On' : 'Off'}</span></button></label>{model?.family !== 'GEMINI' && <label>Reference files<input type="file" multiple accept=".md,.txt,.pdf,.docx" onChange={event => { void attach(event) }}/><span>{files.map(file => file.name).join(', ')}</span></label>}{(error || models.error || routes.error) && <p className="form-error" role="alert">{error || models.error || routes.error}</p>}<div className="actions">{session?.status === 'RUNNING' ? <button type="button" className="danger" onClick={() => { void stop() }}><CircleStop size={17}/>Stop run</button> : <button className="primary" disabled={busy || !task.trim() || !selectedRouteId}><Play size={17}/>{busy ? 'Starting…' : 'Start run'}</button>}</div></form></div></>
}

function AuditPage() {
  const sessions = useResource(() => api.sessions()); const [selected, setSelected] = useState(''); const [actions, setActions] = useState<Action[]>([]); const [events, setEvents] = useState<EventRecord[]>([])
  const selectedId = selected || sessions.data?.data[0]?.id || ''
  useEffect(() => {
    if (!selectedId) return
    let live = true
    void Promise.all([api.actions(selectedId), api.events(selectedId)]).then(([a, e]) => { if (live) { setActions(a.data); setEvents(e.data) } }).catch(() => { if (live) { setActions([]); setEvents([]) } })
    return () => { live = false }
  }, [selectedId])
  async function download() { if (!selectedId) return; const blob = await api.exportSession(selectedId, true); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `session-${selectedId}.zip`; anchor.click(); URL.revokeObjectURL(url) }
  return <><Header eyebrow="Flight recorder" title="Session audit trail" aside={<div><select aria-label="Session" value={selectedId} onChange={e => setSelected(e.target.value)}>{sessions.data?.data.map(item => <option value={item.id} key={item.id}>{item.task.slice(0, 42)}</option>)}</select><button type="button" onClick={() => { void download() }} disabled={!selectedId}>Export ZIP</button></div>}/><section className="panel table-panel"><div className="panel-head"><span>Confirmed action journal</span><span>{actions.length} actions</span></div><div className="timeline">{actions.length ? actions.map((action, index) => <article key={action.id ?? index}><span>{String(action.sequence ?? index + 1).padStart(3, '0')}</span><div><strong>{action.actionType ?? action.action ?? action.type ?? 'ACTION'}</strong><code>{JSON.stringify(action.payload ?? {})}</code></div></article>) : <div className="empty"><History/><h2>No recorded actions</h2><p>Confirmed actions appear here in execution order.</p></div>}</div></section><section className="event-strip">{events.slice(0, 8).map((event, i) => <div key={event.id ?? i}><span>{event.createdAt?.slice(11, 19) ?? '—'}</span>{event.eventType ?? event.type}</div>)}</section></>
}

function WorkflowPage({ onUse }: { onUse: (task: string) => void }) {
  const workflows = useResource(() => api.workflows()); const [name, setName] = useState(''); const [steps, setSteps] = useState(''); const [error, setError] = useState('')
  const navigate = useNavigate()
  async function create(event: FormEvent) { event.preventDefault(); const clean = steps.split('\n').map(s => s.trim()).filter(Boolean); if (!name.trim() || !clean.length) return; try { await api.createWorkflow({ name: name.trim(), slug: name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''), variablesSchema: { type: 'object' }, steps: clean }); setName(''); setSteps(''); await workflows.refresh() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } }
  async function use(workflow: Workflow) { try { const compiled = await api.compileWorkflow(workflow.id); onUse(compiled.instructions.join('\n')); void navigate('/') } catch (e) { setError(e instanceof Error ? e.message : String(e)) } }
  return <><Header eyebrow="Reusable procedures" title="Prompt & skill library" aside={<span className="count">{workflows.data?.data.length ?? 0} workflows</span>}/><div className="library-grid"><section className="workflow-list">{workflows.data?.data.map(workflow => <article className="panel workflow" key={workflow.id}><div><BookOpen size={18}/><span>v{workflow.version}</span></div><h2>{workflow.name}</h2><ol>{workflow.steps.slice(0, 3).map(step => <li key={step}>{step}</li>)}</ol><button type="button" onClick={() => { void use(workflow) }}>Use in live session</button></article>)}{workflows.data?.data.length === 0 && <div className="empty"><BookOpen/><h2>No workflows yet</h2><p>Create a declarative procedure for repeatable runs.</p></div>}</section><form className="panel editor" onSubmit={event => { void create(event) }}><div className="panel-head"><span>New workflow</span><Plus size={17}/></div><label>Name<input value={name} onChange={e => setName(e.target.value)} placeholder="Quarterly access review"/></label><label>Ordered instructions<textarea value={steps} onChange={e => setSteps(e.target.value)} rows={10} placeholder={'Open the admin portal\nExport active accounts\nSave the report'}/></label>{error && <p role="alert" className="form-error">{error}</p>}<button className="primary"><Plus size={17}/>Create workflow</button></form></div></>
}

function ProvidersPage({ onCredential }: { onCredential: (id: string | null) => void }) {
  const routes = useResource(() => api.routes()); const [provider, setProvider] = useState('OPENAI'); const [method, setMethod] = useState<'api_key' | 'oauth'>('api_key'); const [key, setKey] = useState(''); const [quotaProject, setQuotaProject] = useState(''); const [credential, setCredential] = useState<{ id: string; providers: string[]; expiresAt: number } | null>(null); const [error, setError] = useState('')
  useEffect(() => { if (!credential || credential.providers.length) return; const timer = window.setInterval(() => api.credentialSessionStatus(credential.id).then(updated => { setCredential(updated); if (updated.providers.length) window.clearInterval(timer) }).catch(() => undefined), 1000); return () => window.clearInterval(timer) }, [credential])
  async function save(event: FormEvent) { event.preventDefault(); try { if (provider === 'GOOGLE' && method === 'oauth') { const started = await api.startGoogleOAuth(quotaProject); const pending = { id: started.credentialSessionId, providers: [], expiresAt: started.expiresAt }; setCredential(pending); onCredential(pending.id); window.open(started.authorizationUrl, 'cua-google-oauth', 'popup,width=560,height=720') } else { const created = await api.credentialSession({ [provider]: key }); setCredential(created); onCredential(created.id); setKey('') } setError('') } catch (e) { setError(e instanceof Error ? e.message : String(e)) } }
  async function remove() { if (!credential) return; await api.deleteCredentialSession(credential.id); setCredential(null); onCredential(null) }
  const providers = Array.from(new Set(routes.data?.data.map(route => route.provider) ?? ['OPENAI']))
  return <><Header eyebrow="Secrets stay in memory" title="Provider access" aside={credential ? <StatusBadge status="RUNNING"/> : <StatusBadge status="STOPPED"/>}/><div className="provider-grid"><section className="route-list">{routes.data?.data.map(route => <article className="panel route" key={route.id}><div><strong>{route.provider}</strong><StatusBadge status={route.isConfigured ? 'COMPLETED' : 'STOPPED'}/></div><p>{route.transport}</p><dl><div><dt>Auth</dt><dd>{route.authMode}</dd></div><div><dt>Circuit</dt><dd>{route.circuitState}</dd></div></dl></article>)}</section><form className="panel credential" onSubmit={event => { void save(event) }}><div className="panel-head"><span>Ephemeral credential session</span><KeyRound size={17}/></div><p>Credentials remain process-local, are never written to the audit store, and expire within eight hours.</p><label>Provider<select value={provider} onChange={e => { setProvider(e.target.value); setMethod('api_key') }}>{providers.map(item => <option key={item}>{item}</option>)}</select></label>{provider === 'GOOGLE' && <label>Login method<select value={method} onChange={e => setMethod(e.target.value as 'api_key' | 'oauth')}><option value="api_key">API key</option><option value="oauth">Google OAuth</option></select></label>}{method === 'api_key' ? <label>API key<input aria-label="API key" type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)}/></label> : <label>Google Cloud project (optional)<input value={quotaProject} onChange={e => setQuotaProject(e.target.value)} placeholder="Quota project ID"/></label>}{error && <p role="alert" className="form-error">{error}</p>}{credential ? <div className="credential-active"><ShieldCheck/><div><strong>{credential.providers.length ? 'Credential session active' : 'Waiting for Google sign-in'}</strong><span>{credential.providers.join(', ') || 'Complete consent in the popup'}</span></div><button type="button" className="icon-button" onClick={() => { void remove() }} aria-label="Delete credential session"><Trash2/></button></div> : <button className="primary" disabled={method === 'api_key' && !key.trim()}><KeyRound size={17}/>{method === 'oauth' ? 'Sign in with Google' : 'Create credential session'}</button>}</form></div></>
}

function CostPage({ currentSession }: { currentSession: Session | null }) {
  const sessions = useResource(() => api.sessions())
  const [selected, setSelected] = useState(currentSession?.id ?? '')
  const [usage, setUsage] = useState<Analytics | null>(null)
  const listed = sessions.data?.data ?? []
  const rows = currentSession && !listed.some(item => item.id === currentSession.id) ? [currentSession, ...listed] : listed
  const selectedId = selected || currentSession?.id || rows[0]?.id || ''
  const session = rows.find(item => item.id === selectedId) ?? currentSession
  useEffect(() => {
    if (!selectedId) { setUsage(null); return }
    let live = true
    async function load() {
      try { const value = await api.analytics({ sessionId: selectedId }); if (live) setUsage(value) }
      catch { if (live) setUsage(null) }
    }
    void load()
    const timer = session?.status === 'RUNNING' ? window.setInterval(() => { void load() }, 2000) : 0
    return () => { live = false; if (timer) window.clearInterval(timer) }
  }, [selectedId, session?.status])
  const inputTokens = Number(usage?.inputTokens ?? 0)
  const outputTokens = Number(usage?.outputTokens ?? 0)
  const cost = estimateSessionCost(session?.model ?? '', inputTokens, outputTokens)
  const cards = [
    ['Input tokens', String(inputTokens)],
    ['Output tokens', String(outputTokens)],
    ['Estimated cost', cost.known ? formatUsd(cost.totalUsd) : 'Unknown model'],
  ]
  return <><Header eyebrow="Token billing estimate" title="Session cost" aside={<div><select aria-label="Session" value={selectedId} onChange={e => setSelected(e.target.value)}>{rows.map(item => <option value={item.id} key={item.id}>{item.task.slice(0, 42) || item.id}</option>)}</select>{session && <StatusBadge status={session.status}/>}</div>}/>
    <section className="metrics">{cards.map(([label, value]) => <article className="panel metric" key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
    <section className="panel table-panel"><div className="panel-head"><span>{cost.rate?.label ?? session?.model ?? 'No session'}</span><span>{session?.primaryRoute ?? ''}</span></div>
      {session ? <dl className="cost-breakdown"><div><dt>Input rate</dt><dd>{cost.rate ? `${formatUsd(cost.rate.inputPerMillion)} / 1M` : '—'}</dd></div><div><dt>Output rate</dt><dd>{cost.rate ? `${formatUsd(cost.rate.outputPerMillion)} / 1M` : '—'}</dd></div><div><dt>Input cost</dt><dd>{cost.known ? formatUsd(cost.inputUsd) : '—'}</dd></div><div><dt>Output cost</dt><dd>{cost.known ? formatUsd(cost.outputUsd) : '—'}</dd></div></dl> : <div className="empty"><CircleDollarSign/><h2>No session yet</h2><p>Start a run on Live session. Cost uses recorded EXECUTION token totals.</p></div>}
      {session && usage && usage.sampleCount === 0 && <p className="form-error" role="status">No token metrics yet. Totals appear after the session writes an EXECUTION metric.</p>}
      {cost.rate && <p className="cost-note">{cost.rate.details} Estimate = tokens / 1,000,000 × list rate. Not an invoice.</p>}
    </section></>
}

function AnalyticsPage() {
  const analytics = useResource(() => api.analytics()); const diagnostics = useResource(() => api.diagnostics()); const retention = useResource(() => api.retentionPreview()); const values = analytics.data ?? {}
  const metrics = [['Latency samples', values.sampleCount ?? 0], ['Input tokens', values.inputTokens ?? 0], ['Output tokens', values.outputTokens ?? 0], ['Total latency', `${Math.round(Number(values.totalDurationMs ?? 0))} ms`]]
  const max = Math.max(1, ...metrics.slice(0, 3).map(([, value]) => Number(value)))
  async function prune() { await api.pruneRetention(); await retention.refresh() }
  return <><Header eyebrow="System telemetry" title="Latency & token analytics" aside={<span className="header-state"><Timer size={16}/>Live store</span>}/><section className="metrics">{metrics.map(([label, value]) => <article className="panel metric" key={label}><span>{label}</span><strong>{value}</strong><div><i style={{ width: `${Math.max(4, Number(value) / max * 100)}%` }}/></div></article>)}</section><section className="panel telemetry"><div className="panel-head"><span>Diagnostics</span><Activity size={17}/></div><pre>{JSON.stringify({ analytics: values, diagnostics: diagnostics.data, retention: retention.data }, null, 2)}</pre><button type="button" onClick={() => { void prune() }}>Prune expired frames</button></section></>
}

export default function App() {
  const [credentialSessionId, setCredentialSessionId] = useState<string | null>(null)
  const [preferredRoute] = useState('gemini-direct')
  const [prefillTask, setPrefillTask] = useState('')
  const [liveSession, setLiveSession] = useState<Session | null>(null)
  const [appToken, updateAppToken] = useState(getAppToken())
  const [shutdownState, setShutdownState] = useState<'idle' | 'stopping' | 'stopped'>('idle')
  const [shutdownError, setShutdownError] = useState('')
  function saveToken(event: FormEvent) { event.preventDefault(); setAppToken(appToken); location.reload() }
  async function shutdownApp() { if (!window.confirm('Stop the app, active sessions, and background processes?')) return; setShutdownState('stopping'); setShutdownError(''); try { await api.shutdown(); setShutdownState('stopped') } catch (error) { setShutdownState('idle'); setShutdownError(error instanceof Error ? error.message : String(error)) } }
  const shutdownLabel = shutdownState === 'stopping' ? 'Stopping…' : shutdownState === 'stopped' ? 'App stopped' : 'Stop app'
  return <div className="shell"><aside className="sidebar"><div className="brand"><div>CU</div><span><strong>CONTROL</strong><small>Computer use operations</small></span></div><nav aria-label="Primary">{tabs.map(([to, label, Icon]) => <NavLink end={to === '/'} to={to} key={to}><Icon/><span>{label}</span></NavLink>)}</nav><div className="sidebar-shutdown"><button type="button" className="app-stop" aria-label="Stop app and background processes" disabled={shutdownState !== 'idle'} onClick={() => { void shutdownApp() }}><Power/><span>{shutdownLabel}</span></button>{shutdownError && <span className="shutdown-error" role="alert">{shutdownError}</span>}</div><footer><form onSubmit={saveToken}><input aria-label="Workbench token" type="password" autoComplete="off" placeholder="Workbench token" value={appToken} onChange={e => updateAppToken(e.target.value)}/><button type="submit">Apply</button></form><span><span className="pulse"/>v2.0 / local</span></footer></aside><main><Routes>{tabs.map(([to]) => <Route key={to} path={to} element={<RouteBoundary>{to === '/' ? <LivePage credentialSessionId={credentialSessionId} preferredRoute={preferredRoute} initialTask={prefillTask} session={liveSession} onSession={setLiveSession}/> : to === '/audit' ? <AuditPage/> : to === '/cost' ? <CostPage currentSession={liveSession}/> : to === '/workflows' ? <WorkflowPage onUse={setPrefillTask}/> : to === '/providers' ? <ProvidersPage onCredential={setCredentialSessionId}/> : <AnalyticsPage/>}</RouteBoundary>}/>) }<Route path="*" element={<Navigate to="/" replace/>}/></Routes></main></div>
}
