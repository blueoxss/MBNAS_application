/**
 * Two opt-in groups (MBNAS_E2E_REAL=1 + MBNAS_E2E_TOKEN for both):
 *  - V4 (MBNAS_E2E_V4=1): the CPU real stack of scripts/dev/web_real_stack.py (WP-6 section 2 / DD-A50), see the V4 block.
 *  - legacy GPU flow (MBNAS_E2E_GPU=1, starts real GPU work), described below.
 *
 * REAL-SERVER browser E2E (NOT mocked): Chromium -> production build (vite preview, same-origin /api proxy) -> live
 * MBNAS API -> live worker training on the host GPU (PILOT_SMOKE preset, tiny real data; smoke values only).
 *
 * Opt-in: MBNAS_E2E_REAL=1 and MBNAS_E2E_TOKEN=<gateway MBNAS_GW_API_TOKEN> (environment only, never hardcoded).
 * Optional MBNAS_E2E_XORIGIN_API: explicit API origin of a second API process WITHOUT a signing key (bearer_fetch
 * artifacts, exact-origin CORS for the page origin, default http://127.0.0.1:8720). Run with `npm run e2e:real`
 * (playwright.real.config.ts).
 * Optional MBNAS_E2E_FINAL=1 (starts real GPU work): drives the Final Retrain panel on the COMPLETED search run
 * (MBNAS_E2E_FINAL_PARENT_ID, else MBNAS_E2E_RUN_ID, else the run created by the first test) -> a new FINAL_RETRAIN child
 * run trains on the GPU -> the child loads with its finalists -> its final cohort is drawn alone -> the point dialog
 * shows lineage operation 'final' with the finalist source. MBNAS_E2E_FINAL_BUDGET_GPU_HOURS (default 0.25).
 * Screenshots go to evidence/real_*.png; the downloaded export goes to test-results/real/.
 */
import AxeBuilder from '@axe-core/playwright';
import { createHash } from 'node:crypto';
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { captureEvidence } from './mock';
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const TOKEN = process.env.MBNAS_E2E_TOKEN ?? '';
const REAL = process.env.MBNAS_E2E_REAL === '1';
const XORIGIN = process.env.MBNAS_E2E_XORIGIN_API ?? '';
const FINAL = process.env.MBNAS_E2E_FINAL === '1';
const FINAL_BUDGET = process.env.MBNAS_E2E_FINAL_BUDGET_GPU_HOURS ?? '0.25';
const KINDS = ['gt', 'tp', 'fn', 'fp'] as const;

const GPU = process.env.MBNAS_E2E_GPU === '1';
test.skip(!REAL || !TOKEN, 'real-server E2E is opt-in: set MBNAS_E2E_REAL=1 and MBNAS_E2E_TOKEN');

let runId = '';

/** Full-page capture, except while a (fixed-position) dialog is open: then the viewport shows it as the user sees it. */
const shot = async (page: Page, name: string): Promise<void> => {
  const h = await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio);
  const dialog = await page.getByRole('dialog').isVisible();
  await captureEvidence(page, `evidence/real_${name}.png`, !dialog && h < 12000);
};

async function api<T = Record<string, unknown>>(request: APIRequestContext, path: string): Promise<T> {
  const r = await request.get(path, { headers: { Authorization: `Bearer ${TOKEN}` } });
  expect(r.status(), `GET ${path}`).toBe(200);
  return (await r.json()) as T;
}

async function openScreen(page: Page, id: 'monitor' | 'settings' | 'lineage' | 'history' | 'server'): Promise<void> {
  const menu = page.getByTestId('menu-btn');
  if (await menu.isVisible()) {
    if ((await menu.getAttribute('aria-expanded')) !== 'true') await menu.click();
  }
  await page.getByTestId(`nav-${id}`).click();
}

async function connect(page: Page, origin = ''): Promise<void> {
  await page.goto('/#/server');
  if (origin) await page.getByTestId('origin-input').fill(origin);
  await page.getByLabel(/Bearer token/).fill(TOKEN);
  await page.getByTestId('connect-btn').click();
  await expect(page.getByText(/status: (ok|degraded)/)).toBeVisible();
}

async function loadRun(page: Page, id: string): Promise<void> {
  await openScreen(page, 'server');
  await page.getByTestId('run-id-input').fill(id);
  await page.getByTestId('load-run-btn').click();
  await expect(controlCard(page)).toContainText(id);
}

const controlCard = (page: Page) => page.locator('section.card').filter({ has: page.getByTestId('pause-btn') });

async function runStatusIs(page: Page, status: RegExp, timeout: number): Promise<void> {
  await expect(controlCard(page).locator('.badge').filter({ hasText: status }).first()).toBeVisible({ timeout });
}

/** Every chart-point evaluation id of the selected cohort in the fitness chart. */
const pointIds = (page: Page) => page.getByTestId('chart-fitness').getByTestId('chart-point').evaluateAll((els) => els.map((e) => e.getAttribute('data-eval-id') ?? ''));

async function selectCohortContaining(page: Page, evalId: string): Promise<void> {
  const select = page.getByTestId('cohort-select');
  const values = await select.locator('option').evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value));
  for (const v of values) {
    await select.selectOption(v);
    if ((await pointIds(page)).includes(evalId)) return;
  }
  throw new Error(`no cohort draws ${evalId}`);
}

async function expectFourImages(page: Page, mode: 'signed' | 'blob'): Promise<void> {
  const panel = page.getByRole('dialog').getByTestId('artifact-panel');
  await expect(panel).toBeVisible();
  for (const k of KINDS) {
    const fig = panel.getByTestId(`artifact-${k}`);
    // wait for READY first (auto-retrying): while an artifact loads the figure node is replaced by its state node,
    // and scrolling a node that is swapped mid-action fails with "Element is not attached to the DOM" (QA pass flake)
    await expect(fig).toHaveAttribute('data-state', 'READY', { timeout: 60_000 });
    await fig.scrollIntoViewIfNeeded();
    const img = fig.locator('img');
    await expect.poll(() => img.evaluate((e) => (e as HTMLImageElement).complete && (e as HTMLImageElement).naturalWidth), { timeout: 60_000 }).toBeGreaterThan(0);
    await img.scrollIntoViewIfNeeded();
    const src = (await img.getAttribute('src')) ?? '';
    if (mode === 'signed') expect(src).toMatch(/\/api\/v1\/runs\/[^/]+\/artifacts\/[^?]+\?exp=\d+&sig=[0-9a-f]{64}$/);
    else expect(src.startsWith('blob:')).toBe(true);
  }
}

