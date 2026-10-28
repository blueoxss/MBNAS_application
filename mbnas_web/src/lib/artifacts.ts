/**
 * Diagnostic-image loading rules (gt / tp / fn / fp):
 *  - `signed_url`   -> plain image URL, NO request and NO credentials from the client;
 *  - `bearer_fetch` -> Authorization header only when the URL origin equals the API origin, shown through a Blob URL;
 *  - EXPIRED (server state or past `expires_at`) -> the refresh endpoint is asked exactly once, then READY or EXPIRED.
 * Every outcome is a state the UI prints as text; nothing ends in an endless spinner.
 * Also: the `Artifact.matching` record (nested `counts {tp,fp,fn,gt}`) and the per-sample recall (N/A when gt = 0).
 *
 * 재구현(2026-10-15), 원본 누락(리포 루트 .gitignore `lib/` 규칙으로 커밋되지 않음).
 * 근거 테스트: tests/api.test.ts ('artifact origin rule and states', 'Artifact.matching'), tests/components.test.tsx
 * ('ArtifactImage states'), e2e/console.spec.ts + e2e/hardening.spec.ts (artifact state machine), 사용처 src/components/ArtifactImage.tsx.
 */
import { ApiError, type ApiClient } from '../api/client';
import type { Artifact } from '../api/types';

export type LoadState = 'LOADING' | 'READY' | 'NOT_READY' | 'EXPIRED' | 'ACCESS_DENIED' | 'MISSING' | 'LOAD_FAILED';

export interface ArtifactResult {
  /** never LOADING: a result is always a final state */
  state: Exclude<LoadState, 'LOADING'>;
  /** image source when READY */
  src?: string;
  /** true when `src` is a Blob URL the caller must revoke */
  isBlob?: boolean;
  /** human readable detail (only when there is something to say beyond the state name) */
  detail?: string;
  /** `Error.code` of a failed download (the state is derived from it, DD-A39) */
  code?: string;
}

export interface LoadContext {
  client: ApiClient;
  runId: string;
  /** injectable for tests; defaults to URL.createObjectURL */
  createObjectURL?: (b: Blob) => string;
}

/**
 * Error code of an artifact download -> state (DD-A39 / contract 1.5.0; the code decides, not the status): the gateway store
 * answers 404 ARTIFACT_NOT_READY while the bytes are not there yet and 409 LOAD_FAILED when the stored bytes no longer match
 * the registered sha256. null for a code this table does not know (then the HTTP status decides, for older servers).
 */
export const ARTIFACT_ERROR_STATE: Readonly<Record<string, Exclude<LoadState, 'LOADING' | 'READY'>>> = {
  ARTIFACT_NOT_READY: 'NOT_READY',
  NOT_READY: 'NOT_READY',
  LOAD_FAILED: 'LOAD_FAILED',
  UPLOAD_CORRUPT: 'LOAD_FAILED',
  EXPIRED: 'EXPIRED',
  AUTH_REQUIRED: 'ACCESS_DENIED',
  ACCESS_DENIED: 'ACCESS_DENIED',
  NOT_FOUND: 'MISSING',
  ARTIFACT_NOT_FOUND: 'MISSING',
  RUN_NOT_FOUND: 'MISSING',
  CANDIDATE_NOT_FOUND: 'MISSING',
};

/** State of a failed artifact request: by `Error.code` first, by HTTP status only when the code is unknown or absent. */
export function stateFromError(e: ApiError): Exclude<LoadState, 'LOADING' | 'READY'> {
  const byCode = e.code ? ARTIFACT_ERROR_STATE[e.code] : undefined;
  if (byCode) return byCode;
  return e.status !== null ? stateFromStatus(e.status) : 'LOAD_FAILED';
}

/** HTTP status of an artifact request -> state (fallback for an unknown error code). */
export function stateFromStatus(status: number): Exclude<LoadState, 'LOADING' | 'READY'> {
  if (status === 401 || status === 403) return 'ACCESS_DENIED';
  if (status === 404) return 'MISSING';
  if (status === 409) return 'NOT_READY';
  if (status === 410) return 'EXPIRED';
  return 'LOAD_FAILED';
}

/** True when the timestamp is in the past (null/undefined/unparseable: not expired). */
export function isExpiredAt(expiresAt: string | null | undefined, now: number = Date.now()): boolean {
  if (typeof expiresAt !== 'string' || expiresAt === '') return false;
  const t = Date.parse(expiresAt);
  return Number.isFinite(t) && t <= now;
}

