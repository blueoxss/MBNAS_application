/**
 * Final retraining helpers (contract v1.2.0): when the panel is offered, the form check, the classification of
 * the 409/422 answers of POST /runs/{id}/final-retrain, the frozen finalist list and the parent/child run facts.
 * A lower rung is never substituted for a missing L2 finalist (NO_FINALIST is reported as is).
 *
 * 재구현(2026-10-15), 원본 누락(리포 루트 .gitignore `lib/` 규칙으로 커밋되지 않음).
 * 근거 테스트: tests/final_retrain.test.tsx ('final retrain form and run kind helpers', 'parent / child runs', Final Retrain panel),
 * e2e/final.spec.ts, 사용처 src/screens/Server.tsx, src/screens/History.tsx, src/components/FinalRun.tsx, src/state/context.tsx.
 */
import type { ApiError } from '../api/client';
import type { ApiEvent, Finalist, FinalRetrainRequest, Run, RunKind } from '../api/types';
import { getIn } from './policy';

export const NO_FINALIST_TEXT = 'L2 finalist 없음 (하위 rung 대체 없음)';

export type FinalErrorKind = 'NO_FINALIST' | 'INVALID_STATE' | 'BUDGET_INVALID' | 'IDEMPOTENCY_CONFLICT' | 'VALIDATION' | 'RETRYABLE' | 'OTHER';

/** Kind of a run; a row written before contract 1.2.0 has none and is a search run (reported as null here). */
export function runKind(run: { kind?: RunKind | null } | null | undefined): RunKind | null {
  return run?.kind ?? null;
}

/** The Final Retrain panel is offered only for a COMPLETED search run. */
export function canFinalRetrain(run: { kind?: RunKind | null; status: string } | null | undefined): boolean {
  return !!run && run.status === 'COMPLETED' && (run.kind === undefined || run.kind === null || run.kind === 'SEARCH');
}

/** Prefill for top-k: the run's resolved final_retraining value when it is an integer >= 1. */
export function defaultTopK(run: { resolved_protocol?: unknown } | null | undefined): number | null {
  const v = getIn(run?.resolved_protocol, 'final_retraining.top_k_per_arm_per_seed');
  return typeof v === 'number' && Number.isInteger(v) && v >= 1 ? v : null;
}

export interface FinalFormInput {
  topK: number | null;
  budget: number | null;
  reason: string;
}

export interface FinalFormCheck {
  ok: boolean;
  topKError: string | null;
  budgetError: string | null;
  request: Omit<FinalRetrainRequest, 'idempotency_key'> | null;
}

/** Budget: required and > 0 (an empty budget is never unlimited). top-k: optional integer >= 1. Reason: trimmed or omitted. */
export function checkFinalForm(f: FinalFormInput): FinalFormCheck {
  const budgetError =
    f.budget === null || f.budget === undefined
      ? 'Required: a final budget in GPU-hours (greater than 0).'
      : !Number.isFinite(f.budget) || f.budget <= 0
        ? 'The final budget must be a finite number of GPU-hours greater than 0.'
        : null;
  const topKError = f.topK === null || f.topK === undefined ? null : !Number.isInteger(f.topK) || f.topK < 1 ? 'top-k must be an integer of at least 1 (or empty for the server default).' : null;
  if (budgetError || topKError) return { ok: false, topKError, budgetError, request: null };
  const request: Omit<FinalRetrainRequest, 'idempotency_key'> = { budget_gpu_hours: f.budget as number };
  if (f.topK !== null && f.topK !== undefined) request.top_k_per_arm_per_seed = f.topK;
  const reason = (f.reason ?? '').trim();
  if (reason) request.reason = reason;
  return { ok: true, topKError: null, budgetError: null, request };
}

