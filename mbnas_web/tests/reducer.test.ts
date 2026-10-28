import { beforeEach, describe, expect, it } from 'vitest';
import type { ApiEvent } from '../src/api/types';
import { initialRunState, mergeCandidate, runReducer, type RunState } from '../src/state/reducer';
import { cand, fixtureSnapshot, resetCounter, RUN_ID } from './fixtures';

beforeEach(resetCounter);

const load = (): RunState => runReducer(runReducer(initialRunState, { type: 'begin', runId: RUN_ID }), { type: 'snapshot', snap: fixtureSnapshot('RUNNING', 5), reason: 'initial', at: 't' });
const ev = (seq: number, type: ApiEvent['type'], payload: unknown, run = RUN_ID): ApiEvent => ({ seq, type, run_id: run, payload: payload as ApiEvent['payload'] });

describe('immutable evaluation rows', () => {
  it('candidate.updated never clears metrics of an EVALUATED row', () => {
    const s0 = load();
    const target = Object.values(s0.candidates).find((c) => c.status === 'EVALUATED' && c.arm === 'GA')!;
    const partial = { id: target.id, candidate_id: target.candidate_id, status: 'TRAINING', ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null, candidate_status: 'TRAINING', progress: { epoch: 3 } };
    const s1 = runReducer(s0, { type: 'event', ev: ev(6, 'candidate.updated', partial) });
    const after = s1.candidates[target.id];
    expect(after.status).toBe('EVALUATED');
    expect(after.ap).toBe(target.ap);
    expect(after.latency_ms).toBe(target.latency_ms);
    expect(after.fitness).toBe(target.fitness);
    expect(after.candidate_status).toBe('TRAINING'); // only the mutable status moves
    expect(s1.lastSeq).toBe(6);
  });
  it('a second candidate.evaluated for the same evaluation id does not overwrite its metrics', () => {
    const s0 = load();
    const target = Object.values(s0.candidates).find((c) => c.status === 'EVALUATED')!;
    const s1 = runReducer(s0, { type: 'event', ev: ev(6, 'candidate.evaluated', { ...target, ap: 0.99, fitness: 0.99 }) });
    expect(s1.candidates[target.id].ap).toBe(target.ap);
    expect(s1.order.filter((i) => i === target.id)).toHaveLength(1);
  });
  it('provisional rows accept updates, never losing known values to null', () => {
    const prov = cand({ id: 'p1', status: 'TRAINING', ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null, progress: { epoch: 1 } });
    const merged = mergeCandidate(prov, { ...prov, progress: { epoch: 2 }, name: 'renamed', ap: null }, 'candidate.updated');
    expect(merged.progress?.epoch).toBe(2);
    expect(merged.name).toBe('renamed');
    const withLat = mergeCandidate(prov, { ...prov, latency_ms: 12 }, 'candidate.updated');
    expect(mergeCandidate(withLat, { ...prov, latency_ms: null }, 'candidate.updated').latency_ms).toBe(12);
  });
  it('an evaluated event completes a provisional row (same id) and adds new rows once', () => {
    let s = load();
    const prov = Object.values(s.candidates).find((c) => c.status === 'TRAINING')!;
    s = runReducer(s, { type: 'event', ev: ev(6, 'candidate.evaluated', { ...prov, status: 'EVALUATED', ap: 0.3, recall: 0.3, latency_ms: 40, size_mb: 20, fitness: 0.5 }) });
    expect(s.candidates[prov.id].status).toBe('EVALUATED');
    expect(s.candidates[prov.id].ap).toBe(0.3);
    const before = s.order.length;
    const fresh = cand({ id: 'brand-new' });
    s = runReducer(s, { type: 'event', ev: ev(7, 'candidate.evaluated', fresh) });
    expect(s.order.length).toBe(before + 1);
  });
});

