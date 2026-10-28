import type { Run } from '../api/types';
import { rec } from '../api/types';
import { generationOnlyRow } from './generation';

export const TRAINED_NOT_EVALUATED = '학습 완료 · 미평가';

/** Row `reason_code` of a trial that a runner trained but could not evaluate (latency is measured on CUDA only). */
export const EVALUATION_REQUIRES_CUDA = 'EVALUATION_REQUIRES_CUDA';

/**
 * The server keeps `SEARCH_COMPLETE` for a validation-OFF explicit training run (no enum migration) and marks the
 * row `performance_status=NOT_EVALUATED`. Show that as "학습 완료 · 미평가" for an explicit training run
 * (run `console.extra.training.enabled` or a row `training_mode`).
 * A search-arm row (GA, IndependentRandom, Baseline, Final) that a CPU runner trained but could
 * not evaluate is stored the same way with `reason_code=EVALUATION_REQUIRES_CUDA` (metrics, fitness and gate null, no
 * promotion); it gets the same label unless the server reports `training_status=UNTRAINED`. Every other
 * `SEARCH_COMPLETE` + `NOT_EVALUATED` row keeps the raw status.
 * (Moved from screens/Monitor.tsx so the candidate detail can reuse it; Monitor re-exports both names.)
 */
export function isTrainedNotEvaluated(row: unknown, run?: Run | null): boolean {
  const r = rec(row);
  if (r.status !== 'SEARCH_COMPLETE' || r.performance_status !== 'NOT_EVALUATED') return false;
  const explicit = rec(rec(rec(rec(run?.resolved_protocol).console).extra).training).enabled === true;
  if (explicit || typeof r.training_mode === 'string') return true;
  return r.reason_code === EVALUATION_REQUIRES_CUDA && r.training_status !== 'UNTRAINED';
}

/** Storage shape every "학습 완료 · 미평가" row shares. */
const TRAINED_NOT_EVALUATED_STORED = 'SEARCH_COMPLETE + performance_status NOT_EVALUATED';

/**
 * Tooltip of the "학습 완료 · 미평가" badge, shared by the Monitor candidate table and the candidate detail.
 * The badge covers two cases with the same stored shape: an explicit training run with validation OFF, and a row
 * that a runner without CUDA trained but could not evaluate (`reason_code=EVALUATION_REQUIRES_CUDA`:
 * a search arm or an explicit training run with validation ON). The title names the case from the row's reason code
 * and the run's validation switch; any other row gets the generic text. Nothing is guessed.
 */
export function trainedNotEvaluatedTitle(row: unknown, run?: Run | null): string {
  const r = rec(row);
  if (r.reason_code === EVALUATION_REQUIRES_CUDA) {
    return `학습은 끝났지만 평가하지 못했습니다: 이 job을 실행한 runner에 CUDA가 없습니다(reason_code ${EVALUATION_REQUIRES_CUDA}, 지연시간은 CUDA에서만 측정). ${TRAINED_NOT_EVALUATED_STORED} · AP·recall·latency·size·fitness·gate는 없음(null, latency pending) · checkpoint는 유지됩니다.`;
  }
  const validation = rec(rec(rec(rec(run?.resolved_protocol).console).extra).training).validation_enabled;
  if (validation === false) {
    return `validation OFF 명시 학습(DD-5): ${TRAINED_NOT_EVALUATED_STORED}로 끝나며 지표를 측정하지 않았습니다.`;
  }
  const code = typeof r.reason_code === 'string' && r.reason_code !== '' ? `, reason_code ${r.reason_code}` : '';
  return `학습은 끝났지만 성능 평가 기록이 없습니다(${TRAINED_NOT_EVALUATED_STORED}${code}). 측정된 지표가 없으며 값을 추정하지 않습니다.`;
}

/** Badge of a candidate that was neither trained nor evaluated (an evaluation-OFF generated candidate). */
export const UNTRAINED_UNEVALUATED = 'UNTRAINED · UNEVALUATED';

/**
 * Training state of a row: the server's `training_status` (contract 1.5.0: UNTRAINED | TRAINED) when it is present.
 * A 1.4.x server does not send it; then a generated candidate (generation-only row: status NOT_EVALUATED, origin
 * random_generation, effective_method RandomGeneration, or an unstatused row with evaluation_status NOT_EVALUATED and a
 * generation_order) is UNTRAINED, because generation never trains, and every other row is unknown (null): nothing is guessed.
 */
export function trainingStatusOf(row: unknown): 'UNTRAINED' | 'TRAINED' | null {
  const r = rec(row);
  if (r.training_status === 'UNTRAINED' || r.training_status === 'TRAINED') return r.training_status;
  return generationOnlyRow(r) ? 'UNTRAINED' : null;
}

/**
 * True when the row is untrained and has no evaluation: training_status UNTRAINED (reported or derived above) and
 * neither `evaluation_status` nor `status` says EVALUATED. Explicit training runs keep the "학습 완료 · 미평가" badge instead.
 */
export function isUntrainedUnevaluated(row: unknown): boolean {
  const r = rec(row);
  if (trainingStatusOf(r) !== 'UNTRAINED') return false;
  return r.evaluation_status !== 'EVALUATED' && r.status !== 'EVALUATED';
}
