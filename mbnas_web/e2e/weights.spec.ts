/**
 * DD-18 / V10: the fitness-weight round trip in a real Chromium against the SYNTHETIC mocked API.
 * Settings (edit a rung's weights) -> Validate request body -> Run request body -> the loaded run's candidate view shows
 * the weights the evaluation row says were applied (row.fitness_weights). The mock "server" resolves the protocol exactly
 * as requested, rejects a weight sum != 1 like the real contract (no auto-normalisation), and stamps its evaluation row
 * with the weights of the created run. All values are invented.
 */
import { expect, test, type Page } from '@playwright/test';
import { installMock, openScreen, stateOf, TOKEN, type MockState, captureEvidence } from './mock';
import { cand } from '../tests/fixtures';
import { fitnessOracle, type OracleAnchors } from '../tests/fitness_oracle';
import { getIn } from '../src/lib/policy';

const shot = async (page: Page, name: string): Promise<void> => {
  const h = await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio);
  await captureEvidence(page, `evidence/${test.info().project.name}-${name}.png`, h < 12000);
};

const EDIT = { ap: 0.5, recall: 0.3, latency_reward: 0.1, size_reward: 0.1 } as const;
const KEYS = ['ap', 'recall', 'latency_reward', 'size_reward'] as const;
type Weights = Record<(typeof KEYS)[number], number>;

/** Mock-server weight rule: every rung's weights sum to 1 (|sum-1| <= 1e-6), otherwise 422 WEIGHTS_SUM_NOT_ONE. */
function weightRule(st: MockState): void {
  st.custom = async (route, p) => {
    if (p !== '/api/v1/protocols/validate') return false;
    const protocol = (JSON.parse(route.request().postData() ?? '{}') as { protocol?: { rungs?: { id: string; fitness_weights: Record<string, number> }[] } }).protocol;
    const bad = (protocol?.rungs ?? []).filter((r) => Math.abs(Object.values(r.fitness_weights).reduce((a, b) => a + b, 0) - 1) > 1e-6);
    if (bad.length === 0) return false;
    await route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({ code: 'PROTOCOL_INVALID', message: 'synthetic: weights must sum to 1', errors: bad.map((r) => ({ field: `rungs.${r.id}.fitness_weights`, code: 'WEIGHTS_SUM_NOT_ONE', message: 'synthetic: weights must sum to 1' })) }),
    });
    return true;
  };
}

/**
 * The created run's evaluation row carries the weights of its first rung (the snapshot the candidate view shows). Its
 * fitness is filled by the TEST-ONLY oracle (tests/fitness_oracle.ts): the mock plays the server, which computes fitness;
 * the console only displays it (DD-A8).
 */
function stampEvaluationRow(st: MockState): void {
  st.snapshotPatch = (s) => {
    const w = getIn(st.runProtocol, 'rungs.0.fitness_weights') as Weights | undefined;
    if (!w) return s;
    const metrics = { ap: 0.31, recall: 0.42, latency_ms: 60, size_mb: 30 };
    const anchors = { latency_bounds_ms: getIn(st.runProtocol, 'fitness.latency_bounds_ms'), size_bounds_MB: getIn(st.runProtocol, 'fitness.size_bounds_MB') } as OracleAnchors; // the run's own anchors
    const fit = fitnessOracle(metrics, w, anchors);
    stampedFitness = fit.fitness;
    const row = cand({ id: 'synthetic-ev-weighted', candidate_id: 'synthetic-cand-weighted', name: 'weighted-candidate', generation: 0, rung: 'L0', ...metrics, fitness: fit.fitness, fitness_weights: { ...w } });
    return { ...s, candidates: [...s.candidates, row] };
  };
}

/** fitness stamped on the synthetic evaluation row by stampEvaluationRow (what the candidate view must show) */
let stampedFitness: number | null = null;

async function setWeights(page: Page, w: Weights, rung = 0): Promise<void> {
  for (const k of KEYS) await page.getByTestId(`rung-${rung}-w-${k}`).fill(String(w[k]));
}

