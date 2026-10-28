/**
 * SYNTHETIC archive fixtures (design 4 / 5 WP-2). Every id carries the "synthetic-" prefix and every number is
 * invented by a small deterministic generator to exercise the UI; none of it is a measured result. Lives outside
 * src/ so it never reaches the production bundle. `archiveResponder` answers the /api/v1/archive routes for the
 * vitest fetch mock and for the Playwright page.route mock.
 */
import type {
  ArchiveBaseline,
  ArchiveBaselineList,
  ArchiveCandidateDetail,
  ArchiveCandidateRow,
  ArchiveCharacteristics,
  ArchiveCompare,
  ArchiveConfirm,
  ArchiveFidelity,
  ArchiveGeneration,
  ArchiveGenerationDetail,
  ArchiveGenerationList,
  ArchivePoint,
  ArchiveLive,
  ArchiveRun,
  ArchiveRunList,
  ArchiveRunSummary,
} from '../src/archive/types';
import { front2d, front4d, frontChange } from '../src/archive/pareto';
import { fixtureHealth } from './fixtures';

export const RUN_A = 'synthetic-archive-run-a'; // done, 4 generations, finalists, confirm, fidelity
export const RUN_B = 'synthetic-archive-run-b'; // running (partial jsonl), same dataset as A
export const RUN_C = 'synthetic-archive-run-c'; // failed (stop rule), other dataset, nothing else
export const DATASET_1 = 'synthetic-dataset-one';
export const DATASET_2 = 'synthetic-dataset-two';
export const ROOT = '/synthetic/archive-root';

const MODULES = ['Conv', 'C3k2', 'SPPF', 'C2PSA', 'A2C2f', 'Detect'] as const;

