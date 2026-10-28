import { describe, expect, it } from 'vitest';
import { addedEpochs, addRung, effectiveBatch, fixedHorizon, normalizePolicy, removeLastRung, rungsOf, setIn, stableStringify } from '../src/lib/policy';
import { checkServerPath, validatePolicy } from '../src/lib/validate';
import { fixtureDesignExampleProtocol, fixturePilotSmokeProtocol } from './fixtures';

// The console embeds no template any more (DD-A8): the full protocols below are the synthetic server presets of
// tests/fixtures.ts (DEFAULT_BASELINE14 design-example values 10/30/90, PILOT_SMOKE 2/4/6; synthetic /srv paths).
const defaultPolicy = fixtureDesignExampleProtocol;
const pilotSmokePolicy = fixturePilotSmokeProtocol;

const codes = (p: unknown): string[] => validatePolicy(p).filter((i) => i.severity === 'error').map((i) => i.code);

describe('server path pre-check', () => {
  it.each([
    ['relative', 'data/train', 'PATH_NOT_ABSOLUTE'],
    ['tilde', '~/data/train', 'PATH_NOT_ABSOLUTE'],
    ['windows drive', 'C:\\data\\train', 'PATH_NOT_ABSOLUTE'],
    ['traversal', '/data/../etc/passwd', 'PATH_TRAVERSAL'],
    ['placeholder angle', '/data/<your-dataset>/train', 'PATH_PLACEHOLDER'],
    ['placeholder path/to', '/path/to/data', 'PATH_PLACEHOLDER'],
    ['empty', '', 'PATH_NOT_ABSOLUTE'],
  ])('%s is rejected', (_n, value, code) => {
    expect(checkServerPath('f', value).map((i) => i.code)).toEqual([code]);
  });
  it('accepts an absolute Linux path', () => {
    expect(checkServerPath('f', '/srv/datasets/synthetic_pilot/images/train')).toEqual([]);
  });
  it('requires distinct split roots and an output root different from them', () => {
    let p = defaultPolicy();
    p = setIn(p, 'console.paths.final_eval_root', '/srv/datasets/synthetic_design/valid/'); // = search_val_root with a trailing slash
    expect(codes(p)).toContain('SPLIT_SAME_ROOT');
    p = setIn(defaultPolicy(), 'console.paths.output_root', '/srv/datasets/synthetic_design/train'); // = train_root
    expect(codes(p)).toContain('SPLIT_SAME_ROOT');
  });
});

