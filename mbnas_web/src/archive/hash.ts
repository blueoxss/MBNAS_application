/**
 * URL hash of the archive screens: `#/archive?run=<id>&gen=<n>&cid=<id>` and `#/compare?runs=a,b,c`.
 * The bearer token is never part of the hash. Every value is validated; anything malformed is dropped.
 */

export const ID_RE = /^[\w.-]{1,128}$/;
const CID_RE = /^[\w.:@-]{1,160}$/;

export interface ParsedHash {
  /** screen name as written (validated against `screens`), fallback when unknown */
  screen: string;
  run: string | null;
  gen: number | null;
  cid: string | null;
  runs: string[];
}

export function parseHashString(raw: string, screens: readonly string[], fallback: string): ParsedHash {
  const h = raw.replace(/^#\/?/, '');
  const qi = h.indexOf('?');
  const name = qi < 0 ? h : h.slice(0, qi);
  const q = new URLSearchParams(qi < 0 ? '' : h.slice(qi + 1));
  const run = q.get('run');
  const gen = q.get('gen');
  const cid = q.get('cid');
  const runs = (q.get('runs') ?? '').split(',').filter((x) => ID_RE.test(x));
  return {
    screen: screens.includes(name) ? name : fallback,
    run: run && ID_RE.test(run) ? run : null,
    gen: gen !== null && /^\d{1,5}$/.test(gen) ? Number(gen) : null,
    cid: cid && CID_RE.test(cid) ? cid : null,
    runs: [...new Set(runs)].slice(0, 6),
  };
}

export function buildArchiveHash(sel: { run?: string | null; gen?: number | null; cid?: string | null }): string {
  const q = new URLSearchParams();
  if (sel.run) q.set('run', sel.run);
  if (sel.run && sel.gen !== null && sel.gen !== undefined) q.set('gen', String(sel.gen));
  if (sel.run && sel.cid) q.set('cid', sel.cid);
  const s = q.toString();
  return `#/archive${s ? `?${s}` : ''}`;
}

export function buildCompareHash(runs: readonly string[]): string {
  return runs.length ? `#/compare?runs=${runs.map(encodeURIComponent).join(',')}` : '#/compare';
}
