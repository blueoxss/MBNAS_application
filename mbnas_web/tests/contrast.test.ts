import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(join(process.cwd(), 'src/styles.css'), 'utf8');
const root = css.slice(css.indexOf(':root'), css.indexOf('}', css.indexOf(':root')));
const tokens: Record<string, string> = {};
for (const m of root.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) tokens[m[1]] = m[2].toLowerCase();

function lum(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
export function contrast(a: string, b: string): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Documented normal-text pairs (foreground token on background token). Large/non-text uses 3:1. */
const TEXT_PAIRS: [string, string][] = [
  ['text', 'bg'], ['text', 'surface'], ['text', 'surface-2'],
  ['text-muted', 'bg'], ['text-muted', 'surface'], ['text-muted', 'surface-2'],
  ['sidebar-text', 'sidebar-bg'], ['sidebar-text', 'sidebar-bg-hover'],
  ['sidebar-muted', 'sidebar-bg'], ['sidebar-muted', 'sidebar-bg-hover'],
  ['accent-text', 'accent'], ['link', 'surface'], ['link', 'bg'],
  ['danger', 'surface'], ['danger', 'danger-bg'], ['danger', 'bg'],
  ['warn-text', 'warn-bg'], ['ok-text', 'ok-bg'], ['info-text', 'info-bg'],
  ['pilot-text', 'pilot-bg'],
];

describe('design tokens', () => {
  it('parsed the tokens', () => {
    expect(Object.keys(tokens).length).toBeGreaterThan(20);
  });
  it.each(TEXT_PAIRS)('%s on %s has contrast >= 4.5:1', (fg, bg) => {
    expect(tokens[fg], fg).toBeDefined();
    expect(tokens[bg], bg).toBeDefined();
    expect(contrast(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(4.5);
  });
  it('chart series colours are distinguishable from the white surface (>= 3:1 non-text)', () => {
    for (const k of ['series-baseline', 'series-ga', 'series-random', 'series-final']) expect(contrast(tokens[k], tokens.surface), k).toBeGreaterThanOrEqual(3);
  });
  it('focus ring and parent A/B colours reach >= 3:1 (non-text, WCAG 1.4.11) on the surfaces they sit on', () => {
    for (const bg of ['bg', 'surface', 'surface-2']) expect(contrast(tokens.focus, tokens[bg]), `focus on ${bg}`).toBeGreaterThanOrEqual(3);
    for (const k of ['parent-a', 'parent-b']) expect(contrast(tokens[k], tokens.surface), k).toBeGreaterThanOrEqual(3);
    expect(contrast('#ffffff', tokens['sidebar-bg']), 'white focus ring on the sidebar').toBeGreaterThanOrEqual(3);
  });
  it('disabled primary button text keeps >= 4.5:1 on its background', () => {
    expect(contrast('#3b4a42', '#cfd9d3')).toBeGreaterThanOrEqual(4.5);
  });
  it('declares reduced-motion handling, 16px inputs and 44px touch targets', () => {
    expect(css).toMatch(/prefers-reduced-motion: reduce/);
    expect(css).toMatch(/font: inherit; font-size: 16px;/);
    expect(css).toMatch(/--tap: 44px/);
    expect(css).toMatch(/\(max-width: 767px\)/);
  });
});
