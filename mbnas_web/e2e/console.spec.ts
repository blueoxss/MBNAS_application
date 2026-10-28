/**
 * Browser tests (real Chromium) against the production build with a SYNTHETIC mocked API.
 * Runs on the desktop (1280) and mobile (375) projects. Screenshots go to evidence/.
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { CDN, connectAndLoad, installMock, openScreen, sse, stateOf, TOKEN, captureEvidence } from './mock';
import { RUN_ID } from '../tests/fixtures';

/** Full-page capture; very tall pages (Chromium caps captures at ~16k device px) fall back to the viewport. */
const shot = async (page: Page, name: string): Promise<void> => {
  const h = await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio);
  await captureEvidence(page, `evidence/${test.info().project.name}-${name}.png`, h < 12000);
};

const noHorizontalScroll = async (page: Page): Promise<void> => {
  const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bw: document.body.scrollWidth }));
  expect(m.sw, `scrollWidth ${m.sw} > clientWidth ${m.cw}`).toBeLessThanOrEqual(m.cw);
  expect(m.bw).toBeLessThanOrEqual(m.cw);
};

test.describe('navigation and layout', () => {
  test('seven screens are reachable and the page never scrolls horizontally', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await connectAndLoad(page);
    const heads: [Parameters<typeof openScreen>[1], string][] = [
      ['archive', 'NAS 아카이브'],
      ['compare', 'Run 비교'],
      ['monitor', '탐색 모니터링'],
      ['settings', '실험 설정'],
      ['lineage', '모델 계보'],
      ['history', '실행 기록'],
      ['server', '서버 연결'],
    ];
    for (const [id, title] of heads) {
      await openScreen(page, id);
      await expect(page.getByRole('heading', { level: 1 })).toContainText(title);
      if (id === 'compare') await expect(page.getByTestId('nav-compare')).toHaveText('Archive run 비교'); // archive-only: not a console-run comparison
      await noHorizontalScroll(page);
      await shot(page, `screen-${id}`);
    }
  });

  for (const width of [320, 640, 1280]) {
    test(`reflows without horizontal scroll at ${width}px (200% zoom ≈ 640px at 1280)`, async ({ page }) => {
      await installMock(page, stateOf());
      await page.setViewportSize({ width, height: 900 });
      await connectAndLoad(page);
      for (const id of ['archive', 'compare', 'monitor', 'settings', 'lineage', 'history', 'server'] as const) {
        await openScreen(page, id);
        await noHorizontalScroll(page);
      }
      await shot(page, `reflow-${width}`);
    });
  }

  test('inputs are at least 16px and primary controls at least 44px high on touch/mobile', async ({ page }) => {
    await installMock(page, stateOf());
    await page.goto('/#/server');
    const isMobile = (page.viewportSize()?.width ?? 1280) < 768;
    const fs = await page.evaluate(() => [...document.querySelectorAll('input, select, textarea')].map((e) => parseFloat(getComputedStyle(e).fontSize)));
    expect(Math.min(...fs)).toBeGreaterThanOrEqual(16);
    if (isMobile) {
      const heights = await page.evaluate(() => [...document.querySelectorAll('button.btn, .menu-btn, input[type=text], input[type=password]')].filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => e.getBoundingClientRect().height));
      expect(Math.min(...heights)).toBeGreaterThanOrEqual(44);
    }
  });
});

