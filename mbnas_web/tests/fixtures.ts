/**
 * SYNTHETIC TEST FIXTURES. Everything here is invented to exercise the UI (unit tests and the Playwright
 * mock API). None of it is a measured result. Ids are prefixed "synthetic-" so a row can never be
 * mistaken for a real run. This file lives outside src/ and is never part of the production bundle.
 *
 * DD-A43 (a): this module (and mockapi.ts / e2e/mock.ts, which use it) ships in the public and registration bundles, so it
 * must not import the test-only fitness oracle (tests/fitness_oracle.ts, owner only) directly or transitively. The rows
 * carry the fitness the synthetic server stamped, RECORDED below per metric tuple; the owner-only
 * tests/fixtures_oracle.test.ts re-derives every recorded value with the oracle, and tests that need a fitness consistent
 * with arbitrary metrics use the owner-only tests/fixtures_oracle.ts. Contract 1.5.0 shapes (closed components).
 */
import type { ApiEvent, Artifact, Block, Candidate, Finalist, Generation, Health, Preset, Run, Snapshot, ValidateResponse, Worker } from '../src/api/types';

export const RUN_ID = 'synthetic-run-0001';
export const PROTOCOL_HASH = `sha256:${'a'.repeat(64)}`;
export const EVAL_HASH = `sha256:${'b'.repeat(64)}`;
export const OBJ_HASH = `sha256:${'c'.repeat(64)}`;
export const SPLIT_HASH = `sha256:${'d'.repeat(64)}`;
export const GROUP = `sha256:${'e'.repeat(64)}`;

export const WEIGHTS = { ap: 0.2, recall: 0.1, latency_reward: 0.4, size_reward: 0.3 };
export const ANCHORS = { latency_bounds_ms: [20, 200] as const, size_bounds_MB: [5, 100] as const };

/** Key of a metric tuple: `ap|recall|latency_ms|size_mb` (the numbers as JavaScript prints them). */
export function metricKey(m: { ap?: unknown; recall?: unknown; latency_ms?: unknown; size_mb?: unknown }): string {
  return `${String(m.ap)}|${String(m.recall)}|${String(m.latency_ms)}|${String(m.size_mb)}`;
}

/**
 * Fitness the synthetic server stamped on the canonical fixture rows (fixtureCandidates, the final-retraining rows, the
 * default cand() row and the two ad-hoc rows of the Playwright mock), recorded per metric tuple for the run weights
 * WEIGHTS and anchors ANCHORS. Data only: the console never computes fitness (DD-A8) and this public module carries no
 * formula (DD-A43 a). Re-derived by the owner-only tests/fixtures_oracle.test.ts (bit-exact), which fails on any drift.
 */
export const RECORDED_FITNESS: Readonly<Record<string, number>> = {
  '0.18|0.3|20|18': 0.7249473684210527,
  '0.26|0.3|40|40': 0.6270292397660819,
  '0.31|0.3|70|55': 0.5229941520467837,
  '0.12|0.3|30|12': 0.709672514619883,
  '0.15|0.3|45|18': 0.6633918128654972,
  '0.18|0.3|60|24': 0.6171111111111112,
  '0.21|0.3|80|30': 0.5597192982456141,
  '0.2|0.3|28|14': 0.7238011695906432,
  '0.24000000000000002|0.3|52|19': 0.6626783625730994,
  '0.28|0.3|66|24': 0.6237777777777778,
  '0.1|0.3|35|16': 0.6819298245614035,
  '0.15000000000000002|0.3|58|24': 0.6155555555555556,
  '0.2|0.3|90|32': 0.5291812865497076,
  '0.15|0.3|33|20': 0.6837426900584795,
  '0.31|0.3|66|20': 0.6424093567251461,
  '0.22|0.3|58|20': 0.642187134502924,
  '0.2|0.3|50|20': 0.6559649122807018,
  '0.2|0.3|44|20': 0.6692982456140351,
  '0.33|0.3|38|20': 0.7086315789473685,
};

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** The server's input domain: AP and recall in [0, 1], latency and size > 0, all finite. Outside it the server answers null (never 0). */
export function inMetricDomain(m: { ap?: unknown; recall?: unknown; latency_ms?: unknown; size_mb?: unknown }): boolean {
  return finite(m.ap) && finite(m.recall) && finite(m.latency_ms) && finite(m.size_mb) && m.ap >= 0 && m.ap <= 1 && m.recall >= 0 && m.recall <= 1 && m.latency_ms > 0 && m.size_mb > 0;
}

