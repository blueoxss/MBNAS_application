import { describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError, normalizeOrigin } from '../src/api/client';
import { loadArtifact, loadOnce, parseMatching, sampleRecall, stateFromStatus, isExpiredAt } from '../src/lib/artifacts';
import { IdempotencyStore, runFingerprint, submitRun } from '../src/lib/runflow';
import { fixtureValidateOk } from './fixtures';

const json = (status: number, body: unknown): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

describe('normalizeOrigin', () => {
  it.each([
    ['', ''],
    ['   ', ''],
    ['https://nas.example.org', 'https://nas.example.org'],
    ['https://nas.example.org:8443/', 'https://nas.example.org:8443'],
    ['http://localhost:8000', 'http://localhost:8000'],
    ['http://127.0.0.1:8000', 'http://127.0.0.1:8000'],
  ])('accepts %j', (input, origin) => {
    expect(normalizeOrigin(input)).toEqual({ ok: true, origin });
  });
  it.each(['http://nas.example.org', 'ftp://x', 'nas.example.org', 'https://user:pw@x.org', 'https://x.org/api', 'https://x.org?token=1', 'javascript:alert(1)'])('rejects %j', (input) => {
    expect(normalizeOrigin(input).ok).toBe(false);
  });
});

describe('ApiClient', () => {
  it('sends the bearer token only in the Authorization header, never in the URL', async () => {
    const fetchImpl = vi.fn(async () => json(200, { status: 'ok' }));
    const c = new ApiClient({ origin: 'https://nas.example.org', token: 'SECRET-TOKEN', fetchImpl: fetchImpl as unknown as typeof fetch });
    await c.health();
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://nas.example.org/api/v1/health');
    expect(url).not.toContain('SECRET');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer SECRET-TOKEN');
    expect(init.credentials).toBe('omit');
  });
  it('uses relative URLs for the same-origin mode and refuses absolute foreign URLs', async () => {
    const fetchImpl = vi.fn(async () => json(200, {}));
    const c = new ApiClient({ origin: '', token: 't', pageOrigin: 'http://localhost:5173', fetchImpl: fetchImpl as unknown as typeof fetch });
    await c.json('/api/v1/health');
    expect((fetchImpl.mock.calls[0] as unknown as [string])[0]).toBe('/api/v1/health');
    await expect(c.raw('https://evil.example.com/x')).rejects.toMatchObject({ kind: 'config' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
  it('classifies network errors, timeouts and HTTP errors; retryable only for transport and 5xx', async () => {
    const mk = (f: () => Promise<Response>) => new ApiClient({ origin: '', token: '', pageOrigin: 'http://x', fetchImpl: f as unknown as typeof fetch, timeoutMs: 20 });
    const net = await mk(async () => { throw new TypeError('Failed to fetch'); }).health().catch((e: ApiError) => e);
    expect(net).toMatchObject({ kind: 'network', retryable: true });
    const slow = await mk((() => new Promise<Response>(() => undefined)) as () => Promise<Response>).health().catch((e: ApiError) => e);
    expect(slow).toMatchObject({ kind: 'timeout', retryable: true });
    const e500 = await mk(async () => json(503, { code: 'BUSY', message: 'later' })).health().catch((e: ApiError) => e);
    expect(e500).toMatchObject({ kind: 'http', status: 503, retryable: true, code: 'BUSY' });
    const e409 = await mk(async () => json(409, { code: 'IDEMPOTENCY_CONFLICT', message: 'changed payload' })).health().catch((e: ApiError) => e);
    expect(e409).toMatchObject({ status: 409, retryable: false, code: 'IDEMPOTENCY_CONFLICT' });
    const wrapped = await mk(async () => json(422, { detail: { code: 'X', message: 'wrapped', errors: [{ field: 'a' }] } })).health().catch((e: ApiError) => e);
    expect((wrapped as ApiError).body?.errors).toEqual([{ field: 'a' }]);
  });
  it('isApiOrigin compares scheme, host and port exactly', () => {
    const c = new ApiClient({ origin: 'https://nas.example.org', token: 't' });
    expect(c.isApiOrigin('https://nas.example.org/api/v1/x')).toBe(true);
    expect(c.isApiOrigin('/api/v1/x')).toBe(true);
    expect(c.isApiOrigin('https://nas.example.org:444/x')).toBe(false);
    expect(c.isApiOrigin('http://nas.example.org/x')).toBe(false);
    expect(c.isApiOrigin('https://nas.example.org.evil.com/x')).toBe(false);
    expect(c.isApiOrigin('https://evil.com/https://nas.example.org')).toBe(false);
  });
});

describe('Run idempotency', () => {
  const resolved = { schema_version: 'x', console: {} };
  const v = fixtureValidateOk(resolved);

  it('reuses the SAME key after timeout / network error / 5xx and sends it as header and body', async () => {
    const seen: { header: string; body: string }[] = [];
    const answers: (() => Response | Promise<Response>)[] = [
      () => { throw new TypeError('network'); },
      () => json(503, { code: 'BUSY', message: 'busy' }),
      () => new Promise<Response>(() => undefined), // timeout
      () => json(200, { run_id: 'run-1', status: 'QUEUED' }),
    ];
    let n = 0;
    const fetchImpl = vi.fn(async (_u: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string);
      seen.push({ header: (init.headers as Record<string, string>)['Idempotency-Key'], body: body.idempotency_key });
      return answers[n++]();
    });
    const client = new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: fetchImpl as unknown as typeof fetch, timeoutMs: 15 });
    let counter = 0;
    const store = new IdempotencyStore(() => `key-${++counter}`);
    const o1 = await submitRun(client, store, v);
    const o2 = await submitRun(client, store, v);
    const o3 = await submitRun(client, store, v);
    const o4 = await submitRun(client, store, v);
    expect([o1.kind, o2.kind, o3.kind, o4.kind]).toEqual(['retryable', 'retryable', 'retryable', 'created']);
    expect(new Set(seen.map((s) => s.header))).toEqual(new Set(['key-1']));
    expect(seen.every((s) => s.header === s.body)).toBe(true);
    expect(counter).toBe(1);
    // after a 2xx the key is released: a new submit of the same payload gets a fresh key
    expect(store.has(runFingerprint(v))).toBe(false);
  });

  it('sends the resolved protocol, its hash and the validation id (not the editor state)', async () => {
    const fetchImpl = vi.fn(async (_u: string, _i: RequestInit) => json(200, { run_id: 'r', status: 'QUEUED' }));
    const client = new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: fetchImpl as unknown as typeof fetch });
    await submitRun(client, new IdempotencyStore(), v);
    const body = JSON.parse((fetchImpl.mock.calls[0][1] as RequestInit).body as string);
    expect(body.protocol).toEqual(resolved);
    expect(body.protocol_hash).toBe(v.protocol_hash);
    expect(body.validation_id).toBe(v.validation_id);
  });

  it('409 and 422 are definitive answers: the key is released', async () => {
    for (const [status, kind] of [[409, 'conflict'], [422, 'rejected']] as const) {
      const client = new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: (async () => json(status, { code: 'C', message: 'm' })) as unknown as typeof fetch });
      const store = new IdempotencyStore(() => 'k');
      const o = await submitRun(client, store, v);
      expect(o.kind).toBe(kind);
      expect(store.has(runFingerprint(v))).toBe(false);
    }
  });

  it('401 keeps the key (the request may still succeed after fixing the token)', async () => {
    const client = new ApiClient({ origin: '', token: '', pageOrigin: 'http://x', fetchImpl: (async () => json(401, { code: 'AUTH', message: 'no' })) as unknown as typeof fetch });
    const store = new IdempotencyStore(() => 'k');
    const o = await submitRun(client, store, v);
    expect(o.kind).toBe('retryable');
    expect(store.has(runFingerprint(v))).toBe(true);
  });

  it('refuses to submit an unvalidated or invalid response', async () => {
    const client = new ApiClient({ origin: '', token: '', pageOrigin: 'http://x', fetchImpl: vi.fn() as unknown as typeof fetch });
    await expect(submitRun(client, new IdempotencyStore(), { ...v, valid: false })).rejects.toThrow();
    await expect(submitRun(client, new IdempotencyStore(), { ...v, protocol_hash: null })).rejects.toThrow();
  });
});