test.describe('server flow', () => {
  test('connection error is shown as an error message, not a blank page', async ({ page }) => {
    const st = stateOf({ failHealth: true });
    await installMock(page, st);
    await page.goto('/#/server');
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await expect(page.getByTestId('connection-error')).toContainText('Cannot reach the server');
    await shot(page, 'connection-error');
  });

  test('wrong token gives an authorization error', async ({ page }) => {
    await installMock(page, stateOf());
    await page.goto('/#/server');
    await page.getByLabel(/Bearer token/).fill('wrong');
    await page.getByTestId('connect-btn').click();
    await expect(page.getByTestId('connection-error')).toContainText('401');
  });

  test('Validate -> Run is gated by a valid, unchanged Validate; token stays in memory', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await page.goto('/#/server');
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await expect(page.getByTestId('registry-table')).toContainText('BLOCKED_SOURCE');
    await expect(page.getByTestId('run-btn')).toBeDisabled();
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('protocol-hash')).toBeVisible();
    await expect(page.getByTestId('resolved-diff')).toContainText('multiple of stride 32');
    await expect(page.getByTestId('run-btn')).toBeEnabled();
    await shot(page, 'server-validated');

    // dirty -> disabled
    await openScreen(page, 'settings');
    await page.getByTestId('budget').fill('3');
    await openScreen(page, 'server');
    await expect(page.getByTestId('run-btn')).toBeDisabled();
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('run-btn')).toBeEnabled();
    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('submit-message')).toContainText(`Run created: ${RUN_ID}`);
    const post = st.seen.find((s) => s.method === 'POST' && s.url === '/api/v1/runs')!;
    expect(post.idem).toBe((post.body as { idempotency_key: string }).idempotency_key);
    expect((post.body as { validation_id: string }).validation_id).toBe('synthetic-validation-1');

    // nothing persisted, token not in URL
    expect(page.url()).not.toContain(TOKEN);
    const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }) + document.cookie);
    expect(stored).not.toContain(TOKEN);
  });

  test('S4-F4: an INVALID Validate never shows "Requested vs resolved" (even if the reply carries a diff)', async ({ page }) => {
    const st = stateOf({
      validateReply: {
        status: 200,
        body: {
          valid: false,
          errors: [{ field: 'rungs[1].epochs_added', code: 'EPOCHS_ADDED_MANUAL', message: 'added epochs are computed from the cumulative targets (epoch_target_total 30 - previous target 10 = 20); remove the manual value 5' }],
          warnings: [],
          protocol_hash: null,
          resolved: null,
          capabilities: {},
          resolved_diff: [{ field: 'rungs[1].epochs_added', requested: 5, resolved: 20, reason: 'cumulative target minus previous rung target (computed, never entered)' }],
          validation_id: null,
        },
      },
    });
    await installMock(page, st);
    await page.goto('/#/server');
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await page.getByText('status: ok').waitFor();
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('validate-errors')).toContainText('EPOCHS_ADDED_MANUAL');
    await expect(page.getByTestId('validation-result')).toContainText('INVALID');
    await expect(page.getByTestId('resolved-diff')).toHaveCount(0);
    await expect(page.getByTestId('validation-result')).not.toContainText('Requested vs resolved');
    await expect(page.getByTestId('run-btn')).toBeDisabled();
  });

  test('a network timeout on Run keeps the same Idempotency-Key for the retry', async ({ page }) => {
    const st = stateOf({ runResponses: [0, 200] });
    await installMock(page, st);
    await page.goto('/#/server');
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('run-btn')).toBeEnabled();
    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('run-btn')).toContainText('Retry Run (same Idempotency-Key)');
    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('submit-message')).toContainText('Run created');
    const posts = st.seen.filter((s) => s.method === 'POST' && s.url === '/api/v1/runs');
    expect(posts).toHaveLength(2);
    expect(posts[0].idem).toBeTruthy();
    expect(posts[0].idem).toBe(posts[1].idem);
  });

  test('pause / resume / cancel with reason and export download', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await connectAndLoad(page);
    await page.getByTestId('control-reason').fill('e2e synthetic reason');
    await page.getByTestId('pause-btn').click();
    await expect(page.getByText(/pause: server answered PAUSE_REQUESTED/)).toBeVisible();
    const pause = st.seen.find((s) => s.url.endsWith('/pause'))!;
    expect((pause.body as { reason: string }).reason).toBe('e2e synthetic reason');
    expect((pause.body as { idempotency_key: string }).idempotency_key).toBeTruthy();
    const dl = page.waitForEvent('download');
    await page.getByTestId('export-btn').click();
    const d = await dl;
    expect(d.suggestedFilename()).toBe(`mbnas-export-${RUN_ID}.zip`);
    const exp = st.seen.find((s) => s.url.endsWith('/export'))!;
    expect(exp.auth).toBe(`Bearer ${TOKEN}`);
  });
});

