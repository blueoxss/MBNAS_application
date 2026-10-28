/**
 * Client-side pre-check of the experiment policy. It mirrors the rules and the codes of the server validator
 * (its protocol rules and path-syntax helpers) that can be decided without the server's file
 * system: weights, cumulative epochs, thresholds, anchors, seeds, budget, search/benchmark ranges, path SYNTAX and split
 * distinctness. The server stays authoritative (existence, allowlists, registry, devices, dataset content); a pre-check
 * problem never replaces the Validate answer, and a clean pre-check is not a valid protocol.
 *
 * 사용처: src/screens/Settings.tsx, src/state/context.tsx.
 * 규칙의 범위: 값이 '있는데 틀린' 경우를 잡고, 없는 선택·서버 기본값 필드는 서버(Validate)에 맡긴다. 클라이언트가 먼저 요구하는 것은
 * rungs 구조, 예산(null/비어 있으면 BUDGET_MISSING), search.arms/seeds 뿐이다.
 * 서버가 아직 검사하지 않는 일부 값(lr_initial 등)은 '값이 있으면 범위만' 보는 가장 단순한 규칙으로 두었다.
 */
import { evaluationEnabled } from './generation';
import { weightsSum, WEIGHT_TOLERANCE } from './fitness';
import { getIn, PATH_FIELDS, WEIGHT_KEYS } from './policy';

export interface Issue {
  field: string;
  message: string;
  code: string;
  severity: 'error' | 'warning' | 'info';
}

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const int = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v);
const isObj = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);

const err = (field: string, code: string, message: string): Issue => ({ field, code, message, severity: 'error' });
const info = (field: string, code: string, message: string): Issue => ({ field, code, message, severity: 'info' });
const warn = (field: string, code: string, message: string): Issue => ({ field, code, message, severity: 'warning' });

/** Normalised field name: "rungs[1].epochs_added" and "rungs.1.epochs_added" are the same field. */
export function fieldKey(field: string): string {
  return field.replace(/\[(\d+)\]/g, '.$1').replace(/^\./, '');
}

/** Issues for a field and for the fields below it. */
export function issuesFor(issues: readonly Issue[], field: string): Issue[] {
  const k = fieldKey(field);
  return issues.filter((i) => {
    const f = fieldKey(i.field);
    return f === k || f.startsWith(`${k}.`);
  });
}

// ---- server paths ------------------------------------------------------------------------------------------

/** Syntax check of one server path, same rule order and codes as the server (NUL is a placeholder, '..' a traversal). */
export function checkServerPath(field: string, value: unknown): Issue[] {
  if (typeof value !== 'string' || value === '') return [err(field, 'PATH_NOT_ABSOLUTE', 'Enter an absolute Linux path on the server (it starts with "/").')];
  if (value.includes('\u0000')) return [err(field, 'PATH_PLACEHOLDER', 'The path contains a NUL byte.')];
  if (value.startsWith('~')) return [err(field, 'PATH_NOT_ABSOLUTE', 'Home-relative paths ("~") are not accepted; give the absolute server path.')];
  if (value.startsWith('/ABSOLUTE') || /^\/path\/to(\/|$)/.test(value) || /[<>]/.test(value) || value.includes('YOUR_')) {
    return [err(field, 'PATH_PLACEHOLDER', 'The path still contains a placeholder (/ABSOLUTE, /path/to, <...>, YOUR_...).')];
  }
  if (!value.startsWith('/')) return [err(field, 'PATH_NOT_ABSOLUTE', 'Enter an absolute Linux path on the server (it starts with "/"); this is not a path on this computer.')];
  if (value.split('/').includes('..')) return [err(field, 'PATH_TRAVERSAL', 'Path segments ".." are not accepted.')];
  return [];
}

const normPath = (p: string): string => p.replace(/\/+/g, '/').replace(/\/$/, '') || '/';

