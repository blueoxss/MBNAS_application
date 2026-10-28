/**
 * DD-A43 (a) packaging guard (public test; it imports nothing from the oracle). The packager excludes tests/fitness_oracle.ts
 * and every file that imports it, directly or transitively ([deny-importers] in build/allowlists/public.txt, static scan of
 * relative ES / CommonJS imports). The shared fixtures (tests/fixtures.ts, tests/mockapi.ts, e2e/mock.ts and the other
 * fixture modules) must stay outside that closure, or most web tests drop out of the public bundle.
 *  - owner tree (the oracle is present): the closure is exactly the owner-only set below;
 *  - public / registration bundle (the oracle is absent): no remaining file imports an oracle module.
 * The import scan mirrors scripts/dev/package_build.py (RE_JS_IMPORT + _resolve_js); its pattern is assembled from pieces so
 * this file never looks like an import of the modules it names.
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, normalize, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = join(__dirname, '..');
const SUFFIXES = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.mts', '.cts'];
const ORACLE = 'tests/fitness_oracle.ts';
/** The owner-only files: the oracle, the oracle-based fixture helpers and the tests that use them. */
const OWNER_ONLY = ['e2e/weights.spec.ts', 'tests/fitness_oracle.ts', 'tests/fixtures_oracle.test.ts', 'tests/fixtures_oracle.ts'];
/** Shared fixture modules that must remain public. */
const SHARED = ['tests/fixtures.ts', 'tests/mockapi.ts', 'e2e/mock.ts', 'tests/archive_fixtures.ts', 'tests/detail_fixtures.ts', 'tests/setup.ts'];

const q = `['"]`;
const IMPORT_RE = new RegExp(
  [`(?:\\b${'imp'}ort|\\b${'exp'}ort)\\s[^'"\`;]*?\\b${'fr'}om\\s*${q}([^'"]+)${q}`, `\\b${'imp'}ort\\s*${q}([^'"]+)${q}`,
    `\\b${'imp'}ort\\(\\s*${q}([^'"]+)${q}\\s*\\)`, `\\b${'req'}uire\\(\\s*${q}([^'"]+)${q}\\s*\\)`].join('|'), 'g');

function walk(dir: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const files = [...walk(join(ROOT, 'tests')), ...walk(join(ROOT, 'e2e'))].map((p) => relative(ROOT, p).split('\\').join('/')).filter((r) => SUFFIXES.some((s) => r.endsWith(s)));
const present = new Set(files);

function resolveSpec(from: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null;
  const base = normalize(join(dirname(from), spec)).split('\\').join('/');
  const ext = SUFFIXES.find((s) => base.endsWith(s) && ['.js', '.jsx', '.mjs', '.cjs'].includes(s));
  const stem = ext ? base.slice(0, -ext.length) : base;
  for (const cand of [base, ...SUFFIXES.map((s) => base + s), ...SUFFIXES.map((s) => stem + s), ...SUFFIXES.map((s) => `${base}/index${s}`)]) if (present.has(cand)) return cand;
  return null;
}

const deps = new Map<string, Set<string>>(files.map((f) => {
  const text = readFileSync(join(ROOT, f), 'utf8');
  const out = new Set<string>();
  for (const m of text.matchAll(IMPORT_RE)) for (const spec of m.slice(1)) if (spec) { const t = resolveSpec(f, spec); if (t) out.add(t); }
  return [f, out];
}));

/** Every file that imports `target`, directly or transitively (the packager's closure), plus the target. */
function importerClosure(target: string): string[] {
  const closure = new Set([target]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const [f, ds] of deps) if (!closure.has(f) && [...ds].some((d) => closure.has(d))) { closure.add(f); changed = true; }
  }
  return [...closure].sort();
}

describe('the fitness oracle stays in owner-only files (DD-A43 a)', () => {
  it('the import scan finds real imports (guards against a vacuous check)', () => {
    expect(deps.get('tests/mockapi.ts')?.has('tests/fixtures.ts')).toBe(true);
    expect(deps.get('e2e/mock.ts')?.has('tests/fixtures.ts')).toBe(true);
  });

  if (present.has(ORACLE)) {
    it('owner tree: the importer closure of the oracle is exactly the owner-only set', () => {
      expect(importerClosure(ORACLE)).toEqual([...OWNER_ONLY].sort());
    });
  } else {
    it('public bundle: the oracle and its importers are absent and nothing imports an oracle module', () => {
      for (const f of OWNER_ONLY) expect(present.has(f), f).toBe(false);
      for (const [f, ds] of deps) expect([...ds].filter((d) => /(fitness|fixtures)_oracle/.test(d)), f).toEqual([]);
      for (const f of files) {
        const specs = [...readFileSync(join(ROOT, f), 'utf8').matchAll(IMPORT_RE)].flatMap((m) => m.slice(1).filter(Boolean));
        expect(specs.filter((s) => /(fitness|fixtures)_oracle/.test(s)), f).toEqual([]);
      }
    });
  }

  it('the shared fixtures, the unit mock API and the Playwright mock are not in the closure', () => {
    const closure = new Set(importerClosure(ORACLE));
    for (const f of SHARED) {
      expect(present.has(f), `${f} exists`).toBe(true);
      expect(closure.has(f), `${f} must not import the oracle`).toBe(false);
    }
  });

  it('every test file except the owner-only ones stays in the public bundle', () => {
    const closure = new Set(importerClosure(ORACLE));
    const tests = files.filter((f) => /\.(test|spec)\.tsx?$/.test(f));
    expect(tests.length).toBeGreaterThan(40);
    expect(tests.filter((f) => closure.has(f)).sort()).toEqual(OWNER_ONLY.filter((f) => /\.(test|spec)\.tsx?$/.test(f)).filter((f) => present.has(f)).sort());
  });
});
