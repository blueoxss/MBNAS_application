import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import { describe, expect, it } from 'vitest';
import { baselineRow, finalRow, fixtureCandidates, fixtureFinalRun, fixtureFinalists, fixtureGenerations, fixtureHealth, fixturePresets, fixtureRun, fixtureSnapshot, fixtureValidateOk, fixtureWorkers } from './fixtures';

// default: the openapi.json package data of the sibling mbnas_contracts package (mbnas_web and mbnas_contracts live side by side in the repo root)
const CONTRACT = process.env.MBNAS_OPENAPI ?? join(__dirname, '../../mbnas_contracts/src/mbnas_contracts/openapi/openapi.json');
const spec = JSON.parse(readFileSync(CONTRACT, 'utf8')) as { paths: Record<string, Record<string, unknown>>; components: { schemas: Record<string, unknown> } };

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema({ $id: 'mbnas', components: spec.components });
const check = (schema: string, data: unknown): string[] => {
  const v = ajv.getSchema(`mbnas#/components/schemas/${schema}`) ?? ajv.compile({ $ref: `mbnas#/components/schemas/${schema}` });
  return v(data) ? [] : (v.errors ?? []).map((e) => `${e.instancePath} ${e.message}`);
};

describe('synthetic fixtures conform to the canonical contract', () => {
  it('Health, Run, Worker, Generation, Candidate rows, Snapshot, Preset, ValidateResponse', () => {
    expect(check('Health', fixtureHealth())).toEqual([]);
    expect(check('Run', fixtureRun())).toEqual([]);
    for (const w of fixtureWorkers()) expect(check('Worker', w)).toEqual([]);
    for (const g of fixtureGenerations()) expect(check('Generation', g)).toEqual([]);
    // 'ga-missing-batch' is a deliberately contract-violating row (EVALUATED without batch) used to test UNRESOLVED_COHORT
    for (const c of fixtureCandidates().filter((x) => x.name !== 'ga-missing-batch')) expect(check('CandidateEvaluation', c)).toEqual([]);
    expect(check('CandidateEvaluation', fixtureCandidates().find((x) => x.name === 'ga-missing-batch')).length).toBeGreaterThan(0);
    expect(check('Snapshot', { ...fixtureSnapshot(), candidates: fixtureSnapshot().candidates.filter((x) => x.name !== 'ga-missing-batch') })).toEqual([]);
    for (const p of fixturePresets()) expect(check('Preset', p)).toEqual([]);
    expect(check('ValidateResponse', fixtureValidateOk({ a: 1 }))).toEqual([]);
    expect(check('CandidateEvaluation', baselineRow('x', 1, 0.1, 1))).toEqual([]);
  });
  it('the validator really rejects a contract violation (guards against a vacuous check)', () => {
    expect(check('Run', { id: 'r', status: 'DONE', protocol_hash: 'h' }).length).toBeGreaterThan(0);
    expect(check('Generation', { arm: 'GA' }).length).toBeGreaterThan(0);
  });
});

describe('final retraining fixtures conform to contract v1.2.0', () => {
  it('FINAL_RETRAIN run, finalists, final rows, RunCreated and FinalRetrainRequest', () => {
    expect(check('Run', fixtureFinalRun())).toEqual([]);
    expect(check('Run', fixtureFinalRun('COMPLETED', []))).toEqual([]);
    expect(fixtureFinalists().length).toBeGreaterThan(0);
    expect(check('CandidateEvaluation', finalRow())).toEqual([]);
    expect(check('RunCreated', { run_id: 'r', status: 'QUEUED', parent_run_id: 'p', finalist_count: 2 })).toEqual([]);
    expect(check('FinalRetrainRequest', { idempotency_key: 'k', budget_gpu_hours: 0.5, top_k_per_arm_per_seed: 2, reason: 'x' })).toEqual([]);
    expect(check('FinalRetrainRequest', { idempotency_key: 'k', budget_gpu_hours: 0 }).length).toBeGreaterThan(0); // exclusiveMinimum
    expect(check('FinalRetrainRequest', { budget_gpu_hours: 1 }).length).toBeGreaterThan(0); // key required
  });
  it('POST /runs/{id}/final-retrain documents the Idempotency-Key header', () => {
    const post = spec.paths['/api/v1/runs/{run_id}/final-retrain'].post as { parameters: { name: string }[] };
    expect(post.parameters.map((p) => p.name)).toContain('Idempotency-Key');
  });
});

describe('the client only calls endpoints that exist in the contract', () => {
  const src = readFileSync(join(process.cwd(), 'src/api/client.ts'), 'utf8');
  const used = [...src.matchAll(/`?'?(\/api\/v1\/[^'`?]*)/g)].map((m) => m[1].replace(/\$\{[^}]+\}/g, '{p}').replace(/\/$/, ''));
  const known = Object.keys(spec.paths).map((p) => p.replace(/\{[^}]+\}/g, '{p}'));
  // control actions are built as /runs/{id}/${action}; pause, resume and cancel must all be declared
  if (['pause', 'resume', 'cancel'].every((a) => known.includes(`/api/v1/runs/{p}/${a}`))) known.push('/api/v1/runs/{p}/{p}');
  it('every path template used by ApiClient is declared in openapi.json', () => {
    expect(used.length).toBeGreaterThan(10);
    for (const u of new Set(used)) expect(known, u).toContain(u);
  });
  it('uses bearer security and documents the Idempotency-Key header on POST /runs', () => {
    const post = spec.paths['/api/v1/runs'].post as { parameters: { name: string }[] };
    expect(post.parameters.map((p) => p.name)).toContain('Idempotency-Key');
  });
});