/** deterministic pseudo random in [0,1) */
function rng(seed: number): () => number {
  let s = seed >>> 0 || 1;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

export interface SynthOpts {
  runId: string;
  gens: number;
  perGen: number;
  seed: number;
  /** add oom / train_fail / missing-latency rows */
  failures?: boolean;
  finals?: boolean;
}

interface Cand {
  row: ArchiveCandidateRow;
  mods: string[]; // module names of the cfg (backbone + head), one entry per row of the cfg
}

export interface SynthRun {
  opts: SynthOpts;
  rows: ArchiveCandidateRow[];
  cands: Map<string, Cand>;
  generations: ArchiveGeneration[];
}

const hex = (r: () => number): string => Array.from({ length: 12 }, () => Math.floor(r() * 16).toString(16)).join('');

export function synthRun(o: SynthOpts): SynthRun {
  const r = rng(o.seed);
  const rows: ArchiveCandidateRow[] = [];
  const cands = new Map<string, Cand>();
  const seenFp = new Set<string>();
  const generations: ArchiveGeneration[] = [];
  let prevHist: Record<string, number> | null = null;
  let prevFront: string[] = [];
  let bestMap = -1;
  let bestRec = -1;
  let bestLat = Infinity;
  let hv = 100;
  const prev: { hv: number | null; map: number | null; rec: number | null; lat: number | null } = { hv: null, map: null, rec: null, lat: null };

  for (let g = 0; g < o.gens; g++) {
    const genRows: ArchiveCandidateRow[] = [];
    for (let n = 0; n < o.perGen; n++) {
      const cid = `synthetic-g${String(g).padStart(3, '0')}c${String(n).padStart(4, '0')}`;
      const ok = !(o.failures && ((g === 1 && n === o.perGen - 1) || (g === 2 && n === o.perGen - 2)));
      const mode = g === 0 ? 'seeded_anchor' : n < 3 ? 'ga_crossover' : n < 5 ? 'random_crossover' : 'v26_topup';
      const mods = ['Conv', 'Conv', 'C3k2', ...Array.from({ length: 2 + Math.floor(r() * 3) }, () => MODULES[Math.floor(r() * 5)]), 'SPPF', 'Detect'];
      const quality = 0.30 + 0.025 * g + r() * 0.08;
      const lat = 4 + r() * 6 - g * 0.15;
      const params = Math.round(1.8e6 + r() * 1.4e6);
      const fp = !ok || (g > 0 && n === 2) ? (rows[rows.length - 1]?.fingerprint ?? hex(r)) : hex(r);
      const parents = g === 0 ? ['/synthetic/cfg/yolov11n.yaml'] : mode === 'ga_crossover' ? [`synthetic-g${String(g - 1).padStart(3, '0')}c${String(n).padStart(4, '0')}`, `synthetic-g${String(g - 1).padStart(3, '0')}c${String((n + 1) % o.perGen).padStart(4, '0')}`] : [];
      const missingLat = o.failures && g === 2 && n === 3;
      const row: ArchiveCandidateRow = {
        candidate_id: cid,
        generation: g,
        fingerprint: fp,
        tier: 'T@8e',
        status: ok ? 'ok' : g === 1 ? 'oom' : 'train_fail',
        error: ok ? null : g === 1 ? 'CUDA out of memory (synthetic)' : 'loss became NaN (synthetic)',
        seed: 42,
        epochs: 8,
        metrics: ok
          ? {
              map50_95: quality,
              map50: Math.min(0.99, quality + 0.4),
              small_target_recall: Math.min(0.99, 0.5 + quality * 0.9),
              precision: 0.8,
              recall: 0.7,
              latency_median_ms: missingLat ? null : lat,
              latency_p90_ms: missingLat ? null : lat + 0.3,
              latency_mean_ms: lat + 0.05,
              latency_std_ms: 0.2,
              latency_cv: 0.03,
              forward_only_median_ms: missingLat ? null : lat - 0.4,
              t_post_ms: 0.4,
              head_mode: n % 2 ? 'nms' : 'e2e',
              params,
              params_deploy: params,
              params_train: params + 8000,
              gflops: 0,
              peak_mem_mb: 2400,
              train_time_s: 90 + n,
              n_small_gt: 1891,
              n_boxes_conf025: 12.5,
              latency_protocol_hash: 'synthproto01',
            }
          : { map50_95: null, latency_median_ms: null, params_deploy: null },
        lineage: { mode, parents, mutation: mode === 'ga_crossover' && n === 1 ? { op: 'swap_block', rate: 0.2 } : null, cache_hit: n === 2 && g > 0 ? `synthetic-g${String(g - 1).padStart(3, '0')}c0002` : null, notes_parse: 'ok' },
        roles: [],
        is_final: false,
        on_front2d: false,
        on_front4d: false,
        delta_vs_baseline: ok
          ? { map50_95: quality - 0.4939, latency_median_ms: missingLat ? null : lat - 7.6127, params_deploy: params - 2375031, baseline: 'baseline_yolo26n', baseline_tier: 'final@12e' }
          : { map50_95: null, latency_median_ms: null, params_deploy: null, baseline: null, baseline_tier: null },
        hashes: { config: 'cfg' + hex(r).slice(0, 6), genotype: fp, metric_protocol: 'metric000001', training_protocol: 'train0000001' },
        new_fingerprint: ok ? !seenFp.has(fp) : null,
        has_cfg: ok,
        has_checkpoint: ok && n !== 4,
      };
      genRows.push(row);
      if (ok) cands.set(cid, { row, mods });
      else cands.set(cid, { row, mods: [] });
    }
    // promote the two best of the generation to T@10e
    const okRows = genRows.filter((x) => x.status === 'ok').sort((a, b) => (b.metrics?.map50_95 ?? 0) - (a.metrics?.map50_95 ?? 0));
    for (const top of okRows.slice(0, 2)) {
      genRows.push({ ...top, tier: 'T@10e', epochs: 10, metrics: { ...top.metrics, map50_95: (top.metrics?.map50_95 ?? 0) + 0.01 }, is_final: false });
    }
    rows.push(...genRows);
  }
  // finals: the best two candidates overall
  if (o.finals) {
    const best = rows.filter((x) => x.status === 'ok' && x.tier === 'T@8e').sort((a, b) => (b.metrics?.map50_95 ?? 0) - (a.metrics?.map50_95 ?? 0)).slice(0, 2);
    best.forEach((b, i) => {
      rows.push({ ...b, tier: 'final@12e', epochs: 12, is_final: true, metrics: { ...b.metrics, map50_95: (b.metrics?.map50_95 ?? 0) + 0.04 } });
      const base = rows.find((x) => x.candidate_id === b.candidate_id && x.tier === 'T@8e');
      if (base) base.roles = [i === 0 ? 'BEST_ACCURACY' : 'PARETO_KNEE'];
      for (const x of rows) if (x.candidate_id === b.candidate_id) x.roles = [i === 0 ? 'BEST_ACCURACY' : 'PARETO_KNEE'];
    });
  }

  // generation summaries (cumulative values, fronts over search-tier rows up to g)
  for (let g = 0; g < o.gens; g++) {
    const upto = rows.filter((x) => x.generation <= g && x.status === 'ok' && x.tier !== 'final@12e');
    const here = rows.filter((x) => x.generation === g);
    const hereOk = here.filter((x) => x.status === 'ok' && x.tier === 'T@8e');
    const pts = upto.map((x) => ({ id: x.candidate_id + '|' + x.tier, map: x.metrics?.map50_95, lat: x.metrics?.latency_median_ms, recall: x.metrics?.small_target_recall, params: x.metrics?.params_deploy }));
    const f2 = [...new Set(front2d(pts).map((i) => i.split('|')[0]))];
    const f4 = [...new Set(front4d(pts).map((i) => i.split('|')[0]))];
    for (const x of rows) {
      if (x.generation === g) {
        x.on_front2d = f2.includes(x.candidate_id) && g === o.gens - 1;
        x.on_front4d = f4.includes(x.candidate_id) && g === o.gens - 1;
      }
    }
    for (const x of hereOk) {
      bestMap = Math.max(bestMap, x.metrics?.map50_95 ?? -1);
      bestRec = Math.max(bestRec, x.metrics?.small_target_recall ?? -1);
      if (typeof x.metrics?.latency_median_ms === 'number') bestLat = Math.min(bestLat, x.metrics.latency_median_ms);
    }
    hv += 3 + g * 1.5;
    const newFp = hereOk.filter((x) => !seenFp.has(x.fingerprint ?? '')).map((x) => x.candidate_id);
    hereOk.forEach((x) => seenFp.add(x.fingerprint ?? ''));
    const hist: Record<string, number> = {};
    for (const x of hereOk) for (const m of cands.get(x.candidate_id)?.mods ?? []) hist[m] = (hist[m] ?? 0) + 1;
    const modes: Record<string, number> = {};
    for (const x of here.filter((y) => y.tier === 'T@8e')) modes[x.lineage?.mode ?? 'unknown'] = (modes[x.lineage?.mode ?? 'unknown'] ?? 0) + 1;
    const dist = (a: number[]): { min: number | null; med: number | null; max: number | null } => {
      const s = [...a].sort((p, q) => p - q);
      return s.length ? { min: s[0], med: s[Math.floor(s.length / 2)], max: s[s.length - 1] } : { min: null, med: null, max: null };
    };
    const ch: ArchiveCharacteristics = {
      n: hereOk.length,
      n_cfg: hereOk.length,
      n_cfg_missing: 0,
      n_prev: prevHist ? o.perGen : 0,
      n_prev_cfg: prevHist ? o.perGen : 0,
      module_candidates: Object.fromEntries(Object.keys(hist).map((m) => [m, hereOk.length])),
      module_histogram: hist,
      module_histogram_prev: prevHist ?? {},
      head_mode: { e2e: hereOk.filter((x) => x.metrics?.head_mode === 'e2e').length, nms: hereOk.filter((x) => x.metrics?.head_mode === 'nms').length },
      depth: { n_backbone: dist(hereOk.map((x) => (cands.get(x.candidate_id)?.mods.length ?? 0) - 2)), n_head: dist(hereOk.map(() => 2)), stage_count: dist(hereOk.map(() => 5)) },
      params_deploy: dist(hereOk.map((x) => x.metrics?.params_deploy as number)),
      latency_median_ms: dist(hereOk.map((x) => x.metrics?.latency_median_ms).filter((v): v is number => typeof v === 'number')),
      top_modules_delta: prevHist ? Object.keys(hist).map((m) => ({ module: m, count: hist[m], count_prev: prevHist?.[m] ?? 0, delta: hist[m] - (prevHist?.[m] ?? 0), delta_per_candidate: (hist[m] - (prevHist?.[m] ?? 0)) / Math.max(1, hereOk.length) })).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 3) : [],
    };
    const chg = frontChange(prevFront, f2);
    const gen: ArchiveGeneration = {
      generation: g,
      phase: g < o.gens / 2 ? 'early' : 'middle',
      population: o.perGen,
      valid: hereOk.length,
      train_fail: here.filter((x) => x.status === 'train_fail').length,
      degenerate: 0,
      unique_fingerprints: new Set(hereOk.map((x) => x.fingerprint)).size,
      archive_size: f4.length,
      hypervolume: hv,
      hv_improved: g > 0,
      stagnation: 0,
      best_map50_95: bestMap,
      best_small_recall: bestRec,
      best_latency: bestLat,
      mutation: g === 0 ? null : { mutation_rate: 0.2, prefer_modular: true, pure_random_injection: 0, reasons: ['synthetic'] },
      has_summary: true,
      cum_best: { map50_95: bestMap, small_target_recall: bestRec, latency_median_ms: bestLat },
      n_rows: here.length,
      status_counts: here.reduce<Record<string, number>>((m, x) => ({ ...m, [x.status]: (m[x.status] ?? 0) + 1 }), {}),
      new_fingerprints: newFp.length,
      new_fingerprint_ids: newFp,
      lineage_modes: modes,
      delta_vs_prev: { hypervolume: prev.hv === null ? null : hv - prev.hv, best_map50_95: prev.map === null ? null : bestMap - prev.map, best_small_recall: prev.rec === null ? null : bestRec - prev.rec, best_latency: prev.lat === null ? null : bestLat - prev.lat },
      delta_vs_baseline: { best_map50_95: bestMap - 0.4939, best_latency: bestLat - 7.6127, cum_best_map50_95: bestMap - 0.4939, baseline_name: 'baseline_yolo26n', baseline_tier: 'final@12e', note: 'synthetic: baseline is final-tier, candidates are search-tier' },
      front2d: f2,
      front4d: f4,
      front_added: chg.added,
      front_removed: chg.removed,
      front4d_added: null,
      front4d_removed: null,
      characteristics: ch,
    };
    generations.push(gen);
    prevHist = hist;
    prevFront = f2;
    prev.hv = hv;
    prev.map = bestMap;
    prev.rec = bestRec;
    prev.lat = bestLat;
  }
  return { opts: o, rows, cands, generations };
}