/**
 * STAND-IN fitness of an ad-hoc test row whose metric tuple the synthetic server never scored: a deterministic value in
 * [0.05, 0.95] derived from the evaluation id ONLY (FNV-1a). It is not a fitness formula and means nothing beyond "the server
 * returned some valid fitness"; a test that needs a value consistent with the metrics passes `fitness` explicitly or uses the
 * owner-only tests/fixtures_oracle.ts.
 */
export function standInFitness(id: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i += 1) { h ^= id.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return Math.round((0.05 + 0.9 * (h / 0x100000000)) * 10000) / 10000;
}

/** Fitness the synthetic server stamps on a row: null outside the metric domain, the recorded value, else the stand-in. */
export function stampedFitness(row: Pick<Candidate, 'id' | 'ap' | 'recall' | 'latency_ms' | 'size_mb'>): number | null {
  if (!inMetricDomain(row)) return null;
  return RECORDED_FITNESS[metricKey(row)] ?? standInFitness(row.id);
}

let counter = 0;

export interface CandInit extends Partial<Candidate> {
  ap?: number | null;
  recall?: number | null;
  latency_ms?: number | null;
  size_mb?: number | null;
}

/** Complete synthetic EVALUATED row; `fitness` (unless given) is the server-stamped value (recorded, else a stand-in: `stampedFitness`). */
export function cand(over: CandInit = {}): Candidate {
  counter += 1;
  const base: Candidate = {
    id: `synthetic-eval-${counter}`,
    candidate_id: `synthetic-cand-${counter}`,
    name: `synthetic-model-${counter}`,
    family: 'GA-mixed',
    arm: 'GA',
    generation: 0,
    rung: 'L0',
    epochs: 10,
    status: 'EVALUATED',
    ap: 0.2,
    recall: 0.3,
    latency_ms: 50,
    size_mb: 20,
    fitness: null,
    device: 'synthetic-gpu',
    precision: 'FP16',
    latency_source: 'measured',
    parents: [],
    blocks: [],
    // 1.5.0 public values: relative to the run directory, never an absolute host path (files are fetched by id)
    architecture_path: `architectures/synthetic-cand-${counter}.yaml`,
    checkpoint_path: `checkpoints/synthetic-cand-${counter}.pt`,
    artifacts: [],
    objective_hash: OBJ_HASH,
    evaluation_hash: EVAL_HASH,
    split_hash: SPLIT_HASH,
    latency_scope: 'forward_and_postprocess',
    comparison_group: GROUP,
    search_seed: 101,
    batch: 1,
    resolved_input_shape: [640, 640],
    requested_input_shape: [640, 640],
    epoch_target_total: 10,
    protocol_hash: PROTOCOL_HASH,
  };
  const merged = { ...base, ...over } as Candidate;
  if (over.fitness === undefined) merged.fitness = stampedFitness(merged);
  return merged;
}

export function resetCounter(): void {
  counter = 0;
}

export function baselineRow(name: string, latency: number, ap: number, size: number, over: CandInit = {}): Candidate {
  return cand({ id: `synthetic-eval-${name}`, candidate_id: `synthetic-cand-${name}`, name, family: 'YOLO11', arm: 'Baseline', search_seed: undefined, ap, latency_ms: latency, size_mb: size, ...over });
}

export function gen(over: Partial<Generation> = {}): Generation {
  return {
    arm: 'GA',
    generation: 0,
    rung: 'L0',
    complete: true,
    representative_candidate_id: null,
    comparison_group: GROUP,
    search_seed: 101,
    planning_closed: true,
    planned_count: 4,
    evaluated_count: 4,
    failed_count: 0,
    not_admitted_count: 0,
    pending_count: 0,
    ...over,
  };
}

/**
 * Public block lineage of a crossover child (contract 1.5.0 Block, DD-A19): source kinds A / B / mutation; the last row was
 * repaired after the crossover and is reported with the source it was repaired from (repair folded: no repair kind, no old
 * name, no before/after, no repair reason; those stay owner-only). `params` is the YAML row.
 */