// ------------------------------------------------------------------------------------------------------------------
// V4 (WP-6 section 2 acceptance, DD-A50): the console against a REAL CPU stack started by scripts/dev/web_real_stack.py
// (core + owner worker + CPU runner + gateway serving dist/). Opt-in: MBNAS_E2E_V4=1 plus MBNAS_E2E_REAL=1,
// MBNAS_E2E_TOKEN, MBNAS_E2E_BASE_URL (the gateway), MBNAS_E2E_DATASET_YAML / MBNAS_E2E_OUTPUT_ROOT (server paths the
// user types), MBNAS_E2E_STACK_PID (SIGUSR1 = stop ONLY the core process; last test). Each step is its own test (one
// failure does not hide the next); facts shared between steps live in test-results/real/v4_state.json.
// Screenshots: $MBNAS_E2E_EVIDENCE_DIR (default ../docs/internal/evidence/WP6/V4B/screenshots), desktop 1280 + mobile 375.
// ------------------------------------------------------------------------------------------------------------------
const V4 = process.env.MBNAS_E2E_V4 === '1';
const V4_DIR = process.env.MBNAS_E2E_EVIDENCE_DIR ?? '../docs/internal/evidence/WP6/V4B/screenshots';
const V4_STATE = `${V4_DIR}/../v4_state.json`; // outside test-results/ (Playwright empties outputDir per run)
const V4_STEPS = `${V4_DIR}/../e2e_steps.jsonl`;
const EXAMPLES = '../examples/models';

type V4State = { trainRunId?: string; trainCid?: string; uploadSha?: string; offRunId?: string; [k: string]: unknown };
const v4Load = (): V4State => (existsSync(V4_STATE) ? (JSON.parse(readFileSync(V4_STATE, 'utf8')) as V4State) : {});
const v4Save = (patch: V4State): void => {
  mkdirSync(dirname(V4_STATE), { recursive: true });
  writeFileSync(V4_STATE, JSON.stringify({ ...v4Load(), ...patch }, null, 2));
};
const v4Note = (step: string, data: Record<string, unknown>): void => {
  mkdirSync(V4_DIR, { recursive: true });
  appendFileSync(V4_STEPS, JSON.stringify({ at: new Date().toISOString(), step, ...data }) + '\n');
};
const sha256Hex = (b: Buffer | string): string => createHash('sha256').update(b).digest('hex');

/** Desktop (1280) then mobile (375) capture of the current state; a dialog is captured as the viewport. */
async function v4Shot(page: Page, name: string): Promise<void> {
  mkdirSync(V4_DIR, { recursive: true });
  const size = page.viewportSize() ?? { width: 1280, height: 900 };
  const dialog = await page.getByRole('dialog').isVisible().catch(() => false);
  const full = async (): Promise<boolean> => !dialog && (await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio)) < 12000;
  await captureEvidence(page, `${V4_DIR}/desktop-${name}.png`, await full());
  await page.setViewportSize({ width: 375, height: 812 });
  await page.waitForTimeout(300);
  await captureEvidence(page, `${V4_DIR}/mobile-${name}.png`, await full());
  const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  await page.setViewportSize(size);
  await page.waitForTimeout(200);
  v4Note(`shot:${name}`, { mobile_horizontal_overflow_px: m.sw - m.cw });
}

