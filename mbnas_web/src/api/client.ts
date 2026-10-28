/**
 * Typed fetch wrapper for the MBNAS server (REST prefix /api/v1).
 *
 * Origin rules: the configured origin is either '' (same origin: in production the gateway serves this bundle and the API
 * together; in development the Vite dev proxy forwards /api to the local gateway) or an explicit
 * `https://host[:port]`; plain `http://` is accepted only for localhost / 127.0.0.1 / [::1].
 * The bearer token is passed in by the caller (React state), is never persisted, never placed in a URL,
 * and is sent only to the configured API origin (see `isApiOrigin`).
 */
import type {
  ArchiveBaselineList,
  ArchiveCandidateDetail,
  ArchiveCandidateList,
  ArchiveCompare,
  ArchiveConfirm,
  ArchiveFidelity,
  ArchiveGenerationDetail,
  ArchiveGenerationList,
  ArchiveLive,
  ArchivePareto,
  ArchivePlotKind,
  ArchiveRun,
  ArchiveRunList,
} from '../archive/types';
import type {
  ApiErrorBody,
  Artifact,
  BlockCatalogTable,
  CandidateDetail,
  FinalRetrainRequest,
  Health,
  Preset,
  RegistryEntry,
  Run,
  RunCreated,
  RunRequest,
  Snapshot,
  StructureCheckRequest,
  StructureReport,
  ValidateResponse,
  TrainingRequest,
  TrainingRunCreated,
  ModelConfigRevisionList,
  RegistryStructure,
  UploadResponse,
} from './types';

export type { RegistryStructure, StructureCheckRequest };

/** Upload limit of POST /uploads/model-config (contract 1.5.0 default `upload_max_bytes`: 1 MiB). The server is authoritative. */
export const MODEL_CONFIG_MAX_BYTES = 1024 * 1024;
/** File name rule of POST /uploads/model-config: .yaml or .yml (case-insensitive). */
export const MODEL_CONFIG_NAME = /\.ya?ml$/i;

/** Bytes of one stored configuration revision (GET .../config-revisions/{revision_id}/yaml) with the sha256 the server sent. */
export interface RevisionBytes {
  blob: Blob;
  /** `X-Artifact-Sha256` header (hex, no prefix); null when the server sent none. */
  headerSha256: string | null;
}

export type ApiErrorKind = 'network' | 'timeout' | 'http' | 'parse' | 'config';

/** Failure of a request. `retryable` is true for network errors, timeouts and 5xx answers. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly body: ApiErrorBody | null;
  readonly code: string | null;

  constructor(kind: ApiErrorKind, message: string, status: number | null = null, body: ApiErrorBody | null = null) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.body = body;
    this.code = body?.code ?? null;
  }

  get retryable(): boolean {
    return this.kind === 'network' || this.kind === 'timeout' || (this.kind === 'http' && (this.status ?? 0) >= 500);
  }
}

export type OriginCheck = { ok: true; origin: string } | { ok: false; reason: string };

const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

/** Validate/normalise the API origin typed by the user. '' means same-origin. */
export function normalizeOrigin(input: string): OriginCheck {
  const raw = input.trim();
  if (raw === '') return { ok: true, origin: '' };
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return { ok: false, reason: 'Not a valid origin. Use https://host[:port] or leave empty for same-origin.' };
  }
  if (u.username || u.password) return { ok: false, reason: 'Credentials in the origin are not allowed.' };
  if (u.pathname !== '/' || u.search || u.hash) return { ok: false, reason: 'Enter the origin only (scheme://host[:port]), without path, query or fragment.' };
  if (u.protocol === 'https:') return { ok: true, origin: u.origin };
  if (u.protocol === 'http:' && LOOPBACK.has(u.hostname)) return { ok: true, origin: u.origin };
  return { ok: false, reason: 'Only https:// origins are allowed (http:// only for localhost / 127.0.0.1).' };
}

export interface ClientOptions {
  /** '' = same-origin; otherwise a normalised origin. */
  origin: string;
  /** Bearer token held in memory only. */
  token: string;
  /** Origin of the page; used to resolve relative URLs when origin is ''. Defaults to window.location.origin. */
  pageOrigin?: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
}