function checkPaths(p: unknown): Issue[] {
  const paths = getIn(p, 'console.paths');
  if (!isObj(paths)) return [];
  const out: Issue[] = [];
  for (const f of PATH_FIELDS) if (paths[f] !== undefined) out.push(...checkServerPath(`console.paths.${f}`, paths[f]));
  const roots = ['train_root', 'search_val_root', 'final_eval_root', 'output_root'] as const;
  if (roots.every((k) => typeof paths[k] === 'string' && checkServerPath(k, paths[k]).length === 0)) {
    const seen = new Map<string, string>();
    let clash: [string, string] | null = null;
    for (const k of roots) {
      const n = normPath(paths[k] as string);
      const prev = seen.get(n);
      if (prev !== undefined && clash === null) clash = [prev, k];
      seen.set(n, k);
    }
    if (clash) out.push(err('console.paths', 'SPLIT_SAME_ROOT', `train / search_val / final_eval roots and the output root must be distinct (${clash[0]} and ${clash[1]} are the same directory).`));
  }
  return out;
}

// ---- rungs -------------------------------------------------------------------------------------------------

function checkWeights(w: unknown, field: string): Issue[] {
  if (!isObj(w)) return [err(field, 'FIELD_MISSING', `Named weights {${WEIGHT_KEYS.join(', ')}} are required.`)];
  const out: Issue[] = [];
  for (const k of Object.keys(w)) if (!(WEIGHT_KEYS as readonly string[]).includes(k)) out.push(err(`${field}.${k}`, 'UNKNOWN_FIELD', `Unknown fitness weight "${k}".`));
  let all = true;
  for (const k of WEIGHT_KEYS) {
    const v = w[k];
    if (!num(v)) {
      out.push(err(`${field}.${k}`, v === undefined || v === null ? 'FIELD_MISSING' : 'FIELD_INVALID', 'The weight must be a finite number.'));
      all = false;
    } else if (v < 0) {
      out.push(err(`${field}.${k}`, 'WEIGHTS_NEGATIVE', 'Weights must be >= 0.'));
      all = false;
    }
  }
  if (all) {
    const sum = weightsSum(w as Record<string, number>);
    if (Math.abs(sum - 1) > WEIGHT_TOLERANCE) out.push(err(field, 'WEIGHTS_SUM_NOT_ONE', `Weights sum to ${Number(sum.toPrecision(6))}, must be 1 within ${WEIGHT_TOLERANCE} (there is no auto-normalisation).`));
  }
  return out;
}

function checkPromotion(promo: unknown, i: number, ids: string[]): Issue[] {
  const f = `rungs.${i}.promotion`;
  if (i === ids.length - 1) return promo === null || promo === undefined ? [] : [err(f, 'FIELD_INVALID', 'The last search rung must have promotion null.')];
  if (!isObj(promo)) return [err(f, 'FIELD_MISSING', 'A rung that is not the last needs a promotion rule.')];
  const out: Issue[] = [];
  if (promo.to !== ids[i + 1]) out.push(err(`${f}.to`, 'FIELD_INVALID', `promotion.to must name the next rung "${ids[i + 1]}".`));
  for (const [k, code] of [['score_ge', 'THRESHOLD_RANGE'], ['ap_ge', 'AP_GUARD_RANGE']] as const) {
    const v = promo[k];
    if (!num(v) || v < 0 || v > 1) out.push(err(`${f}.${k}`, code, `${k} must be a finite fraction in [0, 1].`));
  }
  return out;
}