/** axe (wcag2a/aa, 2.1aa) serious + critical violations; recorded, then asserted softly so later steps still run. */
async function v4Axe(page: Page, screen: string): Promise<void> {
  const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  const bad = res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.impact} ${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`);
  const html = res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').flatMap((v) => v.nodes.slice(0, 2).map((n) => `${v.id}: ${n.html.slice(0, 240)} :: ${n.failureSummary?.slice(0, 240) ?? ''}`));
  v4Note(`axe:${screen}`, { serious_critical: bad.length, violations: bad, nodes: html, all_violations: res.violations.length });
  expect.soft(bad, `axe serious/critical on ${screen}`).toEqual([]);
}

async function v4Api<T = Record<string, unknown>>(request: APIRequestContext, path: string): Promise<T> {
  const r = await request.get(path, { headers: { Authorization: `Bearer ${TOKEN}` } });
  expect(r.status(), `GET ${path}`).toBe(200);
  return (await r.json()) as T;
}

async function v4Connect(page: Page): Promise<void> {
  await page.goto('/#/server');
  await page.getByLabel(/Bearer token/).fill(TOKEN);
  await page.getByTestId('connect-btn').click();
  await expect(page.getByText(/status: (ok|degraded)/)).toBeVisible();
}

const trainingCard = (page: Page) => page.locator('section.card').filter({ has: page.getByTestId('training-submit') });

async function v4UserSource(page: Page): Promise<void> {
  await openScreen(page, 'settings');
  await trainingCard(page).getByLabel('모델 출처').selectOption('user');
}

async function v4Upload(page: Page, file: string): Promise<{ hex: string; bytes: Buffer }> {
  const bytes = readFileSync(`${EXAMPLES}/${file}`);
  const hex = sha256Hex(bytes);
  await page.getByTestId('model-upload-input').setInputFiles({ name: file, mimeType: 'application/x-yaml', buffer: bytes });
  const result = page.getByTestId('model-upload-result');
  await expect(result.getByTestId('upload-sha256')).toHaveText(`sha256:${hex}`);
  await expect(result.getByTestId('upload-local-sha256')).toHaveText(hex);
  await expect(result.getByTestId('upload-verdict')).toContainText('일치');
  return { hex, bytes };
}

async function v4StructureCheck(page: Page): Promise<string> {
  await page.getByTestId('registry-structure-open').click();
  await page.getByTestId('registry-structure-check').click();
  const res = page.getByTestId('registry-structure-result');
  await expect(res).toBeVisible({ timeout: 120_000 });
  return (await page.getByTestId('structure-status').first().textContent()) ?? '';
}

async function v4LoadRun(page: Page, id: string): Promise<void> {
  await openScreen(page, 'server');
  await page.getByTestId('run-id-input').fill(id);
  await page.getByTestId('load-run-btn').click();
  await expect(controlCard(page)).toContainText(id);
}

/** Open the candidate dialog of the training run's candidate from the Monitor (candidate table, else training progress). */
async function v4OpenTrainCandidate(page: Page, request: APIRequestContext): Promise<ReturnType<Page['getByRole']>> {
  const s = v4Load();
  expect(s.trainRunId, 'training run created by the training step').toBeTruthy();
  await v4Connect(page);
  await v4LoadRun(page, s.trainRunId!);
  await openScreen(page, 'monitor');
  const snap = await v4Api<{ candidates: { id: string; name: string; candidate_id: string }[] }>(request, `/api/v1/runs/${s.trainRunId}/snapshot`);
  const row = snap.candidates.find((c) => c.candidate_id === s.trainCid) ?? snap.candidates[0];
  expect(row, 'the training run has a candidate row in its snapshot').toBeTruthy();
  await page.getByTestId('candidate-table').getByRole('button', { name: new RegExp(`evaluation ${row!.id}$`) }).first().click();
  const dlg = page.getByRole('dialog');
  await expect(dlg.getByTestId('candidate-view')).toBeVisible();
  return dlg;
}

test.describe('V4 real CPU stack: upload -> check -> train -> detail -> revisions -> diff -> download -> OFF -> core down', () => {
  test.skip(!V4, 'set MBNAS_E2E_V4=1 (with web_real_stack.py running) for the V4 real-stack flow');

  test('V4-01 connect with the bearer; presets auto-apply (DEFAULT_BASELINE14)', async ({ page }) => {
    await v4Connect(page);
    await expect(page.getByText('status: ok')).toBeVisible();
    await v4Shot(page, '01-server-connected');
    await v4Axe(page, 'server');
    await openScreen(page, 'settings');
    await expect(page.getByTestId('policy-auto-applied')).toBeVisible();
    const label = (await page.getByTestId('policy-label').textContent()) ?? '';
    v4Note('V4-01', { policy_label: label });
    await v4Shot(page, '01-settings-preset-auto');
    await v4Axe(page, 'settings');
  });

  test('V4-02 upload examples/models/yolo11n_user.yaml -> structure check PASS', async ({ page }) => {
    await v4Connect(page);
    await v4UserSource(page);
    const { hex } = await v4Upload(page, 'yolo11n_user.yaml');
    await expect(page.getByTestId('upload-id')).toHaveText(/^upl-[0-9a-f]+/);
    await page.getByTestId('model-upload-result').scrollIntoViewIfNeeded();
    await v4Shot(page, '02-upload-good');
    const status = await v4StructureCheck(page);
    await expect(page.getByTestId('structure-status').first()).toHaveText(/PASS/);
    await expect(page.getByTestId('structure-source-sha256')).toHaveText(hex);
    await expect(page.getByTestId('structure-source-verdict')).toContainText('일치');
    v4Note('V4-02', { upload_sha256: hex, structure_status: status, upload_id: await page.getByTestId('upload-id').textContent() });
    v4Save({ uploadSha: hex });
    await v4Shot(page, '02-structure-pass');
    await v4Axe(page, 'structure-check-dialog');
  });

  test('V4-03 upload yolo11n_user_broken.yaml -> structure check FAIL with diagnostics', async ({ page }) => {
    await v4Connect(page);
    await v4UserSource(page);
    await v4Upload(page, 'yolo11n_user_broken.yaml');
    const status = await v4StructureCheck(page);
    await expect(page.getByTestId('structure-status').first()).toHaveText(/FAIL/);
    const res = page.getByTestId('registry-structure-result');
    await expect(res.getByTestId('structure-fail-notice')).toBeVisible();
    const text = (await res.textContent()) ?? '';
    const codes = ['SHAPE_MISMATCH', 'MERGE_INPUT_MISMATCH'].filter((c) => text.includes(c));
    v4Note('V4-03', { structure_status: status, diagnostic_codes_shown: codes });
    expect(codes.length, 'the FAIL diagnostics (SHAPE_MISMATCH / MERGE_INPUT_MISMATCH) are shown').toBeGreaterThan(0);
    await v4Shot(page, '03-structure-fail');
  });

  test('V4-04 start a CPU explicit training run from the upload (1 epoch, 64 px, validation off)', async ({ page }) => {
    const DATASET = process.env.MBNAS_E2E_DATASET_YAML ?? '';
    const OUTPUT = process.env.MBNAS_E2E_OUTPUT_ROOT ?? '';
    expect(DATASET && OUTPUT, 'MBNAS_E2E_DATASET_YAML and MBNAS_E2E_OUTPUT_ROOT').toBeTruthy();
    await v4Connect(page);
    await v4UserSource(page);
    const { hex } = await v4Upload(page, 'yolo11n_user.yaml');
    const card = trainingCard(page);
    // recipe: the server's PILOT_SMOKE preset (smoke values), then CPU-friendly loader settings in the full-protocol JSON
    await card.getByRole('button', { name: '서버 학습 recipe 불러오기' }).click();
    await expect(card.getByTestId('training-recipe').locator('option', { hasText: 'PILOT_SMOKE' })).toHaveCount(1);
    await card.getByTestId('training-recipe').selectOption({ label: (await card.getByTestId('training-recipe').locator('option', { hasText: 'PILOT_SMOKE' }).textContent())! });
    await card.locator('summary', { hasText: '전체 recipe' }).click();
    const box = card.getByLabel('새 학습 protocol JSON');
    const proto = JSON.parse(await box.inputValue()) as { training: Record<string, unknown> };
    Object.assign(proto.training, { workers: 0, precision: 'fp32', warmup_epochs: 0, close_mosaic_epochs: 0 });
    await box.fill(JSON.stringify(proto, null, 2));
    await card.getByRole('button', { name: '전체 설정 적용' }).click();
    await card.getByLabel('데이터셋 YAML · 서버 경로').fill(DATASET);
    await card.getByLabel('결과 저장 · 서버 경로').fill(OUTPUT);
    await card.getByLabel(/^목표 epoch/).fill('1');
    await card.getByLabel(/^Batch per GPU/).fill('2');
    await card.getByLabel(/^입력 크기/).fill('64');
    await card.getByLabel('Optimizer').selectOption('SGD');
    await card.getByTestId('training-validation').uncheck();
    await expect(card.getByTestId('training-submit')).toBeEnabled();
    await card.getByTestId('training-submit').scrollIntoViewIfNeeded();
    await v4Shot(page, '04-training-form');
    await card.getByTestId('training-submit').click();
    await expect(card.getByTestId('training-source-sha')).toContainText(`yolo11n_user.yaml source_sha256 sha256:${hex}`, { timeout: 120_000 });
    const msg = (await card.locator('.banner').filter({ hasText: '학습 run 생성' }).first().textContent()) ?? '';
    const runId = /run 생성: (\S+)/.exec(msg)?.[1]?.replace(/·.*$/, '') ?? '';
    expect(runId).toMatch(/^[a-z]+-[0-9a-f]+$/);
    v4Save({ trainRunId: runId, uploadSha: hex });
    v4Note('V4-04', { run_id: runId, message: msg });
    await v4Shot(page, '04-training-created');
  });

  test('V4-05 monitor the training run until finished', async ({ page, request }) => {
    const s = v4Load();
    expect(s.trainRunId).toBeTruthy();
    await v4Connect(page);
    await v4LoadRun(page, s.trainRunId!);
    await openScreen(page, 'monitor');
    await expect.poll(async () => (await v4Api<{ status: string }>(request, `/api/v1/runs/${s.trainRunId}`)).status, { timeout: 15 * 60_000, intervals: [3_000] }).toMatch(/^(COMPLETED|FAILED|CANCELLED)$/);
    const run = await v4Api<{ status: string }>(request, `/api/v1/runs/${s.trainRunId}`);
    const snap = await v4Api<{ candidates: { id: string; candidate_id: string; status: string }[] }>(request, `/api/v1/runs/${s.trainRunId}/snapshot`);
    v4Note('V4-05', { run_status: run.status, candidates: snap.candidates.map((c) => ({ id: c.id, candidate_id: c.candidate_id, status: c.status })) });
    expect(run.status).toBe('COMPLETED');
    v4Save({ trainCid: snap.candidates[0]?.candidate_id });
    await openScreen(page, 'server');
    await runStatusIs(page, /^COMPLETED$/, 60_000);
    await v4Shot(page, '05-run-completed-server');
    await openScreen(page, 'monitor');
    await expect(page.getByTestId('candidate-table').getByRole('button').first()).toBeVisible({ timeout: 60_000 });
    await v4Shot(page, '05-run-completed-monitor');
    await v4Axe(page, 'monitor');
  });

  test('V4-06 candidate detail: gene/structure diagram, block table, configuration YAML', async ({ page, request }) => {
    const dlg = await v4OpenTrainCandidate(page, request);
    await dlg.getByTestId('model-tab-structure').click();
    const diagram = dlg.getByTestId('model-structure');
    await expect(diagram).toBeVisible({ timeout: 60_000 });
    const genes = await diagram.locator('[data-testid^="gene-"]').count();
    await v4Shot(page, '06-detail-structure');
    await dlg.getByTestId('model-tab-all').click();
    const blocks = dlg.getByTestId('block-table');
    await expect(blocks).toBeVisible();
    const blockRows = await blocks.locator('tbody tr').count();
    const yamlSummary = dlg.locator('summary', { hasText: '실제 보유 구성 YAML' });
    const hasYaml = await yamlSummary.count();
    if (hasYaml) await yamlSummary.click();
    v4Note('V4-06', { gene_nodes: genes, block_rows: blockRows, architecture_yaml_section: hasYaml });
    expect(genes, 'gene / structure diagram nodes').toBeGreaterThan(0);
    expect(blockRows, 'block table rows').toBeGreaterThan(0);
    expect(hasYaml, 'configuration YAML section (detail.architecture_yaml)').toBeGreaterThan(0);
    await expect(dlg.locator('pre.log')).toContainText('backbone');
    await blocks.scrollIntoViewIfNeeded();
    await v4Shot(page, '06-detail-blocks-yaml');
    await v4Axe(page, 'candidate-dialog');
  });

  test('V4-07..09 revisions tab -> diff -> download exact bytes (sha256 = source_sha256)', async ({ page, request }) => {
    const dlg = await v4OpenTrainCandidate(page, request);
    const note = dlg.getByTestId('config-tab-note');
    if (await note.count()) v4Note('V4-07', { config_tab_note: await note.textContent(), state: await note.getAttribute('data-state') });
    await expect(dlg.getByTestId('model-tab-config'), '"구성" tab offered (revision list answered)').toBeVisible({ timeout: 30_000 });
    await dlg.getByTestId('model-tab-config').click();
    const table = dlg.getByTestId('revision-table');
    await expect(table.getByTestId('revision-kind').first()).toBeVisible();
    const kinds = await table.getByTestId('revision-kind').allTextContents();
    const shas = await table.getByTestId('revision-source-sha256').allTextContents();
    const ids = await table.getByTestId('revision-id').allTextContents();
    v4Note('V4-07', { kinds, source_sha256: shas, revision_ids: ids });
    await v4Shot(page, '07-revisions');
    expect(kinds).toEqual(expect.arrayContaining(['original', 'selected', 'executed']));
    const s = v4Load();
    expect(shas[kinds.indexOf('original')], 'original revision = the uploaded bytes').toBe(`sha256:${s.uploadSha}`);

    // V4-08 diff (default selected <-> executed), both sides re-hashed in the browser and matched to the record
    await dlg.getByTestId('diff-run').click();
    await expect(dlg.getByTestId('diff-summary')).toBeVisible({ timeout: 60_000 });
    await expect(dlg.getByTestId('diff-verdict-A')).toHaveAttribute('data-verdict', 'match');
    await expect(dlg.getByTestId('diff-verdict-B')).toHaveAttribute('data-verdict', 'match');
    v4Note('V4-08', { left: await dlg.getByTestId('diff-left').inputValue(), right: await dlg.getByTestId('diff-right').inputValue(), summary: await dlg.getByTestId('diff-summary').textContent(), add_rows: await dlg.getByTestId('diff-lines').locator('tr[data-op="add"]').count(), del_rows: await dlg.getByTestId('diff-lines').locator('tr[data-op="del"]').count() });
    await dlg.getByTestId('diff-summary').scrollIntoViewIfNeeded();
    await v4Shot(page, '08-diff');

    // V4-09 download the exact stored bytes of every revision; sha256 of the saved file == the row's source_sha256
    const results: Record<string, unknown>[] = [];
    for (let i = 0; i < ids.length; i++) {
      const row = dlg.getByTestId(`revision-download-${ids[i]}`);
      const [download] = await Promise.all([page.waitForEvent('download'), row.getByTestId('revision-download').click()]);
      const path = `test-results/real/${download.suggestedFilename()}`;
      await download.saveAs(path);
      const got = sha256Hex(readFileSync(path));
      results.push({ kind: kinds[i], file: download.suggestedFilename(), saved_sha256: got, source_sha256: shas[i], match: `sha256:${got}` === shas[i] });
      await expect(row.getByTestId('revision-download-verdict')).toHaveAttribute('data-verdict', 'match');
      expect(`sha256:${got}`).toBe(shas[i]);
    }
    v4Note('V4-09', { downloads: results });
    await dlg.getByTestId(`revision-download-${ids[ids.length - 1]}`).scrollIntoViewIfNeeded();
    await v4Shot(page, '09-download-verdict');
  });

  test('V4-10 download a runner-produced checkpoint by id through the gateway (sha header match)', async ({ page, request }) => {
    const dlg = await v4OpenTrainCandidate(page, request);
    await dlg.getByTestId('model-tab-all').click();
    const table = dlg.getByTestId('checkpoint-table');
    await expect(table, 'checkpoint table in the candidate detail').toBeVisible({ timeout: 30_000 });
    const s = v4Load();
    // gateway level first (recorded, not a UI pass): every listed checkpoint id fetched from the artifact route by id
    const detail = await v4Api<{ checkpoints?: { id: string; kind: string; sha256: string; state: string; artifact_id?: string | null }[] }>(request, `/api/v1/runs/${s.trainRunId}/candidates/${s.trainCid}`);
    const gw: Record<string, unknown>[] = [];
    for (const ck of detail.checkpoints ?? []) {
      const r = await request.get(`/api/v1/runs/${s.trainRunId}/artifacts/${ck.id}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
      const body = await r.body();
      gw.push({ id: ck.id, kind: ck.kind, state: ck.state, artifact_id: ck.artifact_id ?? null, http: r.status(), bytes: body.length, body_sha256: sha256Hex(body), x_artifact_sha256: (r.headers()['x-artifact-sha256'] ?? '').replace(/^sha256:/, ''), recorded_sha256: ck.sha256 });
    }
    v4Note('V4-10-gateway', { checkpoints: gw });
    await table.scrollIntoViewIfNeeded();
    await v4Shot(page, '10-checkpoint-table');
    // the UI: a PRESENT checkpoint must offer "id로 다운로드" (CandidateView CheckpointTable)
    const btn = table.locator('[data-testid^="checkpoint-download-"][data-testid$="-btn"]').first();
    await expect(btn, 'a PRESENT checkpoint offers "id로 다운로드" in the UI').toBeVisible();
    const testId = ((await btn.getAttribute('data-testid')) ?? '').replace(/-btn$/, '');
    const row = table.locator('tbody tr').filter({ has: page.getByTestId(`${testId}-btn`) });
    const recorded = ((await row.getByTestId('checkpoint-sha256').textContent()) ?? '').replace(/^sha256:/, '');
    const [download] = await Promise.all([page.waitForEvent('download', { timeout: 300_000 }), btn.click()]);
    const path = `test-results/real/${download.suggestedFilename()}`;
    await download.saveAs(path);
    const got = sha256Hex(readFileSync(path));
    await expect(dlg.getByTestId(`${testId}-state`)).toHaveAttribute('data-state', 'READY');
    await expect(dlg.getByTestId(`${testId}-state`)).toContainText('sha256 일치');
    v4Note('V4-10', { file: download.suggestedFilename(), saved_sha256: got, recorded_sha256: recorded });
    expect(got).toBe(recorded);
    await row.scrollIntoViewIfNeeded();
    await v4Shot(page, '10-checkpoint-download');
  });

  test('V4-11 generation-only (evaluation OFF) run: UNTRAINED · UNEVALUATED in GeneratedTable and Lineage', async ({ page, request }) => {
    const OUTPUT = process.env.MBNAS_E2E_OUTPUT_ROOT ?? '';
    await v4Connect(page);
    await openScreen(page, 'settings');
    await page.getByTestId('evaluation-enabled').uncheck();
    await page.getByTestId('generation-count').fill('2');
    await page.getByTestId('generation-nc').fill('1');
    await page.getByLabel(/^구조 확인 입력 크기/).fill('64');
    const fam = page.getByLabel('모델 계열 (쉼표 구분)');
    await fam.fill('yolo11n');
    await fam.blur();
    const out = page.getByTestId('path-output_root');
    if ((await out.count()) && (await out.isEnabled())) await out.fill(OUTPUT);
    await v4Shot(page, '11-settings-off');
    await openScreen(page, 'server');
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('validation-result')).toContainText('VALID', { timeout: 120_000 });
    await expect(page.getByTestId('run-btn')).toBeEnabled();
    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('submit-message')).toContainText('Run created: run-', { timeout: 60_000 });
    const runId = ((await page.getByTestId('submit-message').textContent()) ?? '').replace(/.*Run created: /, '').trim();
    v4Save({ offRunId: runId });
    await expect.poll(async () => (await v4Api<{ status: string }>(request, `/api/v1/runs/${runId}`)).status, { timeout: 15 * 60_000, intervals: [2_000] }).toMatch(/^(COMPLETED|FAILED|CANCELLED)$/);
    expect((await v4Api<{ status: string }>(request, `/api/v1/runs/${runId}`)).status).toBe('COMPLETED');
    await v4LoadRun(page, runId);
    await openScreen(page, 'monitor');
    const gen = page.getByTestId('generated-candidates');
    await expect(gen).toBeVisible({ timeout: 60_000 });
    const genRows = await gen.locator('tbody tr').count();
    const genBadges = await gen.getByText('UNTRAINED · UNEVALUATED').count();
    await gen.scrollIntoViewIfNeeded();
    await v4Shot(page, '11-generated-table');
    await openScreen(page, 'lineage');
    const lin = page.getByTestId('lineage-table');
    await expect(lin).toBeVisible();
    const linBadges = await lin.getByTestId('lineage-untrained').count();
    v4Note('V4-11', { run_id: runId, generated_rows: genRows, generated_table_badges: genBadges, lineage_badges: linBadges });
    expect(genRows).toBeGreaterThan(0);
    expect(genBadges).toBe(genRows);
    expect(linBadges).toBe(genRows);
    await v4Shot(page, '11-lineage');
    await v4Axe(page, 'lineage');
  });

  test('V4-12 stop the core process: the UI shows degraded + an explicit failure (no fallback)', async ({ page }) => {
    const pid = Number(process.env.MBNAS_E2E_STACK_PID ?? '0');
    expect(pid, 'MBNAS_E2E_STACK_PID of web_real_stack.py').toBeGreaterThan(0);
    await v4Connect(page);
    await expect(page.getByText('status: ok')).toBeVisible();
    process.kill(pid, 'SIGUSR1'); // web_real_stack.py stops ONLY its core process
    await expect.poll(async () => {
      await page.getByTestId('connect-btn').click();
      return (await page.getByText(/status: (ok|degraded)/).first().textContent()) ?? '';
    }, { timeout: 90_000, intervals: [2_000] }).toContain('degraded');
    await v4Shot(page, '12-core-down-degraded');
    await page.getByTestId('validate-btn').click();
    const err = page.locator('[role="alert"], .banner').filter({ hasText: /CORE_UNAVAILABLE|503/ }).first();
    await expect(err).toBeVisible({ timeout: 60_000 });
    v4Note('V4-12', { error_text: await err.textContent(), validation_result_nodes: await page.getByTestId('validation-result').count() });
    // no fallback: no local validation verdict is shown while the core is down
    await expect(page.getByTestId('validation-result').filter({ hasText: /\bVALID\b/ })).toHaveCount(0);
    await expect(page.getByTestId('run-btn')).toBeDisabled();
    await err.scrollIntoViewIfNeeded();
    await v4Shot(page, '12-core-down-explicit-error');
  });
});

