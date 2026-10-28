/** Synthetic in-memory API for component tests: routes fetch() calls to fixtures. Not shipped in the bundle. */
import { vi } from 'vitest';
import type { CandidateDetail, ModelConfigRevisionList, Snapshot } from '../src/api/types';
import { FINAL_RUN_ID, fixtureFinalSnapshot, fixtureHealth, fixturePresets, fixtureSnapshot, fixtureValidateOk, PROTOCOL_HASH, RUN_ID } from './fixtures';

export interface MockCall {
  method: string;
  path: string;
  headers: Record<string, string>;
  /** parsed JSON body; undefined for a raw (non-JSON) body */
  body: unknown;
  /** the request body as sent when it is not a JSON string (e.g. the File of POST /uploads/model-config) */
  raw?: Blob | ArrayBuffer | Uint8Array | null;
}

export interface MockOptions {
  snapshot?: Snapshot;
  validateValid?: boolean;
  runStatus?: number;
  onRun?: (call: MockCall) => Response | Promise<Response>;
  /** answers POST /runs/{id}/final-retrain (default: 200 RunCreated for the synthetic final run) */
  onFinalRetrain?: (call: MockCall) => Response | Promise<Response>;
  /** extra snapshots by run id (the final run is always available) */
  snapshots?: Record<string, Snapshot>;
  /** candidate detail documents by candidate_id (GET /runs/{id}/candidates/{cid}); unknown ids stay 404. See tests/detail_fixtures.ts. */
  details?: Record<string, CandidateDetail>;
  /** answers POST /uploads/model-config (default: 404 NOT_FOUND, as a gateway without the route) */
  onUpload?: (call: MockCall) => Response | Promise<Response>;
  /** GET .../candidates/{cid}/config-revisions by candidate_id (unknown ids: 404 NOT_FOUND, as a gateway without the route) */
  revisions?: Record<string, ModelConfigRevisionList>;
  /** GET .../config-revisions/{revision_id}/yaml: stored bytes by revision id (served with X-Artifact-Sha256 = `sha256` when given) */
  revisionBytes?: Record<string, { bytes: string | Uint8Array; sha256?: string }>;
}

export function installMockApi(opts: MockOptions = {}): { calls: MockCall[]; restore: () => void } {
  const calls: MockCall[] = [];
  const original = globalThis.fetch;
  const json = (status: number, body: unknown): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  const impl = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const url = new URL(typeof input === 'string' ? input : input.toString(), 'http://localhost');
    const rawBody = init.body !== undefined && init.body !== null && typeof init.body !== 'string' ? (init.body as Blob | ArrayBuffer | Uint8Array) : null;
    const call: MockCall = { method: init.method ?? 'GET', path: url.pathname + url.search, headers: { ...(init.headers as Record<string, string>) }, body: typeof init.body === 'string' && init.body ? JSON.parse(init.body) : undefined, raw: rawBody };
    calls.push(call);
    const p = url.pathname;
    if (p === '/api/v1/health') return json(200, fixtureHealth());
    if (p === '/api/v1/protocols/presets') return json(200, { presets: fixturePresets() });
    if (p === '/api/v1/protocols/validate') {
      const protocol = (call.body as { protocol: Record<string, unknown> }).protocol;
      return opts.validateValid === false
        ? json(200, { valid: false, errors: [{ field: 'console.paths.train_root', message: 'synthetic: not found', code: 'PATH_NOT_FOUND' }], protocol_hash: null, resolved: null, capabilities: {} })
        : json(200, fixtureValidateOk(protocol));
    }
    if (p === '/api/v1/runs' && call.method === 'POST') return opts.onRun ? opts.onRun(call) : json(200, { run_id: RUN_ID, status: 'QUEUED' });
    if (p === '/api/v1/runs') return json(200, { runs: [fixtureSnapshot().run] });
    if (p === `/api/v1/runs/${RUN_ID}/final-retrain` && call.method === 'POST') {
      return opts.onFinalRetrain ? opts.onFinalRetrain(call) : json(200, { run_id: FINAL_RUN_ID, status: 'QUEUED', parent_run_id: RUN_ID, finalist_count: 2 });
    }
    const snap = /^\/api\/v1\/runs\/([^/]+)\/snapshot$/.exec(p);
    if (snap) {
      const s = snap[1] === RUN_ID ? (opts.snapshot ?? fixtureSnapshot()) : snap[1] === FINAL_RUN_ID ? (opts.snapshots?.[FINAL_RUN_ID] ?? fixtureFinalSnapshot()) : opts.snapshots?.[snap[1]];
      return s ? json(200, s) : json(404, { code: 'NOT_FOUND', message: 'synthetic: no such run' });
    }
    if (p === `/api/v1/runs/${RUN_ID}/events` || p === `/api/v1/runs/${FINAL_RUN_ID}/events`) {
      const sig = init.signal as AbortSignal | undefined;
      const stream = new ReadableStream<Uint8Array>({
        start(c) {
          sig?.addEventListener('abort', () => {
            try {
              c.close();
            } catch {
              /* already closed */
            }
          });
        },
      });
      return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } });
    }
    if (p === '/api/v1/uploads/model-config' && call.method === 'POST') return opts.onUpload ? opts.onUpload(call) : json(404, { code: 'NOT_FOUND', message: 'synthetic: no upload route' });
    const revs = /^\/api\/v1\/runs\/[^/]+\/candidates\/([^/]+)\/config-revisions$/.exec(p);
    if (revs && call.method === 'GET') {
      const list = opts.revisions?.[decodeURIComponent(revs[1])];
      return list ? json(200, list) : json(404, { code: 'NOT_FOUND', message: 'synthetic: no revision route' });
    }
    const yaml = /^\/api\/v1\/runs\/[^/]+\/candidates\/[^/]+\/config-revisions\/([^/]+)\/yaml$/.exec(p);
    if (yaml && call.method === 'GET') {
      const hit = opts.revisionBytes?.[decodeURIComponent(yaml[1])];
      if (!hit) return json(404, { code: 'ARTIFACT_NOT_READY', message: 'synthetic: bytes not stored' });
      return new Response(typeof hit.bytes === 'string' ? hit.bytes : (hit.bytes as unknown as BodyInit), { status: 200, headers: { 'content-type': 'application/x-yaml', ...(hit.sha256 ? { 'x-artifact-sha256': hit.sha256 } : {}) } });
    }
    const detail = /^\/api\/v1\/runs\/[^/]+\/candidates\/([^/]+)$/.exec(p);
    if (detail && call.method === 'GET' && opts.details?.[decodeURIComponent(detail[1])]) return json(200, opts.details[decodeURIComponent(detail[1])]);
    if (p.includes('/candidates/')) return json(404, { code: 'NOT_FOUND', message: 'synthetic: no detail endpoint data' });
    return json(404, { code: 'NOT_FOUND', message: `unmocked ${p}` });
  };
  globalThis.fetch = vi.fn(impl) as unknown as typeof fetch;
  return { calls, restore: () => { globalThis.fetch = original; } };
}

export { PROTOCOL_HASH, RUN_ID };