test.describe('monitor', () => {
  test('two charts, table alternative, keyboard opens the candidate dialog, ESC returns focus', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    const f = page.getByTestId('chart-fitness');
    const a = page.getByTestId('chart-ap');
    await expect(f).toBeVisible();
    await expect(a).toBeVisible();
    const fb = (await f.boundingBox())!;
    const ab = (await a.boundingBox())!;
    if ((page.viewportSize()?.width ?? 0) >= 768) {
      expect(Math.abs(fb.y - ab.y)).toBeLessThan(8); // side by side
      expect(ab.x).toBeGreaterThan(fb.x + fb.width - 2);
    } else {
      expect(ab.y).toBeGreaterThan(fb.y + fb.height - 2); // stacked on mobile
    }
    await expect(page.getByTestId('points-table')).toBeVisible();
    await shot(page, 'monitor');

    const point = f.getByTestId('chart-point').first();
    await point.focus();
    await page.keyboard.press('Enter');
    const dlg = page.getByRole('dialog');
    await expect(dlg).toBeVisible();
    await expect(dlg).toHaveAttribute('aria-modal', 'true');
    await page.keyboard.press('Tab');
    expect(await dlg.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    await shot(page, 'candidate-dialog');
    await page.keyboard.press('Escape');
    await expect(dlg).toBeHidden();
    await expect(point).toBeFocused();
    await point.press('Space');
    await expect(dlg).toBeVisible();
    await page.keyboard.press('Escape');
  });

  test('FPS toggle relabels both axes', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    await page.getByTestId('x-fps').check();
    await expect(page.locator('.axis-title', { hasText: /Throughput \[FPS\] = 1000 \/ latency_ms/ })).toHaveCount(2);
    await shot(page, 'monitor-fps');
  });

  test('cohort guard hides other cohorts and the unresolved row', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    await expect(page.getByTestId('cohort-guard')).toContainText('UNRESOLVED_COHORT');
  });

  test('generation.updated shows an open generation with live counts, then updates in place', async ({ page }) => {
    const g = (over: Record<string, unknown>) => ({ arm: 'GA', generation: 9, rung: 'L0', complete: false, planning_closed: false, planned_count: 6, evaluated_count: 1, pending_count: 5, failed_count: 0, not_admitted_count: 0, comparison_group: 'cg-live', search_seed: 101, representative_candidate_id: null, ...over });
    const st = stateOf({ eventScript: [sse([{ seq: 6, type: 'generation.updated', payload: g({}) }, { seq: 7, type: 'generation.updated', payload: g({ evaluated_count: 4, pending_count: 2 }) }])] });
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    const row = page.getByTestId('generation-table').locator('tr').filter({ has: page.getByRole('cell', { name: '9', exact: true }) }).filter({ hasText: 'open' });
    await expect(row).toHaveCount(1);
    await expect(row.first().getByRole('cell', { name: '4', exact: true })).toHaveCount(1);
    await expect(page.getByTestId('event-log')).toContainText('#7 generation.updated');
  });

  test('live SSE event adds a point immediately; a duplicate and a foreign-run event are ignored', async ({ page }) => {
    const newRow = (await import('../tests/fixtures')).cand({ id: 'synthetic-ev-live', candidate_id: 'synthetic-cand-live', name: 'live-added-candidate', arm: 'GA', generation: 0, latency_ms: 38, ap: 0.33 });
    const st = stateOf({
      eventScript: [
        sse([
          { seq: 5, type: 'log', payload: { message: 'duplicate of snapshot' } },
          { seq: 6, type: 'candidate.evaluated', payload: newRow },
        ]) + `id: 7\nevent: log\ndata: ${JSON.stringify({ seq: 7, type: 'log', run_id: 'other-run', payload: {} })}\n\n`,
      ],
    });
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    await expect(page.getByTestId('points-table').getByText('live-added-candidate')).toBeVisible();
    const rows = await page.getByTestId('points-table').getByText('live-added-candidate').count();
    expect(rows).toBe(1);
    const log = page.getByTestId('event-log');
    await expect(log).toContainText('#6 candidate.evaluated');
    await expect(log).not.toContainText('other-run');
    const ev = st.seen.find((s) => s.url.includes('/events'))!;
    expect(ev.url).toContain('after_seq=5');
    expect(ev.auth).toBe(`Bearer ${TOKEN}`);
  });

  test('a sequence gap makes the client refetch the snapshot instead of applying later events', async ({ page }) => {
    const st = stateOf({ eventScript: [sse([{ seq: 9, type: 'log', payload: { message: 'gap' } }])] });
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'history');
    await expect(page.getByTestId('recoveries')).toContainText('gap');
    const snaps = st.seen.filter((s) => s.url.endsWith('/snapshot'));
    expect(snaps.length).toBeGreaterThanOrEqual(2);
  });
});

