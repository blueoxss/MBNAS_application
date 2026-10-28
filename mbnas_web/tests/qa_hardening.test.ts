/**
 * QA pass (vitest): SSE duplicate / out-of-order / gap / foreign-run handling end to end through RunSync + the reducer,
 * token containment, client path pre-check parity with the server negatives, and static checks that the shipped
 * bundle/source keeps the token in memory and never mixes synthetic data into the LIVE screens.
 * Everything here is SYNTHETIC (no network, no server).
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';
import { describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError } from '../src/api/client';
import { SseParser, parseApiEvent } from '../src/api/sse';
import { RunSync, type ConnStatus, type SyncDeps, type SyncSink } from '../src/api/sync';
import type { Snapshot } from '../src/api/types';
import { checkServerPath } from '../src/lib/validate';
import { initialRunState, runReducer, type RunState } from '../src/state/reducer';
import { cand, fixtureSnapshot, RUN_ID } from './fixtures';

const wire = (seq: number | string, type = 'log', run = RUN_ID, payload: unknown = { message: `m${seq}` }): string => `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ seq, type, run_id: run, payload })}\n\n`;
const evaluatedWire = (seq: number, id: string): string => wire(seq, 'candidate.evaluated', RUN_ID, { ...cand({ id }), id });

interface Result {
  state: RunState;
  applied: number[];
  reasons: string[];
  opens: number[];
  statuses: ConnStatus[];
}

/** Drive RunSync against scripted streams and feed every callback into the REAL reducer. */
async function drive(streams: (string[] | Error | null)[], snapshots: Snapshot[], terminalAfter = Infinity): Promise<Result> {
  let state = runReducer(initialRunState, { type: 'begin', runId: RUN_ID });
  const applied: number[] = [];
  const reasons: string[] = [];
  const opens: number[] = [];
  const statuses: ConnStatus[] = [];
  let fetches = 0;
  let si = 0;
  let status = 'RUNNING';
  const deps: SyncDeps = {
    runId: RUN_ID,
    fetchSnapshot: async () => snapshots[Math.min(fetches++, snapshots.length - 1)],
    openStream: async (after) => {
      opens.push(after);
      const s = streams[si++];
      if (s === undefined) throw new ApiError('http', 'forbidden', 403);
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
      reasons.push(reason);
      status = snap.run.status;
      state = runReducer(state, { type: 'snapshot', snap, reason, at: 't' });
    },
    onEvent: (ev) => {
      const before = state.lastSeq;
      state = runReducer(state, { type: 'event', ev });
      if (state.lastSeq !== before) applied.push(ev.seq);
      if (ev.seq >= terminalAfter) status = 'COMPLETED';
    },
    onStatus: (s) => statuses.push(s),
    isTerminal: () => ['COMPLETED', 'FAILED', 'CANCELLED'].includes(status),
  };
  await new RunSync(deps, sink).start();
  return { state, applied, reasons, opens, statuses };
}

const snap = (last: number, status: Snapshot['run']['status'] = 'RUNNING'): Snapshot => fixtureSnapshot(status, last);