export interface RequestOptions {
  method?: 'GET' | 'POST';
  /** JSON body (serialised with Content-Type application/json). */
  body?: unknown;
  /** Raw body sent unchanged (e.g. an uploaded file) with `contentType`; exclusive with `body`. */
  rawBody?: Blob | ArrayBuffer | Uint8Array | string;
  contentType?: string;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Keep `signal` linked to the response body after headers arrive (used for SSE). */
  stream?: boolean;
}

function defaultPageOrigin(): string {
  return typeof window !== 'undefined' ? window.location.origin : 'http://localhost';
}

export class ApiClient {
  readonly origin: string;
  private readonly token: string;
  private readonly pageOrigin: string;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;

  constructor(opts: ClientOptions) {
    this.origin = opts.origin;
    this.token = opts.token;
    this.pageOrigin = opts.pageOrigin ?? defaultPageOrigin();
    this.fetchImpl = opts.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
    this.timeoutMs = opts.timeoutMs ?? 20000;
  }

  /** The origin all authenticated requests go to (the page origin for same-origin mode). */
  get effectiveOrigin(): string {
    return this.origin || this.pageOrigin;
  }

  get hasToken(): boolean {
    return this.token.trim() !== '';
  }

  url(path: string): string {
    return `${this.origin}${path}`;
  }

  /** True when `url` (absolute or relative) points at exactly the configured API origin. */
  isApiOrigin(url: string): boolean {
    try {
      return new URL(url, this.effectiveOrigin + '/').origin === new URL(this.effectiveOrigin).origin;
    } catch {
      return false;
    }
  }

  /** Resolve an artifact/refresh URL against the API origin. */
  resolve(url: string): string {
    return new URL(url, this.effectiveOrigin + '/').toString();
  }

  authHeaders(): Record<string, string> {
    return this.hasToken ? { Authorization: `Bearer ${this.token.trim()}` } : {};
  }

