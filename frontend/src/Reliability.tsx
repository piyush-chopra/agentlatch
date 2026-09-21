import { useEffect, useState } from 'react';
import { ArrowRight, Database, Inbox, Play, ShieldCheck } from 'lucide-react';

type Claim = { workflow_id: string; owner: string | null; generation: number; expires_at: number; mode: string; status: string };
type Delivery = { id: string; workflow_id: string; kind: string; destination: string; status: string; attempts: number; max_attempts: number; token: number; payload: unknown; context: unknown };
type Health = { backend: string; claims: Claim[]; scheduler_errors: number };
type Props = { api: (path: string, options?: RequestInit) => Promise<any> };
export default function Reliability({api}: Props) {
  const [health, setHealth] = useState<Health | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState('');
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const [next, messages] = await Promise.all([api('/api/executions'), api('/api/deliveries')]);
        if (!stopped) {setHealth(next); setDeliveries(messages); setError('');}
      } catch(e) {if (!stopped) setError((e as Error).message);}
      if (!stopped) timer = setTimeout(poll, 1500);
    };
    void poll();
    return () => {stopped = true; clearTimeout(timer);};
  }, [api]);
  const demo = async () => {
    setBusy(true);
    try { const result = await api('/api/demos/handoff', {method: 'POST'}); setRun(result.id); }
    catch(e) {setError((e as Error).message);}
    finally {setBusy(false);}
  };
  const pending = deliveries.filter(d => ['pending','inflight'].includes(d.status)).length;
  return <div className="reliability-view">
    {error && <p className="error" role="alert">{error}</p>}
    <section className="reliability-intro panel"><div><span className="eyebrow">DURABLE COORDINATION</span><h2>Work can resume. State stays protected.</h2><p>Schedulers claim runs with expiring ownership. Messages and effects are queued in the same transaction as state changes.</p><button className="button dark" disabled={busy} onClick={() => void demo()}><Play size={16}/>{busy ? 'Starting…' : 'Run a handoff demo'}<ArrowRight size={16}/></button><small>Simulated agents · real coordinator · effect delivery requires a configured dispatcher</small>{run && <p className="mono" role="status">Started {run}</p>}</div><div className="reliability-facts"><p><Database size={20}/><strong>{health?.backend || 'Connecting…'}</strong><span>State storage</span></p><p><ShieldCheck size={20}/><strong>{health?.claims.filter(c => c.status === 'running').length ?? '—'}</strong><span>Unfinished durable runs</span></p><p><Inbox size={20}/><strong>{pending}</strong><span>Pending in loaded deliveries</span></p></div></section>
    <div className="section-heading"><h2>Execution ownership</h2><span className="muted">Latest 100 runs</span></div>
    <section className="panel table-wrap"><table><thead><tr><th>Workflow</th><th>Status</th><th>Generation</th><th>Ownership</th><th>Runtime</th></tr></thead><tbody>{health?.claims.map(c => <tr key={c.workflow_id}><td className="mono">{c.workflow_id}</td><td>{c.status}</td><td>{c.generation}</td><td>{c.status !== 'running' ? 'Finished' : c.owner && c.expires_at * 1000 > Date.now() ? 'Claim held' : 'Awaiting scheduler'}</td><td>{c.mode}</td></tr>)}</tbody></table>{!health?.claims.length && <p className="empty">New runs appear here with durable scheduler ownership.</p>}</section>
    <div className="section-heading"><h2>Messages &amp; effects</h2><span className="muted">Latest 100 deliveries</span></div>
    <p className="muted">Delivery is at least once. Receivers must deduplicate the stable delivery ID. Unconfigured effects stay queued; exhausted retries become dead letters.</p>
    <div className="delivery-grid">{deliveries.map(d => <article className="panel delivery-card" key={d.id}><header><span className={`badge ${d.status === 'delivered' ? 'completed' : d.status === 'dead' ? 'failed' : 'running'}`}>{d.status}</span><span>{d.kind}</span></header><h3>{d.destination}</h3><p className="mono">{d.workflow_id}</p><p>Attempts {d.attempts} / {d.max_attempts} · Claim generation {d.token}</p><details><summary>Inspect envelope</summary><p className="mono">{d.id}</p><pre>{JSON.stringify({payload:d.payload, context:d.context},null,2)}</pre></details></article>)}</div>
    {!deliveries.length && <div className="empty"><Inbox size={30}/><strong>No messages yet</strong><p>Run a handoff demo to see a consumed message and a queued external effect.</p></div>}
  </div>;
}
