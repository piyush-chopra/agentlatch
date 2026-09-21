/** Illustrative schedules, never database mutations or claimed production traces. */
export type Lane = {
  agents: [string, string];
  value: string;
  stamp: string;
  gate: string;
  result: string;
  tone: 'neutral' | 'warning' | 'success';
};
export type Frame = { title: string; explanation: string; unsafe: Lane; safe: Lane };
export type Scenario = { id: string; label: string; problem: string; guarantee: string; caveat: string; frames: Frame[] };
const lane = (agents: [string, string], value: string, stamp: string, gate: string, result = 'In progress', tone: Lane['tone'] = 'neutral'): Lane => ({ agents, value, stamp, gate, result, tone });

function writeRace(schema: boolean): Scenario {
  const original = '{ "count": 10 }';
  const first = schema ? '{ "available": 10 }' : '{ "count": 11 }';
  const last = schema ? '{ "available": 11 }' : '{ "count": 12 }';
  const contract = schema ? 'Schema 2 · requires available' : 'Schema 1 · requires count';
  const finalOutcome = schema ? 'The new contract stays valid' : 'Both increments are preserved';
  const a = schema ? 'Rename count → available' : 'Increase count by 1';
  const stale = schema ? 'Restore old field count = 11' : 'Replace count with 11';
  return {
    id: schema ? 'schema' : 'race', label: schema ? 'Stale schema' : 'Lost update',
    problem: schema ? 'One agent changes a contract while another is still reasoning about the old one.' : 'Two agents read 10, each adds 1, but the final value becomes 11 instead of 12.',
    guarantee: 'Snapshot versions + an atomic, fenced commit',
    caveat: 'The unsafe lane illustrates writers bypassing version and schema validation. Actual model timing can change which agent commits first. Only declared dependencies and coordinator-managed writes are protected.',
    frames: [
      { title: 'One shared starting point', explanation: 'Both lanes begin with the same inventory. Agent A and Agent B are independent workers.',
        unsafe: lane(['Ready', 'Ready'], original, 'Original contract: count', 'Direct writes'),
        safe: lane(['Ready', 'Ready'], original, 'Data v1 · Schema 1', 'Coordinator ready') },
      { title: 'Both agents read the same state', explanation: 'Each agent sees count = 10. AgentLatch binds both proposals to the data and schema versions they actually read.',
        unsafe: lane(['Read count = 10', 'Read count = 10'], original, 'No enforced read stamps', 'No conflict check'),
        safe: lane(['Read count = 10 · v1/s1', 'Read count = 10 · v1/s1'], original, 'Data v1 · Schema 1', 'Original read stamps retained') },
      { title: 'Reasoning happens concurrently', explanation: 'Agents can think independently. AgentLatch holds no resource leases while models generate proposals.',
        unsafe: lane([a, 'Propose count = 11'], original, 'Both plans use old state', 'Direct writes'),
        safe: lane([a + ' · from v1/s1', 'Propose count = 11 · from v1/s1'], original, 'Data v1 · Schema 1', 'Plan outside the transaction') },
      { title: 'Agent A commits first', explanation: schema ? 'The migration changes the field and JSON Schema together. Agent B still has the original count-based snapshot.' : 'Agent A writes 11. Agent B still has a proposal calculated from 10.',
        unsafe: lane(['Write complete', 'Still using count = 10'], first, contract, 'Agent B is now stale'),
        safe: lane(['Committed · lease released', 'Still using v1/s1'], first, `Data v2 · ${contract}`, 'Atomic write + version increment') },
      { title: 'The collision happens here', explanation: schema ? 'Without checks, B replaces the value using the old field. AgentLatch rejects B because its read stamp no longer matches; shared state is unchanged.' : 'Without checks, B writes 11 over 11 and loses an increment. AgentLatch rejects the stale proposal before any mutation.',
        unsafe: lane(['Finished', stale], '{ "count": 11 }', contract, 'No precondition stops the write', schema ? 'Data no longer matches the schema' : 'One increment is lost', 'warning'),
        safe: lane(['Finished', 'Rejected · version_conflict'], first, `Data v2 · ${contract}`, 'Expected v1/s1 ≠ current state', 'Stale write prevented', 'success') },
      { title: 'Refresh the context, not just the request', explanation: 'The losing agent releases its lease, reserves another attempt, reads the new snapshot, and replans. Re-sending its old proposal would still be wrong.',
        unsafe: lane(['Finished', 'Believes the write succeeded'], '{ "count": 11 }', contract, 'No recovery protocol', 'Silent inconsistency remains', 'warning'),
        safe: lane(['Finished', schema ? 'Re-read available = 10 · propose 11' : 'Re-read count = 11 · propose 12'], first, `Data v2 · ${contract}`, 'Fresh context + bounded retry', 'New proposal prepared') },
      { title: 'One consistent outcome', explanation: `${finalOutcome}. The accepted state, receipt, and event are persisted in the same transaction.`,
        unsafe: lane(['Finished', 'Finished'], '{ "count": 11 }', contract, 'No validated commit boundary', schema ? 'Broken contract' : 'Expected 12, got 11', 'warning'),
        safe: lane(['Finished', 'Committed after replan'], last, `Data v3 · ${contract}`, 'Versions + schema + lease verified', finalOutcome, 'success') },
    ],
  };
}