describe('artifact origin rule and states', () => {
  const art = { id: 'a1', kind: 'gt' as const, url: '/api/v1/runs/r/artifacts/a1', auth_mode: 'bearer_fetch' as const };
  const png = (): Response => new Response(new Uint8Array([137, 80, 78, 71]), { status: 200, headers: { 'content-type': 'image/png' } });

  it('bearer_fetch to the API origin carries Authorization and yields a blob URL', async () => {
    const calls: { url: string; auth?: string }[] = [];
    const fetchImpl = vi.fn(async (u: string, init: RequestInit) => {
      calls.push({ url: u, auth: (init.headers as Record<string, string>).Authorization });
      return png();
    });
    const client = new ApiClient({ origin: 'https://nas.example.org', token: 'TOK', fetchImpl: fetchImpl as unknown as typeof fetch });
    const r = await loadOnce(art, { client, runId: 'r', createObjectURL: () => 'blob:fake' });
    expect(r).toMatchObject({ state: 'READY', src: 'blob:fake', isBlob: true });
    expect(calls[0].auth).toBe('Bearer TOK');
    expect(calls[0].url.startsWith('https://nas.example.org/')).toBe(true);
  });
  it('bearer_fetch to any other host is blocked and sends no request (no token leak)', async () => {
    const fetchImpl = vi.fn();
    const client = new ApiClient({ origin: 'https://nas.example.org', token: 'TOK', fetchImpl: fetchImpl as unknown as typeof fetch });
    for (const url of ['https://evil.example.com/a.png', 'http://nas.example.org/a.png', 'https://nas.example.org.evil.com/a.png', '//evil.example.com/a.png']) {
      const r = await loadOnce({ ...art, url }, { client, runId: 'r' });
      expect(r.state).toBe('LOAD_FAILED');
      expect(r.detail).toMatch(/never sent/);
    }
    expect(fetchImpl).not.toHaveBeenCalled();
  });
  it('signed_url is used as a plain image URL with no request from the client (no credentials)', async () => {
    const fetchImpl = vi.fn();
    const client = new ApiClient({ origin: 'https://nas.example.org', token: 'TOK', fetchImpl: fetchImpl as unknown as typeof fetch });
    const r = await loadOnce({ ...art, auth_mode: 'signed_url', url: 'https://cdn.other.example/img.png?sig=abc' }, { client, runId: 'r' });
    expect(r).toEqual({ state: 'READY', src: 'https://cdn.other.example/img.png?sig=abc' });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
  it.each([[401, 'ACCESS_DENIED'], [403, 'ACCESS_DENIED'], [404, 'MISSING'], [409, 'NOT_READY'], [410, 'EXPIRED'], [500, 'LOAD_FAILED']])('HTTP %i maps to %s', (status, state) => {
    expect(stateFromStatus(status)).toBe(state);
  });
  it('bearer 403 / 404 / 409 become ACCESS_DENIED / MISSING / NOT_READY; network failure becomes LOAD_FAILED', async () => {
    const mk = (f: () => Promise<Response>) => new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: f as unknown as typeof fetch });
    expect((await loadOnce(art, { client: mk(async () => json(403, { code: 'A', message: 'm' })), runId: 'r' })).state).toBe('ACCESS_DENIED');
    expect((await loadOnce(art, { client: mk(async () => json(404, { code: 'A', message: 'm' })), runId: 'r' })).state).toBe('MISSING');
    expect((await loadOnce(art, { client: mk(async () => json(409, { code: 'A', message: 'm' })), runId: 'r' })).state).toBe('NOT_READY');
    expect((await loadOnce(art, { client: mk(async () => { throw new TypeError('x'); }), runId: 'r' })).state).toBe('LOAD_FAILED');
  });
  it('EXPIRED triggers the refresh endpoint exactly once and then shows EXPIRED if still expired', async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (u: string, init: RequestInit) => {
      calls.push(`${init.method ?? 'GET'} ${u}`);
      return json(200, { ...art, auth_mode: 'signed_url', state: 'EXPIRED', expires_at: '2000-01-01T00:00:00Z' });
    });
    const client = new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: fetchImpl as unknown as typeof fetch });
    const r = await loadArtifact({ ...art, state: 'EXPIRED' }, { client, runId: 'r' });
    expect(r.state).toBe('EXPIRED');
    expect(calls).toEqual(['POST /api/v1/runs/r/artifacts/a1/refresh']);
  });
  it('EXPIRED followed by a successful refresh shows the new signed URL', async () => {
    const fetchImpl = vi.fn(async () => json(200, { ...art, auth_mode: 'signed_url', url: 'https://cdn.other.example/new.png?sig=2', state: 'READY', expires_at: '2999-01-01T00:00:00Z' }));
    const client = new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: fetchImpl as unknown as typeof fetch });
    const r = await loadArtifact({ ...art, auth_mode: 'signed_url', expires_at: '2000-01-01T00:00:00Z' }, { client, runId: 'r' });
    expect(r).toEqual({ state: 'READY', src: 'https://cdn.other.example/new.png?sig=2' });
  });
  it('refresh failures end in ACCESS_DENIED / EXPIRED, never in an endless state', async () => {
    const mk = (status: number) => new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: (async () => json(status, { code: 'x', message: 'm' })) as unknown as typeof fetch });
    expect((await loadArtifact({ ...art, state: 'EXPIRED' }, { client: mk(403), runId: 'r' })).state).toBe('ACCESS_DENIED');
    expect((await loadArtifact({ ...art, state: 'EXPIRED' }, { client: mk(500), runId: 'r' })).state).toBe('EXPIRED');
  });
  it('isExpiredAt and sampleRecall (N/A when gt = 0)', () => {
    expect(isExpiredAt('2000-01-01T00:00:00Z')).toBe(true);
    expect(isExpiredAt('2999-01-01T00:00:00Z')).toBe(false);
    expect(isExpiredAt(null)).toBe(false);
    expect(sampleRecall({ tp: 3, gt: 4 })).toBe(0.75);
    expect(sampleRecall({ tp: 0, gt: 0 })).toBeNull();
    expect(sampleRecall({ tp: 2, fp: 1 })).toBeNull();
  });
});