export const BLOCKS: Block[] = [
  { name: 'Conv-3x3', source: 'parent_a', source_kind: 'parent_a', mutated: false, position: 0, operation: 'inherit', parent_block_id: 'synthetic-cand-p1:0', params: { module: 'Conv', from: -1, repeats: 1, args: [32, 3, 2], scope: 'backbone' } },
  { name: 'C2f-attn', source: 'mutation', source_kind: 'mutation', mutated: true, position: 1, operation: 'mutate', parent_block_id: 'synthetic-cand-p2:1', params: { module: 'C2f', from: -1, repeats: 2, args: [64, true], scope: 'backbone' } },
  { name: 'SPPF', source: 'parent_b', source_kind: 'parent_b', mutated: false, position: 2, operation: 'inherit', parent_block_id: 'synthetic-cand-p2:2', params: { module: 'SPPF', from: -1, repeats: 1, args: [128, 5], scope: 'backbone' } },
  { name: 'Conv-1x1', source: 'parent_b', source_kind: 'parent_b', mutated: true, position: 3, operation: 'inherit', parent_block_id: 'synthetic-cand-p2:3', params: { module: 'Conv', from: [-1, 2], repeats: 1, args: [64, 1, 1], scope: 'head' } },
];

export function artifactSet(evaluationId: string, mode: 'signed_url' | 'bearer_fetch' = 'signed_url', sample = 'synthetic-sample-001'): Artifact[] {
  return (['gt', 'tp', 'fn', 'fp'] as const).map((kind) => ({
    id: `synthetic-art-${evaluationId}-${kind}`,
    kind,
    url: `/synthetic/artifacts/${kind}.png`,
    auth_mode: mode,
    sample_id: sample,
    evaluation_id: evaluationId,
    split_hash: SPLIT_HASH,
    state: 'READY',
    matching: { counts: { tp: 7, fp: 2, fn: 3, gt: 10 }, sample_recall: 0.7, rule: 'greedy_conf_desc_iou_v1', iou: 0.5, conf: 0.25 },
  }));
}

export function fixtureCandidates(): Candidate[] {
  resetCounter();
  const rows: Candidate[] = [
    baselineRow('yolo11s', 20, 0.18, 18),
    baselineRow('yolo11m', 40, 0.26, 40),
    baselineRow('yolo11l', 70, 0.31, 55),
  ];
  const ga0 = [30, 45, 60, 80].map((lat, i) => cand({ name: `ga-g0-${i}`, candidate_id: `synthetic-ga-g0-${i}`, id: `synthetic-ev-ga-g0-${i}`, generation: 0, latency_ms: lat, ap: 0.12 + i * 0.03, size_mb: 12 + i * 6 }));
  const ga1 = [28, 52, 66].map((lat, i) =>
    cand({ name: `ga-g1-${i}`, candidate_id: `synthetic-ga-g1-${i}`, id: `synthetic-ev-ga-g1-${i}`, generation: 1, latency_ms: lat, ap: 0.2 + i * 0.04, size_mb: 14 + i * 5, parents: i === 0 ? [] : [`synthetic-ga-g0-${i}`, `synthetic-ga-g0-${i + 1}`], blocks: i === 1 ? BLOCKS : [] }),
  );
  const failed = cand({ name: 'ga-g1-failed', candidate_id: 'synthetic-ga-g1-3', id: 'synthetic-ev-ga-g1-3', generation: 1, status: 'TRAIN_FAILED', candidate_status: 'TRAIN_FAILED', reason_code: 'OOM', ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null, latency_source: 'pending' });
  const open = cand({ name: 'ga-g2-training', candidate_id: 'synthetic-ga-g2-0', id: 'synthetic-ev-ga-g2-0', generation: 2, status: 'TRAINING', candidate_status: 'TRAINING', ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null, latency_source: 'pending', progress: { epoch: 4, epoch_target_total: 10, learning_rate: 0.0008, global_step: 420 } });
  const rnd = [35, 58, 90].map((lat, i) => cand({ name: `rnd-g0-${i}`, candidate_id: `synthetic-rnd-g0-${i}`, id: `synthetic-ev-rnd-g0-${i}`, arm: 'IndependentRandom', family: 'Random-mixed', generation: 0, latency_ms: lat, ap: 0.1 + i * 0.05, size_mb: 16 + i * 8 }));
  const unresolved = cand({ name: 'ga-missing-batch', candidate_id: 'synthetic-ga-nb', id: 'synthetic-ev-ga-nb', generation: 0, batch: undefined, latency_ms: 33, ap: 0.15 });
  return [...rows, ...ga0, ...ga1, failed, open, ...rnd, unresolved];
}