// ---- API shaped objects ---------------------------------------------------------------------------------------------

const SRC = (id: string): { run_dir: string; files: string[] } => ({ run_dir: `${ROOT}/${id}`, files: ['search/candidates/candidate_evaluations.jsonl', 'search/generations/gen_000_summary.json'] });

export interface FixtureState {
  /** generations of RUN_B that are visible (grows to simulate a running search) */
  bRunning: boolean;
  bGens: number;
  /** answer every call with 500 (network-like failure) when set */
  failAll?: boolean;
  /** number of times /archive/runs/{id} of RUN_B was fetched */
  detailHits: Record<string, number>;
}

export function newFixtureState(over: Partial<FixtureState> = {}): FixtureState {
  return { bRunning: true, bGens: 2, detailHits: {}, ...over };
}

const A = synthRun({ runId: RUN_A, gens: 4, perGen: 6, seed: 7, failures: true, finals: true });
const Bfull = synthRun({ runId: RUN_B, gens: 5, perGen: 5, seed: 11 });
export const SYNTH_A = A;

function runB(state: FixtureState): SynthRun {
  const gens = Bfull.generations.slice(0, state.bGens);
  return { ...Bfull, generations: gens, rows: Bfull.rows.filter((x) => x.generation < state.bGens) };
}

function summaryOf(id: string, state: FixtureState): ArchiveRunSummary {
  if (id === RUN_A) {
    const b = A.generations[A.generations.length - 1];
    return {
      id,
      family: 'yolo_ga_full_drone',
      path: `${ROOT}/${id}`,
      dataset_id: DATASET_1,
      scale: 'day',
      status: 'done',
      status_detail: 'NBCONVERT_EXIT=0 and final_summary.json',
      started_at: '2026-10-01T01:05:16Z',
      ended_at: '2026-10-01T09:30:00Z',
      duration_s: 30284,
      verdicts: { notebook: 'PASS', transition: 'GO', confirm: 'CONFIRMED', smoke_check: null },
      generations: { planned: 4, done: 4 },
      candidates: { total: A.rows.length, ok: A.rows.filter((x) => x.status === 'ok').length, failed: A.rows.filter((x) => x.status !== 'ok').length, by_status: null },
      best: { map50_95: b.best_map50_95, small_target_recall: b.best_small_recall, latency_min_ms: b.best_latency },
      judge_baseline: { name: 'baseline_yolo26n', tier: 'final@12e', status: 'ok', map50_95: 0.4939, latency_median_ms: 7.6127, params_deploy: 2375031 },
      arm: { space: 'modular', method: 'nsga2', seed: 42 },
      hashes: { protocol_v2_train: 'synthprotov2', metric_protocol: 'metric000001', dataset_manifest: 'synthmanifest' },
      has: { baselines: true, generations: true, pareto: true, fidelity: true, confirm: true, plots: true, live: false },
    };
  }
  if (id === RUN_B) {
    const rb = runB(state);
    const b = rb.generations[rb.generations.length - 1];
    return {
      id,
      family: 'yolo_ga_full_drone',
      path: `${ROOT}/${id}`,
      dataset_id: DATASET_1,
      scale: 'smoke',
      status: state.bRunning ? 'running' : 'done',
      status_detail: state.bRunning ? 'nb_pid alive' : 'NBCONVERT_EXIT=0',
      started_at: '2026-10-14T22:48:00Z',
      ended_at: null,
      duration_s: null,
      verdicts: { notebook: null, transition: null, confirm: null, smoke_check: null },
      generations: { planned: 5, done: rb.generations.length },
      candidates: { total: rb.rows.length, ok: rb.rows.filter((x) => x.status === 'ok').length, failed: 0, by_status: null },
      best: { map50_95: b.best_map50_95, small_target_recall: b.best_small_recall, latency_min_ms: b.best_latency },
      judge_baseline: { name: 'baseline_yolo26n', tier: 'final@12e', status: 'ok', map50_95: 0.4939, latency_median_ms: 7.6127, params_deploy: 2375031 },
      arm: null,
      hashes: { protocol_v2_train: 'synthprotov2', metric_protocol: 'metric000001', dataset_manifest: 'synthmanifest' },
      has: { baselines: false, generations: true, pareto: false, fidelity: false, confirm: false, plots: false, live: true },
    };
  }
  return {
    id,
    family: 'yolo_ga_full_visdrone',
    path: `${ROOT}/${id}`,
    dataset_id: DATASET_2,
    scale: 'mini',
    status: 'failed',
    status_detail: 'driver/stop_rule.json: stagnation',
    started_at: '2026-10-12T22:48:29Z',
    ended_at: '2026-10-13T01:00:00Z',
    duration_s: 7900,
    verdicts: { notebook: 'FAIL', transition: null, confirm: null, smoke_check: 'WARN' },
    generations: { planned: 6, done: 0 },
    candidates: { total: 0, ok: 0, failed: 0, by_status: null },
    best: { map50_95: null, small_target_recall: null, latency_min_ms: null },
    judge_baseline: null,
    arm: null,
    hashes: { protocol_v2_train: null, metric_protocol: 'metric000002', dataset_manifest: null },
    has: { baselines: false, generations: false, pareto: false, fidelity: false, confirm: false, plots: false, live: false },
  };
}

