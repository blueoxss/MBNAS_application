/**
 * State of the loaded run (snapshot + incremental events).
 *
 * Invariants:
 *  - events of another run and events with seq <= lastSeq are ignored; only seq == lastSeq + 1 is applied
 *    (a gap is resolved by RunSync with an authoritative snapshot that replaces this state);
 *  - evaluation rows are keyed by evaluation id; an EVALUATED row is immutable: later partial
 *    `candidate.updated` events may only change mutable status fields and never clear metrics;
 *  - snapshots replace the whole state (authoritative), so polling cannot duplicate points.
 */
import { rec, type ApiEvent, type Candidate, type GeneratedCandidate, type Generation, type Run, type Snapshot, type Worker } from '../api/types';
import type { ConnStatus } from '../api/sync';
import { generationKey } from '../lib/lines';

export const MAX_EVENTS = 2000;

export interface SyncNote {
  at: string;
  kind: 'resync' | 'status';
  detail: string;
}

export interface RunState {
  runId: string | null;
  run: Run | null;
  candidates: Record<string, Candidate>;
  /** evaluation ids in first-seen order */
  order: string[];
  generatedCandidates: GeneratedCandidate[];
  generations: Generation[];
  workers: Record<string, Worker>;
  events: ApiEvent[];
  lastSeq: number;
  conn: ConnStatus;
  connDetail: string;
  syncNotes: SyncNote[];
  loaded: boolean;
}

export const initialRunState: RunState = {
  runId: null,
  run: null,
  candidates: {},
  order: [],
  generatedCandidates: [],
  generations: [],
  workers: {},
  events: [],
  lastSeq: 0,
  conn: 'idle',
  connDetail: '',
  syncNotes: [],
  loaded: false,
};

export type RunAction =
  | { type: 'reset' }
  | { type: 'begin'; runId: string }
  | { type: 'snapshot'; snap: Snapshot; reason: string; at: string }
  | { type: 'event'; ev: ApiEvent }
  | { type: 'status'; status: ConnStatus; detail?: string; at: string }
  | { type: 'run'; run: Run };

/** Fields that may change on an EVALUATED row after it was written (mutable candidate status, not metrics). */
const MUTABLE_AFTER_EVALUATED = ['candidate_status', 'reason_code', 'attempt', 'job_id', 'progress', 'gate'] as const;

function isNil(v: unknown): boolean {
  return v === null || v === undefined;
}

/** Merge a partial update into an existing row; null/undefined never overwrite a known value. */
function mergeProvisional(existing: Candidate, incoming: Candidate): Candidate {
  const out: Record<string, unknown> = { ...existing };
  for (const [k, v] of Object.entries(incoming)) if (!isNil(v)) out[k] = v;
  return out as unknown as Candidate;
}

export function mergeCandidate(existing: Candidate | undefined, incoming: Candidate, type: 'candidate.updated' | 'candidate.evaluated'): Candidate {
  if (!existing) return incoming;
  if (existing.status === 'EVALUATED') {
    const out: Record<string, unknown> = { ...existing };
    for (const k of MUTABLE_AFTER_EVALUATED) {
      const v = (incoming as unknown as Record<string, unknown>)[k];
      if (!isNil(v)) out[k] = v;
    }
    return out as unknown as Candidate;
  }
  void type; // an evaluated row arriving for a provisional id completes it; both event types merge the same way
  return mergeProvisional(existing, incoming);
}

function unwrap<T>(payload: unknown, keys: string[], requiredKey: string): T | null {
  if (!payload || typeof payload !== 'object') return null;
  const o = payload as Record<string, unknown>;
  if (typeof o[requiredKey] === 'string') return o as T;
  for (const k of keys) {
    const inner = o[k];
    if (inner && typeof inner === 'object' && typeof (inner as Record<string, unknown>)[requiredKey] === 'string') return inner as T;
  }
  return null;
}

function upsertGeneration(list: Generation[], g: Generation): Generation[] {
  const key = generationKey(g);
  const i = list.findIndex((x) => generationKey(x) === key);
  if (i === -1) return [...list, g];
  const copy = list.slice();
  copy[i] = { ...list[i], ...g };
  return copy;
}

function appendEvent(events: ApiEvent[], ev: ApiEvent): ApiEvent[] {
  const next = [...events, ev];
  return next.length > MAX_EVENTS ? next.slice(next.length - MAX_EVENTS) : next;
}

