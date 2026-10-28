/**
 * WP-6A (DD-A8 / DD-A21 / T8 HOST_FACT): the bundle SOURCE (src/, which is all the production bundle is built from)
 * carries no protocol template, handoff value marker, host fact or client fitness recompute. The built dist/ is checked
 * by `npm run check:bundle` (scripts/check-bundle.mjs) after `npm run build`; this test guards the source on every
 * vitest run. src/api/types.gen.ts is excluded: it is generated from the public contract and type-only (erased by the build).
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const walk = (dir: string): string[] => readdirSync(dir).flatMap((n) => (statSync(join(dir, n)).isDirectory() ? walk(join(dir, n)) : [join(dir, n)]));
const src = walk(join(__dirname, '../src')).filter((f) => /\.(ts|tsx|css)$/.test(f) && !f.endsWith('types.gen.ts'));
const rel = (f: string): string => f.replace(/.*\/src\//, 'src/');

/** Needles are written as parts joined at run time so that this test file is never itself a hit for a host-fact scan of the repository / ZIP (T8). */
const joinParts = (parts: string[][]): string[] => parts.map((p) => p.join(''));
/** WP-6 R2 acceptance needles + T8 HOST_FACT needles: never in the source text, comments included. */
const HOST_AND_TEMPLATE = joinParts([
  ['value_', 'provenance'], ['illustrative_', 'new_revision'], ['/root/', 'detection'], ['/data/', 'drone'], ['147.', '46.'], ['pc', '2300'], ['/etri_', 'nas/'],
  ['/data/', 'mg', 'jeong'], ['etri_nas_', 'runs'], ['mg', 'jeong'], ['NPS_', 'YOLO'], ['pilot_', 'visdrone_v1'], ['unresolved_before_', 'real_execution'],
  ['DESIGN_EXAMPLE_', 'NOT_EXECUTABLE'],
]);
/**
 * Identifiers / texts of the code WP-6A removed (R1 recompute, R2 templates, R6 client rule badge) and, WP-6B, the owner-only
 * block repair trace of contract 1.5.0 that the console no longer displays (DD-A19: repair reason, old name, repair kind).
 */
const REMOVED = joinParts([
  ['compute', 'Fitness'], ['bounded', 'Reward'], ['metricInvalid', 'Reason'], ['HANDOFF_', 'JSON'], ['default', 'Policy'], ['pilotSmoke', 'Policy'],
  ['CLIENT_', 'TEMPLATES'], ['apply', 'Template'], ['SMOKE_', 'WEIGHTS'], ['PILOT_', 'ROOT'], ['Client ', 'recompute'], ['not ', 'computable'],
  ['differs from ', 'client rule'], ['client ', 'template'],
  ['repair_', 'reason'], ['Repair ', 'reason'], ['R (', 'repair)'], ['old_', 'name'],
]);

describe('bundle source hygiene (src/)', () => {
  it('scans a non-trivial source tree', () => {
    expect(src.length).toBeGreaterThan(50);
  });

  it.each(HOST_AND_TEMPLATE)('no %j anywhere in src', (needle) => {
    expect(src.filter((f) => readFileSync(f, 'utf8').includes(needle)).map(rel)).toEqual([]);
  });

  it.each(REMOVED)('removed code %j stays removed', (needle) => {
    expect(src.filter((f) => readFileSync(f, 'utf8').includes(needle)).map(rel)).toEqual([]);
  });

  it('the embedded BASELINE14 list stays removed (the order now comes from the server preset DEFAULT_BASELINE14)', () => {
    expect(src.filter((f) => /(^|[^_A-Za-z])BASELINE14\b/m.test(readFileSync(f, 'utf8'))).map(rel)).toEqual([]);
  });

  it('src never imports the test-only fitness oracle or any fixture', () => {
    expect(src.filter((f) => /fitness_oracle|from\s+['"][^'"]*tests\//.test(readFileSync(f, 'utf8'))).map(rel)).toEqual([]);
  });
});