export function fixtureRunList(state: FixtureState = newFixtureState()): ArchiveRunList {
  return {
    roots: [
      { path: ROOT, family: 'yolo_ga_full_drone', exists: true },
      { path: '/synthetic/missing-root', family: 'yolo_ga_full_visdrone', exists: false },
    ],
    runs: [summaryOf(RUN_A, state), summaryOf(RUN_B, state), summaryOf(RUN_C, state)],
    scanned_at: '2026-10-15T10:00:00Z',
    source: { run_dir: null, files: [] },
    warnings: [],
  };
}

/** the run-level fields of a run that has none of them (contract: all present, null / empty) */
const RUN_EMPTY = {
  stages: [],
  gates: [],
  budget: null,
  preset: null,
  dataset: null,
  accuracy_floor: null,
  roles: [],
  final_val: [],
  fidelity_summary: null,
  gpu_guard: { records: 0, episodes: [], giveups: 0 },
  stop_rule: null,
  transition: null,
  orphan_check: null,
  live: null,
};

export function fixtureRun(id: string, state: FixtureState = newFixtureState()): ArchiveRun {
  const s = summaryOf(id, state);
  if (id === RUN_A) {
    return {
      ...s,
      source: SRC(id),
      warnings: [],
      stages: [
        { label: 'P0', started_at: '2026-10-01T01:05:16Z', ended_at: '2026-10-01T01:08:00Z', duration_s: 164 },
        { label: 'P3', started_at: '2026-10-01T01:08:00Z', ended_at: '2026-10-01T07:08:00Z', duration_s: 21600 },
        { label: 'P10-1', started_at: '2026-10-01T07:08:00Z', ended_at: '2026-10-01T09:30:00Z', duration_s: 8520 },
      ],
      gates: [
        { name: '[BOOT] synthetic boot check', status: 'PASS', hard: true, measured: 'ok', criterion: '' },
        { name: '[DATA] synthetic split non-empty', status: 'PASS', hard: true, measured: "{'train': 100}", criterion: 'images>0' },
        { name: '[SEARCH] synthetic fidelity floor', status: 'FAIL', hard: true, measured: 'rho=0.30', criterion: 'rho>=0.6' },
        { name: '[SEARCH] synthetic stagnation', status: 'WARN', hard: false, measured: '2', criterion: '<=3' },
        { name: '[INFO] synthetic note', status: 'INFO', hard: false, measured: 'n/a', criterion: '' },
      ],
      budget: { s2_ratio: 0.25, k: 3, nested: { wall_s: 28800 } },
      preset: { name: 'synthetic-preset', epochs: [8, 10, 12] },
      dataset: { id: DATASET_1, train: 100, val: 20 },
      accuracy_floor: { map50_95: 0.3 },
      roles: [
        { role: 'BEST_ACCURACY', candidate_id: A.rows.find((x) => x.roles?.includes('BEST_ACCURACY'))?.candidate_id ?? 'synthetic-none', metric: 'val_map50_95', value: 0.43, values: null },
        { role: 'PARETO_KNEE', candidate_id: A.rows.find((x) => x.roles?.includes('PARETO_KNEE'))?.candidate_id ?? 'synthetic-none', metric: null, value: null, values: null },
      ],
      final_val: [{ cid: A.rows.find((x) => x.is_final)?.candidate_id ?? null, status: 'ok', val_map50: 0.9, val_map50_95: 0.47, small_target_recall: 0.97, latency_median_ms: 6.9, params_deploy: 2400000, head_mode: 'e2e' }],
      fidelity_summary: { spearman: 0.81, kendall: 0.64, n: 8, snr: 5.26 },
      gpu_guard: { records: 3, episodes: [{ gpu: '0', waited_s: 120, external_mib_max: 20480, start: '2026-10-01T02:00:00Z', end: '2026-10-01T02:02:00Z', outcome: 'proceed' }], giveups: 0 },
      stop_rule: null,
      transition: { verdict: 'GO' },
      orphan_check: null,
      live: null,
      plots: [
        { kind: 'pareto_map50_latency', exists: true },
        { kind: 'final_pareto_val', exists: true },
        { kind: 'hypervolume', exists: true },
        { kind: 'params_vs_map50', exists: false },
        { kind: 'rank_correlation', exists: true },
      ],
    };
  }
  if (id === RUN_B) {
    return { ...RUN_EMPTY, ...s, source: SRC(id), warnings: ['candidate_evaluations.jsonl: 1 partial line skipped (file is being rewritten)'], stages: [{ label: 'P0', started_at: '2026-10-14T22:48:00Z', ended_at: '2026-10-14T22:50:00Z', duration_s: 120 }], gates: [], plots: [], live: { drivers: [], running_runs: [{ id, stage: 'P3', nb_pid_alive: true, kernel_pid_alive: true, confirm_pid_alive: null, budget_s: 7200, elapsed_s: 1800, watchdog_last: 'synthetic watchdog ok' }], gpus: [{ index: 0, name: 'Synthetic GPU', used_mib: 10240, total_mib: 49140, util_percent: 87 }], server_time: '2026-10-15T10:00:00Z', source: SRC(id), warnings: [] } };
  }
  return { ...RUN_EMPTY, ...s, source: SRC(id), warnings: [], stages: [], gates: [], plots: [], stop_rule: { rule: 'stagnation>=3' } };
}

