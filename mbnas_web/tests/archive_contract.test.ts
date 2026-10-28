/**
 * The synthetic archive fixtures (the mock of the UI tests) must satisfy the canonical openapi 1.3.0 schemas, so the
 * mock cannot drift from the server. A deliberately contract-violating fixture is checked to fail (not a vacuous check).
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import { describe, expect, it } from 'vitest';
import {
  RUN_A,
  RUN_B,
  RUN_C,
  fixtureBaselines,
  fixtureCandidateDetail,
  fixtureCandidates,
  fixtureConfirm,
  fixtureFidelity,
  fixtureGenerationDetail,
  fixtureGenerations,
  fixtureLive,
  fixtureRun,
  fixtureRunList,
  SYNTH_A,
} from './archive_fixtures';

const CONTRACT = process.env.MBNAS_OPENAPI ?? join(__dirname, '../../mbnas_contracts/src/mbnas_contracts/openapi/openapi.json');
const spec = JSON.parse(readFileSync(CONTRACT, 'utf8')) as { info: { version: string }; paths: Record<string, unknown>; components: { schemas: Record<string, unknown> } };

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema({ $id: 'mbnas', components: spec.components });
const check = (schema: string, data: unknown): string[] => {
  const v = ajv.getSchema(`mbnas#/components/schemas/${schema}`) ?? ajv.compile({ $ref: `mbnas#/components/schemas/${schema}` });
  return v(data) ? [] : (v.errors ?? []).slice(0, 8).map((e) => `${e.instancePath} ${e.message}`);
};

describe('archive fixtures conform to the contract (openapi >= 1.3.0)', () => {
  it('the contract declares the 13 archive paths', () => {
    expect(spec.info.version).toMatch(/^1\.(3|[4-9])\./);
    expect(Object.keys(spec.paths).filter((p) => p.startsWith('/archive/') || p.startsWith('/api/v1/archive/')).length).toBeGreaterThanOrEqual(13);
  });
  it('run list and run detail (done, running, failed)', () => {
    expect(check('ArchiveRunList', fixtureRunList())).toEqual([]);
    for (const id of [RUN_A, RUN_B, RUN_C]) expect(check('ArchiveRun', fixtureRun(id))).toEqual([]);
  });
  it('baselines, generations, generation detail, candidates, candidate detail', () => {
    expect(check('ArchiveBaselineList', fixtureBaselines(RUN_A))).toEqual([]);
    expect(check('ArchiveGenerationList', fixtureGenerations(RUN_A))).toEqual([]);
    for (const g of SYNTH_A.generations) expect(check('ArchiveGenerationDetail', fixtureGenerationDetail(RUN_A, g.generation))).toEqual([]);
    expect(check('ArchiveCandidateList', fixtureCandidates(RUN_A, new URLSearchParams('limit=500')))).toEqual([]);
    for (const cid of [...new Set(SYNTH_A.rows.map((r) => r.candidate_id))]) expect(check('ArchiveCandidateDetail', fixtureCandidateDetail(RUN_A, cid))).toEqual([]);
  });
  it('fidelity, confirm, live', () => {
    expect(check('ArchiveFidelity', fixtureFidelity())).toEqual([]);
    expect(check('ArchiveConfirm', fixtureConfirm())).toEqual([]);
    expect(check('ArchiveLive', fixtureLive())).toEqual([]);
  });
  it('the validator rejects a violation (guards against a vacuous check)', () => {
    expect(check('ArchiveRunList', { runs: [{ id: 'x' }] }).length).toBeGreaterThan(0);
    expect(check('ArchiveCandidateRow', { candidate_id: 'x' }).length).toBeGreaterThan(0);
  });
});
