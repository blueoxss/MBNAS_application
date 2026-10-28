/**
 * Named fitness weights on the Settings screen: input hygiene only.
 *
 * The fitness value, its rewards and the gate decision are computed by the server and only displayed here (DD-A8: the
 * client never recomputes fitness). What stays is the weight-sum check of the user's own input, which mirrors the
 * public contract error code WEIGHTS_SUM_NOT_ONE: |sum - 1| <= 1e-6, never auto-normalised.
 * 근거 테스트: tests/fitness_weights.test.ts; 사용처 src/lib/validate.ts, src/screens/Settings.tsx.
 */

export const WEIGHT_TOLERANCE = 1e-6;

export interface FitnessWeights {
  ap: number;
  recall: number;
  latency_reward: number;
  size_reward: number;
}

export interface FitnessAnchors {
  latency_bounds_ms: readonly [number, number];
  size_bounds_MB: readonly [number, number];
}

const KEYS = ['ap', 'recall', 'latency_reward', 'size_reward'] as const;

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** Sum of the four named weights (a missing or non-finite weight counts as 0 here; validate.ts reports it separately). */
export function weightsSum(w: Partial<Record<(typeof KEYS)[number], number | null | undefined>>): number {
  return KEYS.reduce((s, k) => s + (isNum(w[k]) ? (w[k] as number) : 0), 0);
}

/** |sum - 1| <= 1e-6. There is no auto-normalisation. */
export function weightsSumOk(w: Partial<Record<(typeof KEYS)[number], number | null | undefined>>): boolean {
  return Math.abs(weightsSum(w) - 1) <= WEIGHT_TOLERANCE;
}
