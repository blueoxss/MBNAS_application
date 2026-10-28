/**
 * Display formatting shared by all screens. A missing or non-finite value is ALWAYS the dash, never 0.
 *
 * 사용처 시그니처: src/screens/*.tsx, src/components/*.tsx (fmtNum/fmtPct/fmtMs/fmtMb/fmtAny/fmtTime/fmtShape/fmtSeconds/fmtGpuHours/latencyToFps).
 */

export const DASH = '—';

function finite(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** Fixed-point number; `digits` defaults to 3. */
export function fmtNum(v: number | null | undefined, digits = 3): string {
  return finite(v) ? v.toFixed(digits) : DASH;
}

/** Fraction (0-1) shown as percent with a unit: 0.2 -> "20.00 %". */
export function fmtPct(v: number | null | undefined, digits = 2): string {
  return finite(v) ? `${(v * 100).toFixed(digits)} %` : DASH;
}

/** Milliseconds: "50.00 ms". */
export function fmtMs(v: number | null | undefined, digits = 2): string {
  return finite(v) ? `${v.toFixed(digits)} ms` : DASH;
}

/** Decimal megabytes: "20.00 MB". */
export function fmtMb(v: number | null | undefined, digits = 2): string {
  return finite(v) ? `${v.toFixed(digits)} MB` : DASH;
}

/** Seconds as "1 h 30 min 0 s" (for tooltips next to a raw second count). */
export function fmtSeconds(v: number | null | undefined): string {
  if (!finite(v) || v < 0) return DASH;
  const total = Math.round(v);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h > 0 ? `${h} h` : '', h > 0 || m > 0 ? `${m} min` : '', `${s} s`].filter(Boolean).join(' ');
}

/** GPU seconds as GPU-hours with three decimals: 5400 -> "1.500 GPU-h"; unmeasured -> dash (never 0). */
export function fmtGpuHours(seconds: number | null | undefined): string {
  return finite(seconds) ? `${(seconds / 3600).toFixed(3)} GPU-h` : DASH;
}

/** Input shape [h, w] -> "640×640". */
export function fmtShape(shape: readonly number[] | null | undefined): string {
  return Array.isArray(shape) && shape.length > 0 && shape.every(finite) ? shape.join('×') : DASH;
}

/** ISO timestamp -> "2026-10-06 10:00:00 UTC" (the raw string when it does not parse). */
export function fmtTime(iso: string | null | undefined): string {
  if (typeof iso !== 'string' || iso.trim() === '') return DASH;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  return `${new Date(t).toISOString().replace('T', ' ').replace(/\.\d+Z$/, '').replace(/Z$/, '')} UTC`;
}

/** Any value as a short text: null/undefined/NaN -> dash, objects as compact JSON. */
export function fmtAny(v: unknown): string {
  if (v === null || v === undefined) return DASH;
  if (typeof v === 'string') return v === '' ? DASH : v;
  if (typeof v === 'number') return Number.isFinite(v) ? (Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(6)))) : DASH;
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  try {
    return JSON.stringify(v) ?? DASH;
  } catch {
    return DASH;
  }
}

/** Throughput [FPS] = 1000 / latency_ms (batch-1 derived). Non-positive latency gives NaN. */
export function latencyToFps(latencyMs: number): number {
  return latencyMs > 0 && Number.isFinite(latencyMs) ? 1000 / latencyMs : Number.NaN;
}
