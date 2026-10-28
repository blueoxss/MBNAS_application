/**
 * The weight-sum input check that stays in the console after DD-A8 (src/lib/fitness.ts). These assertions were in
 * tests/fitness.test.ts, which was deleted with the client fitness recompute (WP-6 R1; record:
 * docs/internal/evidence/WP6/6A/TEST_ADAPTATIONS.md). The fitness formula itself is pinned by the server unit tests
 * (tests/owner/unit/test_protocol_fitness.py); the console must not compute it.
 */
import { describe, expect, it } from 'vitest';
import * as fitnessModule from '../src/lib/fitness';
import { WEIGHT_TOLERANCE, weightsSum, weightsSumOk } from '../src/lib/fitness';

describe('named fitness weights: input hygiene only', () => {
  it('blocks a weights sum of .43 and never auto-normalises it', () => {
    const w = { ap: 0.2, recall: 0.1, latency_reward: 0.08, size_reward: 0.05 };
    expect(weightsSum(w)).toBeCloseTo(0.43, 12);
    expect(weightsSumOk(w)).toBe(false);
    expect(w).toEqual({ ap: 0.2, recall: 0.1, latency_reward: 0.08, size_reward: 0.05 }); // untouched, not normalised
  });

  it('accepts sums within 1e-6 and rejects just outside', () => {
    expect(WEIGHT_TOLERANCE).toBe(1e-6);
    expect(weightsSumOk({ ap: 0.2, recall: 0.1, latency_reward: 0.4, size_reward: 0.3 + 5e-7 })).toBe(true);
    expect(weightsSumOk({ ap: 0.2, recall: 0.1, latency_reward: 0.4, size_reward: 0.3 + 2e-6 })).toBe(false);
  });

  it('counts a missing or non-finite weight as 0 in the displayed sum (validate.ts reports the field itself)', () => {
    expect(weightsSum({ ap: 0.5, recall: null, latency_reward: undefined, size_reward: Number.NaN })).toBe(0.5);
  });

  it('DD-A8: the module exports no fitness computation (server values are only displayed)', () => {
    expect(Object.keys(fitnessModule).sort()).toEqual(['WEIGHT_TOLERANCE', 'weightsSum', 'weightsSumOk']);
  });
});
