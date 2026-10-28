/**
 * SYNTHETIC mock of the MBNAS API for browser tests (page.route). Values come from tests/fixtures.ts and are
 * not measured results. Never imported by src/, so it is not part of the production bundle.
 */
import { createHash } from 'node:crypto';
import { test as pwTest, type Page, type Route } from '@playwright/test';
import type { Artifact, Candidate, CandidateDetail, ModelConfigRevisionList, Snapshot } from '../src/api/types';
import { archiveResponder, newFixtureState, type FixtureState } from '../tests/archive_fixtures';
import { syntheticStructureReport } from '../tests/detail_fixtures';
import { artifactSet, cand, fixtureHealth, fixturePresets, fixtureSnapshot, fixtureValidateOk, PROTOCOL_HASH, RUN_ID } from '../tests/fixtures';

export const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
export const TOKEN = 'synthetic-e2e-token';
export const CDN = 'https://cdn.synthetic.test';

export interface Seen {
  method: string;
  url: string;
  auth: string | undefined;
  idem: string | undefined;
  body: unknown;
}

export interface MockState {
  seen: Seen[];
  runStatus: Snapshot['run']['status'];
  failHealth: boolean;
  /** override of /health fields (e.g. a long commit_sha) */
  healthOverride?: Record<string, unknown>;
  runResponses: number[]; // statuses answered for successive POST /runs
  eventScript: string[]; // SSE bodies for successive event requests (after the script ends: empty)
  snapshotLast: number;
  extraCandidates: Candidate[];
  /** override for POST /protocols/validate: answered instead of the happy 200 */
  validateReply?: { status: number; body: unknown };
  /** optional per-test handler tried after the auth check; return true when it answered the route */
  custom?: (route: Route, path: string) => Promise<boolean> | boolean;
  /** state of the synthetic NAS archive API (tests/archive_fixtures.ts); created on first use */
  archive?: FixtureState;
  /**
   * Candidate detail documents served at GET /runs/{id}/candidates/{candidate_id} (unknown ids stay 404).
   * Synthetic: build them with tests/detail_fixtures.ts.
   */
  details?: Record<string, CandidateDetail>;
  /** Mutates the synthetic snapshot before it is served (e.g. generated_candidates for an evaluation-OFF run). */
  snapshotPatch?: (s: Snapshot) => Snapshot;
  /**
   * The resolved protocol served as run.resolved_protocol. With `captureRunProtocol` it is set from the body of the
   * last POST /runs (the mock "server" resolves a run exactly as requested), so a reload restores what was submitted.
   */
  runProtocol?: Record<string, unknown>;
  captureRunProtocol?: boolean;
  /** Applied to the echoed protocol of POST /protocols/validate (the mock "server" resolving defaults, e.g. duplicate_scope). */
  resolve?: (protocol: Record<string, unknown>) => Record<string, unknown>;
  /** Run id answered by POST /training-runs (default synthetic-training-run-0001); every request is recorded in `seen`. */
  trainingRunId?: string;
  /**
   * POST /uploads/model-config (F5a): the mock gateway stores the raw body content-addressed (sha256 -> bytes, also the
   * stored `uploads` record here) and answers {upload_id, source_sha256, bytes}; `uploadReply` overrides the answer.
   */
  uploads?: Record<string, { name: string; bytes: Buffer }>;
  uploadReply?: { status: number; body: unknown };
  /** POST /structure-check answer (default: the synthetic mbnas-structure/2 report of tests/detail_fixtures.ts). */
  structureReply?: { status: number; body: unknown };
  /** GET .../candidates/{cid}/config-revisions by candidate id; absent ids answer 404 NOT_FOUND (a gateway without the route). */
  revisions?: Record<string, ModelConfigRevisionList>;
  /** GET .../config-revisions/{revision_id}/yaml stored bytes (X-Artifact-Sha256 = sha256 of the bytes unless `sha256` is given). */
  revisionBytes?: Record<string, { bytes: string; sha256?: string }>;
  /**
   * Gateway store objects served at GET /runs/{id}/artifacts/{object_id} (DD-A51 c: checkpoints by their ck- id or by the
   * store artifact_id): the bytes with X-Artifact-Sha256 (sha256 of the bytes unless `sha256` is given), or an Error answer
   * such as 404 ARTIFACT_NOT_READY (bytes not transferred yet). Ids not listed keep the default artifact handling.
   */
  storeObjects?: Record<string, StoreObject>;
}

export type StoreObject = { bytes: string | Buffer; sha256?: string } | { status: number; code: string; message?: string };

/** hex sha256 of bytes (the mock gateway's content address). */
export const sha256Of = (data: string | Buffer): string => createHash('sha256').update(data).digest('hex');