/** Tell the answers of the final-retrain endpoint apart; 5xx / transport errors are retryable with the same key. */
export function classifyFinalError(e: ApiError): { kind: FinalErrorKind; message: string } {
  if (e.retryable) return { kind: 'RETRYABLE', message: `${e.message || 'The server could not be reached.'}` };
  if (e.status === 401 || e.status === 403) return { kind: 'RETRYABLE', message: `HTTP ${e.status}: not authorized. Check the token and retry.` };
  if (e.status === 409) {
    switch (e.code) {
      case 'NO_FINALIST':
        return { kind: 'NO_FINALIST', message: NO_FINALIST_TEXT };
      case 'INVALID_STATE':
        return { kind: 'INVALID_STATE', message: `The parent run must be COMPLETED and a search run: ${e.message}` };
      case 'BUDGET_INVALID':
        return { kind: 'BUDGET_INVALID', message: `Budget rejected by the server: ${e.message}` };
      case 'IDEMPOTENCY_CONFLICT':
        return { kind: 'IDEMPOTENCY_CONFLICT', message: `Idempotency conflict: the key was already used with a different payload. ${e.message}` };
      default:
        return { kind: 'OTHER', message: `409 ${e.code ?? ''}: ${e.message}`.replace('  ', ' ') };
    }
  }
  if (e.status === 422) return { kind: 'VALIDATION', message: `Rejected by the server: ${e.message}` };
  return { kind: 'OTHER', message: `HTTP ${e.status ?? ''}${e.code ? ` ${e.code}` : ''}: ${e.message}` };
}

/** Frozen finalist list of a FINAL_RETRAIN run (empty when none or when it is not a final run). */
export function finalistsOf(run: Run | null | undefined): Finalist[] {
  return run?.kind === 'FINAL_RETRAIN' && Array.isArray(run.finalists) ? run.finalists : [];
}

export interface RelatedRun {
  relation: 'parent' | 'child';
  id: string;
  status?: string;
  source: string;
}

export interface RelatedInput {
  run: Pick<Run, 'id'> & Partial<Pick<Run, 'parent_run_id'>> | null | undefined;
  runs?: readonly Run[] | null;
  events: readonly Pick<ApiEvent, 'seq' | 'type' | 'payload'>[];
  createdChildId?: string | null;
}

/** Parent and child runs from explicit server facts only (never guessed from names or timestamps). Deduplicated. */
export function relatedRuns(input: RelatedInput): RelatedRun[] {
  const run = input.run;
  if (!run) return [];
  const out: RelatedRun[] = [];
  const seen = new Set<string>();
  const add = (r: RelatedRun): void => {
    const k = `${r.relation}:${r.id}`;
    if (!r.id || r.id === run.id || seen.has(k)) return;
    seen.add(k);
    out.push(r.status === undefined ? { relation: r.relation, id: r.id, source: r.source } : r);
  };
  if (run.parent_run_id) add({ relation: 'parent', id: run.parent_run_id, source: 'run.parent_run_id' });
  for (const r of input.runs ?? []) if (r.parent_run_id === run.id) add({ relation: 'child', id: r.id, status: r.status, source: 'run list (parent_run_id)' });
  for (const ev of input.events) {
    if (ev.type !== 'run.updated') continue;
    const p = (ev.payload ?? {}) as Record<string, unknown>;
    const pid = typeof p.id === 'string' ? p.id : undefined;
    const parent = typeof p.parent_run_id === 'string' ? p.parent_run_id : undefined;
    const status = typeof p.status === 'string' ? p.status : undefined;
    if (pid === run.id && parent) add({ relation: 'parent', id: parent, source: `run.updated event #${ev.seq}` });
    else if (pid && pid !== run.id && parent === run.id) add({ relation: 'child', id: pid, status, source: `run.updated event #${ev.seq}` });
    if (typeof p.child_run_id === 'string' && (pid === undefined || pid === run.id)) add({ relation: 'child', id: p.child_run_id, status, source: `run.updated event #${ev.seq}` });
  }
  if (input.createdChildId) add({ relation: 'child', id: input.createdChildId, source: 'created from this page' });
  return out;
}
