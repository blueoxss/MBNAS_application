/**
 * Evaluation-OFF generation workflow in a real Chromium against the SYNTHETIC mocked API (e2e/mock.ts):
 * (a) the "성능 평가 수행" checkbox (default ON -> OFF is carried by the validate and run bodies and survives a reload
 * of the resolved run), (b) the generated-candidate table in generation order with missing metrics never shown as 0,
 * (c) a generated candidate's structure graph from a mock `mbnas-structure/2` report, block selection and its URL
 * restore, (d) the training panel for a generated candidate (scratch sends epoch_target_total; evaluate_only is not offered), (e) a baseline
 * evaluate_only submission whose POST body carries no epoch target.
 * Fixtures are invented (tests/detail_fixtures.ts); nothing here is a measured result.
 */
import { expect, test, type Page } from '@playwright/test';
import { connectAndLoad, installMock, openScreen, stateOf, TOKEN, type MockState, captureEvidence } from './mock';
import { RUN_ID, fixtureRun } from '../tests/fixtures';
import { GEN_CID, SYNTHETIC_CONCAT_INDEX, SYNTHETIC_DETECT_INDEX, SYNTHETIC_EDGE_COUNT, syntheticGeneratedDetail, syntheticGeneratedRows } from '../tests/detail_fixtures';
import { getIn, setIn } from '../src/lib/policy';
import { DASH } from '../src/lib/format';

const shot = async (page: Page, name: string): Promise<void> => {
  const h = await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio);
  await captureEvidence(page, `evidence/${test.info().project.name}-${name}.png`, h < 12000);
};

/** The mock "server" resolves the requested protocol and records the default duplicate scope (DD-2). */
const resolveWithScope = (p: Record<string, unknown>): Record<string, unknown> => setIn(p, 'console.search.duplicate_scope', 'per_seed') as Record<string, unknown>;

/** A COMPLETED evaluation-OFF run whose snapshot carries only generated candidates (no evaluation rows). */
function offRunState(over: Partial<MockState> = {}): MockState {
  const protocol = setIn(setIn(setIn(fixtureRun().resolved_protocol as Record<string, unknown>, 'console.search.evaluation_enabled', false), 'console.search.nc', 3), 'console.search.duplicate_scope', 'per_seed') as Record<string, unknown>;
  const rows = syntheticGeneratedRows();
  return stateOf({
    runStatus: 'COMPLETED',
    runProtocol: protocol,
    snapshotPatch: (s) => ({ ...s, candidates: [], generations: [], generated_candidates: rows }),
    details: Object.fromEntries(rows.map((r) => [r.candidate_id, syntheticGeneratedDetail(r.candidate_id)])),
    ...over,
  });
}

async function connectOnly(page: Page): Promise<void> {
  await page.goto('/#/server');
  await page.getByLabel(/Bearer token/).fill(TOKEN);
  await page.getByTestId('connect-btn').click();
  await page.getByText('status: ok').waitFor();
}

const bodyOf = (st: MockState, url: string): { protocol: Record<string, unknown> } => {
  const hit = st.seen.filter((s) => s.method === 'POST' && s.url === url);
  expect(hit, `exactly one POST ${url}`).toHaveLength(1);
  return hit[0].body as { protocol: Record<string, unknown> };
};

