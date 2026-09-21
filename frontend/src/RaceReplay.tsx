import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, Database, GitBranch, LockKeyhole, Pause, Play, RotateCcw, ShieldCheck, TriangleAlert, X } from 'lucide-react';
import { scenarios, type Lane } from './replay';

type RecordedEvent = { sequence: number; workflow_id: string | null; kind: string; payload: Record<string, unknown>; created_at: number };
type Props = { events: RecordedEvent[]; onInspectRun: (id: string) => void; onOpenDemo: () => void };

export default function RaceReplay({events, onInspectRun, onOpenDemo}: Props) {
  const [scenarioId, setScenarioId] = useState('race');
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const scenario = scenarios.find(s => s.id === scenarioId)!;
  const frame = scenario.frames[step];
  const interventionStep = scenario.id === 'deadlock' ? 3 : 4;
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setStep(previous => {
      if (previous >= scenario.frames.length - 1) return previous;
      return previous + 1;
    }), 2400);
    return () => clearInterval(timer);
  }, [playing, scenario]);
  useEffect(() => { if (step === interventionStep || step === scenario.frames.length - 1) setPlaying(false); }, [step, scenario, interventionStep]);

  const conflict = [...events].reverse().find(e => e.kind === 'commit_rejected' && e.payload.code === 'version_conflict');
  const runEvents = conflict ? events.filter(e => e.workflow_id === conflict.workflow_id) : [];
  const retry = runEvents.find(e => e.sequence > (conflict?.sequence || 0) && e.kind === 'task_retry' && e.payload.task_id === conflict?.payload.operation_id);
  const recovery = runEvents.find(e => e.sequence > (conflict?.sequence || 0) && e.kind === 'commit_accepted' && e.payload.operation_id === conflict?.payload.operation_id);
  const completed = runEvents.some(e => e.kind === 'workflow_completed');
  const jump = (next: number) => { setPlaying(false); setStep(next); };

  return <div className="race-lab">
    <section className="replay-panel" aria-labelledby="replay-title">
      <div className="replay-intro"><div><span className="eyebrow">SAME AGENTS. SAME STARTING STATE.</span><h2 id="replay-title">The difference is the commit.</h2><p>Follow the collision on the left. See the coordinator intervene on the right.</p></div><span className="replay-disclosure">Illustrated replay · no model calls</span></div>
      <div className="scenario-tabs" aria-label="Failure scenario">{scenarios.map(s => <button key={s.id} aria-pressed={s.id === scenarioId} onClick={() => {setPlaying(false); setStep(0); setScenarioId(s.id);}}>{s.label}</button>)}</div>
      <p className="replay-problem"><TriangleAlert size={17}/>{scenario.problem}</p>
      <div className="replay-lanes"><ReplayLane lane={frame.unsafe} protectedLane={false}/><ReplayLane lane={frame.safe} protectedLane/></div>
      <div className="replay-narration" aria-live="polite" aria-atomic="true"><span className="step-number">{step + 1}</span><div><h3>{frame.title}</h3><p>{frame.explanation}</p></div></div>
      <div className="replay-controls"><div className="playback-actions"><button className="button dark" onClick={() => { if (step === scenario.frames.length - 1) setStep(0); setPlaying(!playing); }}>{playing ? <Pause size={16}/> : <Play size={16}/>} {playing ? 'Pause' : step === scenario.frames.length - 1 ? 'Replay' : step === interventionStep ? 'Continue replay' : 'Play replay'}</button><button className="icon-button" aria-label="Restart replay" onClick={() => jump(0)}><RotateCcw size={18}/></button></div><div className="step-navigation"><button className="icon-button" aria-label="Previous step" disabled={step === 0} onClick={() => jump(step - 1)}><ArrowLeft size={18}/></button><span>Step {step + 1} of {scenario.frames.length}</span><button className="icon-button" aria-label="Next step" disabled={step === scenario.frames.length - 1} onClick={() => jump(step + 1)}><ArrowRight size={18}/></button></div></div>
      <label className="replay-scrubber"><span className="sr-only">Replay step</span><input type="range" min={0} max={scenario.frames.length - 1} value={step} onChange={e => jump(Number(e.target.value))} aria-valuetext={`${step + 1}: ${frame.title}`}/></label>
      <div className="replay-guarantee"><ShieldCheck size={20}/><div><strong>{scenario.guarantee}</strong><p>{scenario.caveat}</p></div></div>
    </section>
    <section className="replay-evidence" aria-labelledby="evidence-title"><div className="section-heading"><h2 id="evidence-title">Now, the evidence.</h2><span className="badge completed">Recorded coordinator events</span></div><p>The replay above explains the mechanism. These records come from your actual runs, independent of which replay is selected.</p>
      {conflict ? <><div className="evidence-chain"><Evidence icon={<ShieldCheck size={21}/>} title="Stale write rejected" event={conflict} detail={String(conflict.payload.message || 'version_conflict')}/><Evidence icon={<RotateCcw size={21}/>} title="Fresh attempt requested" event={retry} detail={retry ? String(retry.payload.task_id) : 'No later retry in the loaded events'}/><Evidence icon={<Check size={21}/>} title="Same task committed" event={recovery} detail={recovery ? JSON.stringify(recovery.payload.versions || {}) : 'No later accepted commit in the loaded events'}/></div><div className="evidence-footer"><span className="mono">{conflict.workflow_id} · {completed ? 'completion recorded' : 'completion not in loaded events'}</span><button className="text-button" onClick={() => onInspectRun(conflict.workflow_id!)}>Inspect this run <ArrowRight size={15}/></button></div></> : <div className="empty"><GitBranch size={28}/><strong>No recorded version conflict yet</strong><p>Run a schema-race scenario. Once a conflict is recorded, its rejection and recovery appear here.</p><button className="button dark" onClick={onOpenDemo}>Open the playground <ArrowRight size={16}/></button></div>}
      <p className="evidence-footnote">Uses the events currently loaded in this session. A missing event is not proof that an action did not occur. The unsafe lane is an illustration, never an unprotected write to your database.</p>
    </section>
  </div>;
}