  /** Raw authenticated fetch with timeout; throws ApiError for transport failures and non-2xx answers. */
  async raw(path: string, opts: RequestOptions = {}): Promise<Response> {
    const url = /^https?:\/\//i.test(path) ? path : this.url(path);
    if (/^https?:\/\//i.test(path) && !this.isApiOrigin(path)) {
      throw new ApiError('config', 'Refusing to send credentials to a different origin.');
    }
    const ctrl = new AbortController();
    const ms = opts.timeoutMs ?? this.timeoutMs;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // race against a timer as well as aborting, so a fetch implementation that ignores abort cannot hang
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        ctrl.abort();
        reject(new ApiError('timeout', `Request timed out after ${ms} ms`));
      }, ms);
    });
    const onAbort = (): void => ctrl.abort();
    opts.signal?.addEventListener('abort', onAbort);
    const headers: Record<string, string> = { Accept: 'application/json', ...this.authHeaders(), ...(opts.headers ?? {}) };
    if (opts.body !== undefined && opts.rawBody !== undefined) throw new ApiError('config', 'A request has either a JSON body or a raw body, not both.');
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (opts.rawBody !== undefined) headers['Content-Type'] = opts.contentType ?? 'application/octet-stream';
    try {
      const res = await Promise.race([
        this.fetchImpl(url, {
          method: opts.method ?? 'GET',
          headers,
          body: opts.rawBody !== undefined ? (opts.rawBody as BodyInit) : opts.body === undefined ? undefined : JSON.stringify(opts.body),
          signal: ctrl.signal,
          credentials: 'omit',
          cache: 'no-store',
        }),
        timeout,
      ]);
      if (!res.ok) throw await this.httpError(res);
      return res;
    } catch (e) {
      if (e instanceof ApiError) throw e;
      if (opts.signal?.aborted) throw e;
      throw new ApiError('network', e instanceof Error ? e.message : 'Network error');
    } finally {
      clearTimeout(timer);
      if (!opts.stream) opts.signal?.removeEventListener('abort', onAbort);
    }
  }

  private async httpError(res: Response): Promise<ApiError> {
    let body: ApiErrorBody | null = null;
    let text = '';
    try {
      text = await res.text();
      const j: unknown = JSON.parse(text);
      if (j && typeof j === 'object') {
        const o = j as Record<string, unknown>;
        // FastAPI wraps errors as {detail: ...}; accept both the contract Error and detail-wrapped Error
        const inner = (o.detail && typeof o.detail === 'object' ? o.detail : o) as Record<string, unknown>;
        if (typeof inner.code === 'string' || typeof inner.message === 'string') {
          body = { code: String(inner.code ?? `HTTP_${res.status}`), message: String(inner.message ?? res.statusText), errors: inner.errors as ApiErrorBody['errors'], request_id: inner.request_id as string | undefined };
          // 1.5.0: which component produced the error (gateway | core | runner | store) and the relayed core request id
          if (inner.location === 'gateway' || inner.location === 'core' || inner.location === 'runner' || inner.location === 'store') body.location = inner.location;
          if (typeof inner.core_request_id === 'string') body.core_request_id = inner.core_request_id;
        }
      }
    } catch {
      /* non-JSON body */
    }
    const msg = body?.message ?? (text.slice(0, 200) || res.statusText || `HTTP ${res.status}`);
    return new ApiError('http', msg, res.status, body);
  }

  async json<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const res = await this.raw(path, opts);
    try {
      return (await res.json()) as T;
    } catch {
      throw new ApiError('parse', 'Response was not valid JSON', res.status);
    }
  }

  health(): Promise<Health> {
    return this.json<Health>('/api/v1/health');
  }

  async presets(): Promise<Preset[]> {
    return (await this.json<{ presets: Preset[] }>('/api/v1/protocols/presets')).presets;
  }

  async registry(): Promise<RegistryEntry[]> {
    return (await this.json<{ registry: RegistryEntry[] }>('/api/v1/registry')).registry;
  }

  validate(protocol: unknown): Promise<ValidateResponse> {
    return this.json<ValidateResponse>('/api/v1/protocols/validate', { method: 'POST', body: { protocol } });
  }

  /** Create a run. The Idempotency-Key header always equals body.idempotency_key. */
  createRun(req: RunRequest, timeoutMs?: number): Promise<RunCreated> {
    return this.json<RunCreated>('/api/v1/runs', { method: 'POST', body: req, headers: { 'Idempotency-Key': req.idempotency_key }, timeoutMs });
  }

  /** Create the separate FINAL_RETRAIN child run. The Idempotency-Key header always equals body.idempotency_key. */
  finalRetrain(parentRunId: string, req: FinalRetrainRequest, timeoutMs?: number): Promise<RunCreated> {
    return this.json<RunCreated>(`/api/v1/runs/${encodeURIComponent(parentRunId)}/final-retrain`, { method: 'POST', body: req, headers: { 'Idempotency-Key': req.idempotency_key }, timeoutMs });
  }

  trainingRun(req: TrainingRequest): Promise<TrainingRunCreated> {
    return this.json<TrainingRunCreated>('/api/v1/training-runs', { method: 'POST', body: req, headers: { 'Idempotency-Key': req.idempotency_key }, timeoutMs: 120000 });
  }

  /** Saved structure evidence of a registry model, or a declaration-only document (evidence_source declared_configuration). The server builds nothing for a GET. */
  registryStructure(modelId: string, signal?: AbortSignal): Promise<RegistryStructure> {
    return this.json<RegistryStructure>(`/api/v1/registry/${encodeURIComponent(modelId)}/structure`, { signal });
  }

  /** Explicit server-side CPU observation of a registry model (stored by the server). `inputSize` must be a multiple of 32. */
  registryStructureCheck(modelId: string, inputSize?: number): Promise<RegistryStructure> {
    return this.json<RegistryStructure>(`/api/v1/registry/${encodeURIComponent(modelId)}/structure-check`, { method: 'POST', body: inputSize === undefined ? {} : { input_size: inputSize }, timeoutMs: 180000 });
  }

  /** Static block design-origin table; the server imports nothing for it. In the contract since 1.4.2 (generated BlockCatalogTable). */
  blockCatalog(signal?: AbortSignal): Promise<BlockCatalogTable> {
    return this.json<BlockCatalogTable>('/api/v1/structure/block-catalog', { signal });
  }

  /**
   * Explicit CPU check of a user model YAML (nothing is stored): an `upload_id` from POST /uploads/model-config (the browser
   * path, 1.5.0) or a legacy server `model_path` under the allow-listed roots (advanced; owner-local deployments).
   */
  structureCheck(req: StructureCheckRequest): Promise<StructureReport> {
    return this.json<StructureReport>('/api/v1/structure-check', { method: 'POST', body: req, timeoutMs: 180000 });
  }

  /**
   * Upload a detector model configuration YAML as a raw body (the bytes are sent unchanged; the file name travels in
   * `?filename=` so any Unicode name is safe, unlike a header). The gateway stores the original bytes content-addressed and
   * answers {upload_id, source_sha256, bytes}; it never returns a server path.
   */
  uploadModelConfig(file: Blob, filename: string): Promise<UploadResponse> {
    return this.json<UploadResponse>(`/api/v1/uploads/model-config?filename=${encodeURIComponent(filename)}`, {
      method: 'POST', rawBody: file, contentType: 'application/x-yaml', timeoutMs: 60000,
    });
  }

  /** Configuration revisions of a candidate, oldest first (original / selected / executed / generated). */
  configRevisions(runId: string, candidateId: string, signal?: AbortSignal): Promise<ModelConfigRevisionList> {
    return this.json<ModelConfigRevisionList>(`/api/v1/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}/config-revisions`, { signal });
  }

  /** The stored bytes of one revision, unchanged (download and diff), with the server's X-Artifact-Sha256 header. */
  async configRevisionYaml(runId: string, candidateId: string, revisionId: string): Promise<RevisionBytes> {
    const res = await this.raw(`/api/v1/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}/config-revisions/${encodeURIComponent(revisionId)}/yaml`, { headers: { Accept: 'application/x-yaml, text/plain;q=0.9, */*;q=0.1' }, timeoutMs: 60000 });
    const header = res.headers.get('x-artifact-sha256');
    return { blob: await res.blob(), headerSha256: header ? header.trim().toLowerCase().replace(/^sha256:/, '') : null };
  }

  /** Bytes of one registered run artifact by id (never by path; digest-checked by the gateway store). */
  async artifactBytes(runId: string, artifactId: string): Promise<RevisionBytes> {
    const res = await this.raw(`/api/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`, { headers: { Accept: '*/*' }, timeoutMs: 600000 });
    const header = res.headers.get('x-artifact-sha256');
    return { blob: await res.blob(), headerSha256: header ? header.trim().toLowerCase().replace(/^sha256:/, '') : null };
  }

  async listRuns(limit = 50): Promise<Run[]> {
    return (await this.json<{ runs: Run[] }>(`/api/v1/runs?limit=${limit}`)).runs;
  }

  getRun(id: string): Promise<Run> {
    return this.json<Run>(`/api/v1/runs/${encodeURIComponent(id)}`);
  }

  snapshot(id: string, signal?: AbortSignal): Promise<Snapshot> {
    return this.json<Snapshot>(`/api/v1/runs/${encodeURIComponent(id)}/snapshot`, { signal });
  }

  candidateDetail(runId: string, candidateId: string): Promise<CandidateDetail> {
    return this.json<CandidateDetail>(`/api/v1/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}`);
  }

  control(runId: string, action: 'pause' | 'resume' | 'cancel', body: { reason?: string; idempotency_key: string }): Promise<Run> {
    return this.json<Run>(`/api/v1/runs/${encodeURIComponent(runId)}/${action}`, { method: 'POST', body });
  }

  refreshArtifact(runId: string, artifactId: string): Promise<Artifact> {
    return this.json<Artifact>(`/api/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}/refresh`, { method: 'POST' });
  }

  /** Export bundle as a Blob (fetched with the Authorization header; never a link with a token). */
  async exportRun(runId: string): Promise<Blob> {
    const res = await this.raw(`/api/v1/runs/${encodeURIComponent(runId)}/export`, { headers: { Accept: 'application/zip' }, timeoutMs: 120000 });
    return res.blob();
  }

  /** Open the SSE stream (fetch streaming so the Authorization header can be sent). */
  openEvents(runId: string, afterSeq: number, signal: AbortSignal): Promise<Response> {
    return this.raw(`/api/v1/runs/${encodeURIComponent(runId)}/events?after_seq=${afterSeq}`, {
      headers: { Accept: 'text/event-stream' },
      signal,
      stream: true,
      timeoutMs: 30000,
    });
  }

  // ---- NAS archive (read-only; design section 3). Path ids are always percent-encoded. ----------------------------

  archiveRuns(signal?: AbortSignal): Promise<ArchiveRunList> {
    return this.json<ArchiveRunList>('/api/v1/archive/runs', { signal });
  }

  archiveRun(id: string, signal?: AbortSignal): Promise<ArchiveRun> {
    return this.json<ArchiveRun>(`/api/v1/archive/runs/${encodeURIComponent(id)}`, { signal });
  }

  archiveBaselines(id: string, signal?: AbortSignal): Promise<ArchiveBaselineList> {
    return this.json<ArchiveBaselineList>(`/api/v1/archive/runs/${encodeURIComponent(id)}/baselines`, { signal });
  }

  archiveGenerations(id: string, signal?: AbortSignal): Promise<ArchiveGenerationList> {
    return this.json<ArchiveGenerationList>(`/api/v1/archive/runs/${encodeURIComponent(id)}/generations`, { signal });
  }

  archiveGeneration(id: string, generation: number, signal?: AbortSignal): Promise<ArchiveGenerationDetail> {
    return this.json<ArchiveGenerationDetail>(`/api/v1/archive/runs/${encodeURIComponent(id)}/generations/${encodeURIComponent(String(generation))}`, { signal });
  }

  archiveCandidates(id: string, q: { generation?: number; tier?: string; status?: string; limit?: number; offset?: number } = {}, signal?: AbortSignal): Promise<ArchiveCandidateList> {
    const qs = new URLSearchParams();
    if (q.generation !== undefined) qs.set('generation', String(q.generation));
    if (q.tier) qs.set('tier', q.tier);
    if (q.status) qs.set('status', q.status);
    if (q.limit !== undefined) qs.set('limit', String(q.limit));
    if (q.offset !== undefined) qs.set('offset', String(q.offset));
    const query = qs.toString();
    return this.json<ArchiveCandidateList>(`/api/v1/archive/runs/${encodeURIComponent(id)}/candidates` + (query ? `?${query}` : ''), { signal });
  }

  archiveCandidate(id: string, candidateId: string, signal?: AbortSignal): Promise<ArchiveCandidateDetail> {
    return this.json<ArchiveCandidateDetail>(`/api/v1/archive/runs/${encodeURIComponent(id)}/candidates/${encodeURIComponent(candidateId)}`, { signal });
  }

  archivePareto(id: string, signal?: AbortSignal): Promise<ArchivePareto> {
    return this.json<ArchivePareto>(`/api/v1/archive/runs/${encodeURIComponent(id)}/pareto`, { signal });
  }

  archiveFidelity(id: string, signal?: AbortSignal): Promise<ArchiveFidelity> {
    return this.json<ArchiveFidelity>(`/api/v1/archive/runs/${encodeURIComponent(id)}/fidelity`, { signal });
  }

  archiveConfirm(id: string, signal?: AbortSignal): Promise<ArchiveConfirm> {
    return this.json<ArchiveConfirm>(`/api/v1/archive/runs/${encodeURIComponent(id)}/confirm`, { signal });
  }

  /** A plot / report file by kind enum (never by path). Fetched with the Authorization header; the caller turns it into a Blob URL. */
  async archiveFile(id: string, kind: ArchivePlotKind | 'final_report_md' | 'run_log_tail' | 'viz_train' | 'viz_val', signal?: AbortSignal): Promise<Blob> {
    const res = await this.raw(`/api/v1/archive/runs/${encodeURIComponent(id)}/files/${encodeURIComponent(kind)}`, { headers: { Accept: '*/*' }, signal, timeoutMs: 60000 });
    return res.blob();
  }

  archiveCompare(ids: readonly string[], signal?: AbortSignal): Promise<ArchiveCompare> {
    return this.json<ArchiveCompare>(`/api/v1/archive/compare?runs=${encodeURIComponent(ids.join(','))}`, { signal });
  }

  archiveLive(signal?: AbortSignal): Promise<ArchiveLive> {
    return this.json<ArchiveLive>('/api/v1/archive/live', { signal });
  }
}