/** a baseline row with every contract key present (missing numbers are null, never 0) */
const bl = (o: Partial<ArchiveBaseline> & { name: string }): ArchiveBaseline => ({
  kind: 'baseline',
  cfg_name: null,
  tier: 'final@12e',
  epochs: 12,
  is_judge: false,
  status: 'ok',
  map50_95: null,
  map50: null,
  small_target_recall: null,
  latency_median_ms: null,
  latency_exclusive_ms: null,
  forward_only_median_ms: null,
  t_post_ms: null,
  params_deploy: null,
  params_train: null,
  peak_mem_mb: null,
  train_time_s: null,
  head_mode: null,
  best_ckpt: null,
  checkpoint_sha256: null,
  confirm: null,
  ...o,
});

export function fixtureBaselines(id: string): ArchiveBaselineList | null {
  if (id !== RUN_A) return null;
  return {
    source: { run_dir: `${ROOT}/${id}`, files: ['protocol/baselines_v2.json', 'search/relative_to_anchor.json'] },
    warnings: [],
    notes: ['synthetic: judge baseline is final-tier (12 ep); the anchor is a search-tier (T1, 8 ep) candidate'],
    judge_baseline: { name: 'baseline_yolo26n', tier: 'final@12e', status: 'ok', map50_95: 0.4939, latency_median_ms: 7.6127, params_deploy: 2375031 },
    baselines: [
      bl({ name: 'baseline_yolov11n', kind: 'baseline', tier: 'final@12e', is_judge: false, status: 'ok', map50_95: 0.47, map50: 0.94, small_target_recall: 0.97, latency_median_ms: 8.2, latency_exclusive_ms: 8.0, params_deploy: 2582347, peak_mem_mb: 2500, train_time_s: 3000, head_mode: 'nms', epochs: 12, confirm: null  }),
      bl({ name: 'baseline_yolo26n', kind: 'baseline', tier: 'final@12e', is_judge: true, status: 'ok', map50_95: 0.4939, map50: 0.9506, small_target_recall: 0.9836, latency_median_ms: 7.6127, latency_exclusive_ms: 7.5, forward_only_median_ms: 7.4287, t_post_ms: 0.184, params_deploy: 2375031, params_train: 2504190, peak_mem_mb: 2416, train_time_s: 3109.25, head_mode: 'e2e', epochs: 12, confirm: { val_map50_95: 0.49, test_tiles_map50_95: 0.41, test_native_map50_95: 0.39, latency_median_ms: 7.7, n_success: 3, val_map50_95_std: 0.004, tier: 'confirm' }  }),
      bl({ name: 'baseline_yolo26n_p2', kind: 'baseline', tier: 'final@12e', is_judge: false, status: 'train_fail', map50_95: null, map50: null, small_target_recall: null, latency_median_ms: null, params_deploy: null, epochs: 12  }),
    ],
    anchor: { candidate_id: 'synthetic-g000c0000', tier: 'T@8e', epochs: 8, fingerprint: A.rows[0].fingerprint, metrics: { map50_95: 0.4235, map50: 0.88, small_target_recall: 0.98, latency_median_ms: 8.0763, params_deploy: 2375031, peak_mem_mb: 2414, train_time_s: 459, head_mode: 'e2e' } },
  };
}

export function fixtureGenerations(id: string, state: FixtureState = newFixtureState()): ArchiveGenerationList | null {
  const run = id === RUN_A ? A : id === RUN_B ? runB(state) : null;
  if (!run) return null;
  return {
    source: SRC(id),
    warnings: [],
    objectives: [
      { key: 'map50_95', direction: 'max', label: 'mAP50-95' },
      { key: 'small_target_recall', direction: 'max', label: 'small recall' },
      { key: 'latency_median_ms', direction: 'min', label: 'latency' },
      { key: 'params_deploy', direction: 'min', label: 'params' },
    ],
    generations: run.generations,
    hv_history: run.generations.map((g) => g.hypervolume as number),
    stagnation_rule: 'stop after 3 generations without HV improvement',
    notes: ['NSGA-II 다목적; 스칼라 fitness 없음'],
  };
}

function runOf(id: string, state: FixtureState): SynthRun | null {
  return id === RUN_A ? A : id === RUN_B ? runB(state) : null;
}