function applyEvent(state: RunState, ev: ApiEvent): RunState {
  if (state.runId === null || ev.run_id !== state.runId) return state;
  if (ev.seq !== state.lastSeq + 1) return state;
  let next: RunState = { ...state, lastSeq: ev.seq, events: appendEvent(state.events, ev) };
  switch (ev.type) {
    case 'candidate.updated':
    case 'candidate.evaluated': {
      const row = unwrap<Candidate>(ev.payload, ['candidate', 'evaluation', 'row'], 'id');
      if (!row) break;
      // a generated candidate (GeneratedCandidate: evaluation_status NOT_EVALUATED) is not an evaluation row
      if (rec(row).evaluation_status === 'NOT_EVALUATED') {
        const generated = row as unknown as GeneratedCandidate;
        const exists = state.generatedCandidates.some((c) => c.candidate_id === generated.candidate_id);
        next = { ...next, generatedCandidates: exists ? state.generatedCandidates.map((c) => c.candidate_id === generated.candidate_id ? { ...c, ...generated } : c) : [...state.generatedCandidates, generated] };
        break;
      }
      const existed = row.id in state.candidates;
      next = {
        ...next,
        candidates: { ...state.candidates, [row.id]: mergeCandidate(state.candidates[row.id], row, ev.type) },
        order: existed ? state.order : [...state.order, row.id],
      };
      break;
    }
    case 'generation.updated': // live counts while open; upsert only, completion is never inferred here
    case 'generation.completed': {
      const g = unwrap<Generation>(ev.payload, ['generation'], 'rung');
      if (g) next = { ...next, generations: upsertGeneration(state.generations, g) };
      break;
    }
    case 'run.updated': {
      const r = unwrap<Run>(ev.payload, ['run'], 'status');
      if (r && (!r.id || r.id === state.runId)) next = { ...next, run: { ...(state.run ?? ({ id: state.runId } as Run)), ...r } };
      break;
    }
    case 'worker.updated': {
      const w = unwrap<Worker>(ev.payload, ['worker'], 'id');
      if (w) next = { ...next, workers: { ...state.workers, [w.id]: { ...state.workers[w.id], ...w } } };
      break;
    }
    default:
      break;
  }
  return next;
}

export function applySnapshot(state: RunState, snap: Snapshot, reason: string, at: string): RunState {
  const runId = snap.run?.id ?? state.runId;
  if (state.runId !== null && runId !== state.runId) return state; // snapshot for a run that is no longer loaded
  const candidates: Record<string, Candidate> = {};
  const order: string[] = [];
  for (const c of snap.candidates ?? []) {
    if (!(c.id in candidates)) order.push(c.id);
    candidates[c.id] = c;
  }
  const workers: Record<string, Worker> = {};
  for (const w of snap.workers ?? []) workers[w.id] = w;
  const notes: SyncNote[] = reason === 'initial' || reason === 'poll' ? state.syncNotes : [...state.syncNotes, { at, kind: 'resync' as const, detail: `snapshot refetched (${reason}); lastSeq=${snap.last_seq}` }];
  return {
    ...state,
    runId,
    run: snap.run,
    candidates,
    order,
    generatedCandidates: snap.generated_candidates ?? [],
    generations: snap.generations ?? [],
    workers,
    events: (snap.events ?? []).slice(-MAX_EVENTS),
    lastSeq: snap.last_seq,
    syncNotes: notes.slice(-200),
    loaded: true,
  };
}

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case 'reset':
      return initialRunState;
    case 'begin':
      return { ...initialRunState, runId: action.runId, conn: 'connecting' };
    case 'snapshot':
      return applySnapshot(state, action.snap, action.reason, action.at);
    case 'event':
      return applyEvent(state, action.ev);
    case 'status':
      return {
        ...state,
        conn: action.status,
        connDetail: action.detail ?? '',
        syncNotes: action.status === 'reconnecting' || action.status === 'error' || action.status === 'auth_error' ? [...state.syncNotes, { at: action.at, kind: 'status' as const, detail: `${action.status}${action.detail ? `: ${action.detail}` : ''}` }].slice(-200) : state.syncNotes,
      };
    case 'run':
      return state.runId === action.run.id ? { ...state, run: { ...(state.run ?? action.run), ...action.run } } : state;
  }
}

export function candidateList(state: RunState): Candidate[] {
  return state.order.map((id) => state.candidates[id]).filter(Boolean);
}