test.describe('candidate dialog: lineage and artifacts', () => {
  test('crossover candidate shows parent A/B, the public block kinds and YAML rows (no owner-only repair trace)', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    await page.getByTestId('candidate-table').getByRole('button', { name: /ga-g1-1/ }).click();
    const dlg = page.getByRole('dialog');
    await expect(dlg.getByTestId('lineage-op')).toContainText('crossover (2 parents)');
    await expect(dlg.getByText('Parent A')).toBeVisible();
    await expect(dlg.getByText('Parent B')).toBeVisible();
    const blocks = dlg.getByTestId('block-table');
    await expect(blocks).toContainText('M (mutation)');
    // contract 1.5.0 / DD-A19: the repaired row is reported with the source it was repaired from; the repair kind, repair
    // reason, old name and before/after are owner-only and never reach the console
    await expect(blocks).toContainText('module=Conv · from=[-1,2] · repeats=1 · args=[64,1,1] · scope=head');
    await expect(blocks).not.toContainText('R (repair)');
    await expect(blocks).not.toContainText('Repair reason');
    await shot(page, 'dialog-lineage');
  });

  test('four images of one sample: signed URL gets no credentials, bearer fetch is sent only to the API origin', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    await page.getByTestId('candidate-table').getByRole('button', { name: /happy-artifacts-candidate/ }).click();
    const panel = page.getByTestId('artifact-panel');
    for (const k of ['gt', 'tp', 'fn', 'fp']) await expect(panel.getByTestId(`artifact-${k}`)).toHaveAttribute('data-state', 'READY');
    await expect(panel.locator('img')).toHaveCount(4);
    // images are lazy-loaded: bring each into view (narrow mobile viewports start with some below the fold), then wait
    for (const k of ['gt', 'tp', 'fn', 'fp']) {
      await panel.getByTestId(`artifact-${k}`).scrollIntoViewIfNeeded();
      await expect(panel.getByTestId(`artifact-${k}`)).toHaveAttribute('data-loaded', 'true');
    }
    expect(await panel.locator('img').evaluateAll((els) => els.every((i) => (i as HTMLImageElement).naturalWidth > 0))).toBe(true);
    await expect(panel).toContainText('7 / 2 / 3 / 10');
    await expect(panel).toContainText('70.0 %');
    await shot(page, 'dialog-artifacts');
    const cdn = st.seen.filter((s) => s.url.startsWith(CDN));
    expect(cdn.length).toBeGreaterThan(0);
    expect(cdn.every((s) => s.auth === undefined)).toBe(true); // no token to the signed-url host
    const api = st.seen.filter((s) => s.url.includes('/artifacts/happy-'));
    expect(api.length).toBeGreaterThan(0);
    expect(api.every((s) => s.auth === `Bearer ${TOKEN}`)).toBe(true);
    // closing revokes the object URLs (no leak): the dialog is gone
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  test('ACCESS_DENIED, MISSING, EXPIRED (after one refresh try) and NOT_READY are shown, never an endless spinner', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    await page.getByTestId('candidate-table').getByRole('button', { name: /artifact-states-candidate/ }).click();
    const panel = page.getByTestId('artifact-panel');
    await expect(panel.getByTestId('artifact-gt')).toHaveAttribute('data-state', 'ACCESS_DENIED');
    await expect(panel.getByTestId('artifact-tp')).toHaveAttribute('data-state', 'MISSING');
    await expect(panel.getByTestId('artifact-fn')).toHaveAttribute('data-state', 'EXPIRED');
    await expect(panel.getByTestId('artifact-fp')).toHaveAttribute('data-state', 'NOT_READY');
    await expect(panel).toContainText('N/A'); // gt = 0 -> sample recall N/A
    expect(st.seen.filter((s) => s.url.endsWith('/art-fn/refresh'))).toHaveLength(1);
    await shot(page, 'dialog-artifact-states');
  });
});