describe('SSE client: duplicates, out-of-order, gaps and foreign runs (RunSync + reducer)', () => {
  it('a duplicate inside one stream, an old seq and a repeat after reconnect are applied exactly once', async () => {
    const r = await drive([[wire(6), wire(6), wire(5), wire(7)], [wire(6), wire(7), wire(8)]], [snap(5)], 8);
    expect(r.applied).toEqual([6, 7, 8]);
    expect(r.state.lastSeq).toBe(8);
    expect(r.opens).toEqual([5, 7]); // the reconnect asks from the last applied seq, not from 5
    expect(new Set(r.state.events.map((e) => e.seq)).size).toBe(r.state.events.length);
  });

  it('an out-of-order seq after a newer one is dropped and never moves the cursor backwards', async () => {
    const r = await drive([[wire(6), wire(7), wire(6), wire(7), wire(8)]], [snap(5)], 8);
    expect(r.applied).toEqual([6, 7, 8]);
    expect(r.state.lastSeq).toBe(8);
  });

  it('a gap is never applied: later events are discarded, the snapshot replaces state, the stream resumes from its last_seq', async () => {
    const r = await drive([[wire(6), wire(9), wire(7), wire(10)], [wire(11)]], [snap(5), snap(10)], 11);
    expect(r.applied).toEqual([6, 11]);
    expect(r.reasons).toEqual(['initial', 'gap', 'reconnect']);
    expect(r.opens).toEqual([5, 10]);
    expect(r.state.lastSeq).toBe(11);
  });

  it('events of another run (even with the next seq) are ignored while the right run continues', async () => {
    const r = await drive([[wire(6, 'log', 'other-run'), wire(7, 'log', 'other-run'), wire(6), wire(7, 'log', 'third-run'), wire(7)]], [snap(5)], 7);
    expect(r.applied).toEqual([6, 7]);
  });

  it('malformed frames, heartbeats, empty data, non-integer / zero / negative seqs never break the stream', async () => {
    const junk = [': heartbeat\n\n', 'data: {not json}\n\n', 'id: 9\ndata:\n\n', wire(0), wire(-4), wire('6.5'), wire(6), 'event: nonsense\ndata: {"seq":7,"type":"nonsense","run_id":"' + RUN_ID + '","payload":{}}\n\n', wire(7)];
    const r = await drive([junk], [snap(5)], 7);
    expect(r.applied).toEqual([6, 7]);
    expect(r.statuses).not.toContain('error');
  });

  it('chunk boundaries inside frames do not change what is applied', async () => {
    const text = wire(6) + wire(6) + wire(7);
    const whole = await drive([[text]], [snap(5)], 7);
    const bytewise = await drive([text.split('')], [snap(5)], 7);
    expect(bytewise.applied).toEqual(whole.applied);
    expect(whole.applied).toEqual([6, 7]);
  });

  it('snapshot.required mid-stream replaces state; events at or below the snapshot cursor are duplicates afterwards', async () => {
    const r = await drive([[wire(6), wire(7, 'snapshot.required')], [wire(8), wire(9), wire(10)]], [snap(5), snap(8)], 10);
    expect(r.reasons).toContain('snapshot.required');
    expect(r.state.lastSeq).toBe(10);
    expect(r.applied).toEqual([6, 9, 10]); // 8 was baked into the snapshot (cursor 8): not replayed
  });

  it('a stream that dies mid-run reconnects with backoff and loses nothing', async () => {
    const r = await drive([[wire(6)], new Error('network down'), new Error('still down'), [wire(7), wire(8)]], [snap(5)], 8);
    expect(r.applied).toEqual([6, 7, 8]);
    expect(r.opens).toEqual([5, 6, 6, 6]);
    expect(r.statuses.filter((s) => s === 'reconnecting').length).toBeGreaterThanOrEqual(2);
  });

  it('a replayed EVALUATED event never adds a second point or changes its metrics', async () => {
    const first = evaluatedWire(6, 'ev-new');
    const mutated = wire(7, 'candidate.evaluated', RUN_ID, { ...cand({ id: 'ev-new', ap: 0.99, fitness: 0.99 }), id: 'ev-new' });
    const r = await drive([[first, mutated]], [snap(5)], 7);
    expect(r.state.order.filter((i) => i === 'ev-new')).toHaveLength(1);
    expect(r.state.candidates['ev-new'].ap).not.toBe(0.99);
  });

  it('401/403 on reconnect stops with an auth error instead of looping', async () => {
    const r = await drive([[wire(6)]], [snap(5)]);
    expect(r.statuses).toContain('auth_error');
    expect(r.opens.length).toBeLessThanOrEqual(3);
  });

  it('parseApiEvent rejects events with an unusable seq', () => {
    const msg = (seq: unknown) => ({ id: null, event: 'log', data: JSON.stringify({ seq, type: 'log', run_id: RUN_ID, payload: {} }), retry: null });
    for (const bad of ['6', 6.5, null, {}, -1, Number.NaN]) expect(parseApiEvent(msg(bad)), String(bad)).toBeNull();
    expect(parseApiEvent(msg(6))?.seq).toBe(6);
    expect(new SseParser().push(wire(6))).toHaveLength(1);
  });
});