export function fixtureGenerations(): Generation[] {
  return [
    gen({ generation: 0, representative_candidate_id: 'synthetic-ga-g0-3', representative_evaluation_id: 'synthetic-ev-ga-g0-3', planned_count: 5, evaluated_count: 5 }),
    gen({ generation: 1, representative_candidate_id: 'synthetic-ga-g1-2', representative_evaluation_id: 'synthetic-ev-ga-g1-2', planned_count: 4, evaluated_count: 3, failed_count: 1 }),
    gen({ generation: 2, complete: false, planning_closed: false, planned_count: 4, evaluated_count: 0, pending_count: 4 }),
    gen({ arm: 'IndependentRandom', generation: 0, planned_count: 3, evaluated_count: 3 }),
  ];
}

export function fixtureRun(status: Run['status'] = 'RUNNING'): Run {
  return {
    id: RUN_ID,
    status,
    protocol_hash: PROTOCOL_HASH,
    created_at: '2026-10-06T10:00:00Z',
    namespace: 'synthetic_test',
    kind: 'SEARCH',
    parent_run_id: null,
    execution_mode: 'PILOT',
    purpose: 'SMOKE',
    costs: { ga_billed_gpu_seconds: 5400, random_billed_gpu_seconds: 3600, actual_gpu_seconds: 7200, baseline_gpu_seconds: 1800 },
    resolved_protocol: {
      schema_version: 'nas-console/1.0',
      fitness: { latency_bounds_ms: [20, 200], size_bounds_MB: [5, 100] },
      rungs: [{ id: 'L0', epoch_target_total: 2, fitness_weights: WEIGHTS, promotion: null }],
      training: { warmup_epochs: 0, scheduler_horizon_epochs: 2 },
      final_retraining: { top_k_per_arm_per_seed: 2, epoch_target_total: 4, scheduler_horizon_epochs: 4 },
      console: {
        paths: { dataset_yaml: '/srv/x/data.yaml', train_root: '/srv/x/train', search_val_root: '/srv/x/val', final_eval_root: '/srv/x/test', output_root: '/srv/out' },
        baselines: [{ id: 'yolo11s', enabled: true }],
        search: { arms: ['GA'], seeds: [101], budget_gpu_hours: 0.5 },
        benchmark: {},
      },
    },
  };
}

export function fixtureWorkers(): Worker[] {
  return [{ id: 'synthetic-worker-0', status: 'busy', device: 'cuda:0', candidate_id: 'synthetic-ga-g2-0', utilization_percent: 87, epoch: 4, epoch_target_total: 10, learning_rate: 0.0008 }];
}

export function fixtureEvents(lastSeq = 5): ApiEvent[] {
  const ev: ApiEvent[] = [];
  for (let s = 1; s <= lastSeq; s++) ev.push({ seq: s, type: s === 1 ? 'run.updated' : 'log', run_id: RUN_ID, payload: s === 1 ? { id: RUN_ID, status: 'RUNNING', protocol_hash: PROTOCOL_HASH } : { message: `synthetic log ${s}` } });
  return ev;
}

export function fixtureSnapshot(status: Run['status'] = 'RUNNING', lastSeq = 5): Snapshot {
  return { run: fixtureRun(status), candidates: fixtureCandidates(), generations: fixtureGenerations(), workers: fixtureWorkers(), events: fixtureEvents(lastSeq), last_seq: lastSeq };
}

/** An open contract object (declared keys plus server keys the schema leaves open, e.g. Health.capabilities.full_state_resume). */
export function openObject<T>(value: Readonly<Record<string, unknown>>): T {
  return value as unknown as T;
}

