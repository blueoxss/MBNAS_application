import { describe, expect, it } from 'vitest';
import { ApiError } from '../src/api/client';
import { classifyEvent, parseApiEvent, SseParser } from '../src/api/sse';
import { backoffDelay, RunSync, type ConnStatus, type SyncDeps, type SyncSink } from '../src/api/sync';
import type { ApiEvent, Snapshot } from '../src/api/types';
import { fixtureSnapshot, RUN_ID } from './fixtures';

const wire = (seq: number, type = 'log', run = RUN_ID, payload: unknown = { message: `m${seq}` }): string => `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ seq, type, run_id: run, payload })}\n\n`;

describe('SseParser', () => {
  it('parses LF and CRLF framing identically', () => {
    const lf = new SseParser().push(wire(1));
    const crlf = new SseParser().push(wire(1).replace(/\n/g, '\r\n'));
    expect(lf).toHaveLength(1);
    expect(crlf).toEqual(lf);
    expect(lf[0].id).toBe('1');
    expect(lf[0].event).toBe('log');
  });
  it('handles a lone CR and CRLF split across chunks', () => {
    const p = new SseParser();
    const out = [...p.push('id: 3\r'), ...p.push('\nevent: log\r\ndata: {"a":1}\r'), ...p.push('\n\r'), ...p.push('\n')];
    expect(out).toHaveLength(1);
    expect(out[0].data).toBe('{"a":1}');
    // a trailing lone CR is held until the next byte (it could be the first half of CRLF)
    const cr = new SseParser();
    expect(cr.push('data: x\r\rdata: y\r\r').map((m) => m.data)).toEqual(['x']);
    expect(cr.push(': next\n').map((m) => m.data)).toEqual(['y']);
  });
  it('joins multi-line data with newline and ignores comments / heartbeats', () => {
    const out = new SseParser().push(': heartbeat\n\nid: 7\ndata: line1\ndata: line2\n: inline comment\ndata:line3\n\n');
    expect(out).toHaveLength(1);
    expect(out[0].data).toBe('line1\nline2\nline3');
    expect(out[0].id).toBe('7');
  });
  it('handles chunk boundaries anywhere in the stream', () => {
    const text = wire(1) + wire(2) + wire(3);
    for (const size of [1, 2, 3, 7, 13]) {
      const p = new SseParser();
      const got: string[] = [];
      for (let i = 0; i < text.length; i += size) got.push(...p.push(text.slice(i, i + size)).map((m) => m.id ?? ''));
      expect(got).toEqual(['1', '2', '3']);
    }
  });
  it('does not dispatch an event without data and strips a BOM and one leading space', () => {
    const out = new SseParser().push('﻿event: ping\n\ndata:  two spaces\n\n');
    expect(out).toHaveLength(1);
    expect(out[0].data).toBe(' two spaces');
  });
});

describe('parseApiEvent / classifyEvent', () => {
  it('rejects malformed events and accepts the contract shape', () => {
    const [m] = new SseParser().push(wire(4, 'run.updated', RUN_ID, { status: 'RUNNING' }));
    expect(parseApiEvent(m)?.seq).toBe(4);
    expect(parseApiEvent({ id: '1', event: 'log', data: 'not json', retry: null })).toBeNull();
    expect(parseApiEvent({ id: null, event: 'log', data: '{"seq":0,"type":"log","run_id":"r","payload":{}}', retry: null })).toBeNull();
    expect(parseApiEvent({ id: null, event: 'weird', data: '{"seq":1,"type":"weird","run_id":"r","payload":{}}', retry: null })).toBeNull();
  });
  it('decides apply / duplicate / wrong_run / gap / snapshot', () => {
    const ev = (seq: number, type: ApiEvent['type'] = 'log', run = RUN_ID): ApiEvent => ({ seq, type, run_id: run, payload: {} });
    expect(classifyEvent(RUN_ID, 5, ev(6))).toBe('apply');
    expect(classifyEvent(RUN_ID, 5, ev(5))).toBe('duplicate');
    expect(classifyEvent(RUN_ID, 5, ev(2))).toBe('duplicate');
    expect(classifyEvent(RUN_ID, 5, ev(6, 'log', 'other-run'))).toBe('wrong_run');
    expect(classifyEvent(RUN_ID, 5, ev(8))).toBe('gap');
    expect(classifyEvent(RUN_ID, 5, ev(6, 'snapshot.required'))).toBe('snapshot');
  });
});

interface Harness {
  sync: RunSync;
  applied: number[];
  snapshots: { reason: string; last: number }[];
  statuses: ConnStatus[];
  opens: number[];
  fetches: () => number;
}