describe('token containment (memory only, header only)', () => {
  const TOKEN = 'QA-SECRET-TOKEN-9f3a1c7e5b2d4a60';
  const json = (status: number, body: unknown): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

  it('every client call sends the token only as an Authorization header: never in URL, body, credentials or storage', async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      if (url.endsWith('/export')) return new Response('zip-bytes', { status: 200 });
      if (url.includes('/events')) return new Response('', { status: 200 });
      return json(200, { status: 'ok', presets: [], registry: [], runs: [] });
    });
    const c = new ApiClient({ origin: 'https://nas.example.org', token: TOKEN, fetchImpl: fetchImpl as unknown as typeof fetch });
    await c.health();
    await c.presets();
    await c.registry();
    await c.validate({ a: 1 });
    await c.createRun({ protocol: {} as never, protocol_hash: 'h', idempotency_key: 'k' });
    await c.listRuns();
    await c.getRun('r1');
    await c.snapshot('r1');
    await c.candidateDetail('r1', 'c1');
    await c.control('r1', 'pause', { reason: 'x', idempotency_key: 'k2' });
    await c.refreshArtifact('r1', 'a1');
    await c.exportRun('r1');
    await c.openEvents('r1', 0, new AbortController().signal);
    expect(seen).toHaveLength(13);
    for (const { url, init } of seen) {
      expect(url.startsWith('https://nas.example.org/api/v1/')).toBe(true);
      expect(url).not.toContain(TOKEN);
      expect(decodeURIComponent(url)).not.toMatch(/token|bearer|authorization|api_key/i);
      expect(String(init.body ?? '')).not.toContain(TOKEN);
      expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
      expect(init.credentials).toBe('omit');
    }
    for (const store of [window.localStorage, window.sessionStorage]) expect(JSON.stringify({ ...store })).not.toContain(TOKEN);
    expect(document.cookie).not.toContain(TOKEN);
    expect(window.location.href).not.toContain(TOKEN);
  });

  it('errors carry neither the token nor the Authorization header', async () => {
    const mk = (f: () => Promise<Response>) => new ApiClient({ origin: 'https://nas.example.org', token: TOKEN, fetchImpl: f as unknown as typeof fetch, timeoutMs: 20 });
    const errs = await Promise.all([
      mk(async () => json(401, { code: 'AUTH_REQUIRED', message: 'bearer token required' })).health().catch((e: ApiError) => e),
      mk(async () => json(403, { code: 'ACCESS_DENIED', message: 'invalid bearer token' })).health().catch((e: ApiError) => e),
      mk(async () => { throw new TypeError('Failed to fetch'); }).health().catch((e: ApiError) => e),
      mk(((): Promise<Response> => new Promise(() => undefined)) as () => Promise<Response>).health().catch((e: ApiError) => e),
      mk(async () => new Response('plain text 500', { status: 500 })).health().catch((e: ApiError) => e),
    ]);
    for (const e of errs) {
      expect(e).toBeInstanceOf(ApiError);
      expect(`${(e as ApiError).message} ${JSON.stringify(e)} ${String(e)}`).not.toContain(TOKEN);
    }
  });

  it('a foreign absolute URL never receives the Authorization header', async () => {
    const fetchImpl = vi.fn(async () => json(200, {}));
    const c = new ApiClient({ origin: 'https://nas.example.org', token: TOKEN, fetchImpl: fetchImpl as unknown as typeof fetch });
    for (const url of ['https://evil.example.com/x', 'http://nas.example.org/x', 'https://nas.example.org:8443/x', 'https://nas.example.org.evil.com/x']) {
      await expect(c.raw(url)).rejects.toMatchObject({ kind: 'config' });
    }
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('a client without a token sends no Authorization header at all', async () => {
    const fetchImpl = vi.fn(async () => json(200, {}));
    await new ApiClient({ origin: 'https://nas.example.org', token: '   ', fetchImpl: fetchImpl as unknown as typeof fetch }).health();
    const init = (fetchImpl.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(Object.keys(init.headers as object).map((k) => k.toLowerCase())).not.toContain('authorization');
  });
});

describe('client path pre-check mirrors the server negatives', () => {
  // the same inputs as MBNAS_server/tests/qa/test_security_negatives.py (server code in brackets)
  it.each([
    ['/data/ds/../../../etc/passwd', 'PATH_TRAVERSAL'],
    ['/data/..', 'PATH_TRAVERSAL'],
    ['/ABSOLUTE/path/to/data', 'PATH_PLACEHOLDER'],
    ['/ABSOLUTE/data', 'PATH_PLACEHOLDER'],
    ['/data/<your-dataset>', 'PATH_PLACEHOLDER'],
    ['/data/dataset>', 'PATH_PLACEHOLDER'],
    ['/data/YOUR_DATASET', 'PATH_PLACEHOLDER'],
    ['datasets/ds/data.yaml', 'PATH_NOT_ABSOLUTE'],
    ['./data.yaml', 'PATH_NOT_ABSOLUTE'],
    ['~/data.yaml', 'PATH_NOT_ABSOLUTE'],
    ['file:///etc/passwd', 'PATH_NOT_ABSOLUTE'],
    ['', 'PATH_NOT_ABSOLUTE'],
    ['/data/ds/data.yaml\u0000.png', 'PATH_PLACEHOLDER'],
  ])('%j -> %s', (value, code) => {
    expect(checkServerPath('f', value).map((i) => i.code)).toEqual([code]);
  });
  it('accepts a normal absolute path and does not flag a dotted file name', () => {
    expect(checkServerPath('f', '/srv/datasets/synthetic_pilot/data.yaml')).toEqual([]);
    expect(checkServerPath('f', '/data/ds.v2/images/train.1')).toEqual([]);
  });
});

describe('static checks of the shipped source and build', () => {
  const walk = (dir: string): string[] => readdirSync(dir).flatMap((n) => (statSync(join(dir, n)).isDirectory() ? walk(join(dir, n)) : [join(dir, n)]));
  const stripComments = (s: string): string => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
  const src = walk(join(__dirname, '../src')).filter((f) => /\.(ts|tsx)$/.test(f) && !f.endsWith('types.gen.ts'));

  it('no persistent browser storage is touched anywhere in src (token lives only in memory)', () => {
    const offenders = src.filter((f) => /\b(localStorage|sessionStorage|indexedDB|document\.cookie|caches\.open)\b/.test(stripComments(readFileSync(f, 'utf8'))));
    expect(offenders).toEqual([]);
  });

  it('the token is never put into a URL or history entry', () => {
    const offenders = src.filter((f) => /(history\.(push|replace)State|location\.(search|hash)\s*=)[^;]*token/i.test(stripComments(readFileSync(f, 'utf8'))) || /[?&](access_)?token=/i.test(stripComments(readFileSync(f, 'utf8'))));
    expect(offenders).toEqual([]);
  });

  it('LIVE source never imports fixtures, mocks or sample data and never fabricates numbers', () => {
    const offenders = src.filter((f) => {
      const code = stripComments(readFileSync(f, 'utf8'));
      const idempotencyKeyFallback = f.endsWith('src/lib/runflow.ts'); // crypto-less fallback for Idempotency-Key bytes only
      return /from\s+['"][^'"]*(tests|e2e|fixtures|mockapi|mock)['"/]/.test(code) || (!idempotencyKeyFallback && /Math\.random\(/.test(code)) || /\bsynthetic[-_]/i.test(code);
    });
    expect(offenders).toEqual([]);
  });

  it('the only place that builds an Authorization header is the API client', () => {
    const builds = /['"`]Authorization['"`]\s*:|\bAuthorization\s*:\s*[`'"]|`Bearer\s*\$\{/;
    const users = src.filter((f) => builds.test(stripComments(readFileSync(f, 'utf8')))).map((f) => f.replace(/.*\/src\//, 'src/'));
    expect(users).toEqual(['src/api/client.ts']);
  });

  it('production bundle has a reasonable size and contains no token-looking literal or fixture marker', () => {
    const dist = join(__dirname, '../dist/assets');
    let files: string[];
    try {
      files = readdirSync(dist);
    } catch {
      return; // dist is produced by `npm run build`; the e2e suite always builds first
    }
    const js = files.filter((f) => f.endsWith('.js'));
    expect(js.length).toBeGreaterThan(0);
    for (const f of js) {
      const raw = readFileSync(join(dist, f));
      expect(raw.length).toBeLessThan(700 * 1024);
      expect(gzipSync(raw).length).toBeLessThan(200 * 1024);
      const text = raw.toString('utf8');
      expect(text).not.toMatch(/synthetic-e2e-token|QA-SECRET-TOKEN|synthetic-run-0001/);
    }
    const css = files.filter((f) => f.endsWith('.css')).reduce((n, f) => n + statSync(join(dist, f)).size, 0);
    expect(css).toBeLessThan(100 * 1024);
  });
});