describe('Artifact.matching (contract shape: counts {tp,fp,fn,gt})', () => {
  // exact shape returned by the live server for a PILOT_SMOKE evaluation (integration regression: the UI used to
  // read top-level tp/fp/fn/gt and showed "— / — / — / —" plus "N/A" for a sample with 32 ground-truth boxes)
  const server = {
    boxes_drawn: 32, checkpoint_sha256: '16bb972a', conf: 0.25, iou: 0.5, render_scale: 1.0, rule: 'greedy_conf_desc_iou_v1',
    counts: { fn: 32, fp: 0, gt: 32, kept: 0, matched_gt: 0, tp: 0 }, evaluation_hash: 'sha256:9c9a', sample_precision: null,
    sample_recall: 0.0, sample_rule: 'first_n_sorted_sample_ids',
  };
  it('reads nested counts, rule and thresholds', () => {
    const m = parseMatching(server);
    expect(m.counts).toEqual({ tp: 0, fp: 0, fn: 32, gt: 32 });
    expect(m.rule).toBe('greedy_conf_desc_iou_v1');
    expect([m.iou, m.conf]).toEqual([0.5, 0.25]);
    expect(sampleRecall(m.counts, m.sampleRecall)).toBe(0);
  });
  it('missing or malformed matching gives empty counts and N/A recall (never 0)', () => {
    expect(parseMatching(undefined).counts).toEqual({});
    expect(parseMatching({ counts: { tp: 'x' } }).counts).toEqual({});
    expect(sampleRecall(parseMatching(null).counts)).toBeNull();
  });
});