export function stateOf(over: Partial<MockState> = {}): MockState {
  return { seen: [], runStatus: 'RUNNING', failHealth: false, runResponses: [200], eventScript: [], snapshotLast: 5, extraCandidates: [], ...over };
}

/** Candidate with one artifact per state/auth mode to exercise every UI state. */
export function stateCandidate(): Candidate {
  const id = 'synthetic-ev-artstates';
  const mk = (kind: Artifact['kind'], over: Partial<Artifact>): Artifact => ({ id: `art-${kind}`, kind, url: `/api/v1/runs/${RUN_ID}/artifacts/art-${kind}`, auth_mode: 'bearer_fetch', sample_id: 'synthetic-sample-009', evaluation_id: id, state: 'READY', matching: { counts: { tp: 0, fp: 1, fn: 0, gt: 0 }, sample_recall: null, rule: 'greedy_conf_desc_iou_v1', iou: 0.5, conf: 0.25 } as Artifact['matching'], ...over });
  return cand({
    id,
    candidate_id: 'synthetic-cand-artstates',
    name: 'artifact-states-candidate',
    generation: 0,
    artifacts: [mk('gt', {}), mk('tp', {}), mk('fn', { auth_mode: 'signed_url', url: `${CDN}/expired.png?sig=old`, expires_at: '2000-01-01T00:00:00Z' }), mk('fp', { state: 'NOT_READY' })],
  });
}