export function fixtureGenerationDetail(id: string, g: number, state: FixtureState = newFixtureState()): ArchiveGenerationDetail | null {
  const run = runOf(id, state);
  const gen = run?.generations.find((x) => x.generation === g);
  if (!run || !gen) return null;
  const prev = run.generations.find((x) => x.generation === g - 1);
  const rows = run.rows.filter((x) => x.generation === g);
  const pointOf = (c: string): ArchivePoint => {
    const m = run.rows.find((x) => x.candidate_id === c && x.tier === 'T@8e')?.metrics;
    return { candidate_id: c, map50_95: m?.map50_95 ?? null, latency_median_ms: m?.latency_median_ms ?? null, small_target_recall: m?.small_target_recall ?? null, params_deploy: m?.params_deploy ?? null };
  };
  const improvements = [
    { objective: 'map50_95', kind: (prev ? 'improved' : 'initial') as 'improved' | 'initial', prev_best: prev?.best_map50_95 ?? null, new_best: gen.best_map50_95 as number, delta: prev && typeof gen.best_map50_95 === 'number' && typeof prev.best_map50_95 === 'number' ? gen.best_map50_95 - prev.best_map50_95 : null, candidate_id: rows.filter((x) => x.status === 'ok').sort((a, b) => (b.metrics?.map50_95 ?? 0) - (a.metrics?.map50_95 ?? 0))[0]?.candidate_id ?? 'synthetic-none' },
  ].filter((i) => g === 0 || (typeof i.delta === 'number' && i.delta > 0));
  return {
    source: SRC(id),
    warnings: [],
    generation: gen,
    candidates: rows,
    improvements,
    front2d_points: gen.front2d.map(pointOf),
    prev_front2d_points: prev?.front2d.map(pointOf) ?? [],
    lineage: rows.filter((x) => x.tier === 'T@8e').map((x) => ({ candidate_id: x.candidate_id, mode: x.lineage.mode, parents: x.lineage.parents, mutation: x.lineage.mutation, cache_hit: x.lineage.cache_hit, notes_parse: x.lineage.notes_parse })),
    characteristics: gen.characteristics,
  };
}

export function fixtureCandidates(id: string, q: URLSearchParams, state: FixtureState = newFixtureState()): { total: number; candidates: ArchiveCandidateRow[]; warnings: string[]; source: { run_dir: string; files: string[] } } | null {
  const run = runOf(id, state);
  if (!run) return null;
  let rows = run.rows;
  const gen = q.get('generation');
  if (gen !== null) rows = rows.filter((x) => String(x.generation) === gen);
  if (q.get('tier')) rows = rows.filter((x) => x.tier === q.get('tier'));
  if (q.get('status')) rows = rows.filter((x) => x.status === q.get('status'));
  const limit = Number(q.get('limit') ?? 100);
  const offset = Number(q.get('offset') ?? 0);
  return { total: rows.length, candidates: rows.slice(offset, offset + limit), warnings: [], source: SRC(id) };
}

export function fixtureCandidateDetail(id: string, cid: string, state: FixtureState = newFixtureState()): ArchiveCandidateDetail | null {
  const run = runOf(id, state);
  const evs = run?.rows.filter((x) => x.candidate_id === cid);
  if (!run || !evs || evs.length === 0) return null;
  const c = run.cands.get(cid);
  const row = evs.find((x) => x.tier === 'T@8e') ?? evs[0];
  const parents = (row.lineage?.parents ?? []).filter((p) => run.cands.has(p));
  const mods = c?.mods ?? [];
  const hist: Record<string, number> = {};
  for (const m of mods) hist[m] = (hist[m] ?? 0) + 1;
  const parentMods = parents.map((p) => run.cands.get(p)?.mods ?? []);
  return {
    source: SRC(id),
    warnings: [],
    row,
    evaluations: [
      ...evs.map((e) => ({ stage: e.is_final ? ('final' as const) : ('search' as const), tier: e.tier, epochs: e.epochs, seed: e.seed, status: e.status, metrics: e.metrics ?? {}, source: 'search/candidates/candidate_evaluations.jsonl' })),
      ...(row.status === 'ok' && !row.is_final ? [{ stage: 'exclusive' as const, tier: null, epochs: null, seed: null, status: 'ok', metrics: { latency_median_ms: 5.5 }, source: 'search/exclusive_latency.json' }] : []),
    ],
    architecture: mods.length
      ? {
          yaml_text: `# synthetic yaml for ${cid}\nnc: 1\nbackbone:\n${mods.slice(0, -1).map((m, i) => `  - [-1, 1, ${m}, [${16 * (i + 1)}]]`).join('\n')}\nhead:\n  - [-1, 1, ${mods[mods.length - 1]}, [1]]\n`,
          cfg: { nc: 1, scales: {}, backbone: [], head: [] },
          n_backbone: mods.length - 1,
          n_head: 1,
          stage_count: 5,
          stages: [
            { stage_index: 0, global_start: 0, modules: mods.slice(0, 2), downsamples: 2 },
            { stage_index: 1, global_start: 2, modules: mods.slice(2, mods.length - 1), downsamples: 1 },
          ],
          stages_source: 'cfg',
          module_histogram: hist,
          source: { kind: 'own' as const, candidate_id: cid },
        }
      : null,
    lineage: {
      mode: row.lineage.mode,
      parents: parents.map((p) => ({ candidate_id: p, kind: 'candidate' as const, fingerprint: run.cands.get(p)?.row.fingerprint ?? null, generation: run.cands.get(p)?.row.generation ?? null, metrics_summary: run.cands.get(p)?.row.metrics ?? null, has_cfg: true })),
      mutation: row.lineage.mutation,
      cache_hit: row.lineage.cache_hit,
      notes_parse: row.lineage.notes_parse,
      diff_vs_parents: parents.map((p, i) => {
        const pm = parentMods[i];
        return { parent: p, module_delta: null, modules_added: [...new Set(mods.filter((m) => !pm.includes(m)))], modules_removed: [...new Set(pm.filter((m) => !mods.includes(m)))], stage_diff: [{ stage_index: 1, parent_modules: pm.slice(2), child_modules: mods.slice(2) }], n_rows_parent: pm.length, n_rows_child: mods.length };
      }),
    },
    latency: { median: row.metrics.latency_median_ms ?? null, p90: row.metrics.latency_p90_ms ?? null, mean: row.metrics.latency_mean_ms ?? null, std: row.metrics.latency_std_ms ?? null, cv: row.metrics.latency_cv ?? null, forward_only: row.metrics.forward_only_median_ms ?? null, t_post: row.metrics.t_post_ms ?? null, head_mode: row.metrics.head_mode ?? null, protocol_hash: row.metrics.latency_protocol_hash ?? null, exclusive: row.is_final ? null : { latency_median_ms: 5.5, exclusive: true } },
    checkpoints: [
      { tier: 'T@8e', path: `${ROOT}/${id}/search/candidates/train_runs/${cid}/weights/best.pt`, exists: row.has_checkpoint === true, sha256: row.has_checkpoint ? 'synthsha256' : null },
    ],
    roles: row.roles,
    confirm: null,
    provenance: { cfg_path: `${ROOT}/${id}/search/candidates/train_runs/${cid}.cfg.json`, yaml_path: `${ROOT}/${id}/search/candidates/train_runs/${cid}.yaml`, jsonl_path: `${ROOT}/${id}/search/candidates/candidate_evaluations.jsonl` },
  };
}

