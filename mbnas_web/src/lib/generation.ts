import type { Candidate, GeneratedCandidate } from '../api/types';
import { getIn } from './policy';

/** Missing field preserves the evaluated search behavior of older saved protocols. */
export function evaluationEnabled(protocol: unknown): boolean {
  return getIn(protocol, 'console.search.evaluation_enabled') !== false;
}

export function generationOnlyRow(row: unknown): boolean {
  if (!row || typeof row !== 'object') return false;
  const r = row as Record<string, unknown>;
  return r.status === 'NOT_EVALUATED' || r.origin === 'random_generation' || r.effective_method === 'RandomGeneration' ||
    (r.status == null && r.evaluation_status === 'NOT_EVALUATED' && r.generation_order != null);
}

/**
 * A generated (evaluation-OFF) candidate in the shape the candidate views render: the GeneratedCandidate fields plus the
 * display status NOT_EVALUATED and 0 epochs (no evaluation row exists; `generationOnlyRow` recognises it). The one place
 * where a GeneratedCandidate is presented as a Candidate; the GeneratedCandidate fields stay readable through `rec()`.
 */
export function generatedAsCandidate(g: GeneratedCandidate): Candidate {
  return { ...g, status: 'NOT_EVALUATED', epochs: 0 } as unknown as Candidate;
}

/** Preserve first-seen seed order, then the draw order inside that seed. Never sort by metrics. */
export function inGenerationOrder<T>(rows: readonly T[]): T[] {
  const groups = new Map<unknown, { row: T; index: number; order: number }[]>();
  rows.forEach((row, index) => {
    const data = row as Record<string, unknown>;
    const seed = data.search_seed ?? data.seed ?? '__unrecorded_seed__';
    const order = typeof data.generation_order === 'number' ? data.generation_order : NaN;
    const group = groups.get(seed) ?? [];
    group.push({ row, index, order });
    groups.set(seed, group);
  });
  return [...groups.values()].flatMap((group) => group
    .sort((a, b) => Number.isFinite(a.order) && Number.isFinite(b.order) ? a.order - b.order || a.index - b.index : a.index - b.index)
    .map(({ row }) => row));
}