export async function installMock(page: Page, st: MockState): Promise<void> {
  const json = (route: Route, status: number, body: unknown): Promise<void> => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  const snapshot = (): Snapshot => {
    const s = fixtureSnapshot(st.runStatus, st.snapshotLast);
    if (st.runProtocol) s.run = { ...s.run, resolved_protocol: st.runProtocol as typeof s.run.resolved_protocol };
    const happy = cand({ id: 'synthetic-ev-happy', candidate_id: 'synthetic-cand-happy', name: 'happy-artifacts-candidate', generation: 0, latency_ms: 44, artifacts: artifactSet('synthetic-ev-happy', 'bearer_fetch').map((a, i) => (i % 2 === 0 ? { ...a, id: `happy-${a.kind}`, url: `/api/v1/runs/${RUN_ID}/artifacts/happy-${a.kind}` } : { ...a, id: `happy-${a.kind}`, auth_mode: 'signed_url' as const, url: `${CDN}/${a.kind}.png?sig=ok` })) });
    s.candidates = [...s.candidates, happy, stateCandidate(), ...st.extraCandidates];
    return st.snapshotPatch ? st.snapshotPatch(s) : s;
  };

  // signed-URL host: must never see our token
  await page.route(`${CDN}/**`, async (route) => {
    const req = route.request();
    st.seen.push({ method: req.method(), url: req.url(), auth: req.headers()['authorization'], idem: undefined, body: undefined });
    if (req.url().includes('expired.png')) return route.fulfill({ status: 410, body: 'expired' });
    if (req.url().includes('broken.png')) return route.fulfill({ status: 500, body: 'broken' });
    return route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
  });

  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    const headers = req.headers();
    let body: unknown;
    try {
      body = req.postData() ? JSON.parse(req.postData() as string) : undefined;
    } catch {
      body = undefined; // a raw (non-JSON) body, e.g. an uploaded YAML: read it with req.postDataBuffer()
    }
    st.seen.push({ method: req.method(), url: p + url.search, auth: headers['authorization'], idem: headers['idempotency-key'], body });
    if (headers['authorization'] !== `Bearer ${TOKEN}`) return json(route, 401, { code: 'AUTH_REQUIRED', message: 'synthetic: bad token' });
    if (p === '/api/v1/health') return st.failHealth ? route.abort('connectionrefused') : json(route, 200, { ...fixtureHealth(), ...(st.healthOverride ?? {}) });
    if (p === '/api/v1/protocols/presets') return json(route, 200, { presets: fixturePresets() });
    if (st.custom && (await st.custom(route, p))) return;
    if (p.startsWith('/api/v1/archive/')) {
      st.archive ??= newFixtureState();
      const a = archiveResponder(p, url.search, st.archive);
      if (a) return a.png ? route.fulfill({ status: 200, contentType: 'image/png', body: PNG }) : json(route, a.status, a.json);
    }
    if (p === '/api/v1/protocols/validate' && st.validateReply) return json(route, st.validateReply.status, st.validateReply.body);
    if (p === '/api/v1/protocols/validate') {
      const requested = (body as { protocol: Record<string, unknown> }).protocol;
      return json(route, 200, fixtureValidateOk(st.resolve ? st.resolve(requested) : requested));
    }
    if (p === '/api/v1/uploads/model-config' && req.method() === 'POST') {
      if (st.uploadReply) return json(route, st.uploadReply.status, st.uploadReply.body);
      const name = url.searchParams.get('filename') ?? headers['x-filename'] ?? '';
      const bytes = req.postDataBuffer() ?? Buffer.alloc(0);
      if (!/\.ya?ml$/i.test(name)) return json(route, 422, { code: 'UPLOAD_TYPE_UNSUPPORTED', message: 'model config must be a .yaml or .yml file', location: 'gateway' });
      if (bytes.length === 0) return json(route, 422, { code: 'UPLOAD_EMPTY', message: 'model config is empty', location: 'gateway' });
      if (bytes.length > 1024 * 1024) return json(route, 413, { code: 'PAYLOAD_TOO_LARGE', message: 'model config larger than 1048576 bytes', location: 'gateway' });
      const hex = sha256Of(bytes);
      st.uploads = { ...(st.uploads ?? {}), [hex]: { name, bytes } };
      return json(route, 200, { upload_id: `upl-${hex}`, source_sha256: `sha256:${hex}`, bytes: bytes.length });
    }
    if (p === '/api/v1/structure-check' && req.method() === 'POST') {
      const b = (body ?? {}) as { upload_id?: string };
      if (b.upload_id && !st.uploads?.[b.upload_id.replace(/^upl-/, '')]) return json(route, 404, { code: 'UPLOAD_NOT_FOUND', message: 'no upload with this upload_id', location: 'gateway' });
      if (st.structureReply) return json(route, st.structureReply.status, st.structureReply.body);
      // 1.5.0: a user structure check reports the sha256 of the YAML bytes it checked (the stored upload here)
      return json(route, 200, { ...syntheticStructureReport(), evidence_source: 'executed_structure_check', source_sha256: b.upload_id ? b.upload_id.replace(/^upl-/, '') : null });
    }
    if (p === '/api/v1/training-runs' && req.method() === 'POST') {
      const b = body as { source?: Record<string, unknown>; mode?: string } | undefined;
      const src = b?.source ?? {};
      // like the gateway: an upload_id becomes the stored bytes' sha256; the answer names the model and its source sha256
      const hex = typeof src.upload_id === 'string' ? src.upload_id.replace(/^upl-/, '') : null;
      if (hex && !st.uploads?.[hex]) return json(route, 404, { code: 'UPLOAD_NOT_FOUND', message: 'no upload with this upload_id', location: 'gateway' });
      const source = hex ? { kind: 'user', model_name: src.model_name ?? null, source_sha256: `sha256:${hex}` } : src;
      return json(route, 200, { run_id: st.trainingRunId ?? 'synthetic-training-run-0001', status: 'QUEUED', protocol_hash: PROTOCOL_HASH, created: true, candidate_id: 'synthetic-cand-trained', source, mode: b?.mode });
    }
    if (p === '/api/v1/runs' && req.method() === 'POST') {
      const status = st.runResponses.shift() ?? 200;
      if (status === 200 && st.captureRunProtocol) st.runProtocol = (body as { protocol: Record<string, unknown> }).protocol;
      if (status === 200) return json(route, 200, { run_id: RUN_ID, status: 'QUEUED' });
      if (status === 0) return route.abort('timedout');
      return json(route, status, { code: status === 409 ? 'IDEMPOTENCY_CONFLICT' : 'ERR', message: `synthetic ${status}` });
    }
    if (p === '/api/v1/runs') return json(route, 200, { runs: [snapshot().run] });
    if (p === `/api/v1/runs/${RUN_ID}/snapshot`) return json(route, 200, snapshot());
    if (p === `/api/v1/runs/${RUN_ID}/events`) {
      const next = st.eventScript.shift() ?? '';
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: next });
    }
    const revs = /\/runs\/[^/]+\/candidates\/([^/]+)\/config-revisions$/.exec(p);
    if (revs && req.method() === 'GET') {
      const list = st.revisions?.[decodeURIComponent(revs[1])];
      return list ? json(route, 200, list) : json(route, 404, { code: 'NOT_FOUND', message: 'Not Found', location: 'gateway' });
    }
    const yaml = /\/runs\/[^/]+\/candidates\/[^/]+\/config-revisions\/([^/]+)\/yaml$/.exec(p);
    if (yaml && req.method() === 'GET') {
      const hit = st.revisionBytes?.[decodeURIComponent(yaml[1])];
      if (!hit) return json(route, 404, { code: 'ARTIFACT_NOT_READY', message: 'revision bytes not stored', location: 'store' });
      return route.fulfill({ status: 200, contentType: 'application/x-yaml', headers: { 'x-artifact-sha256': hit.sha256 ?? sha256Of(hit.bytes) }, body: hit.bytes });
    }
    const detail = /\/runs\/[^/]+\/candidates\/([^/]+)$/.exec(p);
    if (detail && req.method() === 'GET') {
      const d = st.details?.[decodeURIComponent(detail[1])];
      if (d) return json(route, 200, d);
    }
    if (p.includes('/candidates/')) return json(route, 404, { code: 'NOT_FOUND', message: 'synthetic: detail not mocked' });
    if (p.endsWith('/refresh')) return json(route, 200, { id: 'art-fn', kind: 'fn', url: `${CDN}/expired.png?sig=new`, auth_mode: 'signed_url', state: 'EXPIRED', expires_at: '2000-01-01T00:00:00Z' });
    const storeId = /\/runs\/[^/]+\/artifacts\/([^/]+)$/.exec(p);
    const stored = storeId && req.method() === 'GET' ? st.storeObjects?.[decodeURIComponent(storeId[1])] : undefined;
    if (stored && 'code' in stored) return json(route, stored.status, { code: stored.code, message: stored.message ?? `synthetic ${stored.code}`, location: 'store' });
    if (stored) return route.fulfill({ status: 200, contentType: 'application/octet-stream', headers: { 'x-artifact-sha256': stored.sha256 ?? sha256Of(stored.bytes) }, body: stored.bytes });
    const art = /\/artifacts\/art-(\w+)$/.exec(p);
    if (art) {
      if (art[1] === 'gt') return json(route, 403, { code: 'ACCESS_DENIED', message: 'synthetic: forbidden' });
      if (art[1] === 'tp') return json(route, 404, { code: 'NOT_FOUND', message: 'synthetic: missing' });
      return route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
    }
    if (/\/artifacts\/happy-\w+$/.test(p)) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
    if (p === `/api/v1/runs/${RUN_ID}/export`) return route.fulfill({ status: 200, contentType: 'application/zip', body: Buffer.from('PK\u0005\u0006') });
    if (/\/(pause|resume|cancel)$/.test(p)) {
      st.runStatus = p.endsWith('pause') ? 'PAUSE_REQUESTED' : p.endsWith('resume') ? 'RESUMING' : 'CANCEL_REQUESTED';
      return json(route, 200, { ...snapshot().run });
    }
    return json(route, 404, { code: 'NOT_FOUND', message: `unmocked ${p}` });
  });
}