/** Cumulative epoch targets that were accepted (strictly increasing integers) -> used by the training checks. */
function checkRungs(p: unknown): { issues: Issue[]; targets: number[] } {
  const rungs = getIn(p, 'rungs');
  if (!Array.isArray(rungs) || rungs.length === 0) return { issues: [err('rungs', 'FIELD_MISSING', 'rungs must be a non-empty list.')], targets: [] };
  const out: Issue[] = [];
  const targets: number[] = [];
  const ids: string[] = [];
  rungs.forEach((r: unknown, i: number) => {
    const f = `rungs.${i}`;
    if (!isObj(r)) {
      out.push(err(f, 'FIELD_INVALID', 'A rung must be an object.'));
      ids.push(`#${i}`);
      return;
    }
    const id = r.id;
    if (typeof id !== 'string' || id === '' || ids.includes(id)) out.push(err(`${f}.id`, 'FIELD_INVALID', 'The rung id must be a unique non-empty string.'));
    ids.push(typeof id === 'string' && id ? id : `#${i}`);
    const t = r.epoch_target_total;
    if (!int(t)) out.push(err(`${f}.epoch_target_total`, 'EPOCHS_NOT_INT', 'The cumulative epoch target must be an integer.'));
    else if (t < 1 || (targets.length > 0 && t <= targets[targets.length - 1])) out.push(err(`${f}.epoch_target_total`, 'EPOCHS_NOT_INCREASING', 'Cumulative epoch targets must be >= 1 and strictly increasing.'));
    else {
      const prev = targets.length > 0 ? targets[targets.length - 1] : 0;
      if ('epochs_added' in r && !(int(r.epochs_added) && r.epochs_added === t - prev)) {
        out.push(err(`${f}.epochs_added`, 'EPOCHS_ADDED_MANUAL', `Added epochs are computed (${t} - ${prev} = ${t - prev}); remove the manual value ${JSON.stringify(r.epochs_added)}.`));
      }
      targets.push(t);
    }
    out.push(...checkWeights(r.fitness_weights, `${f}.fitness_weights`));
  });
  rungs.forEach((r: unknown, i: number) => {
    if (isObj(r)) out.push(...checkPromotion(r.promotion, i, ids));
  });
  return { issues: out, targets: targets.length === rungs.length ? targets : [] };
}

// ---- training, fitness, constraints ----------------------------------------------------------------------------

const TRAIN_PRECISIONS = ['amp_fp16_fp32', 'fp32'];
const INIT_MODES = ['scratch', 'pretrained_native'];

function checkTraining(p: unknown, targets: number[]): Issue[] {
  const t = getIn(p, 'training');
  if (!isObj(t)) return [];
  const out: Issue[] = [];
  const warm = t.warmup_epochs ?? 0;
  if (!num(warm) || warm < 0) out.push(err('training.warmup_epochs', 'FIELD_INVALID', 'warmup_epochs must be a finite number >= 0.'));
  else if (targets.length > 0 && warm >= targets[0]) out.push(err('training.warmup_epochs', 'WARMUP_GE_L0', `warmup_epochs ${warm} must be smaller than the first rung target ${targets[0]}.`));
  for (const k of ['batch_per_gpu', 'gradient_accumulation', 'gpus_per_candidate'] as const) {
    if (t[k] !== undefined && (!int(t[k]) || (t[k] as number) < 1)) out.push(err(`training.${k}`, 'FIELD_INVALID', `${k} must be an integer >= 1.`));
  }
  if (t.precision !== undefined && !TRAIN_PRECISIONS.includes(t.precision as string)) out.push(err('training.precision', 'PRECISION_UNSUPPORTED', `Training precision must be one of ${TRAIN_PRECISIONS.join(', ')}.`));
  const size = t.requested_input_size;
  if (size !== undefined && !(Array.isArray(size) && size.length === 2 && size.every((s) => int(s) && s >= 32))) out.push(err('training.requested_input_size', 'FIELD_INVALID', 'requested_input_size must be [height, width] integers >= 32.'));
  if (t.workers !== undefined && (!int(t.workers) || t.workers < 0)) out.push(err('training.workers', 'FIELD_INVALID', 'workers must be an integer >= 0.'));
  if (t.initialization !== undefined && !INIT_MODES.includes(t.initialization as string)) out.push(err('training.initialization', 'INIT_UNSUPPORTED', `initialization must be one of ${INIT_MODES.join(', ')}.`));
  if (t.lr_initial !== undefined && (!num(t.lr_initial) || t.lr_initial <= 0)) out.push(err('training.lr_initial', 'FIELD_INVALID', 'lr_initial must be a finite number > 0.'));
  if (t.lr_final_ratio !== undefined && (!num(t.lr_final_ratio) || t.lr_final_ratio <= 0 || t.lr_final_ratio > 1)) out.push(err('training.lr_final_ratio', 'FIELD_INVALID', 'lr_final_ratio must be in (0, 1].'));
  if (t.weight_decay !== undefined && (!num(t.weight_decay) || t.weight_decay < 0)) out.push(err('training.weight_decay', 'FIELD_INVALID', 'weight_decay must be a finite number >= 0.'));
  const h = t.scheduler_horizon_epochs;
  if (h === undefined) return out; // the server decides whether a missing horizon is acceptable
  if (!int(h)) out.push(err('training.scheduler_horizon_epochs', 'EPOCHS_NOT_INT', 'scheduler_horizon_epochs must be an integer.'));
  else if (targets.length > 0 && h < targets[targets.length - 1]) out.push(err('training.scheduler_horizon_epochs', 'HORIZON_LT_LAST_RUNG', `horizon ${h} < last rung target ${targets[targets.length - 1]}.`));
  else if (targets.length > 0 && h !== targets[targets.length - 1]) out.push(warn('training.scheduler_horizon_epochs', 'HORIZON_ADJUSTED', `The fixed schedule horizon is the last rung target ${targets[targets.length - 1]}, not ${h}.`));
  return out;
}