export function fixtureFidelity(): ArchiveFidelity {
  return {
    source: { run_dir: `${ROOT}/${RUN_A}`, files: ['fidelity/rank_correlation.json', 'fidelity/noise.json'] },
    warnings: [],
    rank_correlation: { spearman: 0.8095, kendall: 0.6429, n: 8 },
    noise: { snr: 5.26, noise: 0.00997, spread: 0.05243 },
    corr_by_head_mode: { e2e: { spearman: 0.9, n: 4 }, nms: { spearman: 0.7, n: 4 } },
    bootstrap_ci: { spearman: [0.2, 0.95] },
    evidence_note: 'n=8: 파이프라인 점검, 비증거 (synthetic)',
  };
}

export function fixtureConfirm(): ArchiveConfirm {
  return {
    source: { run_dir: `${ROOT}/${RUN_A}`, files: ['three_seed/three_seed_summary.json'] },
    warnings: [],
    final_verdict: 'CONFIRMED',
    seeds: [42, 142, 242],
    baseline_name: 'baseline_yolo26n',
    reference: null,
    summary: {
      baseline_yolo26n: { val_map50_95_mean: 0.49, test_tile_map50_95_mean: 0.41, test_native_map50_95_mean: 0.39, latency_median_ms_mean: 7.7, params_deploy: 2375031 },
      synthetic_final_a: { val_map50_95_mean: 0.52, test_tile_map50_95_mean: 0.43, test_native_map50_95_mean: null, latency_median_ms_mean: 6.9, params_deploy: 2400000 },
    },
    verdicts: { synthetic_final_a: 'PASS' },
    max_det_observed: 300,
    test_unlock_used: false,
    final_report_md: '# synthetic report\nthis is not a measured result',
  };
}

export function fixtureLive(): ArchiveLive {
  return {
    drivers: [{ path: '/synthetic/driver', status_json: { stage: 'P3' }, pipeline_log_tail: ['synthetic pipeline line 1', 'synthetic pipeline line 2'] }],
    running_runs: [{ id: RUN_B, stage: 'P3', nb_pid_alive: true, kernel_pid_alive: true, confirm_pid_alive: null, budget_s: 7200, elapsed_s: 1800, watchdog_last: 'synthetic watchdog ok' }],
    gpus: [{ index: 0, name: 'Synthetic GPU', used_mib: 10240, total_mib: 49140, util_percent: 87 }],
    server_time: '2026-10-15T10:00:00Z',
    source: { run_dir: null, files: [] },
    warnings: [],
  };
}

export function fixtureCompare(ids: string[], state: FixtureState = newFixtureState()): ArchiveCompare {
  return {
    source: { run_dir: ROOT, files: [] },
    warnings: [],
    comparable: new Set(ids.map((i) => summaryOf(i, state).dataset_id)).size <= 1,
    incomparable_reason: new Set(ids.map((i) => summaryOf(i, state).dataset_id)).size <= 1 ? null : 'dataset_id differs',
    notes: [],
    runs: ids.map((id) => {
      const run = runOf(id, state);
      const gens = run?.generations ?? [];
      let t = 0;
      return {
        id,
        family: summaryOf(id, state).family,
        metric_protocol_hash: summaryOf(id, state).hashes.metric_protocol,
        baselines: summaryOf(id, state).judge_baseline ? [{ name: 'baseline_yolo26n', kind: 'baseline', tier: 'final@12e', map50_95: 0.4939, small_target_recall: 0.9836, latency_median_ms: 7.6127, params_deploy: 2375031, is_judge: true }] : null,
        arm: summaryOf(id, state).arm,
        dataset_id: summaryOf(id, state).dataset_id,
        judge_baseline: summaryOf(id, state).judge_baseline,
        curves: {
          best_so_far_map50_95: gens.map((g) => ({ generation: g.generation, cum_train_time_s: (id === RUN_B && g.generation === 1 ? null : (t += 600 * (g.generation + 1))) as number, cum_train_time_s_no_cache: null, value: g.best_map50_95 as number, candidate_id: `synthetic-g${String(g.generation).padStart(3, '0')}c0000` })),
          hypervolume: gens.map((g) => ({ generation: g.generation, value: g.hypervolume })),
        },
        finalists: id === RUN_A ? [{ candidate_id: 'synthetic-final-a', val_map50_95: 0.52, val_map50: null, small_target_recall: null, latency_median_ms: null, params_deploy: 2400000, roles: [], tier: 'final@12e' }] : [],
        comparable: summaryOf(id, state).dataset_id === DATASET_1,
        incomparable_reason: summaryOf(id, state).dataset_id === DATASET_1 ? null : 'dataset_id differs',
      };
    }),
  };
}

// ---- router ---------------------------------------------------------------------------------------------------------

export interface Answer {
  status: number;
  json?: unknown;
  /** a PNG body */
  png?: boolean;
}

const notFound = (code: string, message: string): Answer => ({ status: 404, json: { code, message } });
const RUNS = new Set([RUN_A, RUN_B, RUN_C]);