describe('event ordering and replacement', () => {
  it('ignores duplicates, other runs and out-of-order seqs; applies only lastSeq+1', () => {
    const s0 = load();
    const a = runReducer(s0, { type: 'event', ev: ev(5, 'log', { message: 'dup' }) });
    expect(a).toBe(s0);
    const b = runReducer(s0, { type: 'event', ev: ev(6, 'log', { message: 'x' }, 'other') });
    expect(b).toBe(s0);
    const c = runReducer(s0, { type: 'event', ev: ev(9, 'log', { message: 'gap' }) });
    expect(c).toBe(s0);
    const d = runReducer(s0, { type: 'event', ev: ev(6, 'log', { message: 'ok' }) });
    expect(d.lastSeq).toBe(6);
    expect(d.events[d.events.length - 1].seq).toBe(6);
  });
  it('a snapshot replaces the state wholesale (polling cannot duplicate points)', () => {
    let s = load();
    const n = s.order.length;
    s = runReducer(s, { type: 'snapshot', snap: fixtureSnapshot('RUNNING', 5), reason: 'poll', at: 't' });
    s = runReducer(s, { type: 'snapshot', snap: fixtureSnapshot('RUNNING', 5), reason: 'poll', at: 't' });
    expect(s.order).toHaveLength(n);
    expect(Object.keys(s.candidates)).toHaveLength(n);
  });
  it('a resync is recorded for the History screen', () => {
    const s = runReducer(load(), { type: 'snapshot', snap: fixtureSnapshot('RUNNING', 9), reason: 'gap', at: 'T' });
    expect(s.syncNotes.some((x) => x.detail.includes('gap'))).toBe(true);
    expect(s.lastSeq).toBe(9);
  });
  it('run.updated, generation.completed and worker.updated update their slices', () => {
    let s = load();
    s = runReducer(s, { type: 'event', ev: ev(6, 'run.updated', { id: RUN_ID, status: 'PAUSED', protocol_hash: 'h' }) });
    expect(s.run?.status).toBe('PAUSED');
    s = runReducer(s, { type: 'event', ev: ev(7, 'worker.updated', { id: 'w9', status: 'idle', device: 'cuda:1' }) });
    expect(s.workers.w9.status).toBe('idle');
    const gens = s.generations.length;
    s = runReducer(s, { type: 'event', ev: ev(8, 'generation.completed', { arm: 'GA', generation: 2, rung: 'L0', complete: true, planning_closed: true, planned_count: 1, evaluated_count: 1, pending_count: 0, failed_count: 0, not_admitted_count: 0, comparison_group: s.generations[0].comparison_group, search_seed: 101, representative_candidate_id: null }) });
    expect(s.generations.length).toBe(gens); // generation 2 already existed: replaced, not duplicated
    expect(s.generations.find((g) => g.generation === 2)?.complete).toBe(true);
  });
});

describe('generation.updated (contract 1.2.9)', () => {
  const gen = (over: Record<string, unknown>) => ({ arm: 'GA', generation: 7, rung: 'L0', complete: false, planning_closed: false, planned_count: 4, evaluated_count: 1, pending_count: 3, failed_count: 0, not_admitted_count: 0, comparison_group: 'cg', search_seed: 101, representative_candidate_id: null, ...over });
  it('inserts an open generation, then upserts live counts without duplicating or completing', () => {
    let s = load();
    const n = s.generations.length;
    s = runReducer(s, { type: 'event', ev: ev(6, 'generation.updated', gen({})) });
    expect(s.generations.length).toBe(n + 1);
    s = runReducer(s, { type: 'event', ev: ev(7, 'generation.updated', gen({ evaluated_count: 3, pending_count: 1 })) });
    expect(s.generations.length).toBe(n + 1);
    const g = s.generations.find((x) => x.generation === 7)!;
    expect(g.evaluated_count).toBe(3);
    expect(g.complete).toBe(false);
    expect(s.lastSeq).toBe(7);
    s = runReducer(s, { type: 'event', ev: ev(8, 'generation.completed', gen({ complete: true, planning_closed: true, evaluated_count: 4, pending_count: 0 })) });
    expect(s.generations.length).toBe(n + 1);
    expect(s.generations.find((x) => x.generation === 7)?.complete).toBe(true);
  });
});
