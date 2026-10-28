/**
 * Performance probe (real Chromium, SYNTHETIC mocked API): the Monitor with 600 evaluated points.
 * Measures render time, long main-thread tasks during interaction, keyboard open latency and the FPS toggle, asserts
 * generous budgets and writes the measurements to evidence/perf_600_points_<project>.json (a measurement record, not a
 * benchmark of the server).
 */
import { expect, test } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { connectAndLoad, installMock, openScreen, stateOf } from './mock';
import { cand } from '../tests/fixtures';

const N = 600;

test(`${N} points: renders, stays interactive and opens a dialog quickly`, async ({ page }, info) => {
  const extra = Array.from({ length: N }, (_, i) =>
    cand({
      id: `synthetic-ev-perf-${i}`,
      candidate_id: `synthetic-cand-perf-${i}`,
      name: `perf-${i}`,
      generation: i % 12,
      arm: i % 2 === 0 ? 'GA' : 'IndependentRandom',
      ap: 0.05 + ((i * 37) % 400) / 1000,
      recall: 0.1 + ((i * 53) % 500) / 1000,
      latency_ms: 25 + ((i * 91) % 1500) / 10,
      size_mb: 8 + ((i * 17) % 400) / 10,
    }),
  );
  await installMock(page, stateOf({ extraCandidates: extra }));
  await page.addInitScript(() => {
    (window as unknown as { __long: number[] }).__long = [];
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) (window as unknown as { __long: number[] }).__long.push(e.duration);
    }).observe({ entryTypes: ['longtask'] });
  });
  const t0 = Date.now();
  await connectAndLoad(page);
  await openScreen(page, 'monitor');
  await expect(page.getByTestId('chart-fitness')).toBeVisible();
  const countPoints = (): Promise<number> => page.getByTestId('chart-fitness').getByTestId('chart-point').count();
  await expect.poll(countPoints, { timeout: 15_000 }).toBeGreaterThanOrEqual(N * 0.5); // cohort guard may hide other cohorts
  const renderMs = Date.now() - t0;
  const points = await countPoints();

  // FPS toggle re-renders both charts with the full point set
  const toggle = page.getByTestId('x-fps');
  let toggleMs = -1;
  if (await toggle.count()) {
    const s = Date.now();
    await toggle.check();
    await expect(page.getByTestId('chart-fitness')).toContainText(/FPS/);
    toggleMs = Date.now() - s;
  }

  // keyboard: focus a point and open its dialog
  const first = page.getByTestId('chart-fitness').getByTestId('chart-point').nth(Math.floor(points / 2));
  await first.focus();
  const s = Date.now();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  const dialogMs = Date.now() - s;
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();

  // 30 consecutive Tab presses across chart points (focus handling must not stall)
  const s2 = Date.now();
  for (let i = 0; i < 30; i++) await page.keyboard.press('Tab');
  const tabMs = Date.now() - s2;

  const long = await page.evaluate(() => (window as unknown as { __long: number[] }).__long);
  const rows = await page.getByTestId('points-table').locator('tbody tr').count().catch(() => -1);
  const record = { kind: 'perf_600_points', evidence_type: 'browser (mock API, synthetic points)', project: info.project.name, points_requested: N, points_drawn_fitness_chart: points, table_rows: rows, render_ms: renderMs, fps_toggle_ms: toggleMs, dialog_open_ms: dialogMs, tab_30_ms: tabMs, long_tasks_ms: long, max_long_task_ms: long.length ? Math.max(...long) : 0 };
  writeFileSync(`evidence/perf_600_points_${info.project.name}.json`, JSON.stringify(record, null, 1));
  console.log(JSON.stringify(record));
  expect(renderMs, 'time to a rendered Monitor with the points').toBeLessThan(8000);
  expect(dialogMs, 'time from Enter to an open dialog').toBeLessThan(1500);
  expect(tabMs / 30, 'average time per Tab press').toBeLessThan(150);
  expect(record.max_long_task_ms, 'no main-thread task above 1.5 s').toBeLessThan(1500);
  if (toggleMs >= 0) expect(toggleMs).toBeLessThan(3000);
});
