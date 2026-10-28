/**
 * Colours of the archive charts. Generation colour = sequential ramp (viridis stops, early = yellow, late = dark
 * purple); the points also carry a dark outline so that each mark keeps >= 3:1 against the white plot area, and the
 * mark SHAPE (circle / diamond / square / star) carries tier and kind, so colour is never the only cue.
 */

const STOPS: readonly [number, number, number][] = [
  [253, 231, 37],
  [94, 201, 98],
  [33, 145, 140],
  [59, 82, 139],
  [68, 1, 84],
];

const hex = (n: number): string => Math.round(n).toString(16).padStart(2, '0');

/** t in [0,1] -> '#rrggbb' on the ramp. Out-of-range t is clamped. */
export function rampColor(t: number): string {
  const x = Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0.5;
  const f = x * (STOPS.length - 1);
  const i = Math.min(STOPS.length - 2, Math.floor(f));
  const u = f - i;
  const a = STOPS[i];
  const b = STOPS[i + 1];
  return `#${hex(a[0] + (b[0] - a[0]) * u)}${hex(a[1] + (b[1] - a[1]) * u)}${hex(a[2] + (b[2] - a[2]) * u)}`;
}

/** Colour of generation g in a run whose last generation is maxGen (maxGen 0 -> the middle of the ramp). */
export function genColor(g: number, maxGen: number): string {
  return rampColor(maxGen > 0 ? g / maxGen : 0.5);
}

/** Categorical colours for compared runs (colour-blind safe Okabe-Ito subset, all >= 3:1 on white). */
export const RUN_COLORS: readonly string[] = ['#0072b2', '#d55e00', '#009e73', '#cc79a7', '#56402a', '#6a3d9a'];

export function runColor(i: number): string {
  return RUN_COLORS[((i % RUN_COLORS.length) + RUN_COLORS.length) % RUN_COLORS.length];
}

/** Outline of every mark (>= 3:1 on white and on the lightest ramp colour). */
export const MARK_OUTLINE = '#14231c';
export const FRONT_COLOR = '#a8200f';