test.describe('evaluation switch (성능 평가 수행)', () => {
  test('defaults ON; OFF is carried by the validate and run bodies and is restored from the run after a reload', async ({ page }) => {
    const st = stateOf({ runStatus: 'COMPLETED', captureRunProtocol: true, resolve: resolveWithScope });
    await installMock(page, st);
    await connectOnly(page);
    await openScreen(page, 'settings');
    const box = page.getByTestId('evaluation-enabled');
    await expect(box).toBeChecked(); // default ON
    await box.uncheck();
    await expect(box).not.toBeChecked();
    // DD-17: the evaluation-only fields stay visible, disabled, values preserved; the generation inputs appear
    await expect(page.getByTestId('path-dataset_yaml')).toBeDisabled();
    await page.getByTestId('generation-nc').fill('3');
    await shot(page, 'generation-settings-off');

    await openScreen(page, 'server');
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('run-btn')).toBeEnabled();
    const validated = bodyOf(st, '/api/v1/protocols/validate');
    expect(getIn(validated.protocol, 'console.search.evaluation_enabled')).toBe(false);
    expect(getIn(validated.protocol, 'console.search.nc')).toBe(3);

    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('submit-message')).toContainText('Run created');
    const run = bodyOf(st, '/api/v1/runs');
    expect(getIn(run.protocol, 'console.search.evaluation_enabled')).toBe(false); // the resolved protocol returned by Validate
    expect(getIn(run.protocol, 'console.search.duplicate_scope')).toBe('per_seed');

    // the protocol lives in memory only (no storage): after a reload the editor is restored from the run's resolved protocol
    await page.reload();
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await page.getByTestId('pause-btn').waitFor(); // the run id of the hash is loaded after the health check
    await openScreen(page, 'settings');
    await expect(page.getByTestId('evaluation-enabled')).not.toBeChecked();
    await expect(page.getByTestId('generation-nc')).toHaveValue('3');
  });

  test('turning it back ON re-enables the evaluation controls and the bodies say true again', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await connectOnly(page);
    await openScreen(page, 'settings');
    const box = page.getByTestId('evaluation-enabled');
    await box.uncheck();
    await expect(page.getByTestId('rung-0-w-ap')).toBeDisabled();
    await box.check();
    await expect(page.getByTestId('rung-0-w-ap')).toBeEnabled();
    await openScreen(page, 'server');
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('run-btn')).toBeEnabled();
    expect(getIn(bodyOf(st, '/api/v1/protocols/validate').protocol, 'console.search.evaluation_enabled')).toBe(true);
  });
});

