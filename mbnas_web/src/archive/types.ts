/**
 * NAS archive contract types (design DESIGN_nas_archive_console_20261015.md section 3, openapi 1.3.0).
 *
 * These are ALIASES over `components['schemas']` of the generated `src/api/types.gen.ts` (`npm run gen:types`,
 * checked by `npm run check:types`). Do not hand-write response shapes here. The only local additions are
 * refinements for places where the contract deliberately leaves an object open (`metrics`, `final_val` rows,
 * `archive_entries`, `arm`, `confirm`): they type the keys the UI reads and are intersected with the generated type.
 * Every number may be null; the UI renders a missing value as the dash and never fills it with 0 (design D9).
 */
import type { components } from '../api/types.gen';

type S = components['schemas'];

export type Num = number | null | undefined;

// ---- generated aliases -----------------------------------------------------------------------------------------
export type ArchiveSource = S['ArchiveSource'];
export type ArchiveRoot = S['ArchiveRoot'];
export type ArchiveStatus = S['ArchiveRunSummary']['status'];
export type ArchiveJudge = S['ArchiveJudgeBaseline'];
export type ArchiveStage = S['ArchiveStage'];
export type ArchiveGate = S['ArchiveGate'];
export type ArchiveRole = S['ArchiveRole'];
export type ArchiveGpuGuard = S['ArchiveGpuGuard'];
export type ArchiveGpu = S['ArchiveGpu'];
export type ArchiveLive = S['ArchiveLive'];
export type ArchiveConfirmRow = S['ArchiveConfirmRef'];
export type ArchiveObjective = S['ArchiveObjective'];
export type Dist = S['ArchiveSpread'];
export type ArchiveCharacteristics = S['ArchiveCharacteristics'];
export type ArchiveGeneration = S['ArchiveGeneration'];
export type ArchiveGenerationList = S['ArchiveGenerationList'];
export type ArchiveLineage = S['ArchiveLineage'];
export type ArchiveCandidateList = S['ArchiveCandidateList'];
export type ArchivePoint = S['ArchiveFrontPoint'];
export type ArchiveImprovement = S['ArchiveImprovement'];
export type ArchiveLineageRow = S['ArchiveLineageEntry'];
export type ArchiveStageRow = S['ArchiveStageRow'];
export type ArchiveParentRef = S['ArchiveParent'];
export type ArchiveParentDiff = S['ArchiveParentDiff'];
export type ArchiveFidelity = S['ArchiveFidelity'];
export type ArchiveConfirm = S['ArchiveConfirm'];
export type ArchiveCurvePoint = S['ArchiveCurvePoint'];
export type ArchiveRunList = S['ArchiveRunList'];
export type ArchiveGenerationDetail = S['ArchiveGenerationDetail'];

/** Fields carried by every archive response. */
export interface ArchiveEnvelope {
  source?: ArchiveSource | null;
  warnings?: string[];
}

export type GateStatus = 'PASS' | 'WARN' | 'FAIL' | 'INFO' | 'SKIP';

// ---- refinements of open objects (the contract leaves these dicts open) ------------------------------------------
/** Metric keys of a candidate / anchor (candidate_evaluations.jsonl `metrics`; open dict in the contract). */
export interface ArchiveMetricKeys {
  map50_95?: Num;
  map50?: Num;
  small_target_recall?: Num;
  precision?: Num;
  recall?: Num;
  latency_median_ms?: Num;
  latency_p90_ms?: Num;
  latency_mean_ms?: Num;
  latency_std_ms?: Num;
  latency_cv?: Num;
  forward_only_median_ms?: Num;
  t_post_ms?: Num;
  head_mode?: string | null;
  params?: Num;
  params_deploy?: Num;
  params_train?: Num;
  gflops?: Num;
  peak_mem_mb?: Num;
  train_time_s?: Num;
  n_small_gt?: Num;
  n_boxes_conf025?: Num;
  latency_protocol_hash?: string | null;
}
export type ArchiveMetrics = ArchiveMetricKeys;

/** One row of `final_summary.final_val` (open dict in the contract). */
export interface ArchiveFinalVal {
  cid?: string | null;
  candidate_id?: string | null;
  status?: string | null;
  val_map50?: Num;
  val_map50_95?: Num;
  params_train?: Num;
  params_deploy?: Num;
  train_s?: Num;
  small_target_recall?: Num;
  latency_median_ms?: Num;
  head_mode?: string | null;
  best_ckpt?: string | null;
  checkpoint_sha256?: string | null;
}

export type ArchiveRunSummary = S['ArchiveRunSummary'];
export type ArchiveRun = S['ArchiveRun'] & { final_val: ArchiveFinalVal[] };
export type ArchiveCandidateRow = S['ArchiveCandidateRow'] & { metrics: ArchiveMetricKeys };
export type ArchiveBaseline = S['ArchiveBaseline'];
export type ArchiveBaselineList = S['ArchiveBaselineList'] & {
  anchor?: (S['ArchiveAnchor'] & { metrics: ArchiveMetricKeys }) | null;
};
export type ArchiveEvaluation = S['ArchiveEvaluation'] & { metrics: ArchiveMetricKeys & Record<string, unknown> };
export type ArchiveCandidateDetail = S['ArchiveCandidateDetail'] & { row: ArchiveCandidateRow; evaluations: ArchiveEvaluation[] };
export type ArchivePareto = S['ArchivePareto'] & {
  archive_entries: (ArchivePoint & { fingerprint?: string | null; generation?: Num })[];
};
export type ArchiveCompareRun = S['ArchiveCompareRun'];
export type ArchiveCompare = S['ArchiveCompare'];

/** Allowed values of GET /archive/runs/{id}/files/{kind} (design 3.11). */
export const ARCHIVE_PLOT_KINDS = [
  { kind: 'plot_pareto_map50_latency', label: 'Pareto mAP50-95 vs latency' },
  { kind: 'plot_final_pareto_val', label: 'Final Pareto (val)' },
  { kind: 'plot_hypervolume', label: 'Hypervolume' },
  { kind: 'plot_params_vs_map50', label: 'params vs mAP50' },
  { kind: 'plot_rank_correlation', label: 'Fidelity rank correlation' },
] as const;
export type ArchivePlotKind = (typeof ARCHIVE_PLOT_KINDS)[number]['kind'];