describe('policy rules mirrored from the server', () => {
  it('the default example has no client errors', () => {
    expect(codes(normalizePolicy(defaultPolicy()))).toEqual([]);
    expect(codes(normalizePolicy(pilotSmokePolicy()))).toEqual([]);
  });
  it('blocks a weights sum of .43 without normalising', () => {
    const p = setIn(defaultPolicy(), 'rungs.0.fitness_weights', { ap: 0.2, recall: 0.1, latency_reward: 0.08, size_reward: 0.05 });
    expect(codes(p)).toContain('WEIGHTS_SUM_NOT_ONE');
    expect(rungsOf(p)[0].fitness_weights.latency_reward).toBe(0.08);
  });
  it('flags negative weights, non-increasing and non-integer epochs, warmup >= L0', () => {
    expect(codes(setIn(defaultPolicy(), 'rungs.0.fitness_weights', { ap: 1.5, recall: -0.5, latency_reward: 0, size_reward: 0 }))).toContain('WEIGHTS_NEGATIVE');
    expect(codes(setIn(defaultPolicy(), 'rungs.1.epoch_target_total', 10))).toContain('EPOCHS_NOT_INCREASING');
    expect(codes(setIn(defaultPolicy(), 'rungs.1.epoch_target_total', 30.5))).toContain('EPOCHS_NOT_INT');
    expect(codes(setIn(defaultPolicy(), 'training.warmup_epochs', 10))).toContain('WARMUP_GE_L0');
  });
  it('requires a budget and valid thresholds, anchors, seeds', () => {
    expect(codes(setIn(defaultPolicy(), 'console.search.budget_gpu_hours', null))).toContain('BUDGET_MISSING');
    expect(codes(setIn(defaultPolicy(), 'console.search.budget_gpu_hours', -1))).toContain('BUDGET_INVALID');
    expect(codes(setIn(defaultPolicy(), 'rungs.0.promotion.score_ge', 1.5))).toContain('THRESHOLD_RANGE');
    expect(codes(setIn(defaultPolicy(), 'rungs.0.promotion.ap_ge', -0.1))).toContain('AP_GUARD_RANGE');
    expect(codes(setIn(defaultPolicy(), 'fitness.latency_bounds_ms', [200, 20]))).toContain('ANCHORS_INVALID');
    expect(codes(setIn(defaultPolicy(), 'console.search.seeds', [101, 101]))).toContain('SEEDS_INVALID');
  });
  it('rejects min=0 anchors (server rule 0 < min < max)', () => {
    expect(codes(setIn(defaultPolicy(), 'fitness.latency_bounds_ms', [0, 200]))).toContain('ANCHORS_INVALID');
    expect(codes(setIn(defaultPolicy(), 'fitness.size_bounds_MB', [0, 100]))).toContain('ANCHORS_INVALID');
    expect(codes(setIn(defaultPolicy(), 'fitness.latency_bounds_ms', [-1, 200]))).toContain('ANCHORS_INVALID');
  });
  it('flags a manual epochs_added that differs from the computed delta', () => {
    expect(codes(setIn(defaultPolicy(), 'rungs.1.epochs_added', 5))).toContain('EPOCHS_ADDED_MANUAL');
    const r = rungsOf(defaultPolicy());
    const ok = setIn(defaultPolicy(), 'rungs.1.epochs_added', r[1].epoch_target_total - r[0].epoch_target_total);
    expect(codes(ok)).not.toContain('EPOCHS_ADDED_MANUAL');
  });
  it('flags duplicate baselines', () => {
    const p = defaultPolicy();
    const list = (p.console as { baselines: { id: string; enabled: boolean }[] }).baselines;
    expect(codes(setIn(p, 'console.baselines', [...list, { id: list[0].id, enabled: true }]))).toContain('BASELINE_DUPLICATE');
  });
});

describe('derived values', () => {
  it('computes added epochs and the fixed horizon from cumulative targets (10/30/90 -> +10/+20/+60, 90)', () => {
    const rungs = rungsOf(defaultPolicy());
    expect(addedEpochs(rungs)).toEqual([10, 20, 60]);
    expect(fixedHorizon(rungs)).toBe(90);
  });
  it('writes the derived horizon back and keeps promotion targets consistent when rungs change', () => {
    let p = normalizePolicy(defaultPolicy());
    p = setIn(p, 'rungs.2.epoch_target_total', 120);
    p = normalizePolicy(p);
    expect((p.training as { scheduler_horizon_epochs: number }).scheduler_horizon_epochs).toBe(120);
    p = addRung(p);
    expect(rungsOf(p).map((r) => r.id)).toEqual(['L0', 'L1', 'L2', 'L3']);
    expect(rungsOf(p)[2].promotion?.to).toBe('L3');
    expect(rungsOf(p)[3].promotion).toBeNull();
    p = removeLastRung(p);
    expect(rungsOf(p)).toHaveLength(3);
  });
  it('addRung on an empty rung table starts L0 with equal weights (no embedded template weights), keeps the 0.5 / 0.05 promotion defaults', () => {
    let p = addRung({});
    expect(rungsOf(p)).toEqual([{ id: 'L0', epoch_target_total: 1, fitness_weights: { ap: 0.25, recall: 0.25, latency_reward: 0.25, size_reward: 0.25 }, promotion: null }]);
    p = addRung(p);
    expect(rungsOf(p)[0].promotion).toEqual({ to: 'L1', score_ge: 0.5, ap_ge: 0.05 });
    expect(rungsOf(p)[1].fitness_weights).toEqual(rungsOf(p)[0].fitness_weights);
    expect(codes(p).filter((c) => c.startsWith('WEIGHTS'))).toEqual([]);
  });
  it('computes effective batch = batch x accumulation x gpus', () => {
    const p = setIn(setIn(setIn(defaultPolicy(), 'training.batch_per_gpu', 8), 'training.gradient_accumulation', 2), 'training.gpus_per_candidate', 2);
    expect(effectiveBatch(p)).toBe(32);
  });
  it('stableStringify ignores key order', () => {
    expect(stableStringify({ b: 1, a: [1, { d: 2, c: 3 }] })).toBe(stableStringify({ a: [1, { c: 3, d: 2 }], b: 1 }));
  });
});
