/**
 * SYNTHETIC configuration-revision and upload fixtures (contract 1.5.0, F5a/F5d/F5e) shared by the unit tests and the
 * Playwright mock (e2e/mock.ts). The YAML texts are invented model configurations, not research results; hashes are the
 * real sha256 of these bytes (computed with node:crypto) so the console's own hash checks are exercised for real.
 */
import { createHash } from 'node:crypto';
import type { ModelConfigRevision, ModelConfigRevisionList, UploadResponse } from '../src/api/types';
import { RUN_ID } from './fixtures';

export const sha256Hex = (data: string | Uint8Array): string => createHash('sha256').update(data).digest('hex');

/** The crossover candidate of fixtureCandidates() (two parents, BLOCKS): its configuration has three revisions. */
export const CFG_CID = 'synthetic-ga-g1-1';

export const ORIGINAL_YAML = [
  '# SYNTHETIC model configuration (test fixture, not a research result)',
  'nc: 2',
  'scale: n',
  'backbone:',
  '  - [-1, 1, Conv, [32, 3, 2]]',
  '  - [-1, 2, C2f, [64, True]]',
  '  - [-1, 1, SPPF, [128, 5]]',
  'head:',
  '  - [[-1, 2], 1, Conv, [64, 1, 1]]',
  '  - [-1, 1, Detect, [nc]]',
  '',
].join('\n');

/** What the trainer ran: the dataset's class count applied and the mutated block widened (two changed lines). */
export const EXECUTED_YAML = ORIGINAL_YAML.replace('nc: 2', 'nc: 3').replace('[-1, 2, C2f, [64, True]]', '[-1, 2, C2f, [96, True]]');

export const REVISION_IDS = { original: 'rev-synthetic-0001', selected: 'rev-synthetic-0002', executed: 'rev-synthetic-0003' } as const;

const CANONICAL = (n: number): string => `sha256:${String(n).repeat(64).slice(0, 64)}`;
export const EXECUTED_ARCH_HASH = `sha256:${'ab12'.repeat(16)}`;

/** Oldest first: the uploaded original, the selection bound to the training run (same bytes), the executed configuration. */
export function fixtureRevisions(cid: string = CFG_CID): ModelConfigRevisionList {
  const o = sha256Hex(ORIGINAL_YAML), e = sha256Hex(EXECUTED_YAML);
  const rows: ModelConfigRevision[] = [
    { revision_id: REVISION_IDS.original, kind: 'original', source_sha256: `sha256:${o}`, canonical_hash: CANONICAL(1), architecture_hash: null, created_at: '2026-10-27T06:00:00Z', parent_revision_id: null, artifact_id: `upl-${o}` },
    { revision_id: REVISION_IDS.selected, kind: 'selected', source_sha256: `sha256:${o}`, canonical_hash: CANONICAL(1), architecture_hash: null, created_at: '2026-10-27T06:00:05Z', parent_revision_id: REVISION_IDS.original, artifact_id: `upl-${o}` },
    { revision_id: REVISION_IDS.executed, kind: 'executed', source_sha256: `sha256:${e}`, canonical_hash: CANONICAL(2), architecture_hash: EXECUTED_ARCH_HASH, created_at: '2026-10-27T06:01:00Z', parent_revision_id: REVISION_IDS.selected, artifact_id: 'art-synthetic-exec-yaml' },
  ];
  return { run_id: RUN_ID, candidate_id: cid, revisions: rows };
}

/** Stored bytes per revision id, with the X-Artifact-Sha256 the gateway sends (hex). */
export function fixtureRevisionBytes(): Record<string, { bytes: string; sha256: string }> {
  return {
    [REVISION_IDS.original]: { bytes: ORIGINAL_YAML, sha256: sha256Hex(ORIGINAL_YAML) },
    [REVISION_IDS.selected]: { bytes: ORIGINAL_YAML, sha256: sha256Hex(ORIGINAL_YAML) },
    [REVISION_IDS.executed]: { bytes: EXECUTED_YAML, sha256: sha256Hex(EXECUTED_YAML) },
  };
}

/** The gateway's answer for stored upload bytes (content addressed: upl-<sha256>). */
export function fixtureUploadResponse(bytes: string | Uint8Array): UploadResponse {
  const hex = sha256Hex(bytes);
  return { upload_id: `upl-${hex}`, source_sha256: `sha256:${hex}`, bytes: typeof bytes === 'string' ? Buffer.byteLength(bytes) : bytes.length };
}