function checkFitnessAndConstraints(p: unknown): Issue[] {
  const out: Issue[] = [];
  for (const k of ['latency_bounds_ms', 'size_bounds_MB']) {
    const b = getIn(p, `fitness.${k}`);
    if (b !== undefined && !(Array.isArray(b) && b.length === 2 && num(b[0]) && num(b[1]) && b[0] > 0 && b[0] < b[1])) out.push(err(`fitness.${k}`, 'ANCHORS_INVALID', `${k} must be [min, max] with 0 < min < max.`));
  }
  const hc = getIn(p, 'hard_constraints');
  if (!isObj(hc)) return out;
  for (const k of ['latency_le_ms', 'weights_size_le_MB']) {
    if (hc[k] !== undefined && (!num(hc[k]) || (hc[k] as number) <= 0)) out.push(err(`hard_constraints.${k}`, 'HARD_CONSTRAINT_INVALID', `${k} must be a finite number > 0.`));
  }
  return out;
}

// ---- search and benchmark ----------------------------------------------------------------------------------------

const ARMS = ['GA', 'IndependentRandom'];

function inRange01(v: unknown): boolean {
  return num(v) && v >= 0 && v <= 1;
}

/** Search (arms, sizes, rates, seeds, budget) and benchmark (counts, postprocess operating point) ranges. */
export function checkSearch(p: unknown): Issue[] {
  const out: Issue[] = [];
  const s = getIn(p, 'console.search');
  if (!isObj(s)) return [];
  const arms = s.arms;
  const baselineOnly = Array.isArray(arms) && arms.length === 0;
  if (!Array.isArray(arms) || arms.some((a) => !ARMS.includes(a as string)) || new Set(arms).size !== arms.length) {
    out.push(err('console.search.arms', arms === undefined ? 'FIELD_MISSING' : 'FIELD_INVALID', `arms must be a list of distinct values from ${ARMS.join(', ')} ([] for a baseline-only run).`));
  }
  if (!baselineOnly) {
    for (const [k, lo] of [['population', 2], ['generations', 1]] as const) {
      if (s[k] !== undefined && (!int(s[k]) || (s[k] as number) < lo)) out.push(err(`console.search.${k}`, 'FIELD_INVALID', `${k} must be an integer >= ${lo}.`));
    }
    if (s.mutation_rate !== undefined && !inRange01(s.mutation_rate)) out.push(err('console.search.mutation_rate', 'FIELD_INVALID', 'Mutation rate must be within [0, 1].'));
    const b = s.budget_gpu_hours;
    if (b === undefined || b === null) out.push(err('console.search.budget_gpu_hours', 'BUDGET_MISSING', 'A GPU-hour budget is required (an absent budget is never unlimited).'));
    else if (!num(b) || b <= 0) out.push(err('console.search.budget_gpu_hours', 'BUDGET_INVALID', 'The budget must be a finite number of GPU-hours > 0.'));
    const seeds = s.seeds;
    if (!(Array.isArray(seeds) && seeds.length > 0 && seeds.every((x) => int(x) && x >= 0) && new Set(seeds).size === seeds.length)) {
      out.push(err('console.search.seeds', 'SEEDS_INVALID', 'seeds must be a non-empty list of unique integers >= 0.'));
    }
  }
  if (s.crossover_rate !== undefined && !inRange01(s.crossover_rate)) out.push(err('console.search.crossover_rate', 'FIELD_INVALID', 'Crossover rate must be within [0, 1].'));
  if (s.allow_native_fallback !== undefined && typeof s.allow_native_fallback !== 'boolean') out.push(err('console.search.allow_native_fallback', 'FIELD_INVALID', 'allow_native_fallback must be true or false.'));

  const bench = getIn(p, 'console.benchmark');
  if (isObj(bench)) {
    for (const [k, lo] of [['batch', 1], ['warmup', 0], ['repeats', 1]] as const) {
      if (bench[k] !== undefined && (!int(bench[k]) || (bench[k] as number) < lo)) out.push(err(`console.benchmark.${k}`, 'FIELD_INVALID', `${k} must be an integer >= ${lo}.`));
    }
    const pp = bench.postprocess;
    if (pp !== undefined && pp !== null) {
      if (!isObj(pp)) out.push(err('console.benchmark.postprocess', 'FIELD_INVALID', 'postprocess must be an object {conf, iou, max_det}.'));
      else {
        for (const k of Object.keys(pp)) if (!['conf', 'iou', 'max_det'].includes(k)) out.push(err(`console.benchmark.postprocess.${k}`, 'UNKNOWN_FIELD', `Unknown postprocess key "${k}".`));
        for (const k of ['conf', 'iou'] as const) if (pp[k] !== undefined && !inRange01(pp[k])) out.push(err(`console.benchmark.postprocess.${k}`, 'FIELD_INVALID', `${k} must be a finite number in [0, 1].`));
        if (pp.max_det !== undefined && (!int(pp.max_det) || pp.max_det < 1)) out.push(err('console.benchmark.postprocess.max_det', 'FIELD_INVALID', 'max_det must be an integer >= 1.'));
      }
    }
  }
  return out;
}

