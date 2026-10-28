import type { components, paths } from './types.gen';
type S = components['schemas'];
// field names the web (WP-6A) uses and the WP-4A additions
const g: S['GeneratedCandidate']['training_status'] = 'UNTRAINED';
const e: NonNullable<S['CandidateEvaluation']['training_status']> = 'TRAINED';
const d: S['CandidateDetail']['training_status'] = undefined;
const w: Pick<S['Worker'], 'runner_id' | 'device_id' | 'runtime_manifest_status'> = { runner_id: 'r', device_id: null, runtime_manifest_status: 'verified' };
const u: S['UploadResponse'] = { upload_id: 'upl-x', source_sha256: 'sha256:x', bytes: 1 };
const loc: NonNullable<S['Error']['location']> = 'gateway';
const rev: S['ModelConfigRevision']['kind'] = 'executed';
type RevList = paths['/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions']['get']['responses'][200]['content']['application/json'];
const list: RevList = { run_id: 'r', candidate_id: 'c', revisions: [] };
type Yaml = paths['/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions/{revision_id}/yaml']['get']['responses'][200]['content']['application/x-yaml'];
const y: Yaml = 'nc: 1\n';
type Up = paths['/api/v1/uploads/model-config']['post']['responses'][200]['content']['application/json'];
const up: Up = u;
const b: NonNullable<NonNullable<S['Preset']['protocol']['console']['baselines']>[number]['name']> = 'YOLO11N';
// removed members must not type-check any more
// @ts-expect-error Generation.group_key removed in 1.5.0
const gk: S['Generation']['group_key'] = 'x';
// @ts-expect-error Block.old_name removed in 1.5.0
const on: S['Block']['old_name'] = null;
// @ts-expect-error Worker.pid removed in 1.5.0
const pid: S['Worker']['pid'] = 1;
export { g, e, d, w, u, loc, rev, list, y, up, b, gk, on, pid };
