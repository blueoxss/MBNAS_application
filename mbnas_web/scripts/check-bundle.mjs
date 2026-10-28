#!/usr/bin/env node
// Bundle hygiene gate (WP-6 R2 acceptance, DD-A8 / T8 HOST_FACT): after `npm run build`, no file under dist/ may contain
// the removed policy embeds (handoff template keys), host facts, or the removed client fitness recompute.
//   node scripts/check-bundle.mjs            -> scan ./dist (exit 1 on any hit, exit 2 when dist/ is missing or empty)
//   node scripts/check-bundle.mjs --json     -> same, machine-readable report on stdout
//   node scripts/check-bundle.mjs --dist DIR -> scan DIR instead (e.g. a planted positive control)
// Needles are matched as raw bytes (utf-8) in every file, including .map files should a build ever emit them.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const distArg = process.argv.indexOf('--dist');
const dist = distArg > 0 && process.argv[distArg + 1] ? resolve(process.argv[distArg + 1]) : join(root, 'dist');
const json = process.argv.includes('--json');

// Each needle is written as parts joined at run time, so this file (shipped with mbnas_web) is never itself a hit for a
// repository / ZIP-wide host-fact scan (T8 HOST_FACT); the joined strings are the literal needles.
const joinParts = (parts) => parts.map((p) => p.join(''));
// WP-6 contract item 2 + task list: the handoff template keys and host facts that must never reach the public bundle
const REQUIRED = joinParts([
  ['value_', 'provenance'], ['illustrative_', 'new_revision'], ['/root/', 'detection'], ['/data/', 'drone'], ['147.', '46.'], ['pc', '2300'], ['/etri_', 'nas/'],
]);
// additional T8 HOST_FACT needles and markers of the code removed by WP-6A (R1 fitness recompute, R2 templates, R6 badge),
// WP-6B: the owner-only block repair trace of contract 1.5.0 (DD-A19: never in a public document, so never displayed) and the
// test-only fitness oracle (DD-A43 a: test code, owner only)
const EXTRA = joinParts([
  ['/data/', 'mg', 'jeong'], ['etri_nas_', 'runs'], ['mg', 'jeong'], ['NPS_', 'YOLO'], ['pilot_', 'visdrone_v1'], ['unresolved_before_', 'real_execution'],
  ['DESIGN_EXAMPLE_', 'NOT_EXECUTABLE'], ['new_protocol_not_', 'claimed_historical_GA'], ['Client ', 'recompute'], ['not ', 'computable'],
  ['differs from ', 'client rule'], ['client ', 'template'], ['METRIC_NOT_', 'FINITE'], ['LATENCY_', 'NONPOSITIVE'], ['SIZE_', 'NONPOSITIVE'],
  ['repair_', 'reason'], ['Repair ', 'reason'], ['R (', 'repair)'], ['old_', 'name'], ['fitness', 'Oracle'], ['fitness_', 'oracle'],
]);

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

if (!existsSync(dist)) {
  console.error(`dist/ not found at ${dist}: run \`npm run build\` first`);
  process.exit(2);
}
const files = walk(dist);
if (files.length === 0) {
  console.error('dist/ is empty: run `npm run build` first');
  process.exit(2);
}
const hits = [];
let bytes = 0;
for (const f of files) {
  const text = readFileSync(f).toString('utf8');
  bytes += Buffer.byteLength(text);
  for (const needle of [...REQUIRED, ...EXTRA]) {
    let at = text.indexOf(needle);
    let count = 0;
    while (at !== -1) {
      count += 1;
      at = text.indexOf(needle, at + needle.length);
    }
    if (count) hits.push({ file: relative(root, f), needle, count, required: REQUIRED.includes(needle) });
  }
}
const report = { dist: relative(root, dist), files: files.map((f) => relative(root, f)), bytes, needles: { required: REQUIRED, extra: EXTRA }, hits, ok: hits.length === 0 };
if (json) console.log(JSON.stringify(report, null, 2));
else {
  console.log(`scanned ${files.length} file(s), ${bytes} bytes under ${report.dist}/`);
  for (const f of report.files) console.log(`  ${f}`);
  for (const n of [...REQUIRED, ...EXTRA]) {
    const c = hits.filter((h) => h.needle === n).reduce((s, h) => s + h.count, 0);
    console.log(`  ${REQUIRED.includes(n) ? 'required' : 'extra   '} ${JSON.stringify(n)}: ${c}`);
  }
  console.log(report.ok ? 'bundle hygiene: OK (0 hits)' : `bundle hygiene: FAIL (${hits.length} hit record(s))`);
}
process.exit(report.ok ? 0 : 1);