function checkBaselines(p: unknown): Issue[] {
  const list = getIn(p, 'console.baselines');
  if (list === undefined) return [];
  if (!Array.isArray(list)) return [err('console.baselines', 'FIELD_INVALID', 'console.baselines must be a list.')];
  const out: Issue[] = [];
  const seen = new Set<string>();
  list.forEach((b: unknown, i: number) => {
    const f = `console.baselines.${i}`;
    if (!isObj(b) || typeof b.id !== 'string' || typeof b.enabled !== 'boolean') {
      out.push(err(f, 'FIELD_INVALID', 'Baseline entries need a string id and a boolean enabled.'));
      return;
    }
    if (seen.has(b.id)) out.push(err(`${f}.id`, 'BASELINE_DUPLICATE', `Baseline "${b.id}" is listed twice.`));
    seen.add(b.id);
  });
  return out;
}

function checkFinal(p: unknown): Issue[] {
  const f = getIn(p, 'final_retraining');
  if (!isObj(f)) return [];
  const out: Issue[] = [];
  const { epoch_target_total: t, scheduler_horizon_epochs: h, top_k_per_arm_per_seed: k, budget_GPU_hours_separate: b } = f;
  if (t !== undefined && t !== null && (!int(t) || t < 1)) out.push(err('final_retraining.epoch_target_total', 'EPOCHS_NOT_INT', 'The final epoch_target_total must be an integer >= 1.'));
  if (h !== undefined && h !== null && !int(h)) out.push(err('final_retraining.scheduler_horizon_epochs', 'EPOCHS_NOT_INT', 'The final scheduler_horizon_epochs must be an integer.'));
  else if (int(h) && int(t) && h < t) out.push(err('final_retraining.scheduler_horizon_epochs', 'HORIZON_LT_LAST_RUNG', `Final horizon ${h} < final epoch_target_total ${t}.`));
  if (k !== undefined && k !== null && (!int(k) || k < 1)) out.push(err('final_retraining.top_k_per_arm_per_seed', 'FIELD_INVALID', 'top_k_per_arm_per_seed must be an integer >= 1.'));
  if (b !== undefined && b !== null && (!num(b) || b <= 0)) out.push(err('final_retraining.budget_GPU_hours_separate', 'BUDGET_INVALID', 'The final budget must be a finite number of GPU-hours > 0.'));
  return out;
}

