/**
 * Idempotent Run / Final Retrain submission.
 *  - one Idempotency-Key per request payload (fingerprint); the SAME key is reused after a timeout, network error,
 *    5xx or 401/403 (the request may still succeed) and released after a 2xx, 409, 422 or any other definitive 4xx;
 *  - the key is sent as header and as `idempotency_key` in the body (the client does both);
 *  - Run sends the RESOLVED protocol with its hash and validation id, never the editor state.
 *
 * 이 파일만 Math.random 폴백을 허용한다. 사용처: src/state/context.tsx.
 */
import { ApiError, type ApiClient } from '../api/client';
import type { FinalRetrainRequest, RunCreated, RunRequest, ValidateResponse } from '../api/types';
import { stableStringify } from './policy';

export type SubmitOutcome =
  | { kind: 'created'; created: RunCreated; key: string }
  | { kind: 'retryable' | 'conflict' | 'rejected'; error: ApiError; key: string };

function randomKey(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  const bytes = new Uint8Array(16);
  if (c && typeof c.getRandomValues === 'function') c.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256); // crypto-less fallback, key bytes only
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** fingerprint -> Idempotency-Key. `gen` is injectable for tests. */
export class IdempotencyStore {
  private readonly keys = new Map<string, string>();

  constructor(private readonly gen: () => string = randomKey) {}

  /** The key of this fingerprint; created on first use and reused until `settle`. */
  keyFor(fingerprint: string): string {
    let k = this.keys.get(fingerprint);
    if (k === undefined) {
      k = this.gen();
      this.keys.set(fingerprint, k);
    }
    return k;
  }

  has(fingerprint: string): boolean {
    return this.keys.has(fingerprint);
  }

  /** Release the key (definitive answer received). */
  settle(fingerprint: string): void {
    this.keys.delete(fingerprint);
  }
}

export function runFingerprint(v: Pick<ValidateResponse, 'protocol_hash' | 'validation_id'>): string {
  return `run|${stableStringify({ protocol_hash: v.protocol_hash ?? null, validation_id: v.validation_id ?? null })}`;
}

export interface FinalRetrainInput {
  parentRunId: string;
  request: Omit<FinalRetrainRequest, 'idempotency_key'>;
}

export function finalRetrainFingerprint(input: FinalRetrainInput): string {
  return `final|${input.parentRunId}|${stableStringify(input.request)}`;
}

/** Map a failed request to an outcome and decide whether the key survives. */
function failure(e: unknown, store: IdempotencyStore, fingerprint: string, key: string): SubmitOutcome {
  if (!(e instanceof ApiError)) throw e;
  if (e.kind === 'http' && e.status === 409) {
    store.settle(fingerprint);
    return { kind: 'conflict', error: e, key };
  }
  if (e.kind === 'http' && e.status === 422) {
    store.settle(fingerprint);
    return { kind: 'rejected', error: e, key };
  }
  // transport trouble, 5xx, an unreadable 2xx body, or an authorization problem: the same request may still go through
  if (e.retryable || e.kind === 'parse' || (e.kind === 'http' && (e.status === 401 || e.status === 403))) return { kind: 'retryable', error: e, key };
  store.settle(fingerprint);
  return { kind: 'rejected', error: e, key };
}

/** POST /runs with the resolved protocol of a valid Validate answer. */
export async function submitRun(client: ApiClient, store: IdempotencyStore, v: ValidateResponse): Promise<SubmitOutcome> {
  if (!v.valid || !v.protocol_hash || !v.resolved) throw new Error('Run needs a valid Validate answer with a protocol hash and the resolved protocol.');
  const fingerprint = runFingerprint(v);
  const key = store.keyFor(fingerprint);
  const req: RunRequest = { protocol: v.resolved as RunRequest['protocol'], protocol_hash: v.protocol_hash, idempotency_key: key };
  if (v.validation_id) req.validation_id = v.validation_id;
  try {
    const created = await client.createRun(req);
    store.settle(fingerprint);
    return { kind: 'created', created, key };
  } catch (e) {
    return failure(e, store, fingerprint, key);
  }
}

/** POST /runs/{parent}/final-retrain. */
export async function submitFinalRetrain(client: ApiClient, store: IdempotencyStore, input: FinalRetrainInput): Promise<SubmitOutcome> {
  const fingerprint = finalRetrainFingerprint(input);
  const key = store.keyFor(fingerprint);
  try {
    const created = await client.finalRetrain(input.parentRunId, { ...input.request, idempotency_key: key });
    store.settle(fingerprint);
    return { kind: 'created', created, key };
  } catch (e) {
    return failure(e, store, fingerprint, key);
  }
}