test.describe('fitness weights round trip (DD-18)', () => {
  test('edited rung weights are in the validate body, the run body and the candidate view of the created run', async ({ page }) => {
    const st = stateOf({ runStatus: 'COMPLETED', captureRunProtocol: true });
    weightRule(st);
    stampEvaluationRow(st);
    await installMock(page, st);
    await page.goto('/#/server');
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await page.getByText('status: ok').waitFor();

    await openScreen(page, 'settings');
    const defaults = await Promise.all(KEYS.map((k) => page.getByTestId(`rung-1-w-${k}`).inputValue()));
    await setWeights(page, EDIT);
    await expect(page.getByTestId('rung-0-sum')).toContainText('✔');
    await expect(page.getByTestId('rung-0-sum')).not.toContainText('must be 1');
    await shot(page, 'weights-edited');

    await openScreen(page, 'server');
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('run-btn')).toBeEnabled();
    const validates = st.seen.filter((s) => s.method === 'POST' && s.url === '/api/v1/protocols/validate');
    expect(validates).toHaveLength(1);
    const vp = (validates[0].body as { protocol: Record<string, unknown> }).protocol;
    expect(getIn(vp, 'rungs.0.fitness_weights')).toEqual(EDIT); // exactly the typed weights, no extra or renamed keys
    expect(Object.values(getIn(vp, 'rungs.0.fitness_weights') as Weights).reduce((a, b) => a + b, 0)).toBeCloseTo(1, 9);
    // the untouched rungs keep their own weights
    expect(KEYS.map((k) => String((getIn(vp, 'rungs.1.fitness_weights') as Weights)[k]))).toEqual(defaults);

    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('submit-message')).toContainText('Run created');
    const runs = st.seen.filter((s) => s.method === 'POST' && s.url === '/api/v1/runs');
    expect(runs).toHaveLength(1);
    const rp = (runs[0].body as { protocol: Record<string, unknown> }).protocol;
    expect(getIn(rp, 'rungs.0.fitness_weights')).toEqual(EDIT);
    expect(getIn(rp, 'rungs')).toEqual(getIn(vp, 'rungs')); // the run carries the validated rungs unchanged

    // candidate view of the created run: the applied snapshot weights come from the evaluation row
    await openScreen(page, 'monitor');
    await page.getByTestId('candidate-table').getByRole('button', { name: /weighted-candidate/ }).click();
    const view = page.getByTestId('candidate-view');
    await expect(view).toContainText('Fitness weights applied');
    await expect(view).toContainText('ap=0.5, recall=0.3, latency_reward=0.1, size_reward=0.1');
    // the server's value is shown as is (4 decimals); the console has no client recompute row any more (DD-A8 / WP-6 R1)
    expect(stampedFitness).not.toBeNull();
    await expect(view.locator('dt', { hasText: /^Fitness \(server\)$/ }).locator('xpath=following-sibling::dd[1]')).toHaveText((stampedFitness as number).toFixed(4));
    await expect(view).not.toContainText('Client recompute');
    await shot(page, 'weights-candidate');
  });

  test('a weight sum other than 1 shows "must be 1", is rejected by Validate and never reaches POST /runs', async ({ page }) => {
    const st = stateOf({ runStatus: 'COMPLETED', captureRunProtocol: true });
    weightRule(st);
    await installMock(page, st);
    await page.goto('/#/server');
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await page.getByText('status: ok').waitFor();

    await openScreen(page, 'settings');
    await page.getByTestId('rung-0-w-ap').fill('0.6'); // 0.6 + 0.1 + 0.4 + 0.3 = 1.4
    const sum = page.getByTestId('rung-0-sum');
    await expect(sum).toContainText('1.400000');
    await expect(sum).toContainText('must be 1');
    await expect(page.getByRole('alert').filter({ hasText: 'must be 1' }).first()).toBeVisible(); // WEIGHTS_SUM_NOT_ONE pre-check
    await shot(page, 'weights-invalid');

    await openScreen(page, 'server');
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('validation-result')).toContainText('INVALID');
    await expect(page.getByTestId('validate-errors')).toContainText('WEIGHTS_SUM_NOT_ONE');
    await expect(page.getByTestId('run-btn')).toBeDisabled();
    expect(st.seen.filter((s) => s.url === '/api/v1/runs' && s.method === 'POST')).toHaveLength(0);

    // fixing the sum makes Validate pass again (no auto-normalisation: the user types the value)
    await openScreen(page, 'settings');
    await page.getByTestId('rung-0-w-ap').fill('0.2');
    await expect(page.getByTestId('rung-0-sum')).not.toContainText('must be 1');
    await openScreen(page, 'server');
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('run-btn')).toBeEnabled();
  });

  test('without an edit the run body carries the preset weights untouched', async ({ page }) => {
    const st = stateOf({ runStatus: 'COMPLETED', captureRunProtocol: true });
    await installMock(page, st);
    await page.goto('/#/server');
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await page.getByText('status: ok').waitFor();
    await page.getByTestId('validate-btn').click();
    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('submit-message')).toContainText('Run created');
    const rp = (st.seen.filter((s) => s.method === 'POST' && s.url === '/api/v1/runs')[0].body as { protocol: Record<string, unknown> }).protocol;
    expect(getIn(rp, 'rungs.0.fitness_weights')).toEqual({ ap: 0.2, recall: 0.1, latency_reward: 0.4, size_reward: 0.3 });
    expect(getIn(rp, 'rungs.1.fitness_weights')).toEqual({ ap: 0.45, recall: 0.15, latency_reward: 0.25, size_reward: 0.15 });
  });
});