/** Run RunSync against scripted streams; each stream is a list of text chunks (or an Error to throw). */
async function drive(streams: (string[] | Error | null)[], snapshots: Snapshot[], opts: { terminalAfter?: number } = {}): Promise<Harness> {
  const applied: number[] = [];
  const snaps: { reason: string; last: number }[] = [];
  const statuses: ConnStatus[] = [];
  const opens: number[] = [];
  let f = 0;
  let si = 0;
  let status = 'RUNNING';
  const deps: SyncDeps = {
    runId: RUN_ID,
    fetchSnapshot: async () => {
      const s = snapshots[Math.min(f, snapshots.length - 1)];
      f++;
      return s;
    },
    openStream: async (after) => {
      opens.push(after);
      const s = streams[si++];
      if (s === undefined) {
        throw new ApiError('http', 'forbidden', 403);
      }
      if (s === null) return null;
      if (s instanceof Error) throw s;
      return (async function* () {
        for (const c of s) yield c;
      })();
    },
    sleep: async () => undefined,
    pollIntervalMs: 1,
  };
  const sink: SyncSink = {
    onSnapshot: (snap, reason) => {
      snaps.push({ reason, last: snap.last_seq });
      status = snap.run.status;
    },
    onEvent: (ev) => {
      applied.push(ev.seq);
      if (opts.terminalAfter !== undefined && ev.seq >= opts.terminalAfter) status = 'COMPLETED';
    },
    onStatus: (s) => statuses.push(s),
    isTerminal: () => ['COMPLETED', 'FAILED', 'CANCELLED'].includes(status),
  };
  const sync = new RunSync(deps, sink);
  await sync.start();
  return { sync, applied, snapshots: snaps, statuses, opens, fetches: () => f };
}

describe('RunSync', () => {
  it('loads a snapshot, then streams from after_seq = last_seq and applies only new seqs once', async () => {
    const h = await drive([[wire(5), wire(6), wire(6), wire(7)]], [fixtureSnapshot('RUNNING', 5)], { terminalAfter: 7 });
    expect(h.snapshots[0]).toEqual({ reason: 'initial', last: 5 });
    expect(h.opens[0]).toBe(5);
    expect(h.applied).toEqual([6, 7]); // 5 is a duplicate of the snapshot, 6 repeated
    expect(h.sync.lastSeq).toBe(7);
  });
  it('ignores events of another run', async () => {
    const h = await drive([[wire(6, 'log', 'someone-else'), wire(6)]], [fixtureSnapshot('RUNNING', 5)], { terminalAfter: 6 });
    expect(h.applied).toEqual([6]);
  });
  it('on a seq gap fetches the authoritative snapshot instead of applying later events, then resumes from its last_seq', async () => {
    const h = await drive([[wire(6), wire(9), wire(10)], [wire(11)]], [fixtureSnapshot('RUNNING', 5), fixtureSnapshot('RUNNING', 10)], { terminalAfter: 11 });
    expect(h.applied).toEqual([6, 11]); // 9 and 10 were never applied individually
    expect(h.snapshots.map((s) => s.reason)).toEqual(['initial', 'gap', 'reconnect']);
    expect(h.opens).toEqual([5, 10]);
  });
  it('snapshot.required triggers a snapshot refetch', async () => {
    const h = await drive([[wire(6, 'snapshot.required')], [wire(8)]], [fixtureSnapshot('RUNNING', 5), fixtureSnapshot('RUNNING', 7)], { terminalAfter: 8 });
    expect(h.snapshots.map((s) => s.reason)).toContain('snapshot.required');
    expect(h.applied).toEqual([8]);
    expect(h.opens).toEqual([5, 7]);
  });
  it('reconnects with backoff from after_seq = lastSeq after the stream drops', async () => {
    const h = await drive([[wire(6)], new Error('boom'), [wire(7), wire(8)]], [fixtureSnapshot('RUNNING', 5)], { terminalAfter: 8 });
    expect(h.opens).toEqual([5, 6, 6]);
    expect(h.applied).toEqual([6, 7, 8]);
    expect(h.statuses).toContain('reconnecting');
  });
  it('falls back to snapshot polling when streaming is unsupported, without duplicating state', async () => {
    const snaps = [fixtureSnapshot('RUNNING', 5), fixtureSnapshot('RUNNING', 6), fixtureSnapshot('COMPLETED', 7)];
    const h = await drive([null], snaps);
    expect(h.statuses).toContain('polling');
    expect(h.snapshots.map((s) => s.reason)).toEqual(['initial', 'poll', 'poll']);
    expect(h.applied).toEqual([]); // polling replaces state wholesale; nothing is appended as an "event"
    expect(h.statuses[h.statuses.length - 1]).toBe('closed');
  });
  it('stops on authentication errors instead of retrying forever', async () => {
    const h = await drive([], [fixtureSnapshot('RUNNING', 5)]);
    expect(h.statuses).toContain('auth_error');
    expect(h.opens).toHaveLength(1);
  });
  it('exponential backoff is capped', () => {
    expect([0, 1, 2, 3, 10].map((a) => backoffDelay(a))).toEqual([1000, 2000, 4000, 8000, 15000]);
  });
});

describe('generation.updated parsing', () => {
  it('accepts generation.updated as a known event type', async () => {
    const { parseApiEvent } = await import('../src/api/sse');
    const e = parseApiEvent({ id: '6', event: 'generation.updated', retry: null, data: JSON.stringify({ seq: 6, type: 'generation.updated', run_id: 'r', payload: { rung: 'L0' } }) });
    expect(e?.type).toBe('generation.updated');
  });
});
