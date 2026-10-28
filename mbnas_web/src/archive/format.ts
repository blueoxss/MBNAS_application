/**
 * Archive display formatting: mAP/recall 4 decimals, latency 2 decimals, params as an integer with
 * thousands separators. A missing / non-finite value is ALWAYS the dash and never 0.
 */
import { DASH } from '../lib/format';

export { DASH };

export const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export function fmtFixed(v: unknown, digits: number): string {
  return isNum(v) ? v.toFixed(digits) : DASH;
}
/** mAP / recall (0-1) with 4 decimals. */
export const fmtMap = (v: unknown): string => fmtFixed(v, 4);
/** latency in ms with 2 decimals (no unit; column headers carry it). */
export const fmtLat = (v: unknown): string => fmtFixed(v, 2);
export const fmtLatMs = (v: unknown): string => (isNum(v) ? `${v.toFixed(2)} ms` : DASH);
export const fmtMb = (v: unknown): string => (isNum(v) ? `${v.toFixed(1)} MB` : DASH);

/** Integer with thousands separators ("2,375,031"). */
export function fmtInt(v: unknown): string {
  return isNum(v) ? Math.round(v).toLocaleString('en-US') : DASH;
}

/** Signed difference: "+0.0123" / "-0.0123" / "0.0000"; missing -> dash. */
export function fmtDelta(v: unknown, digits = 4): string {
  if (!isNum(v)) return DASH;
  const s = v.toFixed(digits);
  if (Number(s) === 0) return (0).toFixed(digits);
  return v > 0 ? `+${s}` : s;
}
export function fmtDeltaInt(v: unknown): string {
  if (!isNum(v)) return DASH;
  const s = Math.round(v).toLocaleString('en-US');
  return v > 0 ? `+${s}` : s;
}

/** Throughput [FPS] = 1000 / latency_ms (batch-1 derived). */
export function fmtFps(ms: unknown): string {
  return isNum(ms) && ms > 0 ? (1000 / ms).toFixed(1) : DASH;
}

/** Seconds -> "1 h 02 min 03 s" / "5 min 07 s" / "42 s". */
export function fmtDur(s: unknown): string {
  if (!isNum(s) || s < 0) return DASH;
  const t = Math.round(s);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = t % 60;
  if (h > 0) return `${h} h ${String(m).padStart(2, '0')} min ${String(sec).padStart(2, '0')} s`;
  if (m > 0) return `${m} min ${String(sec).padStart(2, '0')} s`;
  return `${sec} s`;
}

/** ISO timestamp -> "2026-10-12 22:48" (UTC as sent; the raw string if unparseable). */
export function fmtStamp(iso: unknown): string {
  if (typeof iso !== 'string' || iso.trim() === '') return DASH;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  return new Date(t).toISOString().slice(0, 16).replace('T', ' ');
}

export function shortHash(h: unknown, n = 10): string {
  return typeof h === 'string' && h !== '' ? (h.length > n ? `${h.slice(0, n)}…` : h) : DASH;
}

export function fmtBool(v: boolean | null | undefined, yes = '예', no = '아니오'): string {
  return v === true ? yes : v === false ? no : DASH;
}

/** Generic value for tables / key-value lists. */
export function fmtVal(v: unknown): string {
  if (v === null || v === undefined || v === '') return DASH;
  if (typeof v === 'number') return Number.isFinite(v) ? (Number.isInteger(v) ? fmtInt(v) : String(Number(v.toPrecision(6)))) : DASH;
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v) ?? DASH;
  } catch {
    return DASH;
  }
}

/**
 * cache_hit of a candidate: the contract carries the source candidate id (string) of a cache hit and null otherwise
 * (a legacy boolean is still understood). null is shown as the dash, not as 'miss', because the jsonl notes of a row can be cut.
 */
export function fmtCacheHit(v: unknown): string {
  if (typeof v === 'string' && v !== '') return `hit ← ${v}`;
  if (v === true) return 'hit';
  if (v === false) return 'miss';
  return DASH;
}

export interface TierInfo {
  kind: 'search' | 'final' | 'other';
  epochs: number | null;
  raw: string;
  label: string;
}

/**
 * Fidelity tier label. 'T@8e' -> search tier, 8 epochs; 'final@12e' -> final tier, 12 epochs. The tier is always
 * printed next to a number so that a final-tier baseline is never read as a search-tier value (design 4.1-3).
 */
export function tierInfo(tier: string | null | undefined): TierInfo {
  if (!tier) return { kind: 'other', epochs: null, raw: '', label: DASH };
  const m = /^(T|final)@(\d+)e$/.exec(tier);
  if (!m) return { kind: 'other', epochs: null, raw: tier, label: tier };
  const epochs = Number(m[2]);
  return m[1] === 'T' ? { kind: 'search', epochs, raw: tier, label: `탐색 ${tier} (${epochs} ep)` } : { kind: 'final', epochs, raw: tier, label: `최종 ${tier} (${epochs} ep)` };
}

export function epochsLabel(prefix: string, epochs: unknown): string {
  return isNum(epochs) ? `${prefix} ${epochs} ep` : prefix;
}

export interface HistRow {
  module: string;
  cur: number;
  prev: number | null;
  delta: number | null;
}

/**
 * Module histogram comparison. delta = cur - prev (prev missing -> delta null, never cur - 0).
 * Sorted by |delta| descending, then by name; rows without a delta come last (by count).
 */
export function histogramRows(cur: Record<string, number> | null | undefined, prev: Record<string, number> | null | undefined): HistRow[] {
  const c = cur ?? {};
  const havePrev = prev != null;
  const names = new Set<string>([...Object.keys(c), ...(havePrev ? Object.keys(prev) : [])]);
  const rows: HistRow[] = [...names].map((module) => {
    const cv = c[module] ?? 0;
    const pv = havePrev ? (prev[module] ?? 0) : null;
    return { module, cur: cv, prev: pv, delta: pv === null ? null : cv - pv };
  });
  rows.sort((a, b) => {
    if (a.delta === null && b.delta === null) return b.cur - a.cur || a.module.localeCompare(b.module);
    if (a.delta === null) return 1;
    if (b.delta === null) return -1;
    return Math.abs(b.delta) - Math.abs(a.delta) || a.module.localeCompare(b.module);
  });
  return rows;
}

/** Parse a metrics field that may arrive as a number or a numeric string; anything else -> null. */
export function numOrNull(v: unknown): number | null {
  if (isNum(v)) return v;
  if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v);
  return null;
}

export function recOf(v: unknown): Record<string, unknown> {
  return v != null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
