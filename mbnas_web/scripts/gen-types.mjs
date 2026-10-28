#!/usr/bin/env node
// Generates src/api/types.gen.ts from the canonical, commander-owned OpenAPI contract.
//   node scripts/gen-types.mjs          -> (re)write src/api/types.gen.ts
//   node scripts/gen-types.mjs --check  -> regenerate into a temp file and diff against the
//                                          committed file; exit 1 on drift (contract <-> types equivalence).
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
// default: the openapi.json package data of the sibling mbnas_contracts package (mbnas_web and mbnas_contracts live side by side in the repo root)
const CONTRACT = process.env.MBNAS_OPENAPI ?? resolve(root, '../mbnas_contracts/src/mbnas_contracts/openapi/openapi.json');
const TARGET = join(root, 'src/api/types.gen.ts');
const check = process.argv.includes('--check');

if (!existsSync(CONTRACT)) {
  console.error(`contract not found: ${CONTRACT}`);
  process.exit(2);
}

const tmp = check ? mkdtempSync(join(tmpdir(), 'mbnas-types-')) : null;
const out = check ? join(tmp, 'types.gen.ts') : TARGET;
const bin = join(root, 'node_modules/.bin/openapi-typescript');
const res = spawnSync(bin, [CONTRACT, '--empty-objects-unknown', '-o', out], { cwd: root, stdio: 'inherit' });
if (res.status !== 0) {
  if (tmp) rmSync(tmp, { recursive: true, force: true });
  console.error('openapi-typescript failed');
  process.exit(res.status ?? 1);
}
if (check) {
  let same = false;
  try {
    same = readFileSync(out, 'utf8') === readFileSync(TARGET, 'utf8');
  } catch {
    same = false;
  }
  rmSync(tmp, { recursive: true, force: true });
  if (!same) {
    console.error('DRIFT: src/api/types.gen.ts differs from the contract. Run `npm run gen:types`.');
    process.exit(1);
  }
  console.log('types.gen.ts matches the contract');
}