/** All pre-check issues (errors and warnings) of a policy. */
export function validatePolicy(p: unknown): Issue[] {
  if (!isObj(p)) return [err('$', 'FIELD_INVALID', 'The protocol must be a JSON object.')];
  if (!evaluationEnabled(p)) {
    // evaluation-only settings (paths other than output_root, rungs, weights, ...) are preserved, not
    // validated, in OFF mode. The only message about them is this single info-level notice.
    const out: Issue[] = [info('console.search.evaluation_enabled', 'EVALUATION_SETTINGS_PRESERVED', '성능 평가 OFF: 평가 설정 값은 보존되며 검증하지 않습니다')];
    const search = getIn(p, 'console.search') as Record<string, unknown>;
    const n = search.candidate_count ?? search.population ?? 10;
    if (!int(n) || n < 1) out.push(err('console.search.candidate_count', 'FIELD_INVALID', '후보 수는 1 이상의 정수여야 합니다.'));
    if (!int(search.nc) || search.nc < 1) out.push(err('console.search.nc', 'FIELD_INVALID', '구조 확인을 위한 클래스 수 nc는 1 이상의 정수로 입력하세요.'));
    if (search.max_attempts !== undefined && (!int(search.max_attempts) || search.max_attempts < 1)) out.push(err('console.search.max_attempts', 'FIELD_INVALID', '최대 시도 수는 1 이상의 정수여야 합니다.'));
    if (search.input_size !== undefined && (!int(search.input_size) || search.input_size < 32)) out.push(err('console.search.input_size', 'FIELD_INVALID', '입력 크기는 32 이상의 정수여야 합니다.'));
    if (search.allow_duplicates !== undefined && typeof search.allow_duplicates !== 'boolean') out.push(err('console.search.allow_duplicates', 'FIELD_INVALID', '중복 허용은 true/false여야 합니다.'));
    if (search.seeds !== undefined && (!Array.isArray(search.seeds) || !search.seeds.length || search.seeds.some((v) => !int(v) || v < 0) || new Set(search.seeds).size !== search.seeds.length)) out.push(err('console.search.seeds', 'SEEDS_INVALID', 'seed는 중복 없는 0 이상의 정수 목록이어야 합니다.'));
    if (search.families !== undefined && (!Array.isArray(search.families) || !search.families.length || search.families.some((v) => typeof v !== 'string' || !v))) out.push(err('console.search.families', 'FIELD_INVALID', '지원되는 모델 계열을 입력하세요.'));
    const output = getIn(p, 'console.paths.output_root');
    if (output !== undefined) out.push(...checkServerPath('console.paths.output_root', output));
    return out;
  }
  const rungs = checkRungs(p);
  return [
    ...rungs.issues,
    ...checkTraining(p, rungs.targets),
    ...checkFitnessAndConstraints(p),
    ...checkPaths(p),
    ...checkSearch(p),
    ...checkBaselines(p),
    ...checkFinal(p),
  ];
}
