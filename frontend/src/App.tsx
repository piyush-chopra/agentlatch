import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, ArrowDown, ArrowRight, Box, Check, ChevronRight, Circle, Code2, Database, FileJson, GitBranch, Hexagon, Layers3, Loader2, LockKeyhole, Play, Radio, RefreshCw, Settings2, ShieldCheck, Square, Terminal, Workflow, X } from 'lucide-react';

type Resource = { key: string; value: Record<string, unknown>; json_schema: Record<string, unknown>; version: number; schema_version: number };
type Run = { id: string; status: string; steps: number; max_steps: number; created_at: number; deadline: number };
type Event = { sequence: number; workflow_id: string | null; kind: string; payload: Record<string, unknown>; created_at: number };
type State = { resources: Resource[]; workflows: Run[]; leases: {resource: string; owner: string; token: number; expires_at: number}[]; totals: Record<string, number> };
const initial: State = {resources: [], workflows: [], leases: [], totals: {}};
const example = JSON.stringify({ name: 'Inventory update', resources: [{ key: 'warehouse/items', value: { count: 10 }, json_schema: { type: 'object', properties: { count: {type: 'integer', minimum: 0} }, required: ['count'], additionalProperties: false } }], tasks: [{ id: 'inventory-agent', role: 'Inventory specialist', goal: 'Increase count by one.', reads: ['warehouse/items'], writes: ['warehouse/items'], action: 'increment' }], max_steps: 10, timeout_seconds: 300 }, null, 2);
const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
const pretty = (value: unknown) => JSON.stringify(value, null, 2);