function ReplayLane({lane, protectedLane}: {lane: Lane; protectedLane: boolean}) {
  return <article className={`replay-lane ${protectedLane ? 'coordinated' : 'uncoordinated'}`}><header><span className="lane-icon">{protectedLane ? <ShieldCheck size={21}/> : <TriangleAlert size={21}/>}</span><div><h3>{protectedLane ? 'With AgentLatch' : 'Without coordination'}</h3><p>{protectedLane ? 'Propose → validate → commit' : 'Independent, unchecked writes'}</p></div></header><div className="replay-agents">{lane.agents.map((text, i) => <div className="replay-agent" key={i}><span className="agent-letter">{i === 0 ? 'A' : 'B'}</span><div><strong>Agent {i === 0 ? 'A' : 'B'}</strong><p>{text}</p></div></div>)}</div><div className={`commit-gate ${lane.tone}`}><span>{protectedLane ? <LockKeyhole size={16}/> : <GitBranch size={16}/>}</span>{lane.gate}</div><div className="replay-state"><div><Database size={16}/><span>Shared state</span></div><pre>{lane.value}</pre><small>{lane.stamp}</small></div><div className={`lane-outcome ${lane.tone}`}>{lane.tone === 'warning' ? <X size={17}/> : lane.tone === 'success' ? <Check size={17}/> : <span className="outcome-dot"/>}{lane.result}</div></article>;
}

function Evidence({icon, title, event, detail}: {icon: React.ReactNode; title: string; event?: RecordedEvent; detail: string}) {
  return <div className={`evidence-step ${event ? 'recorded' : ''}`}><div className="evidence-step-title">{icon}<span>{event ? `Event #${event.sequence}` : 'Not observed'}</span></div><h3>{title}</h3><p>{detail}</p>{event && <time>{new Date(event.created_at * 1000).toLocaleTimeString()}</time>}</div>;
}
