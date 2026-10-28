/**
 * Final Retrain panel (contract v1.2.0) in real Chromium against the production build with the SYNTHETIC mocked API.
 * Screenshots: evidence/final_<project>_<state>.png
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';
import { connectAndLoad, installMock, openScreen, stateOf, type MockState, captureEvidence } from './mock';
import { FINAL_RUN_ID, fixtureFinalSnapshot, fixtureSnapshot, fixtureWorkers, RUN_ID } from '../tests/fixtures';

const shot = async (page: Page, name: string): Promise<void> => {
  const h = await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio);
  await captureEvidence(page, `evidence/final_${test.info().project.name}_${name}.png`, h < 12000);
};

const json = (route: Route, status: number, body: unknown): Promise<void> => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

/** Answers POST final-retrain and the child run snapshot; `reply` decides the POST answer. */
function finalMock(reply: (n: number) => { status: number; body: unknown }, extra?: (route: Route, path: string) => Promise<boolean>): MockState {
  let n = 0;
  const st = stateOf({ runStatus: 'COMPLETED' });
  st.custom = async (route, path) => {
    if (extra && (await extra(route, path))) return true;
    if (path === `/api/v1/runs/${RUN_ID}/final-retrain` && route.request().method() === 'POST') {
      const r = reply(++n);
      await json(route, r.status, r.body);
      return true;
    }
    if (path === `/api/v1/runs/${FINAL_RUN_ID}/snapshot`) {
      await json(route, 200, fixtureFinalSnapshot());
      return true;
    }
    if (path === `/api/v1/runs/${FINAL_RUN_ID}/events`) {
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
      return true;
    }
    return false;
  };
  return st;
}

const ok = { status: 200, body: { run_id: FINAL_RUN_ID, status: 'QUEUED', parent_run_id: RUN_ID, finalist_count: 2 } };