test.describe('legacy GPU flow (MBNAS_E2E_GPU=1)', () => {
  test.describe.configure({ mode: 'serial' });
  test.skip(!GPU, 'legacy GPU flow is opt-in: set MBNAS_E2E_GPU=1 (starts real GPU work)');

  test('web -> API -> GPU: validate, run, live progress, first point, pause/resume, completion', async ({ page, request }) => {
    test.skip(process.env.MBNAS_E2E_RUN_ID !== undefined, 'MBNAS_E2E_RUN_ID set: only the completed-run checks run');
    const requests: string[] = [];
    page.on('request', (r) => requests.push(r.url()));

    // Server: health + registry
    await connect(page);
    await expect(page.getByTestId('registry-table')).toContainText('etri-controlled-cnn-transformer');
    await expect(page.getByTestId('registry-table')).toContainText('rf-detr-large');
    await shot(page, 'server_health');

    // preset -> Validate -> resolved diff + hash -> Run
    await page.getByTestId('load-presets-btn').click();
    await page.getByTestId('presets-table').locator('tr', { hasText: 'PILOT_SMOKE' }).getByRole('button', { name: 'Use in editor' }).click();
    await expect(page.getByTestId('run-btn')).toBeDisabled();
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('validation-result')).toContainText('VALID', { timeout: 60_000 });
    await expect(page.getByTestId('protocol-hash')).toHaveText(/^sha256:[0-9a-f]{64}$/);
    await expect(page.getByTestId('resolved-diff')).toContainText('training.fixed_horizon_epochs');
    await expect(page.getByTestId('resolved-diff')).toContainText('console.benchmark.device_resolved');
    await shot(page, 'validated');
    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('submit-message')).toContainText('Run created: run-', { timeout: 60_000 });
    runId = ((await page.getByTestId('submit-message').textContent()) ?? '').replace(/.*Run created: /, '').trim();
    expect(runId).toMatch(/^[a-z]+-[0-9a-f]+$/);

    // Monitor: progress rows from the live worker, then the first evaluated point
    await openScreen(page, 'monitor');
    await expect(page.getByTestId('candidate-table')).toContainText(/TRAINING|EVALUATED/, { timeout: 10 * 60_000 });
    await expect(page.getByTestId('worker-table')).toContainText('busy', { timeout: 10 * 60_000 });
    await shot(page, 'monitor_progress');
    await expect(page.getByTestId('chart-fitness').getByTestId('chart-point').first()).toBeVisible({ timeout: 15 * 60_000 });
    await shot(page, 'first_point');

    // pause / resume through the UI; the worker confirms PAUSED at a safe boundary
    await openScreen(page, 'server');
    await expect(page.getByTestId('pause-btn')).toBeEnabled({ timeout: 10 * 60_000 });
    await page.getByTestId('control-reason').fill('real-server e2e pause');
    await page.getByTestId('pause-btn').click();
    await runStatusIs(page, /^(PAUSE_REQUESTED|PAUSED)$/, 30_000);
    await runStatusIs(page, /^PAUSED$/, 10 * 60_000);
    await shot(page, 'paused');
    await openScreen(page, 'settings');
    await expect(page.getByTestId('settings-fieldset')).toHaveAttribute('data-locked', 'true');
    await openScreen(page, 'server');
    await expect(page.getByTestId('resume-btn')).toBeEnabled();
    await page.getByTestId('resume-btn').click();
    await runStatusIs(page, /^RUNNING$/, 60_000);
    await shot(page, 'resumed');

    // completion (bounded): PILOT_SMOKE finishes in minutes on the A100
    await expect.poll(async () => (await api<{ status: string }>(request, `/api/v1/runs/${runId}`)).status, { timeout: 60 * 60_000, intervals: [10_000] }).toMatch(/^(COMPLETED|FAILED|CANCELLED)$/);
    expect((await api<{ status: string }>(request, `/api/v1/runs/${runId}`)).status).toBe('COMPLETED');
    await runStatusIs(page, /^COMPLETED$/, 60_000);
    await shot(page, 'completed');
    expect(requests.filter((u) => u.includes(TOKEN))).toEqual([]);
  });

  test('completed real run: generation lines, crossover lineage + four artifacts, reload restores, export zip', async ({ page, request }) => {
    const requests: string[] = [];
    page.on('request', (r) => requests.push(r.url()));
    runId = process.env.MBNAS_E2E_RUN_ID ?? runId;
    expect(runId, 'run created by the previous test or MBNAS_E2E_RUN_ID').toMatch(/^run-[0-9a-f]+$/);
    expect((await api<{ status: string }>(request, `/api/v1/runs/${runId}`)).status).toBe('COMPLETED');
    await connect(page);
    await loadRun(page, runId);

    // generation lines (dotted = closed generation, solid = representatives) in the GA cohort
    const snap = await api<{ candidates: { id: string; candidate_id: string; arm: string; generation: number; rung: string; status: string }[] }>(request, `/api/v1/runs/${runId}/snapshot`);
    const gaG1 = snap.candidates.filter((c) => c.arm === 'GA' && c.generation === 1 && c.rung === 'L0' && c.status === 'EVALUATED');
    let crossover: { id: string; candidate_id: string } | null = null;
    for (const c of gaG1) {
      const d = await api<{ parents: string[]; lineage: { operation?: string } }>(request, `/api/v1/runs/${runId}/candidates/${encodeURIComponent(c.candidate_id)}`);
      if (d.lineage?.operation === 'crossover' && d.parents.length === 2) {
        crossover = c;
        break;
      }
    }
    expect(crossover, 'a GA generation-1 crossover child with two parents').not.toBeNull();
    await openScreen(page, 'monitor');
    await selectCohortContaining(page, crossover!.id);
    await expect(page.getByTestId('chart-fitness').getByTestId('line-generation').first()).toBeAttached();
    await expect(page.getByTestId('chart-fitness').getByTestId('line-representative').first()).toBeAttached();
    await expect(page.getByTestId('generation-table')).toContainText('complete (closed, pending 0)');
    await shot(page, 'generation_lines');
    const before = (await pointIds(page)).length;

    // keyboard-open the crossover point: parents A/B, blocks, four real artifacts via signed <img>
    const point = page.getByTestId('chart-fitness').locator(`[data-eval-id="${crossover!.id}"]`);
    await point.focus();
    await page.keyboard.press('Enter');
    const dlg = page.getByRole('dialog');
    await expect(dlg).toBeVisible();
    await expect(dlg.getByTestId('lineage-op')).toContainText(/crossover/i);
    await expect(dlg.getByText('Parent A', { exact: true })).toBeVisible();
    await expect(dlg.getByText('Parent B', { exact: true })).toBeVisible();
    await expect(dlg.getByRole('region', { name: /Blocks/ })).toBeVisible();
    await expectFourImages(page, 'signed');
    await shot(page, 'modal_crossover_artifacts');
    await page.keyboard.press('Escape');
    await expect(dlg).toBeHidden();
    await expect(point).toBeFocused();

    // reload: token is gone (memory only); reconnect and load by id -> the snapshot restores the same points
    await page.reload();
    await openScreen(page, 'server');
    await expect(page.getByLabel(/Bearer token/)).toHaveValue('');
    await connect(page);
    await loadRun(page, runId);
    await openScreen(page, 'monitor');
    await selectCohortContaining(page, crossover!.id);
    expect((await pointIds(page)).length).toBe(before);
    await shot(page, 'reload_restored');

    // export downloads a zip
    await openScreen(page, 'server');
    const dl = page.waitForEvent('download');
    await page.getByTestId('export-btn').click();
    const d = await dl;
    expect(d.suggestedFilename()).toBe(`mbnas-export-${runId}.zip`);
    const path = `test-results/real/${d.suggestedFilename()}`;
    await d.saveAs(path);
    expect(readFileSync(path).subarray(0, 2).toString()).toBe('PK');
    await expect(page.getByText(/Export: Downloaded \d+ bytes/)).toBeVisible();

    // token hygiene on the real flow
    expect(requests.filter((u) => u.includes(TOKEN))).toEqual([]);
    const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }) + document.cookie);
    expect(stored).not.toContain(TOKEN);
  });

  test('explicit API origin (CORS) without signing key: four artifacts load as Blob via bearer fetch', async ({ page, request }) => {
    test.skip(!XORIGIN, 'set MBNAS_E2E_XORIGIN_API to a second API process without MBNAS_ARTIFACT_SIGNING_KEY');
    const id = runId || (await api<{ runs: { id: string; status: string }[] }>(request, '/api/v1/runs')).runs.find((r) => r.status === 'COMPLETED')?.id;
    expect(id).toBeTruthy();
    const artifactRequests: { url: string; auth: string | undefined }[] = [];
    page.on('request', (r) => {
      if (r.url().includes('/artifacts/')) artifactRequests.push({ url: r.url(), auth: r.headers()['authorization'] });
    });
    await connect(page, XORIGIN);
    await loadRun(page, id!);
    await openScreen(page, 'monitor');
    const snap = await api<{ candidates: { id: string; arm: string; status: string; artifacts?: { kind: string }[] }[] }>(request, `/api/v1/runs/${id}/snapshot`);
    const row = snap.candidates.find((c) => c.arm === 'GA' && c.status === 'EVALUATED' && KINDS.every((k) => (c.artifacts ?? []).some((a) => a.kind === k)));
    expect(row).toBeTruthy();
    await selectCohortContaining(page, row!.id);
    const point = page.getByTestId('chart-fitness').locator(`[data-eval-id="${row!.id}"]`);
    await point.focus();
    await page.keyboard.press('Enter');
    await expectFourImages(page, 'blob');
    await shot(page, 'bearer_blob_artifacts');
    const origin = new URL(XORIGIN).origin;
    expect(artifactRequests.length).toBeGreaterThanOrEqual(4);
    for (const r of artifactRequests) {
      expect(new URL(r.url).origin).toBe(origin);
      expect(r.auth).toBe(`Bearer ${TOKEN}`);
    }
    await page.keyboard.press('Escape');
  });

  test('mobile 375px: the completed real run reflows and its point dialog opens', async ({ page, request }) => {
    const id = runId || (await api<{ runs: { id: string; status: string }[] }>(request, '/api/v1/runs')).runs.find((r) => r.status === 'COMPLETED')?.id;
    expect(id).toBeTruthy();
    await page.setViewportSize({ width: 375, height: 812 });
    await connect(page);
    await loadRun(page, id!);
    await openScreen(page, 'monitor');
    await expect(page.getByTestId('chart-fitness').getByTestId('chart-point').first()).toBeVisible();
    const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    expect(m.sw).toBeLessThanOrEqual(m.cw);
    await shot(page, 'mobile_monitor');
    // real smoke points overlap (AP 0, similar latency), so open the point the accessible way: focus + Enter
    await page.getByTestId('chart-fitness').getByTestId('chart-point').first().focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog')).toBeVisible();
    await shot(page, 'mobile_dialog');
  });

  type FinalRow = { id: string; candidate_id: string; arm: string; rung: string; status: string; comparison_group: string; split_hash: string };
  type RunBody = { id: string; status: string; kind?: string; parent_run_id?: string | null; finalists?: { candidate_id: string; source_evaluation_id: string; selection_rule: string }[]; resolved_protocol: { dataset: { final_evaluation_manifest_hash: string } }; final_retrain_runs?: { id: string }[]; costs?: { final_gpu_seconds?: number | null } };

  test('final retraining from the browser: panel -> child run trains on the GPU -> final cohort alone -> lineage final', async ({ page, request }) => {
    test.skip(!FINAL, 'set MBNAS_E2E_FINAL=1: this test starts a real FINAL_RETRAIN child run on the GPU');
    const requests: string[] = [];
    page.on('request', (r) => requests.push(r.url()));
    const parentId = process.env.MBNAS_E2E_FINAL_PARENT_ID ?? process.env.MBNAS_E2E_RUN_ID ?? runId;
    expect(parentId, 'a COMPLETED search run').toMatch(/^run-[0-9a-f]+$/);
    const parent = await api<RunBody>(request, `/api/v1/runs/${parentId}`);
    expect(parent.status).toBe('COMPLETED');
    expect(parent.kind ?? 'SEARCH').toBe('SEARCH');
    const childrenBefore = (parent.final_retrain_runs ?? []).map((c) => c.id);

    // Server: the panel is offered for the COMPLETED search run; budget is required; top-k comes from the protocol
    await connect(page);
    await loadRun(page, parentId);
    const panel = page.getByTestId('final-retrain-panel');
    await expect(panel).toBeVisible();
    await expect(page.getByTestId('final-topk')).toHaveValue(/^[1-9]\d*$/);
    await expect(page.getByTestId('final-run-btn')).toBeDisabled();
    await page.getByTestId('final-budget').fill(FINAL_BUDGET);
    await page.getByTestId('final-reason').fill('real-server e2e final retrain');
    await expect(page.getByTestId('final-run-btn')).toBeEnabled();
    await page.locator('#final-retrain').scrollIntoViewIfNeeded();
    await shot(page, 'final_panel');
    await page.getByTestId('final-run-btn').click();
    await expect(page.getByTestId('final-child-id')).toHaveText(/^run-[0-9a-f]+$/, { timeout: 60_000 });
    const childId = ((await page.getByTestId('final-child-id').textContent()) ?? '').trim();
    expect(childrenBefore).not.toContain(childId);
    const count = Number((await page.getByTestId('final-finalist-count').textContent()) ?? '0');
    expect(count).toBeGreaterThanOrEqual(1);
    await page.locator('#final-retrain').scrollIntoViewIfNeeded();
    await shot(page, 'final_created');

    // the server created a FINAL_RETRAIN child of this parent with the frozen finalists
    const created = await api<RunBody>(request, `/api/v1/runs/${childId}`);
    expect(created.kind).toBe('FINAL_RETRAIN');
    expect(created.parent_run_id).toBe(parentId);
    expect(created.finalists ?? []).toHaveLength(count);
    for (const f of created.finalists ?? []) expect(f.selection_rule).toMatch(/^mbnas-final-select\/1:/);

    // Load child run: kind badge, parent link, finalists table; no Final Retrain panel on a child
    await page.getByTestId('final-load-child').click();
    await expect(controlCard(page)).toContainText(childId);
    await expect(page.getByTestId('run-kind-row')).toContainText('kind: FINAL_RETRAIN');
    await expect(page.getByTestId('parent-run-link')).toContainText(parentId);
    const finalists = page.getByTestId('finalists-table');
    await expect(finalists).toBeVisible();
    for (const f of created.finalists ?? []) await expect(finalists).toContainText(f.source_evaluation_id);
    await expect(page.getByTestId('final-retrain-panel')).toHaveCount(0);

    // the child trains on the GPU (2 scratch epochs per finalist in PILOT_SMOKE) and completes (bounded)
    await expect.poll(async () => (await api<RunBody>(request, `/api/v1/runs/${childId}`)).status, { timeout: 45 * 60_000, intervals: [10_000] }).toMatch(/^(COMPLETED|FAILED|CANCELLED)$/);
    const done = await api<RunBody>(request, `/api/v1/runs/${childId}`);
    expect(done.status).toBe('COMPLETED');
    expect(done.costs?.final_gpu_seconds ?? 0).toBeGreaterThan(0);
    await runStatusIs(page, /^COMPLETED$/, 60_000);
    await page.locator('#finalists').scrollIntoViewIfNeeded();
    await shot(page, 'final_child_server');

    // final rows: rung FINAL / comparison_group final / arm Final / final split hash
    const snap = await api<{ candidates: FinalRow[] }>(request, `/api/v1/runs/${childId}/snapshot`);
    const rows = snap.candidates.filter((c) => c.status === 'EVALUATED');
    expect(rows.length).toBe(count);
    for (const r of rows) {
      expect([r.arm, r.rung, r.comparison_group]).toEqual(['Final', 'FINAL', 'final']);
      expect(r.split_hash).toBe(parent.resolved_protocol.dataset.final_evaluation_manifest_hash);
    }

    // Monitor: the final cohort is drawn alone (every drawn point is a final row of this child)
    await openScreen(page, 'monitor');
    await selectCohortContaining(page, rows[0].id);
    await expect(page.getByTestId('final-cohort-note')).toBeVisible();
    await expect(page.getByTestId('cohort-select')).toContainText('FINAL');
    const drawn = await pointIds(page);
    expect(drawn.length).toBeGreaterThan(0);
    const finalIds = new Set(rows.map((r) => r.id));
    expect(drawn.filter((id) => !finalIds.has(id))).toEqual([]);
    await shot(page, 'final_monitor');

    // point dialog (keyboard): lineage operation 'final' with the finalist source as the single parent
    const point = page.getByTestId('chart-fitness').locator(`[data-eval-id="${rows[0].id}"]`);
    await point.focus();
    await page.keyboard.press('Enter');
    const dlg = page.getByRole('dialog');
    await expect(dlg).toBeVisible();
    await expect(dlg.getByTestId('lineage-op')).toContainText(/final/i);
    await expect(dlg.getByText('Source candidate (finalist)')).toBeVisible();
    await expect(dlg.getByTestId('lineage-parent')).toHaveCount(1);
    await shot(page, 'final_modal_lineage');
    await page.keyboard.press('Escape');
    await expect(dlg).toBeHidden();

    // History: parent/child relation from explicit facts; the parent lists the child
    await openScreen(page, 'history');
    await expect(page.getByTestId('related-runs')).toContainText(parentId);
    await shot(page, 'final_history');
    const parentAfter = await api<RunBody>(request, `/api/v1/runs/${parentId}`);
    expect((parentAfter.final_retrain_runs ?? []).map((c) => c.id)).toContain(childId);
    expect(parentAfter.costs?.final_gpu_seconds ?? 0).toBeGreaterThanOrEqual(done.costs?.final_gpu_seconds ?? 0);

    // back to the parent from the child
    await openScreen(page, 'server');
    await page.getByTestId('load-parent-btn').click();
    await expect(controlCard(page)).toContainText(parentId);
    await expect(page.getByTestId('final-retrain-panel')).toBeVisible();
    expect(requests.filter((u) => u.includes(TOKEN))).toEqual([]);
  });
});
