/**
 * REAL-SERVER browser E2E for the NAS archive screens (NOT mocked): Chromium -> production build (vite preview,
 * same-origin /api proxy) -> live MBNAS API -> real run artifacts of the ETRI_NAS pipeline (read-only).
 *
 * Opt-in: MBNAS_E2E_REAL=1 and MBNAS_E2E_TOKEN=<gateway MBNAS_GW_API_TOKEN> (environment only, never hardcoded, never
 * typed into a screenshot: the token input is a password field). Run with `npm run e2e:archive-real`
 * (playwright.archive-real.config.ts). Optional MBNAS_E2E_ARCHIVE_RUN pins the run id (default: the VisDrone smoke run).
 *
 * Flow (design section 6, C4): token -> archive -> select run -> baseline table -> click generation 3 -> generation
 * detail -> click a candidate -> modal -> jump to a parent -> compare screen -> deep link survives a reload.
 * The target candidate is chosen from the live API (a generation-3 candidate that has parents), so the UI steps do not
 * depend on hard-coded ids. Screenshots: evidence/real_archive_*.png. Zero console errors / page errors are asserted.
 *
 * Locators are the data-testid values of the archive screens (run-pick-<id>, baseline-table, gen-open-<g>, cand-open-<cid>,
 * acand-title, parent-link-A, compare-check-<id>, chart-cmp-best). Verified against the integrated UI on 2026-10-15
 * (impl_log/INTEGRATION_2026-10-15.md).
 */
import { captureEvidence } from './mock';
import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';

const TOKEN = process.env.MBNAS_E2E_TOKEN ?? '';
const REAL = process.env.MBNAS_E2E_REAL === '1';
const RUN_PIN = process.env.MBNAS_E2E_ARCHIVE_RUN ?? '';
const GEN = 3;
/** screenshots of a pinned NPS run get an `_nps` suffix so they do not overwrite the VisDrone ones */
const SUFFIX = /drone26/i.test(RUN_PIN) ? '_nps' : '';

test.skip(!REAL || !TOKEN, 'real-server archive E2E is opt-in: set MBNAS_E2E_REAL=1 and MBNAS_E2E_TOKEN');

interface RunSummary {
  id: string;
  family?: string;
}
interface CandidateRow {
  candidate_id: string;
  status?: string;
  lineage?: { parents?: string[] };
}

async function api<T>(request: APIRequestContext, path: string): Promise<T> {
  const r = await request.get(path, { headers: { Authorization: `Bearer ${TOKEN}` } });
  expect(r.status(), `GET ${path}`).toBe(200);
  return (await r.json()) as T;
}

/** Full-page capture, except while a (fixed-position) dialog is open: then the viewport shows it as the user sees it. */
const shot = async (page: Page, name: string): Promise<void> => {
  const h = await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio);
  const dialog = await page.getByRole('dialog').isVisible();
  await captureEvidence(page, `evidence/real_archive_${name}${SUFFIX}.png`, !dialog && h < 12000);
};

async function openScreen(page: Page, id: 'archive' | 'compare' | 'server'): Promise<void> {
  const menu = page.getByTestId('menu-btn');
  if (await menu.isVisible()) {
    if ((await menu.getAttribute('aria-expanded')) !== 'true') await menu.click();
  }
  await page.getByTestId(`nav-${id}`).click();
}

/** Enter the token on whichever connect form is shown (archive gate first, Server screen as the fallback). */
async function connect(page: Page): Promise<void> {
  const gate = page.getByTestId('gate-token');
  if (await gate.isVisible().catch(() => false)) {
    await gate.fill(TOKEN);
    await page.getByTestId('gate-connect').click();
    await expect(gate).toBeHidden();
    return;
  }
  await openScreen(page, 'server');
  await page.getByLabel(/Bearer token/).fill(TOKEN);
  await page.getByTestId('connect-btn').click();
  await expect(page.getByText(/status: (ok|degraded)/)).toBeVisible();
  await openScreen(page, 'archive');
}

/** Pick a run in the run table. */
const runPick = (page: Page, runId: string): Locator => page.getByTestId(`run-pick-${runId}`);