test.describe('generated candidates (no evaluation)', () => {
  test('Lineage lists the generated candidates next to the evaluated rows with the UNTRAINED · UNEVALUATED badge (F14)', async ({ page }) => {
    const rows = syntheticGeneratedRows();
    const st = stateOf({ snapshotPatch: (s) => ({ ...s, generated_candidates: rows }), details: Object.fromEntries(rows.map((r) => [r.candidate_id, syntheticGeneratedDetail(r.candidate_id)])) });
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'lineage');
    const table = page.getByTestId('lineage-table');
    const rowOf = (name: string) => table.locator('tbody tr', { has: page.getByRole('button', { name, exact: true }) });
    await expect(table.getByTestId('lineage-untrained')).toHaveCount(rows.length);
    for (const r of rows) {
      await expect(rowOf(r.name).getByTestId('lineage-untrained')).toHaveText('UNTRAINED · UNEVALUATED');
      await expect(rowOf(r.name)).toContainText('미평가');
    }
    await expect(rowOf('ga-g0-0').getByTestId('lineage-status')).toHaveText('EVALUATED');
    await expect(rowOf('ga-g0-0').getByTestId('lineage-untrained')).toHaveCount(0);
    await expect(page.getByTestId('lineage-generated-count')).toContainText(`생성 후보 ${rows.length}개`);
    await rowOf(`${GEN_CID}-model`).getByRole('button', { name: `${GEN_CID}-model`, exact: true }).click();
    await expect(page.getByTestId('candidate-view').getByTestId('untrained-unevaluated')).toHaveText('UNTRAINED · UNEVALUATED');
    await rowOf(`${GEN_CID}-model`).scrollIntoViewIfNeeded();
    await shot(page, 'lineage-generated');
  });

  test('the Monitor table lists them in generation order and never turns a missing metric into 0', async ({ page }) => {
    const st = offRunState();
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    const table = page.getByTestId('generated-candidates');
    await expect(table).toBeVisible();
    // the mock returned them as [order 2, order 0, order 1]; the table must show 0, 1, 2
    const names = await table.locator('tbody tr').evaluateAll((rows) => rows.map((r) => (r.querySelector('button') as HTMLElement).textContent));
    expect(names).toEqual(['synthetic-gen-c1-model', 'synthetic-gen-c2-model', 'synthetic-gen-c3-model']);
    const orders = await table.locator('tbody tr td:first-child').allTextContents();
    expect(orders.map((t) => t.trim())).toEqual(['0', '1', '2']);
    // order 2 has no parameter count: a dash, not 0. Metric cells never carry a number (no AP/recall/latency/fitness exists).
    const last = table.locator('tbody tr').nth(2);
    await expect(last.locator('td').nth(6)).toHaveText(DASH);
    const metrics = await table.locator('tbody tr td:nth-child(8)').allTextContents();
    expect(metrics).toHaveLength(3);
    for (const m of metrics) expect(m).not.toMatch(/\d/);
    await expect(table).toContainText('NOT_EVALUATED');
    await expect(page.getByRole('heading', { name: '평가 없는 랜덤 후보 생성' })).toBeVisible();
    // evaluation charts are not drawn for an evaluation-OFF run
    await expect(page.getByTestId('chart-fitness')).toHaveCount(0);
    await shot(page, 'generated-table');
  });

  test('a generated candidate shows the structure graph from the mock report; a clicked block and the tab are written to the URL', async ({ page }) => {
    const st = offRunState();
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    await page.getByTestId('generated-candidates').getByRole('button', { name: 'synthetic-gen-c1-model' }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    expect(st.seen.some((s) => s.method === 'GET' && s.url === `/api/v1/runs/${RUN_ID}/candidates/${GEN_CID}`), 'candidate detail is requested').toBe(true);
    await page.getByRole('button', { name: '구조·gene' }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('model_tab')).toBe('structure');

    const graph = page.getByTestId('structure-graph');
    await expect(graph).toBeVisible();
    await expect(page.getByTestId('structure-status')).toHaveText('PASS');
    await expect(graph.locator('g[data-target]')).toHaveCount(SYNTHETIC_EDGE_COUNT);
    const sourcesOf = async (index: number): Promise<string[]> =>
      graph.locator(`g[data-target="block-${String(index).padStart(4, '0')}"]`).evaluateAll((gs) => gs.map((g) => g.getAttribute('data-source') ?? '').sort());
    expect(await sourcesOf(SYNTHETIC_CONCAT_INDEX)).toEqual(['block-0002', 'block-0005']); // list-from [-1, 2]
    expect(await sourcesOf(SYNTHETIC_DETECT_INDEX)).toEqual(['block-0004', 'block-0007']); // Detect from [7, 4]
    expect(await sourcesOf(0)).toEqual(['INPUT']);

    await page.getByTestId(`gene-${SYNTHETIC_CONCAT_INDEX}`).click();
    await expect(page.getByTestId('selected-block')).toContainText(`#${SYNTHETIC_CONCAT_INDEX} · Concat`);
    await expect.poll(() => new URL(page.url()).searchParams.get('block')).toBe(`block-${String(SYNTHETIC_CONCAT_INDEX).padStart(4, '0')}`);
    await shot(page, 'generated-structure');
  });

  // Regression for WP-W3 finding R1 (fixed by DD-32): after a reload the Server screen is needed for the memory-only token,
  // setScreen() rewrites the hash without `cid`, and the boot restore must keep `?block=` and `?model_tab=` (openCandidate restore option).
  test('reload restores the selected block and model_tab', async ({ page }) => {
    const st = offRunState();
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    await page.getByTestId('generated-candidates').getByRole('button', { name: 'synthetic-gen-c1-model' }).click();
    await page.getByRole('button', { name: '구조·gene' }).click();
    await page.getByTestId(`gene-${SYNTHETIC_CONCAT_INDEX}`).click();
    const wantedBlock = `block-${String(SYNTHETIC_CONCAT_INDEX).padStart(4, '0')}`;
    await expect.poll(() => new URL(page.url()).searchParams.get('block')).toBe(wantedBlock);
    expect(new URL(page.url()).hash).toContain(`cid=${GEN_CID}`);
    // the token is memory-only: reload, open the connection screen, enter it again; the candidate comes back from the hash
    await page.reload();
    await page.getByRole('button', { name: /Go to server connection/ }).click();
    await page.getByLabel(/Bearer token/).fill(TOKEN);
    await page.getByTestId('connect-btn').click();
    await expect(page.getByRole('dialog')).toBeVisible(); // restored (works today)
    await expect(page.getByTestId('structure-graph')).toBeAttached();
    expect(new URL(page.url()).searchParams.get('model_tab'), 'model_tab restored').toBe('structure');
    expect(new URL(page.url()).searchParams.get('block'), 'block restored').toBe(wantedBlock);
    await expect(page.getByTestId('selected-block')).toContainText(`#${SYNTHETIC_CONCAT_INDEX} · Concat`);
  });

  test('training panel: scratch sends epoch_target_total, evaluate_only is not offered for a generated candidate', async ({ page }) => {
    const st = offRunState();
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    await page.getByTestId('generated-candidates').getByRole('button', { name: 'synthetic-gen-c1-model' }).click();
    await page.getByRole('button', { name: '학습', exact: true }).click();
    const submit = page.getByTestId('training-submit');

    await expect(page.getByTestId('training-mode')).toHaveValue('scratch');
    await submit.click();
    await expect(page.getByText('별도 학습 run 생성')).toBeVisible();
    const scratch = st.seen.filter((s) => s.method === 'POST' && s.url === '/api/v1/training-runs');
    expect(scratch).toHaveLength(1);
    const sb = scratch[0].body as { mode: string; protocol: Record<string, unknown>; source: Record<string, unknown> };
    expect(sb.mode).toBe('scratch');
    expect(sb.source).toMatchObject({ kind: 'generated', run_id: RUN_ID, candidate_id: GEN_CID });
    expect(typeof getIn(sb.protocol, 'console.training.epoch_target_total')).toBe('number');

    // S05 fix 1: the server refuses evaluate_only for a generated/search candidate (422 EVAL_ONLY_UNSUPPORTED), so the panel does not
    // offer it; the evaluate_only request body (no epoch_target_total) is covered by tests/training_panel.test.tsx on a baseline source.
    await expect(page.getByTestId('training-mode').locator('option[value="evaluate_only"]')).toHaveCount(0);
    await expect(page.getByTestId('training-mode').locator('option[value="resume"]')).toHaveCount(1);
    expect(st.seen.filter((s2) => s2.method === 'POST' && s2.url === '/api/v1/training-runs')).toHaveLength(1);
    await shot(page, 'generated-training-no-evaluate-only');
  });

  // S05 fix 3: browser-level evaluate_only request body. evaluate_only exists only for an official baseline, which the standalone panel on
  // the settings screen offers (Baseline registry ID); the POST body must carry no epoch target (jsdom twin: tests/training_panel.test.tsx).
  test('baseline evaluate_only submission sends no epoch_target_total / epochs', async ({ page }) => {
    const st = stateOf({ runStatus: 'COMPLETED' });
    await installMock(page, st);
    await connectOnly(page);
    await openScreen(page, 'settings');
    await page.getByLabel(/Baseline registry ID/).fill('synthetic-baseline-id');
    const mode = page.getByTestId('training-mode');
    await expect(mode.locator('option[value="evaluate_only"]')).toHaveCount(1);
    await mode.selectOption('evaluate_only');
    await expect(page.getByTestId('evaluate-only-epoch-help')).toContainText('평가 전용: 학습 epoch 없음');
    await page.getByTestId('training-submit').click();
    await expect(page.getByText('별도 평가 run 생성')).toBeVisible();
    const hit = st.seen.filter((s2) => s2.method === 'POST' && s2.url === '/api/v1/training-runs');
    expect(hit, 'exactly one POST /training-runs').toHaveLength(1);
    const b = hit[0].body as { mode: string; source: Record<string, unknown>; protocol: Record<string, unknown> };
    expect(b.mode).toBe('evaluate_only');
    expect(b.source).toMatchObject({ kind: 'baseline', baseline_id: 'synthetic-baseline-id' });
    expect(getIn(b.protocol, 'console.training.epoch_target_total')).toBeUndefined();
    expect(getIn(b.protocol, 'console.training.scheduler_horizon_epochs')).toBeUndefined();
    expect(Object.keys(b)).not.toContain('epochs');
    expect(Object.keys(b)).not.toContain('epoch_target_total');
    // the recipe's own rung table legitimately keeps epoch_target_total; the explicit-training section must not carry any epoch key
    expect(Object.keys((getIn(b.protocol, 'console.training') ?? {}) as Record<string, unknown>).filter((k) => /epoch/.test(k))).toEqual([]);
    await shot(page, 'baseline-training-evaluate-only');
  });
});
