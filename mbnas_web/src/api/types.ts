/**
 * Aliases over the generated contract types (types.gen.ts is produced by `npm run gen:types` from the canonical
 * mbnas_contracts openapi.json and must not be edited). Contract 1.5.0: every alias is a generated component as is; there
 * are no hand-written field augmentations (WP-6C). Fields a view reads from a row that the contract does not declare on
 * that component (e.g. the GeneratedCandidate fields of a generated candidate shown through the candidate view) are read
 * through `rec()` as `unknown`, never typed by hand.
 *
 * The contract leaves several objects free-form (`gate`, `cost`, `resolved` ...). They are typed `Record<string, never>`
 * or a weak object by the generator, so the UI reads them through `rec()` which narrows to `Record<string, unknown>`
 * without pretending to know their shape.
 */
import type { components, paths } from './types.gen';

type S = components['schemas'];

export type Protocol = S['Protocol'];
export type Artifact = S['Artifact'];
export type Block = S['Block'];
export type Candidate = S['CandidateEvaluation'];
export type Generation = S['Generation'];
export type Run = S['Run'];
export type Worker = S['Worker'];
export type ApiEvent = S['Event'];
export type Snapshot = S['Snapshot'];
export type Health = S['Health'];
export type ValidateResponse = S['ValidateResponse'];
export type RunRequest = S['RunRequest'];
export type RunCreated = S['RunCreated'];
export type FinalRetrainRequest = S['FinalRetrainRequest'];
export type ApiErrorBody = S['Error'];
/** Component that produced an error (1.5.0 `Error.location`): gateway, core, runner or store. */
export type ErrorLocation = NonNullable<S['Error']['location']>;
export type PresetSummary = S['PresetSummary'];
export type Preset = S['Preset'];
export type CandidateDetail = S['CandidateDetail'];
/** 1.5.0 checkpoint row of a candidate detail: id (ck-...) + sha256 + optional store artifact_id, never a host path. */
export type CandidateCheckpoint = NonNullable<CandidateDetail['checkpoints']>[number];
export type RegistryEntry = S['RegistryList']['registry'][number];

export type RunStatus = Run['status'];
export type RunKind = NonNullable<Run['kind']>;
export type Finalist = NonNullable<Run['finalists']>[number];
export type WorkerStatus = Worker['status'];
export type CandidateStatus = Candidate['status'];
export type EventType = ApiEvent['type'];
export type Arm = Candidate['arm'];
export type Precision = Candidate['precision'];
export type LatencySource = Candidate['latency_source'];
export type ArtifactState = NonNullable<Artifact['state']>;

/** Run states in which the experiment policy is immutable (contract x-status-vocabulary). */
export const NONTERMINAL_RUN_STATUSES: readonly RunStatus[] = [
  'QUEUED',
  'RUNNING',
  'PAUSE_REQUESTED',
  'PAUSED',
  'RESUMING',
  'CANCEL_REQUESTED',
];
export const TERMINAL_RUN_STATUSES: readonly RunStatus[] = ['COMPLETED', 'FAILED', 'CANCELLED'];

export function isNonterminal(status: string | null | undefined): boolean {
  return status != null && (NONTERMINAL_RUN_STATUSES as readonly string[]).includes(status);
}

/** Narrow a free-form contract object to a readable record. */
export function rec(v: unknown): Record<string, unknown> {
  return v != null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

export function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

export function str(v: unknown): string | null {
  return typeof v === 'string' && v !== '' ? v : null;
}

/** Contract 1.5.0: a generated (evaluation-OFF) candidate, with blocks, parents, hashes, seeds and training_status. */
export type GeneratedCandidate = S['GeneratedCandidate'];
/** `T` with the contract keys `K` required and non-null (no field is added or renamed: only optionality changes). */
type WithRequired<T, K extends keyof T> = Omit<T, K> & { [P in K]-?: NonNullable<T[P]> };
/** Contract components (generated; the hand-written TrainingRequest/TrainingSource of earlier rounds are gone). */
export type TrainingRequestBody = S['TrainingRequest'];
/** The generated request body with the keys the console always sends (the server defaults mode to scratch and validation_enabled to true). */
export type TrainingRequest = WithRequired<TrainingRequestBody, 'mode' | 'validation_enabled'>;
export type TrainingSource = TrainingRequestBody['source'];
export type TrainingRunCreated = S['TrainingRunCreated'];
export type StructureReport = S['StructureReport'];
/** GET|POST /registry/{model_id}/structure[-check]: the structure document plus the model id and where its evidence comes from (generated response type). */
export type RegistryStructure = paths['/api/v1/registry/{model_id}/structure']['get']['responses'][200]['content']['application/json'];
/** Body of POST /structure-check (1.5.0: exactly one of upload_id, model_yaml_text + source_sha256, model_path). */
export type StructureCheckRequest = paths['/api/v1/structure-check']['post']['requestBody']['content']['application/json'];

/** Contract 1.5.0 (F5a / F5d): the answer of POST /uploads/model-config and the configuration revisions of a candidate. */
export type UploadResponse = S['UploadResponse'];
export type ModelConfigRevision = S['ModelConfigRevision'];
export type ModelConfigRevisionList = S['ModelConfigRevisionList'];
export type RevisionKind = ModelConfigRevision['kind'];

/** Contract 1.4.2 components (generated): the static block design-origin table of GET /structure/block-catalog. */
export type BlockCatalogTable = S['BlockCatalogTable'];
export type BlockCatalogEntry = S['BlockCatalogEntry'];
/** Wire form of `StructureReport.blocks[].catalog` (snake_case, every key present, values may be null). */
export type BlockCatalogRecord = S['BlockCatalog'];
export type BlockCatalogSummary = S['BlockCatalogSummary'];
/** Optional campaign members of an archive run (`ArchiveRunSummary` / `ArchiveRun`); a plain run has none. */
export type ArchiveCampaignProgress = S['ArchiveCampaignProgress'];
export type ArchiveCampaignResult = S['ArchiveCampaignResult'];