export default function App() {
  const [state, setState] = useState<State>(initial);
  const [events, setEvents] = useState<Event[]>([]);
  const [view, setView] = useState('Overview');
  const [scenario, setScenario] = useState('schema');
  const [mode, setMode] = useState('scripted');
  const [online, setOnline] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [resource, setResource] = useState<Resource | null>(null);
  const [modal, setModal] = useState<'workflow' | 'connection' | null>(null);
  const [workflow, setWorkflow] = useState(example);
  const [token, setToken] = useState('');
  const cursor = useRef(0);
  const api = useCallback(async (path: string, options: RequestInit = {}) => {
    const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {}), ...options.headers}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || (data.detail ? JSON.stringify(data.detail) : `Request failed (${response.status})`));
    return data;
  }, [token]);

  const refresh = useCallback(async () => {
    try {
      const [next, incoming] = await Promise.all([api('/api/state'), api(`/api/events?after=${cursor.current}&limit=1000`)]);
      setState(next); setOnline(true);
      if (incoming.length) {
        cursor.current = incoming[incoming.length - 1].sequence;
        setEvents(prev => [...new Map([...prev, ...incoming].map((e: Event) => [e.sequence, e])).values()].slice(-3000));
      }
    } catch (e) { setOnline(false); setError((e as Error).message); }
  }, [api]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { await refresh(); if (!stopped) timer = setTimeout(poll, 1200); };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [refresh]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') {setModal(null); setResource(null);} };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, []);

  const run = async (custom = false) => {
    setBusy(true); setError('');
    try {
      const result = await api(custom ? '/api/runs' : '/api/demos', {method: 'POST', body: JSON.stringify(custom ? {spec: JSON.parse(workflow), mode} : {scenario, mode})});
      setSelected(result.id); setModal(null); setView('Overview'); await refresh();
    } catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  };
  const cancel = async (id: string) => {
    try { await api(`/api/runs/${encodeURIComponent(id)}/cancel`, {method: 'POST'}); await refresh(); }
    catch (e) {setError((e as Error).message);}
  };
  const active = state.workflows.filter(r => r.status === 'running').length;
  const commits = state.totals.commit_accepted || 0;
  const rejected = state.totals.commit_rejected || 0;
  const visibleEvents = events.filter(e => !selected || e.workflow_id === selected).slice(-80).reverse();
  const activeRun = state.workflows.find(r => r.id === selected);
  const navigation = [{name: 'Overview', icon: Layers3}, {name: 'Workflows', icon: Workflow}, {name: 'Shared state', icon: Database}, {name: 'Event stream', icon: Activity}];

  const eventList = <div className="event-list">{visibleEvents.length ? visibleEvents.map(e => <div className="event" key={e.sequence}><span className={`event-dot ${e.kind.includes('reject') || e.kind.includes('failed') ? 'warn' : e.kind.includes('accepted') || e.kind.includes('completed') ? 'success' : ''}`} /><div><div className="event-name">{e.kind.replaceAll('_', ' ')} <span>#{e.sequence}</span></div><div className="event-detail">{String(e.payload.message || e.payload.rationale || e.payload.operation_id || e.payload.task_id || e.payload.key || e.workflow_id || 'Coordinator')} {e.payload.code ? `· ${e.payload.code}` : ''}</div></div><time>{time(e.created_at)}</time></div>) : <div className="empty"><Radio size={26}/><strong>Waiting for the first signal</strong><p>Run a scenario to see every coordination decision here.</p></div>}</div>;
  const runTable = <div className="table-wrap"><table><thead><tr><th>Workflow run</th><th>Status</th><th>Attempt budget</th><th>Started</th><th /></tr></thead><tbody>{state.workflows.map(r => <tr key={r.id} className={selected === r.id ? 'selected-row' : ''}><td><button className="text-button mono" onClick={() => {setSelected(r.id); setView('Overview');}}>{r.id}</button></td><td><span className={`badge ${r.status}`}><Circle size={7} fill="currentColor"/>{r.status}</span></td><td><span className="mono">{r.steps} / {r.max_steps}</span><div className="budget"><i style={{width: `${Math.min(100, r.steps / r.max_steps * 100)}%`}}/></div></td><td className="muted mono">{time(r.created_at)}</td><td>{r.status === 'running' ? <button className="icon-button" aria-label={`Cancel ${r.id}`} onClick={() => void cancel(r.id)}><Square size={14}/></button> : <ChevronRight size={15} className="muted"/>}</td></tr>)}</tbody></table>{!state.workflows.length && <div className="empty small"><GitBranch size={25}/><strong>No runs yet</strong><p>Your first workflow starts with a scenario.</p></div>}</div>;
  const resources = <div className="resource-grid">{state.resources.map(r => <button className="resource-card" key={r.key} onClick={() => setResource(r)}><div><Database size={18}/><span className="version">v{r.version}</span></div><strong>{r.key}</strong><pre>{pretty(r.value)}</pre><footer>Schema v{r.schema_version}<span>Inspect <ArrowRight size={13}/></span></footer></button>)}{!state.resources.length && <div className="empty"><Database size={26}/><strong>No shared resources yet</strong><p>A demo or custom workflow will initialize versioned state.</p></div>}</div>;

  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="/"><span className="brand-mark"><Hexagon size={24}/><LockKeyhole size={10}/></span>AgentLatch<span className="brand-dot">.</span></a><div className="workspace"><span className="workspace-avatar">A</span><div>Local workspace<small>Development environment</small></div><span className="live-dot"/></div><div className="nav-label">WORKSPACE</div><nav>{navigation.map(({name, icon: Icon}) => <button key={name} className={view === name ? 'nav-item active' : 'nav-item'} onClick={() => setView(name)}><Icon size={18}/>{name}{name === 'Workflows' && <span className="nav-count">{state.workflows.length}</span>}</button>)}</nav><div className="sidebar-bottom"><div className="engine-note"><ShieldCheck size={20}/><strong>Coordination, enforced by code.</strong><p>Agents propose.<br/>The coordinator commits.</p><span>LOCAL MVP <span>v0.1.0</span></span></div><a className="nav-item" href="/docs" target="_blank" rel="noreferrer"><Code2 size={18}/>API documentation<ArrowRight size={14}/></a><button className="nav-item" onClick={() => setModal('connection')}><Settings2 size={18}/>Connection settings</button><div className="local-status"><span className={online ? 'live-dot' : 'live-dot offline'}/>{online ? 'Coordinator connected' : 'Coordinator disconnected'}</div></div></aside>
    <div className="main-shell"><header className="topbar"><div>Workspace <ChevronRight size={14}/><strong>{view}</strong></div><div><span className="environment"><span className="live-dot"/>LOCAL</span><a href="/docs" target="_blank" rel="noreferrer">API reference <ArrowRight size={13}/></a></div></header><main>
      <div className="page-heading"><div><div className="eyebrow">AGENT COORDINATION CONSOLE</div><h1>{view === 'Overview' ? 'Orchestrate with confidence.' : view}</h1><p>{view === 'Overview' ? 'Independent agents. Shared state. One consistent outcome.' : view === 'Workflows' ? 'Every execution, from the first proposal to the final commit.' : view === 'Shared state' ? 'Authoritative data and schema versions for every agent.' : 'A durable record of the decisions behind your state.'}</p></div><button className="button dark" onClick={() => setModal('workflow')}><span>+</span> New workflow</button></div>
      {error && <div className="error" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X size={16}/></button></div>}
      {view === 'Overview' && <>
        <div className="stats"><Stat icon={<Workflow size={18}/>} label="Workflow runs" value={state.workflows.length} detail={`${active} currently active`}/><Stat icon={<Check size={18}/>} label="Committed operations" value={commits} detail="Atomic state transitions"/><Stat icon={<ShieldCheck size={18}/>} label="Rejected commits" value={rejected} detail="Invalid writes kept out"/><Stat icon={<Database size={18}/>} label="Versioned resources" value={state.resources.length} detail={`${state.leases.length} active leases`}/></div>
        <section className="playground"><div className="playground-copy"><span className="pill"><span className="live-dot"/>CONCURRENCY LAB</span><h2>Put coordination<br/>to the test.</h2><p>Watch agents collide on shared state.<br/>See AgentLatch turn conflicts into safe retries.</p><div className="scenario-controls"><label>Scenario<select value={scenario} onChange={e => setScenario(e.target.value)}><option value="schema">Schema migration race</option><option value="race">Concurrent inventory updates</option></select></label><label>Agent runtime<select value={mode} onChange={e => setMode(e.target.value)}><option value="scripted">Offline simulation</option><option value="crew">CrewAI · configured LLM</option></select></label></div><button className="button green" disabled={busy || !online} onClick={() => void run()}>{busy ? <Loader2 size={15} className="spin"/> : <Play size={14} fill="currentColor"/>}{busy ? 'Starting…' : 'Run scenario'}<ArrowRight size={15}/></button><small>{mode === 'scripted' ? 'No API key required · Real coordinator, simulated agents' : 'Uses the model and credentials configured on your server'}</small></div><div className="diagram" aria-label="Two agents submit versioned proposals to the coordinator, which atomically commits shared state"><div className="diagram-agents"><div className="agent-node"><div className="node-icon lilac"><GitBranch size={19}/></div><span>Agent A<small>{scenario === 'schema' ? 'Schema architect' : 'Inventory specialist'}</small></span><span className="agent-status"/></div><div className="agent-node"><div className="node-icon blue"><Box size={19}/></div><span>Agent B<small>Inventory specialist</small></span><span className="agent-status"/></div></div><div className="connector-pair"><span/><span/></div><div className="diagram-caption">VERSIONED PROPOSALS</div><div className="coordinator-node"><span className="node-icon mint"><ShieldCheck size={25}/></span><div>AgentLatch<small>Deterministic coordinator</small></div><LockKeyhole size={16}/></div><div className="single-connector"/><div className="diagram-tags"><span><Check size={11}/>Validate</span><span><Check size={11}/>Fence</span><span><Check size={11}/>Commit</span></div><ArrowDown className="diagram-arrow" size={17}/><div className="state-node"><Database size={18}/><span>Shared state</span><span className="badge completed">Consistent</span></div></div></section>
        <div className="section-heading"><h2>Workflow activity <span>{state.workflows.length}</span></h2><button className="text-button" onClick={() => setView('Workflows')}>View all runs <ArrowRight size={14}/></button></div><section className="panel">{runTable}</section>
        <div className="section-heading"><h2>Coordination timeline <span className="live-label"><span className="live-dot"/>LIVE</span></h2><div className="timeline-filter">{activeRun && <span className={`badge ${activeRun.status}`}>{activeRun.status}</span>}<select aria-label="Filter timeline by workflow" value={selected || ''} onChange={e => setSelected(e.target.value || null)}><option value="">All workflows</option>{state.workflows.map(r => <option key={r.id} value={r.id}>{r.id}</option>)}</select></div></div><section className="panel">{eventList}</section>
      </>}
      {view === 'Workflows' && <section className="panel">{runTable}</section>}
      {view === 'Shared state' && resources}
      {view === 'Event stream' && <><div className="section-heading"><span className="muted">Latest 80 events · up to 3,000 buffered in this session</span><button className="text-button" onClick={() => setSelected(null)}>Show all workflows <RefreshCw size={14}/></button></div><section className="panel">{eventList}</section></>}
      <footer className="page-footer"><span><Hexagon size={13}/>AgentLatch · Open-source agent infrastructure</span><span>Python + CrewAI + React</span></footer>
    </main></div>
    {(modal || resource) && <div className="modal-backdrop" onClick={() => {setModal(null); setResource(null);}}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="dialog-title" onClick={e => e.stopPropagation()}><button className="modal-close icon-button" aria-label="Close dialog" onClick={() => {setModal(null); setResource(null);}}><X size={20}/></button>{resource ? <><div className="eyebrow">VERSIONED RESOURCE</div><h2 id="dialog-title">{resource.key}</h2><div className="resource-badges"><span className="badge completed">Data v{resource.version}</span><span className="badge">Schema v{resource.schema_version}</span></div><h3>Current value</h3><pre>{pretty(resource.value)}</pre><h3>JSON Schema</h3><pre>{pretty(resource.json_schema)}</pre></> : modal === 'connection' ? <><Settings2 size={25}/><h2 id="dialog-title">Connection settings</h2><p>The console uses the API on this server. If bearer authentication is enabled, enter your token below. It stays in memory for this page session.</p><label className="form-label">API bearer token<input autoFocus type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="Optional for localhost" autoComplete="off"/></label><button className="button dark" onClick={() => {setError(''); setModal(null); void refresh();}}>Connect <ArrowRight size={15}/></button></> : <><FileJson size={25}/><h2 id="dialog-title">Create a workflow</h2><p>Define your agents, shared resources, and dependencies. Each agent proposes changes within its declared scope.</p><label className="form-label">Workflow JSON<textarea autoFocus spellCheck={false} value={workflow} onChange={e => setWorkflow(e.target.value)}/></label><div className="modal-actions"><label>Runtime<select value={mode} onChange={e => setMode(e.target.value)}><option value="scripted">Offline simulation</option><option value="crew">CrewAI · configured LLM</option></select></label><button className="button dark" disabled={busy} onClick={() => void run(true)}><Play size={14}/>{busy ? 'Starting…' : 'Start workflow'}</button></div><p className="muted"><Terminal size={13}/> Scripted actions: increment, migrate, copy, transfer, noop. CrewAI follows the goal.</p>{error && <div className="error" role="alert">{error}</div>}</>}</section></div>}
  </div>;
}

function Stat({icon, label, value, detail}: {icon: React.ReactNode; label: string; value: number; detail: string}) {
  return <div className="stat"><div>{label}{icon}</div><strong>{value.toString().padStart(2, '0')}</strong><small>{detail}</small></div>;
}