test('NAS archive: token -> run -> baseline -> generation 3 -> candidate modal -> parent -> compare (real data)', async ({ page, request }) => {
  const consoleErrors: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(`console.error: ${m.text()}`);
  });
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));

  // ---- ground truth from the live API (also proves the server reads the real artifacts) ----------------------------
  const runs = (await api<{ runs: RunSummary[] }>(request, '/api/v1/archive/runs')).runs;
  expect(runs.length, 'archive run list is not empty').toBeGreaterThan(0);
  const run = RUN_PIN ? runs.find((r) => r.id === RUN_PIN) : runs.find((r) => /visdrone/i.test(r.id) && /smoke/i.test(r.id));
  expect(run, `target run (${RUN_PIN || 'VisDrone smoke'}) is listed by the API`).toBeTruthy();
  const runId = run!.id;
  const genDetail = await api<{ candidates: CandidateRow[] }>(request, `/api/v1/archive/runs/${encodeURIComponent(runId)}/generations/${GEN}`);
  // parents of seeded / random-crossover candidates are config file names, not candidates: take one whose first parent is a candidate id
  const target = genDetail.candidates.find((c) => c.status === 'ok' && /^g\d{3}_c\d+$/.test(c.lineage?.parents?.[0] ?? ''));
  expect(target, `generation ${GEN} has an ok candidate whose parent is a candidate`).toBeTruthy();
  const cid = target!.candidate_id;
  const parentId = target!.lineage!.parents![0]!;

  await test.step('token -> archive screen loads the run list', async () => {
    await page.goto('/#/archive');
    await connect(page);
    await expect(page.getByText(runId).first()).toBeVisible();
    await shot(page, '01_run_list');
  });

  await test.step('select the run -> overview and baseline table are shown', async () => {
    await runPick(page, runId).click();
    await expect(page).toHaveURL(new RegExp(`run=${runId}`));
    const baseline = page.getByTestId('baseline-table');
    await expect(baseline).toBeVisible();
    await expect(baseline).toContainText(/yolo26n/i);
    await expect(baseline).toContainText(/final|T@|T1|epoch|ep\b/i); // the tier label must always be shown with baseline numbers
    await expect(page.getByTestId('judge-baseline-row')).toBeVisible();
    await expect(page.getByTestId('generation-table')).toBeVisible();
    await shot(page, '02_run_baseline');
  });

  await test.step(`click generation ${GEN} -> generation detail`, async () => {
    const g = page.getByTestId(`gen-open-${GEN}`);
    await g.scrollIntoViewIfNeeded();
    await g.click();
    await expect(page).toHaveURL(new RegExp(`gen=${GEN}\\b`));
    await expect(page.getByTestId('gen-detail')).toBeVisible();
    await expect(page.getByTestId('module-hist')).toBeVisible();
    await shot(page, `03_generation_${GEN}`);
  });

  await test.step('click a candidate -> detail modal', async () => {
    const row = page.getByTestId(`cand-open-${cid}`).first();
    await row.scrollIntoViewIfNeeded();
    await row.click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByTestId('acand-title')).toContainText(cid);
    await expect(page).toHaveURL(new RegExp(`cid=${cid}`));
    await shot(page, '04_candidate_modal');
  });

  await test.step('jump to a parent from the lineage section', async () => {
    const link = page.getByTestId('parent-link-A');
    await expect(link).toContainText(parentId);
    await link.click();
    await expect(page).toHaveURL(new RegExp(`cid=${parentId}`));
    await expect(page.getByTestId('acand-title')).toContainText(parentId);
    await shot(page, '05_parent_modal');
  });

  await test.step('close the modal with Escape (focus returns to the page)', async () => {
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  await test.step('compare screen renders for the two VisDrone runs', async () => {
    await openScreen(page, 'compare');
    await expect(page.getByTestId('nav-compare')).toHaveAttribute('aria-current', 'page');
    const sibling = runs.find((r) => r.id !== runId && r.family === run!.family && !/FAILED_/.test(r.id));
    // the screen lists one dataset at a time (first alphabetically: nps_drones): switch to the dataset of the target run
    const datasetId = (await api<{ dataset_id?: string | null }>(request, `/api/v1/archive/runs/${encodeURIComponent(runId)}`)).dataset_id;
    expect(datasetId, 'target run has a dataset_id').toBeTruthy();
    await page.getByTestId('compare-dataset').selectOption(datasetId!);
    await page.getByTestId(`compare-check-${runId}`).check();
    if (sibling) await page.getByTestId(`compare-check-${sibling.id}`).check();
    await expect(page.getByTestId('chart-cmp-best')).toBeVisible();
    await expect(page.getByTestId('compare-baseline-table')).toBeVisible();
    await shot(page, '06_compare');
  });

  await test.step('deep link survives a reload (token is re-entered; run, generation and candidate are restored)', async () => {
    await page.goto(`/#/archive?run=${encodeURIComponent(runId)}&gen=${GEN}&cid=${encodeURIComponent(cid)}`);
    await page.reload();
    await connect(page);
    await expect(page.getByTestId('acand-title')).toContainText(cid);
    await expect(page).toHaveURL(new RegExp(`gen=${GEN}\\b`));
    await expect(page.getByTestId('gen-detail')).toBeAttached();
    await shot(page, '07_deeplink_restored');
  });

  // The token must never reach the URL or the storage.
  const leak = await page.evaluate((t) => ({ url: location.href.includes(t), local: JSON.stringify({ ...localStorage }).includes(t), session: JSON.stringify({ ...sessionStorage }).includes(t) }), TOKEN);
  expect(leak, 'token is not present in URL / localStorage / sessionStorage').toEqual({ url: false, local: false, session: false });

  expect(consoleErrors, 'zero console errors / page errors during the whole flow').toEqual([]);
});