export function fixtureHealth(): Health {
  return {
    status: 'ok',
    commit_sha: 'synthetic0000000',
    capabilities: openObject<Health['capabilities']>({ full_state_resume: true }),
    versions: openObject<Health['versions']>({ torch: 'synthetic', mbnas_server: '0.0.0-test' }),
    devices: [{ name: 'synthetic GPU', memory_mb: 81920 }],
    registry: [
      { id: 'yolo11s', family: 'YOLO11', status: 'AVAILABLE', capabilities: { init_modes: ['scratch', 'pretrained_native'], native_loss: 'v8DetectionLoss' } },
      { id: 'rf-detr-small', family: 'Transformer', status: 'BLOCKED_SOURCE', reason: 'weights download blocked (synthetic)', capabilities: {} },
      { id: 'rt-detr-l', family: 'Hybrid', status: 'UNVERIFIED', reason: 'smoke not run (synthetic)', capabilities: {} },
    ],
    allowlists: { dataset_roots: ['/srv/datasets'], output_roots: ['/srv/out'] },
    worker_count: 1,
    server_time: '2026-10-06T10:00:00Z',
  };
}

// ---- server presets (SYNTHETIC copies of the shape of GET /api/v1/protocols/presets) -----------------------------------
// The console embeds no template any more (DD-A8 / DD-A21): the server presets are the only policy source, so tests that need
// a full protocol take one of these. Rung epochs, weights, thresholds and recipe values follow the public handoff DESIGN
// EXAMPLE (DD-A21 allows them in test fixtures; they are illustrative, not research values); paths are synthetic /srv/...
// paths (WP-6 R4), the benchmark device is synthetic.

/** The 14 default baselines in their fixed order (ids as in the contract's x-required-default-baselines). */
export const FIXTURE_BASELINE14: readonly { id: string; name: string; family: string }[] = [
  { id: 'yolo11s', name: 'YOLO11S', family: 'YOLO11' },
  { id: 'yolo11m', name: 'YOLO11M', family: 'YOLO11' },
  { id: 'yolo11l', name: 'YOLO11L', family: 'YOLO11' },
  { id: 'yolo11x', name: 'YOLO11X', family: 'YOLO11' },
  { id: 'yolo26s', name: 'YOLO26S', family: 'YOLO26' },
  { id: 'yolo26m', name: 'YOLO26M', family: 'YOLO26' },
  { id: 'yolo26l', name: 'YOLO26L', family: 'YOLO26' },
  { id: 'yolo26x', name: 'YOLO26X', family: 'YOLO26' },
  { id: 'rf-detr-small', name: 'RF-DETR Small', family: 'Transformer' },
  { id: 'rf-detr-medium', name: 'RF-DETR Medium', family: 'Transformer' },
  { id: 'rf-detr-large', name: 'RF-DETR Large', family: 'Transformer' },
  { id: 'rt-detr-l', name: 'RT-DETR-L', family: 'Hybrid' },
  { id: 'rt-detr-x', name: 'RT-DETR-X', family: 'Hybrid' },
  { id: 'etri-controlled-cnn-transformer', name: 'ETRI CNN–Transformer', family: 'Hybrid' },
];

