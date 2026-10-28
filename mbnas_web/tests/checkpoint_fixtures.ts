/**
 * SYNTHETIC candidate detail with checkpoint rows for the V4-B fix (DD-A55 a, DD-A51 c), shared by the unit tests and the
 * Playwright mock (e2e/mock.ts `storeObjects`). One row per download case of CheckpointTable:
 *   - PRESENT, artifact_id null       -> downloaded by its checkpoint id (the gateway serves checkpoints by id; what the
 *                                        real stack showed before the core filled artifact_id);
 *   - PRESENT, artifact_id = the id    -> downloaded by the store id the core filled in;
 *   - PRESENT, artifact_id null, but the store does not hold the bytes yet -> 404 ARTIFACT_NOT_READY ("아직 저장소에 없음");
 *   - PRUNED, artifact_id null         -> nothing to download ("저장소 미등록").
 * Bytes are invented text, not model weights; the sha256 values are the real digests of these bytes (node:crypto), so the
 * console's own browser-side hash check is exercised for real. The structure report is the synthetic mbnas-structure/2
 * document of detail_fixtures.ts, so the same detail also draws the gene graph (axe nested-interactive check).
 */
import type { CandidateDetail } from '../src/api/types';
import { sha256Hex } from './config_fixtures';
import { syntheticStructureReport } from './detail_fixtures';
import { RUN_ID } from './fixtures';

/** A crossover candidate of fixtureCandidates() (button "ga-g1-2" in the Monitor candidate table). */
export const CK_CID = 'synthetic-ga-g1-2';
export const CK_IDS = {
  byCheckpointId: 'ck-synthetic-0001',
  byStoreId: 'ck-synthetic-0002',
  notInStore: 'ck-synthetic-0003',
  pruned: 'ck-synthetic-0004',
} as const;

/** Store bytes by the id the gateway serves them under (checkpoint id = store id here, as the core fills it). */
export const CK_STORE_BYTES: Readonly<Record<string, string>> = {
  [CK_IDS.byCheckpointId]: 'SYNTHETIC full training state bytes (test fixture, not a model)\n'.repeat(8),
  [CK_IDS.byStoreId]: 'SYNTHETIC weights-only bytes (test fixture, not a model)\n'.repeat(4),
};

export function fixtureCheckpointRows(): NonNullable<CandidateDetail['checkpoints']> {
  const a = CK_STORE_BYTES[CK_IDS.byCheckpointId], b = CK_STORE_BYTES[CK_IDS.byStoreId];
  return [
    { id: CK_IDS.byCheckpointId, kind: 'training_full', rung: 'L0', epoch: 2, sha256: sha256Hex(a), bytes: Buffer.byteLength(a), state: 'PRESENT', artifact_id: null },
    { id: CK_IDS.byStoreId, kind: 'weights_only', rung: 'L0', epoch: 2, sha256: sha256Hex(b), bytes: Buffer.byteLength(b), state: 'PRESENT', artifact_id: CK_IDS.byStoreId },
    { id: CK_IDS.notInStore, kind: 'eval_ema', rung: 'L0', epoch: 2, sha256: 'e'.repeat(64), bytes: 64, state: 'PRESENT', artifact_id: null },
    { id: CK_IDS.pruned, kind: 'training_full', rung: 'L0', epoch: 1, sha256: 'd'.repeat(64), bytes: 64, state: 'PRUNED', artifact_id: null, pruned_at: '2026-10-21T00:00:00Z' },
  ];
}

export function fixtureCheckpointDetail(cid: string = CK_CID, runId: string = RUN_ID): CandidateDetail {
  const structure = syntheticStructureReport();
  return {
    candidate_id: cid,
    run_id: runId,
    name: 'ga-g1-2',
    arm: 'GA',
    family: 'GA-mixed',
    architecture_hash: String(structure.architecture_hash),
    architecture_yaml: null,
    architecture_path: null,
    blocks: [],
    parents: ['synthetic-ga-g0-2', 'synthetic-ga-g0-3'],
    lineage: { operation: 'crossover' },
    evaluations: [],
    checkpoints: fixtureCheckpointRows(),
    cost: {},
    gate_history: [],
    candidate_status: 'EVALUATED',
    audit: { structure: { schema: 'mbnas-structure/2', status: 'PASS' } },
    structure: structure as CandidateDetail['structure'],
    structure_status: 'PASS',
    evaluation_status: 'EVALUATED',
    artifacts: [],
  };
}

/** What the (mock) gateway store answers per id: the bytes of the two stored rows, 404 ARTIFACT_NOT_READY for the third. */
export function fixtureStoreObjects(): Record<string, { bytes: string } | { status: number; code: string; message: string }> {
  return {
    [CK_IDS.byCheckpointId]: { bytes: CK_STORE_BYTES[CK_IDS.byCheckpointId] },
    [CK_IDS.byStoreId]: { bytes: CK_STORE_BYTES[CK_IDS.byStoreId] },
    [CK_IDS.notInStore]: { status: 404, code: 'ARTIFACT_NOT_READY', message: 'synthetic: the checkpoint bytes are not in the store yet' },
  };
}