test.describe('Final Retrain panel', () => {
  test('is not offered for a RUNNING search run', async ({ page }) => {
    await installMock(page, stateOf({ runStatus: 'RUNNING' }));
    await connectAndLoad(page);
    await expect(page.getByTestId('final-retrain-panel')).toHaveCount(0);
  });

  test('COMPLETED search run shows the panel with the resolved top-k default and a disabled start until a budget is given', async ({ page }) => {
    await installMock(page, finalMock(() => ok));
    await connectAndLoad(page);
    const panel = page.getByTestId('final-retrain-panel');
    await expect(panel).toBeVisible();
    await expect(page.getByTestId('final-topk')).toHaveValue('2');
    await expect(page.getByTestId('final-run-btn')).toBeDisabled();
    await page.getByTestId('final-budget').fill('0');
    await expect(page.getByTestId('final-run-btn')).toBeDisabled();
    await expect(page.locator('#final-retrain')).toContainText('greater than 0');
    await page.getByTestId('final-budget').fill('0.5');
    await expect(page.getByTestId('final-run-btn')).toBeEnabled();
    await page.locator('#final-retrain').scrollIntoViewIfNeeded();
    await shot(page, 'panel');
    const axe = await new AxeBuilder({ page }).include('#final-retrain').analyze();
    expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });

  test('409 NO_FINALIST is a clear message and creates no child run', async ({ page }) => {
    const st = finalMock(() => ({ status: 409, body: { code: 'NO_FINALIST', message: 'no eligible L2 finalist' } }));
    await installMock(page, st);
    await connectAndLoad(page);
    await page.getByTestId('final-budget').fill('0.5');
    await page.getByTestId('final-run-btn').click();
    const err = page.getByTestId('final-error');
    await expect(err).toContainText('L2 finalist 없음 (하위 rung 대체 없음)');
    await expect(err).toHaveAttribute('data-error-kind', 'NO_FINALIST');
    await expect(page.getByTestId('final-child-id')).toHaveCount(0);
    const post = st.seen.find((s) => s.method === 'POST' && s.url.endsWith('/final-retrain'))!;
    expect(post.idem).toBeTruthy();
    expect((post.body as { idempotency_key: string }).idempotency_key).toBe(post.idem);
    await page.locator('#final-retrain').scrollIntoViewIfNeeded();
    await shot(page, 'no-finalist');
  });

  for (const code of ['INVALID_STATE', 'BUDGET_INVALID'] as const) {
    test(`409 ${code} is shown distinctly`, async ({ page }) => {
      await installMock(page, finalMock(() => ({ status: 409, body: { code, message: `server: ${code}` } })));
      await connectAndLoad(page);
      await page.getByTestId('final-budget').fill('0.5');
      await page.getByTestId('final-run-btn').click();
      await expect(page.getByTestId('final-error')).toHaveAttribute('data-error-kind', code);
      await expect(page.getByTestId('final-error')).not.toContainText('L2 finalist 없음');
      await shot(page, code.toLowerCase());
    });
  }

  test('a 503 keeps the Idempotency-Key for the retry', async ({ page }) => {
    const st = finalMock((n) => (n === 1 ? { status: 503, body: { code: 'BUSY', message: 'later' } } : ok));
    await installMock(page, st);
    await connectAndLoad(page);
    await page.getByTestId('final-budget').fill('0.5');
    await page.getByTestId('final-run-btn').click();
    await expect(page.getByTestId('final-run-btn')).toContainText('Retry Final Retrain (same Idempotency-Key)');
    await page.getByTestId('final-run-btn').click();
    await expect(page.getByTestId('final-child-id')).toContainText(FINAL_RUN_ID);
    const keys = st.seen.filter((s) => s.method === 'POST' && s.url.endsWith('/final-retrain')).map((s) => s.idem);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });

  test('success shows child run id and finalist_count; Load child run shows kind, parent link and finalists; parent can be loaded back', async ({ page }) => {
    const st = finalMock(() => ok);
    await installMock(page, st);
    await connectAndLoad(page);
    await page.getByTestId('final-topk').fill('3');
    await page.getByTestId('final-budget').fill('0.5');
    await page.getByTestId('final-reason').fill('smoke final');
    await page.getByTestId('final-run-btn').click();
    await expect(page.getByTestId('final-child-id')).toContainText(FINAL_RUN_ID);
    await expect(page.getByTestId('final-finalist-count')).toHaveText('2');
    const post = st.seen.find((s) => s.method === 'POST' && s.url.endsWith('/final-retrain'))!;
    expect(post.body).toMatchObject({ budget_gpu_hours: 0.5, top_k_per_arm_per_seed: 3, reason: 'smoke final' });
    await page.locator('#final-retrain').scrollIntoViewIfNeeded();
    await shot(page, 'created');

    await page.getByTestId('final-load-child').click();
    await expect(page.getByTestId('run-kind-row')).toContainText('kind: FINAL_RETRAIN');
    await expect(page.getByTestId('parent-run-link')).toContainText(RUN_ID);
    const table = page.getByTestId('finalists-table');
    await expect(table).toBeVisible();
    await expect(table).toContainText('synthetic-ev-ga-g1-2');
    await expect(page.getByTestId('final-retrain-panel')).toHaveCount(0);
    await page.locator('#finalists').scrollIntoViewIfNeeded();
    await shot(page, 'child-run-server');

    await openScreen(page, 'monitor');
    await expect(page.getByTestId('final-cohort-note')).toBeVisible();
    await expect(page.getByTestId('cohort-select')).toContainText('FINAL');
    await shot(page, 'child-run-monitor');

    await openScreen(page, 'history');
    await expect(page.getByTestId('related-runs')).toContainText(RUN_ID);
    await shot(page, 'child-run-history');

    await openScreen(page, 'server');
    await page.getByTestId('load-parent-btn').click();
    await expect(page.getByTestId('final-retrain-panel')).toBeVisible();
  });
});

test.describe('Workers status vocabulary', () => {
  test('idle, busy, offline and lost are text badges with a legend', async ({ page }) => {
    const workers = ['idle', 'busy', 'offline', 'lost'].map((status, i) => ({ ...fixtureWorkers()[0], id: `synthetic-worker-${i}`, status, candidate_id: status === 'busy' ? 'synthetic-ga-g2-0' : null }));
    const st = stateOf();
    st.custom = async (route, path) => {
      if (path !== `/api/v1/runs/${RUN_ID}/snapshot`) return false;
      await json(route, 200, { ...fixtureSnapshot('RUNNING', 5), workers });
      return true;
    };
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    const table = page.getByTestId('worker-table');
    for (const s of ['idle', 'busy', 'offline', 'lost']) await expect(table).toContainText(s);
    await expect(page.getByTestId('worker-status-legend')).toContainText('lost: heartbeat older than the lease TTL');
    await table.scrollIntoViewIfNeeded();
    await shot(page, 'workers');
  });
});

test.describe('Settings: crossover rate and postprocess', () => {
  test('fields are visible with the operating-point help text', async ({ page }) => {
    await installMock(page, stateOf());
    await page.goto('/#/settings');
    await expect(page.getByTestId('crossover-rate')).toBeVisible();
    await expect(page.getByTestId('pp-conf')).toBeVisible();
    await expect(page.getByText('latency operating point; AP evaluation floor stays 0.001').first()).toBeVisible();
    await page.getByTestId('crossover-rate').scrollIntoViewIfNeeded();
    await shot(page, 'settings');
  });
});