/** Synthetic DEFAULT_BASELINE14 preset protocol: baseline14, cumulative 10/30/90 epochs, AdamW recipe (design example values). */
export function fixtureDesignExampleProtocol(): Record<string, unknown> {
  const root = '/srv/datasets/synthetic_design';
  return {
    schema_version: 'nas-console/1.0',
    protocol_id: 'synthetic-design-example',
    status: 'DESIGN_EXAMPLE_NOT_EXECUTABLE',
    training: {
      initialization: 'scratch', optimizer: 'AdamW', lr_initial: 0.001, lr_final_ratio: 0.01, weight_decay: 0.0005, betas: [0.9, 0.999],
      scheduler: 'cosine_global_search_horizon', scheduler_horizon_epochs: 90, warmup_epochs: 3, batch_per_gpu: 16, gradient_accumulation: 1,
      gpus_per_candidate: 1, precision: 'amp_fp16_fp32', requested_input_size: [640, 640], augmentation_profile: 'PINNED_NATIVE_PROFILE_REQUIRED',
      close_mosaic_epochs: 0, loss_profile: 'model_version_native_pinned_and_exported', change_loss_weights_at_promotion: false,
      candidate_early_stop: 'disabled_for_search_rung_comparability',
    },
    fitness: { latency_bounds_ms: [20, 200], size_bounds_MB: [5, 100] },
    rungs: [
      { id: 'L0', epoch_target_total: 10, fitness_weights: { ap: 0.2, recall: 0.1, latency_reward: 0.4, size_reward: 0.3 }, promotion: { to: 'L1', score_ge: 0.4, ap_ge: 0.02 } },
      { id: 'L1', epoch_target_total: 30, fitness_weights: { ap: 0.45, recall: 0.15, latency_reward: 0.25, size_reward: 0.15 }, promotion: { to: 'L2', score_ge: 0.55, ap_ge: 0.05 } },
      { id: 'L2', epoch_target_total: 90, fitness_weights: { ap: 0.6, recall: 0.1, latency_reward: 0.2, size_reward: 0.1 }, promotion: null },
    ],
    hard_constraints: { latency_le_ms: 100, weights_size_le_MB: 80, proxy_may_certify_final_deployment: false },
    comparison: { arms: ['GA', 'IndependentRandom'], independent_search_seed_pairs: [101, 202, 303], search_budget_GPU_hours_per_arm_per_seed: 12 },
    final_retraining: { finalist_selection: 'highest_AP_at_L2_with_measured_latency_constraint', top_k_per_arm_per_seed: 1, initialization: 'scratch_with_pinned_recipe', epoch_target_total: 300, scheduler_horizon_epochs: 300, budget_GPU_hours_separate: null },
    console: {
      execution_mode: 'PILOT',
      paths: { dataset_yaml: `${root}/data.yaml`, train_root: `${root}/train`, search_val_root: `${root}/valid`, final_eval_root: `${root}/test`, output_root: '/srv/out/design_example' },
      baselines: FIXTURE_BASELINE14.map((b) => ({ ...b, enabled: true, source: 'synthetic fixture' })),
      search: { arms: ['GA', 'IndependentRandom'], population: 12, generations: 8, mutation_rate: 0.15, seeds: [101, 202, 303], budget_gpu_hours: 12 },
      benchmark: { device: 'synthetic-device', precision: 'FP16', batch: 1, warmup: 50, repeats: 200 },
      baseline_epoch_target: 90,
      baseline_capture_rungs: true,
      baseline_budget_gpu_hours: 24,
    },
  };
}

/** Synthetic PILOT_SMOKE preset protocol: 2/4/6 epochs, one small model per family, permissive smoke gates (smoke values). */
export function fixturePilotSmokeProtocol(): Record<string, unknown> {
  const p = fixtureDesignExampleProtocol() as Record<string, any>;
  const root = '/srv/datasets/synthetic_pilot';
  p.status = 'PRESET_PILOT_SMOKE';
  p.protocol_id = 'synthetic-pilot-smoke';
  Object.assign(p.training, { scheduler_horizon_epochs: 6, warmup_epochs: 0, batch_per_gpu: 8, gradient_accumulation: 1, workers: 0 });
  p.rungs = (p.rungs as { id: string; fitness_weights: Record<string, number> }[]).map((r, i) => ({
    id: r.id, epoch_target_total: [2, 4, 6][i], fitness_weights: { ...r.fitness_weights }, promotion: i < 2 ? { to: `L${i + 1}`, score_ge: 0, ap_ge: 0 } : null,
  }));
  p.hard_constraints = { latency_le_ms: 1000, weights_size_le_MB: 500, proxy_may_certify_final_deployment: false };
  p.comparison = { ...p.comparison, independent_search_seed_pairs: [101], search_budget_GPU_hours_per_arm_per_seed: 0.75 };
  Object.assign(p.final_retraining, { epoch_target_total: 2, scheduler_horizon_epochs: 2 });
  p.console = {
    preset: 'PILOT_SMOKE', purpose: 'SMOKE', run_namespace: 'pilot_smoke', execution_mode: 'PILOT', label: 'PILOT_SMOKE (smoke values, not research results)',
    paths: { dataset_yaml: `${root}/data.yaml`, train_root: `${root}/images/train`, search_val_root: `${root}/images/search_val`, final_eval_root: `${root}/images/final_eval`, output_root: '/srv/out/pilot_smoke' },
    baselines: [
      { id: 'yolo11n', name: 'YOLO11N', family: 'YOLO11', enabled: true },
      { id: 'yolo26n', name: 'YOLO26N', family: 'YOLO26', enabled: true },
      { id: 'rt-detr-l', name: 'RT-DETR-L', family: 'Hybrid', enabled: true },
      { id: 'rf-detr-nano', name: 'RF-DETR Nano', family: 'Transformer', enabled: true },
      { id: 'etri-controlled-cnn-transformer', name: 'ETRI CNN–Transformer', family: 'Hybrid', enabled: true },
    ],
    baseline_override_reason: 'synthetic: one small representative per adapter family instead of baseline14',
    search: { arms: ['GA', 'IndependentRandom'], population: 4, generations: 2, mutation_rate: 0.35, seeds: [101], budget_gpu_hours: 0.75, crossover_rate: 1.0 },
    benchmark: { device: 'synthetic-gpu-0', precision: 'FP16', batch: 1, warmup: 20, repeats: 100 },
    baseline_epoch_target: 6,
    baseline_capture_rungs: true,
    baseline_budget_gpu_hours: 0.25,
    retention: { full_state: 'last_rung_end_only' },
  };
  return p;
}