export function sse(events: { seq: number; type: string; payload: unknown }[]): string {
  return events.map((e) => `id: ${e.seq}\nevent: ${e.type}\ndata: ${JSON.stringify({ seq: e.seq, type: e.type, run_id: RUN_ID, payload: e.payload })}\n\n`).join('');
}

/** Connect (token only, same origin) and load the synthetic run; returns when the Monitor is available. */
export async function connectAndLoad(page: Page): Promise<void> {
  await page.goto('/#/server');
  await page.getByLabel(/Bearer token/).fill(TOKEN);
  await page.getByTestId('connect-btn').click();
  await page.getByText('status: ok').waitFor();
  await page.getByTestId('run-id-input').fill(RUN_ID);
  await page.getByTestId('load-run-btn').click();
  await page.getByTestId('pause-btn').waitFor();
}

export async function openScreen(page: Page, id: 'archive' | 'compare' | 'monitor' | 'settings' | 'lineage' | 'history' | 'server'): Promise<void> {
  const menu = page.getByTestId('menu-btn');
  if (await menu.isVisible()) {
    if ((await menu.getAttribute('aria-expanded')) !== 'true') await menu.click();
  }
  await page.getByTestId(`nav-${id}`).click();
}

export { PROTOCOL_HASH, RUN_ID };

/**
 * Evidence capture with a bounded retry. Chromium can answer `Page.captureScreenshot` with a protocol error when the
 * host is under load; an evidence screenshot must not fail a behavioural assertion. Up to 3 attempts, the last one
 * falling back to a viewport capture; a persistent failure is annotated on the test instead of thrown.
 */
export async function captureEvidence(page: Page, path: string, fullPage: boolean): Promise<void> {
  let last: unknown;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      await page.screenshot({ path, fullPage: attempt < 3 ? fullPage : false });
      return;
    } catch (err) {
      last = err;
      await page.waitForTimeout(250 * attempt);
    }
  }
  pwTest.info().annotations.push({ type: 'evidence-capture-failed', description: `${path}: ${String(last)}` });
}
