/**
 * Experiment policy (the protocol JSON edited on the Settings screen): path helpers and derived values.
 *
 * - The editor starts EMPTY. Policies come from the server: `GET /api/v1/protocols/presets` (the console applies the
 *   server preset DEFAULT_BASELINE14 automatically after a successful connection, see state/context.tsx), a loaded run's
 *   resolved protocol, or the user's own edits. No template, example protocol or host path is embedded in the
 *   bundle; the server validator and presets are authoritative.
 * - derived values are never typed: added epochs = cumulative target minus the previous one, the fixed scheduler horizon
 *   = the last rung's cumulative target, effective batch = batch x accumulation x GPUs.
 * - `setIn` / `unsetIn` are immutable; an optional contract field is REMOVED (never set to null).
 *
 * 사용처: src/screens/Settings.tsx, src/state/context.tsx, src/components/CandidateView.tsx.
 */

/** The policy is a free-form JSON object; the server validator is authoritative. */
export type Policy = Record<string, unknown>;

export const WEIGHT_KEYS = ['ap', 'recall', 'latency_reward', 'size_reward'] as const;
export type WeightKey = (typeof WEIGHT_KEYS)[number];

export const PATH_FIELDS = ['dataset_yaml', 'train_root', 'search_val_root', 'final_eval_root', 'output_root'] as const;

export interface Promotion {
  to: string;
  score_ge: number;
  ap_ge: number;
}

export interface Rung {
  id: string;
  epoch_target_total: number;
  epochs_added?: number;
  fitness_weights: Record<WeightKey, number>;
  promotion: Promotion | null;
}

/** One `console.baselines[]` entry of a protocol (name / family are display labels supplied by the preset). */
export interface BaselineEntry {
  id: string;
  enabled: boolean;
  name?: string;
  family?: string;
}

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const isObj = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);

/** True for an empty editor (nothing loaded or typed yet). */
export function isEmptyPolicy(p: unknown): boolean {
  return !isObj(p) || Object.keys(p).length === 0;
}

/** The `console.baselines` list of a protocol in its own order (entries without a string id are skipped). */
export function baselinesOf(p: unknown): BaselineEntry[] {
  const list = getIn(p, 'console.baselines');
  if (!Array.isArray(list)) return [];
  return list
    .filter((b): b is Record<string, unknown> => isObj(b) && typeof b.id === 'string')
    .map((b) => ({ id: b.id as string, enabled: b.enabled === true, name: typeof b.name === 'string' ? b.name : undefined, family: typeof b.family === 'string' ? b.family : undefined }));
}

/** The editor holds the server's PILOT_SMOKE preset (or a run resolved from it): smoke values, not research values. */
export function isPilotSmoke(p: unknown): boolean {
  return getIn(p, 'console.preset') === 'PILOT_SMOKE';
}

// ---- path access (immutable updates) -----------------------------------------------------------------------

/** "a.b[0].c" and "a.b.0.c" are the same path. */
function segments(path: string): string[] {
  return path.replace(/\[(\d+)\]/g, '.$1').split('.').filter((s) => s !== '');
}

/** Value at a dotted path (array indices are numbers in the path); undefined when any step is missing. */
export function getIn(root: unknown, path: string): unknown {
  let node: unknown = root;
  for (const s of segments(path)) {
    if (Array.isArray(node)) node = node[Number(s)];
    else if (isObj(node)) node = node[s];
    else return undefined;
  }
  return node;
}

function setRec(node: unknown, segs: string[], value: unknown): unknown {
  if (segs.length === 0) return value;
  const [head, ...rest] = segs;
  if (Array.isArray(node)) {
    const copy = node.slice();
    copy[Number(head)] = setRec(node[Number(head)], rest, value);
    return copy;
  }
  const base = isObj(node) ? node : {};
  const child = base[head];
  const next = child === undefined && /^\d+$/.test(rest[0] ?? '') ? [] : child;
  return { ...base, [head]: setRec(next, rest, value) };
}

/** Copy of `root` with `value` at `path` (missing parents are created). */
export function setIn(root: unknown, path: string, value: unknown): Policy {
  const segs = segments(path);
  if (segs.length === 0) return (isObj(root) ? root : {}) as Policy;
  return setRec(isObj(root) ? root : {}, segs, value) as Policy;
}

function unsetRec(node: unknown, segs: string[]): unknown {
  const [head, ...rest] = segs;
  if (Array.isArray(node)) {
    const i = Number(head);
    if (!(i in node)) return node;
    if (rest.length === 0) return node.filter((_, k) => k !== i);
    const copy = node.slice();
    copy[i] = unsetRec(node[i], rest);
    return copy;
  }
  if (!isObj(node) || !(head in node)) return node;
  if (rest.length === 0) {
    const { [head]: _removed, ...others } = node;
    void _removed;
    return others;
  }
  return { ...node, [head]: unsetRec(node[head], rest) };
}