/** GET /protocols/presets of the synthetic server: DEFAULT_BASELINE14 (3 rungs 10/30/90) and PILOT_SMOKE (2/4/6), in the server's order. */
export function fixturePresets(): Preset[] {
  return [
    { id: 'DEFAULT_BASELINE14', name: 'Default baseline14 design example', purpose: 'RESEARCH_TEMPLATE', execution_mode: 'PILOT', namespace: 'default', description: 'SYNTHETIC test preset (design example values, synthetic paths)', protocol: fixtureDesignExampleProtocol() as Preset['protocol'] },
    { id: 'PILOT_SMOKE', name: 'Pilot smoke', purpose: 'SMOKE', execution_mode: 'PILOT', namespace: 'pilot_smoke', description: 'SYNTHETIC test preset (smoke values, synthetic paths)', protocol: fixturePilotSmokeProtocol() as Preset['protocol'] },
  ];
}

export function fixtureValidateOk(resolved: Record<string, unknown>): ValidateResponse {
  return {
    valid: true,
    errors: [],
    warnings: [{ field: 'training.batch_per_gpu', message: 'synthetic warning', code: 'W_SYNTH' }],
    protocol_hash: PROTOCOL_HASH,
    resolved,
    capabilities: openObject<ValidateResponse['capabilities']>({ full_state_resume: true }),
    resolved_diff: [{ field: 'training.requested_input_size', requested: [640, 640], resolved: [640, 640], reason: 'multiple of stride 32' }],
    validation_id: 'synthetic-validation-1',
  };
}

// ---- final retraining (contract v1.2.0) ----------------------------------------------------------
export const FINAL_RUN_ID = 'synthetic-run-final-0001';

export function fixtureFinalists(): Finalist[] {
  return [
    { candidate_id: 'synthetic-ga-g1-2', source_evaluation_id: 'synthetic-ev-ga-g1-2', arm: 'GA', search_seed: 101, rung: 'L2', ap: 0.31, latency_ms: 66, selection_rule: 'max_L2_AP_under_latency_constraint' },
    { candidate_id: 'synthetic-rnd-g0-1', source_evaluation_id: 'synthetic-ev-rnd-g0-1', arm: 'IndependentRandom', search_seed: 101, rung: 'L2', ap: 0.22, latency_ms: 58, selection_rule: 'max_L2_AP_under_latency_constraint' },
  ];
}

export function fixtureFinalRun(status: Run['status'] = 'RUNNING', finalists: Finalist[] = fixtureFinalists()): Run {
  return { ...fixtureRun(status), id: FINAL_RUN_ID, kind: 'FINAL_RETRAIN', parent_run_id: RUN_ID, finalists, reason_code: finalists.length === 0 ? 'NO_FINALIST' : null };
}

/** FINAL retraining row: arm Final, rung FINAL, comparison_group final; its single parent is the finalist source candidate. */
export function finalRow(over: CandInit = {}): Candidate {
  return cand({ arm: 'Final', family: 'Final-retrain', rung: 'FINAL', comparison_group: 'final', epochs: 4, epoch_target_total: 4, generation: 0, parents: ['synthetic-ga-g1-2'], search_seed: 101, ...over });
}

export function fixtureFinalSnapshot(status: Run['status'] = 'RUNNING', finalists: Finalist[] = fixtureFinalists()): Snapshot {
  const rows = finalists.map((f, i) => finalRow({ id: `synthetic-ev-final-${i}`, candidate_id: `synthetic-final-${i}`, name: `final-${f.candidate_id}`, parents: [f.candidate_id], latency_ms: f.latency_ms ?? 60, ap: f.ap ?? 0.3 }));
  return { run: fixtureFinalRun(status, finalists), candidates: rows, generations: [], workers: [], events: [], last_seq: 0 };
}