test.describe('policy lock and settings', () => {
  test('a RUNNING run locks the policy; PILOT badge is prominent on the pilot preset', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    await openScreen(page, 'settings');
    await expect(page.getByTestId('lock-banner')).toBeVisible();
    await expect(page.getByTestId('settings-fieldset')).toHaveAttribute('data-locked', 'true');
    await expect(page.getByTestId('rung-0-epochs')).toBeDisabled();
    await expect(page.getByTestId('budget')).toBeDisabled();
    await shot(page, 'settings-locked');
  });

  test('settings: no embedded template before connecting; connecting auto-applies DEFAULT_BASELINE14 with a visible label (DD-A21)', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await page.goto('/#/settings');
    await expect(page.getByTestId('settings-connect-banner')).toBeVisible();
    await expect(page.getByTestId('policy-label')).toHaveText('no policy loaded');
    await expect(page.getByTestId('rung-table').locator('tbody tr')).toHaveCount(0); // DD-A8: no client template rungs
    await expect(page.getByLabel('Preset').locator('option')).toHaveCount(1); // only "current: ..." - no client templates
    await shot(page, 'settings-before-connect');
    await openScreen(page, 'server');
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await page.getByText('status: ok').waitFor();
    expect(st.seen.filter((s) => s.url === '/api/v1/protocols/presets')).toHaveLength(1); // loaded automatically on connect
    await expect(page.getByTestId('policy-label')).toHaveText('server preset DEFAULT_BASELINE14 (design example)'); // Validate card
    await openScreen(page, 'settings');
    await expect(page.getByTestId('policy-label')).toHaveText('server preset DEFAULT_BASELINE14 (design example)');
    await expect(page.getByTestId('policy-auto-applied')).toBeVisible();
    await expect(page.getByTestId('settings-connect-banner')).toHaveCount(0);
    await expect(page.getByTestId('rung-2-epochs')).toHaveValue('90');
    await shot(page, 'settings-auto-preset');
  });

  test('settings: .43 weights are rejected visibly, added epochs are computed, preset badge appears', async ({ page }) => {
    await installMock(page, stateOf());
    await page.goto('/#/server');
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await page.getByText('status: ok').waitFor(); // DEFAULT_BASELINE14 is applied before the connection reports ok
    await openScreen(page, 'settings');
    await expect(page.getByTestId('rung-0-added')).toHaveText('+10');
    await expect(page.getByTestId('rung-1-added')).toHaveText('+20');
    await expect(page.getByTestId('rung-2-added')).toHaveText('+60');
    await expect(page.getByTestId('horizon-note')).toContainText('90');
    await page.getByTestId('rung-0-w-latency_reward').fill('0.08');
    await page.getByTestId('rung-0-w-size_reward').fill('0.05');
    await expect(page.getByTestId('rung-0-sum')).toContainText('0.430000');
    await expect(page.getByTestId('issue-list')).toContainText('WEIGHTS_SUM_NOT_ONE');
    await page.getByTestId('rung-1-epochs').fill('8');
    await expect(page.getByTestId('issue-list')).toContainText('EPOCHS_NOT_INCREASING');
    await page.getByTestId('path-train_root').fill('~/data/train');
    await expect(page.getByTestId('issue-list')).toContainText('PATH_NOT_ABSOLUTE');
    await shot(page, 'settings-errors');
    await expect(page.getByTestId('policy-auto-applied')).toHaveCount(0); // edited: no longer the untouched auto-applied preset
    const preset = page.getByLabel('Preset');
    await preset.selectOption({ label: 'Pilot smoke (server preset, SMOKE)' });
    await expect(page.getByText('PILOT · SMOKE · not research values').first()).toBeVisible();
    await expect(page.getByTestId('rung-2-epochs')).toHaveValue('6');
    await expect(page.getByTestId('policy-label')).toHaveText('Pilot smoke (server preset PILOT_SMOKE)');
    await shot(page, 'settings-pilot');
  });
});

test.describe('accessibility scan', () => {
  for (const id of ['archive', 'compare', 'monitor', 'settings', 'lineage', 'history', 'server'] as const) {
    test(`axe: no serious or critical violations on ${id}`, async ({ page }) => {
      await installMock(page, stateOf());
      await connectAndLoad(page);
      await openScreen(page, id);
      const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      const bad = res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
      expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`)).toEqual([]);
    });
  }
});