/** Copy of `root` without the key at `path` (nothing happens when it does not exist). */
export function unsetIn(root: unknown, path: string): Policy {
  const segs = segments(path);
  const base = (isObj(root) ? root : {}) as Policy;
  return segs.length === 0 ? base : (unsetRec(base, segs) as Policy);
}

/** Canonical JSON: object keys sorted at every level (used for fingerprints and dirty checks). */
export function stableStringify(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map((x) => stableStringify(x)).join(',')}]`;
  if (isObj(v)) {
    return `{${Object.keys(v)
      .sort()
      .filter((k) => v[k] !== undefined)
      .map((k) => `${JSON.stringify(k)}:${stableStringify(v[k])}`)
      .join(',')}}`;
  }
  return JSON.stringify(v) ?? 'null';
}

// ---- rungs and derived values --------------------------------------------------------------------------------

/** The rungs of a policy (empty when absent). */
export function rungsOf(p: unknown): Rung[] {
  const r = getIn(p, 'rungs');
  return Array.isArray(r) ? (r as Rung[]) : [];
}

/** Added epochs per rung = cumulative target minus the previous cumulative target (first rung: its own target). */
export function addedEpochs(rungs: readonly Pick<Rung, 'epoch_target_total'>[]): number[] {
  return rungs.map((r, i) => r.epoch_target_total - (i === 0 ? 0 : rungs[i - 1].epoch_target_total));
}

/** Fixed scheduler horizon = cumulative target of the last rung (null without rungs). */
export function fixedHorizon(rungs: readonly Pick<Rung, 'epoch_target_total'>[]): number | null {
  return rungs.length > 0 ? rungs[rungs.length - 1].epoch_target_total : null;
}

/** Effective batch = batch per GPU x gradient accumulation x GPUs per candidate (null when one factor is missing). */
export function effectiveBatch(p: unknown): number | null {
  const f = ['training.batch_per_gpu', 'training.gradient_accumulation', 'training.gpus_per_candidate'].map((k) => getIn(p, k));
  return f.every((x) => typeof x === 'number' && Number.isFinite(x)) ? (f as number[]).reduce((a, b) => a * b, 1) : null;
}

/**
 * Keep derived fields consistent after an edit: promotion.to names the next rung, the last rung has no promotion,
 * epochs_added is dropped (computed, never entered), and the scheduler horizon equals the last cumulative target.
 * Returns a new object; idempotent.
 */
export function normalizePolicy(p: Policy): Policy {
  const out = clone(p);
  const rungs = out.rungs;
  if (Array.isArray(rungs) && rungs.length > 0) {
    rungs.forEach((r: Record<string, unknown>, i: number) => {
      if (!isObj(r)) return;
      delete r.epochs_added;
      if (i === rungs.length - 1) r.promotion = null;
      else if (isObj(r.promotion)) r.promotion.to = (rungs[i + 1] as Rung).id;
    });
    const last = rungs[rungs.length - 1] as Rung;
    if (isObj(out.training) && typeof last.epoch_target_total === 'number') out.training.scheduler_horizon_epochs = last.epoch_target_total;
  }
  return out;
}

/** Weights of a first rung added to an empty rung table: 0.25 each (sum 1; no research value implied). */
const EQUAL_WEIGHTS: Record<WeightKey, number> = { ap: 0.25, recall: 0.25, latency_reward: 0.25, size_reward: 0.25 };

/**
 * Append a rung after the last one: +ceil(last/3) epochs, weights of the last rung (equal weights for the first rung),
 * thresholds of the previous promotion (default score >= 0.5, AP >= 0.05; editor convenience values).
 */
export function addRung(p: Policy): Policy {
  const rungs = clone(rungsOf(p));
  const last = rungs[rungs.length - 1];
  const nextId = `L${rungs.length}`;
  const prevPromotion = rungs.length >= 2 ? rungs[rungs.length - 2].promotion : null;
  const target = (last?.epoch_target_total ?? 0) + Math.max(1, Math.ceil((last?.epoch_target_total ?? 3) / 3));
  if (last) last.promotion = { to: nextId, score_ge: prevPromotion?.score_ge ?? 0.5, ap_ge: prevPromotion?.ap_ge ?? 0.05 };
  // weights: a copy of the last rung's; the very first rung starts from equal weights (they sum to 1; the user edits them)
  rungs.push({ id: nextId, epoch_target_total: target, fitness_weights: { ...(last?.fitness_weights ?? EQUAL_WEIGHTS) }, promotion: null });
  return normalizePolicy(setIn(p, 'rungs', rungs));
}

/** Drop the last rung (at least one rung stays); the new last rung loses its promotion. */
export function removeLastRung(p: Policy): Policy {
  const rungs = clone(rungsOf(p));
  if (rungs.length <= 1) return p;
  rungs.pop();
  rungs[rungs.length - 1].promotion = null;
  return normalizePolicy(setIn(p, 'rungs', rungs));
}