const REPORTED_STATES: readonly Exclude<LoadState, 'LOADING'>[] = ['NOT_READY', 'EXPIRED', 'ACCESS_DENIED', 'MISSING', 'LOAD_FAILED'];

/** One load attempt without any refresh. A state the server already reported is shown as is. */
export async function loadOnce(artifact: Artifact, ctx: LoadContext): Promise<ArtifactResult> {
  const reported = artifact.state as Exclude<LoadState, 'LOADING'> | undefined;
  if (reported && REPORTED_STATES.includes(reported)) return { state: reported };
  if (!artifact.url) return { state: 'LOAD_FAILED', detail: 'The artifact has no URL.' };
  if (artifact.auth_mode === 'bearer_fetch') {
    if (!ctx.client.isApiOrigin(artifact.url)) {
      return { state: 'LOAD_FAILED', detail: 'The URL is not on the API origin; the bearer token is never sent to another origin.' };
    }
    try {
      const res = await ctx.client.raw(artifact.url, { headers: { Accept: 'image/*' } });
      const blob = await res.blob();
      const make = ctx.createObjectURL ?? ((b: Blob) => URL.createObjectURL(b));
      return { state: 'READY', src: make(blob), isBlob: true };
    } catch (e) {
      if (e instanceof ApiError && e.kind === 'http' && e.status !== null) return { state: stateFromError(e), ...(e.code ? { code: e.code } : {}) };
      return { state: 'LOAD_FAILED', detail: e instanceof Error ? e.message : 'Request failed.' };
    }
  }
  // signed_url (or unspecified): the browser loads it as a plain image; no credentials are attached
  return { state: 'READY', src: artifact.url };
}

/** Load with the refresh rule: an expired artifact asks the refresh endpoint exactly once. */
export async function loadArtifact(artifact: Artifact, ctx: LoadContext): Promise<ArtifactResult> {
  const expired = artifact.state === 'EXPIRED' || isExpiredAt(artifact.expires_at);
  if (!expired) return loadOnce(artifact, ctx);
  if (!artifact.id) return { state: 'EXPIRED' };
  let fresh: Artifact;
  try {
    fresh = await ctx.client.refreshArtifact(ctx.runId, artifact.id);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) return { state: 'ACCESS_DENIED' };
    return { state: 'EXPIRED' };
  }
  if (fresh.state === 'EXPIRED' || isExpiredAt(fresh.expires_at)) return { state: 'EXPIRED' };
  return loadOnce(fresh, ctx);
}

// ---- Artifact.matching -----------------------------------------------------------------------------------

export interface MatchCounts {
  tp?: number;
  fp?: number;
  fn?: number;
  gt?: number;
}

export interface Matching {
  counts: MatchCounts;
  rule?: string;
  iou?: number;
  conf?: number;
  /** recall of the sample as reported by the server (may be null) */
  sampleRecall?: number | null;
}

const isFiniteNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Read the server's matching record (nested `counts`); malformed or absent parts are dropped, never zero-filled. */
export function parseMatching(m: unknown): Matching {
  const out: Matching = { counts: {} };
  if (m === null || typeof m !== 'object' || Array.isArray(m)) return out;
  const o = m as Record<string, unknown>;
  const c = o.counts !== null && typeof o.counts === 'object' ? (o.counts as Record<string, unknown>) : {};
  for (const k of ['tp', 'fp', 'fn', 'gt'] as const) if (isFiniteNumber(c[k])) out.counts[k] = c[k] as number;
  if (typeof o.rule === 'string') out.rule = o.rule;
  if (isFiniteNumber(o.iou)) out.iou = o.iou;
  if (isFiniteNumber(o.conf)) out.conf = o.conf;
  if (isFiniteNumber(o.sample_recall)) out.sampleRecall = o.sample_recall;
  else if (o.sample_recall === null) out.sampleRecall = null;
  return out;
}

/** Recall of ONE sample = tp / gt; null (N/A) when the sample has no ground truth or the counts are missing. */
export function sampleRecall(counts: MatchCounts, serverRecall?: number | null): number | null {
  const { tp, gt } = counts;
  if (isFiniteNumber(gt)) return gt > 0 && isFiniteNumber(tp) ? tp / gt : null;
  return isFiniteNumber(serverRecall) ? serverRecall : null;
}