const locks = '{ "stock": "free", "orders": "free" }';
const stuck = '{ "stock": "Agent A", "orders": "Agent B" }';
const deadlock: Scenario = {
  id: 'deadlock', label: 'Deadlock', problem: 'Each agent holds a different lock and waits for the resource held by the other.',
  guarantee: 'All-or-nothing lease acquisition + expiry + fencing',
  caveat: 'This models application-level resource locks, not a SQLite SQL-lock deadlock. The real protocol never grants a partial resource set; busy agents retry within their budgets. It does not promise fairness or prevent deadlocks in external systems.',
  frames: [
    {title: 'Two tasks need the same pair', explanation: 'Both workers need stock and orders to make a consistent update.', unsafe: lane(['Needs stock + orders', 'Needs orders + stock'], locks, 'Separate resource locks', 'Independent acquisition'), safe: lane(['Needs stock + orders', 'Needs orders + stock'], locks, 'One lease request per full read set', 'Atomic acquisition')},
    {title: 'A asks for resources', explanation: 'The unsafe worker takes only stock. AgentLatch checks the whole set in one transaction and grants both resources or neither.', unsafe: lane(['Holds stock', 'Ready for orders'], '{ "stock": "Agent A", "orders": "free" }', 'Partial ownership', 'One resource at a time'), safe: lane(['Holds stock + orders · token 1', 'Not holding resources'], '{ "stock": "Agent A", "orders": "Agent A" }', 'Fencing token 1', 'Entire set granted')},
    {title: 'B competes for the same set', explanation: 'The unsafe worker B takes orders. AgentLatch returns lease_busy to B without granting it any resource.', unsafe: lane(['Holds stock', 'Holds orders'], stuck, 'Opposite lock order', 'Partial ownership on both sides'), safe: lane(['Holds the full set', 'lease_busy · holds nothing'], '{ "stock": "Agent A", "orders": "Agent A" }', 'Fencing token 1', 'No partial grant')},
    {title: 'A circular wait forms', explanation: 'A waits for B and B waits for A. In the coordinated lane, A can finish because it already owns every required resource.', unsafe: lane(['Waits for orders → B', 'Waits for stock → A'], stuck, 'A → B → A', 'Hold-and-wait cycle', 'Neither agent can progress', 'warning'), safe: lane(['Commits and releases', 'Backs off within attempt budget'], locks, 'Token 1 released', 'No circular wait', 'A made progress', 'success')},
    {title: 'Ownership moves forward', explanation: 'B now acquires the full set with a newer token and validates its snapshot before writing. An expired older holder cannot use the newer lease.', unsafe: lane(['Still waiting for B', 'Still waiting for A'], stuck, 'No recovery rule in this illustration', 'Circular wait remains', 'Blocked', 'warning'), safe: lane(['Finished', 'Holds stock + orders · token 2'], '{ "stock": "Agent B", "orders": "Agent B" }', 'Fencing token 2', 'Validate versions before commit')},
    {title: 'Resources are released', explanation: 'B commits and releases. If a worker crashes instead, expiry permits reacquisition and fencing rejects its old token.', unsafe: lane(['Blocked', 'Blocked'], stuck, 'Partial locks remain', 'No progress', 'Deadlock', 'warning'), safe: lane(['Finished', 'Finished'], locks, 'All resources free', 'Expiry covers abandoned leases', 'Both tasks can complete', 'success')},
  ],
};

const loop: Scenario = {
  id: 'loop', label: 'No-progress loop', problem: 'Agents keep submitting the same state transition, spending time and model calls without making progress.',
  guarantee: 'Repeated-transition detection + durable budgets + deadlines',
  caveat: 'This illustration uses distinct operation IDs for identical no-op transitions and repeat_limit = 3. An exact duplicate operation would return its existing receipt instead. New, nonrepeating transitions are still bounded by attempt budgets and deadlines.',
  frames: [0, 1, 2, 3, 4, 5].map(i => ({
    title: i === 0 ? 'The value is already correct' : i < 4 ? `Identical transition ${i} is accepted` : i === 4 ? 'The repetition limit is reached' : 'Stop before autonomy becomes a loop',
    explanation: i === 0 ? 'Each new operation proposes count = 10 when count is already 10.' : i < 4 ? 'The coordinator counts repeated input/output transitions without including version counters. The configured limit here is three.' : i === 4 ? 'The fourth identical transition is rejected with loop_detected before changing state. It is not automatically retried.' : 'The built-in engine marks the task and workflow failed. Endless new transitions are additionally bounded by durable step budgets and deadlines.',
    unsafe: lane([i % 2 ? 'Proposes count = 10 again' : 'Waiting for next turn', i % 2 ? 'Waiting for next turn' : 'Proposes count = 10 again'], '{ "count": 10 }', `${i} redundant commits`, 'No stopping condition', i >= 4 ? 'Still spending calls, no progress' : 'No value change', i >= 4 ? 'warning' : 'neutral'),
    safe: lane([i >= 4 ? 'Stopped' : 'Proposes count = 10', i >= 4 ? 'Stopped' : 'Checks the same state'], '{ "count": 10 }', `Repeated transitions: ${Math.min(i, 3)} / 3`, i >= 4 ? 'loop_detected · no mutation' : 'Durable transition count', i >= 4 ? 'Bounded failure, explicit diagnosis' : 'Within the configured limit', i >= 4 ? 'success' : 'neutral'),
  })),
};
export const scenarios: Scenario[] = [writeRace(false), writeRace(true), deadlock, loop];