/** Answers GET /api/v1/archive/... Returns null when the path is not an archive path. */
export function archiveResponder(pathname: string, search: string, state: FixtureState = newFixtureState()): Answer | null {
  if (!pathname.startsWith('/api/v1/archive/')) return null;
  if (state.failAll) return { status: 500, json: { code: 'INTERNAL', message: 'synthetic failure' } };
  const p = pathname.slice('/api/v1/archive/'.length);
  const q = new URLSearchParams(search);
  if (p === 'runs') return { status: 200, json: fixtureRunList(state) };
  if (p === 'live') return { status: 200, json: fixtureLive() };
  if (p === 'compare') {
    const ids = (q.get('runs') ?? '').split(',').filter(Boolean);
    const bad = ids.find((i) => !RUNS.has(i));
    return bad ? notFound('ARCHIVE_RUN_NOT_FOUND', `unknown run ${bad}`) : { status: 200, json: fixtureCompare(ids, state) };
  }
  const m = /^runs\/([^/]+)(?:\/(.*))?$/.exec(p);
  if (!m) return notFound('ARCHIVE_NOT_FOUND', 'no such archive route');
  const id = decodeURIComponent(m[1]);
  const rest = m[2] ?? '';
  if (!/^[A-Za-z0-9._-]{1,128}$/.test(id)) return { status: 422, json: { code: 'ARCHIVE_BAD_ID', message: 'bad run id' } };
  if (!RUNS.has(id)) return notFound('ARCHIVE_RUN_NOT_FOUND', `run ${id} not found`);
  if (id === RUN_B) state.detailHits[rest || 'detail'] = (state.detailHits[rest || 'detail'] ?? 0) + 1;
  if (rest === '') return { status: 200, json: fixtureRun(id, state) };
  if (rest === 'baselines') {
    const b = fixtureBaselines(id);
    return b ? { status: 200, json: b } : { status: 200, json: { judge_baseline: summaryOf(id, state).judge_baseline, baselines: [], anchor: null, warnings: ['protocol/baselines_v2.json is missing'], source: { run_dir: `${ROOT}/${id}`, files: [] } } };
  }
  if (rest === 'generations') {
    const g = fixtureGenerations(id, state);
    return g ? { status: 200, json: g } : { status: 200, json: { generations: [], warnings: ['search/generations is missing'], source: { run_dir: `${ROOT}/${id}`, files: [] } } };
  }
  const g = /^generations\/(\d+)$/.exec(rest);
  if (g) {
    const d = fixtureGenerationDetail(id, Number(g[1]), state);
    return d ? { status: 200, json: d } : notFound('ARCHIVE_NOT_FOUND', `generation ${g[1]} not found`);
  }
  if (rest === 'candidates') {
    const c = fixtureCandidates(id, q, state);
    return c ? { status: 200, json: c } : { status: 200, json: { total: 0, candidates: [], warnings: [] } };
  }
  const c = /^candidates\/([^/]+)$/.exec(rest);
  if (c) {
    const d = fixtureCandidateDetail(id, decodeURIComponent(c[1]), state);
    return d ? { status: 200, json: d } : notFound('ARCHIVE_NOT_FOUND', `candidate ${c[1]} not found`);
  }
  if (rest === 'fidelity') return id === RUN_A ? { status: 200, json: fixtureFidelity() } : notFound('ARCHIVE_NOT_FOUND', 'no fidelity');
  if (rest === 'confirm') return id === RUN_A ? { status: 200, json: fixtureConfirm() } : notFound('ARCHIVE_NOT_FOUND', 'no confirm');
  const f = /^files\/([a-z0-9_]+)$/.exec(rest);
  if (f) return id === RUN_A && f[1] !== 'plot_params_vs_map50' ? { status: 200, png: true } : notFound('ARCHIVE_NOT_FOUND', `file ${f[1]} missing`);
  return notFound('ARCHIVE_NOT_FOUND', 'no such archive route');
}

/** A large synthetic run for the 2,000-row performance check. */
export function bigRows(n: number): ArchiveCandidateRow[] {
  const r = rng(99);
  return Array.from({ length: n }, (_, i) => {
    const g = Math.floor(i / 100);
    const q = 0.3 + 0.01 * g + r() * 0.1;
    return {
      candidate_id: `synthetic-big-${i}`,
      generation: g,
      fingerprint: `fp${i}`,
      tier: 'T@8e',
      status: 'ok',
      metrics: { map50_95: q, map50: q + 0.4, small_target_recall: 0.5 + q / 2, latency_median_ms: 4 + r() * 6, params_deploy: Math.round(1.8e6 + r() * 1.4e6) },
      lineage: { mode: 'random_crossover', parents: [] },
      roles: [],
    } as unknown as ArchiveCandidateRow;
  });
}

// ---- vitest fetch mock ----------------------------------------------------------------------------------------------

export const PNG_BYTES = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), (c) => c.charCodeAt(0));

export interface ArchiveCall {
  method: string;
  path: string;
  headers: Record<string, string>;
}

/**
 * Replaces globalThis.fetch with an in-memory archive API (plus /health). `hook` may answer a request first
 * (return a Response) or return undefined to fall through to the fixtures.
 */
export function installArchiveFetch(
  state: FixtureState,
  hook?: (url: URL, call: ArchiveCall) => Response | Promise<Response> | undefined,
): { calls: ArchiveCall[]; restore: () => void } {
  const calls: ArchiveCall[] = [];
  const original = globalThis.fetch;
  const json = (status: number, body: unknown): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  const impl = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const url = new URL(typeof input === 'string' ? input : input.toString(), 'http://localhost');
    const call: ArchiveCall = { method: init.method ?? 'GET', path: url.pathname + url.search, headers: { ...(init.headers as Record<string, string>) } };
    calls.push(call);
    const hooked = hook?.(url, call);
    if (hooked) return hooked;
    if (url.pathname === '/api/v1/health') return json(200, fixtureHealth());
    const a = archiveResponder(url.pathname, url.search, state);
    if (a) return a.png ? new Response(PNG_BYTES, { status: 200, headers: { 'content-type': 'image/png' } }) : json(a.status, a.json);
    return json(404, { code: 'NOT_FOUND', message: `unmocked ${url.pathname}` });
  };
  globalThis.fetch = impl as unknown as typeof fetch;
  return { calls, restore: () => { globalThis.fetch = original; } };
}
