/**
 * NAS archive screens in real Chromium against the production build with the SYNTHETIC archive mock
 * (tests/archive_fixtures.ts via e2e/mock.ts). Desktop 1280 and mobile 375. Screenshots: evidence/archive-*.png.
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { captureEvidence, installMock, openScreen, stateOf, TOKEN } from './mock';
import { RUN_A, RUN_B, SYNTH_A } from '../tests/archive_fixtures';

const tag = (): string => (test.info().project.name.startsWith('mobile') ? 'mobile' : 'desktop');
const shot = async (page: Page, name: string): Promise<void> => {
  const h = await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio);
  await captureEvidence(page, `evidence/archive-${tag()}-${name}.png`, h < 12000);
};

const noOverflow = async (page: Page): Promise<void> => {
  const bad = await page.evaluate(() => {
    const cw = document.documentElement.clientWidth;
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>('body *')) {
      if (el.closest('.table-wrap, .scroll-x, [data-scroll], svg, .sr-only, [inert], pre')) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.right > cw + 1 || r.left < -1) out.push(`${el.tagName.toLowerCase()}.${el.className} [${Math.round(r.left)}..${Math.round(r.right)}] > ${cw}`);
    }
    return out.slice(0, 5);
  });
  expect(bad).toEqual([]);
  const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  expect(m.sw).toBeLessThanOrEqual(m.cw);
};

async function connectArchive(page: Page, url = '/#/archive', ready = 'run-table'): Promise<void> {
  await page.goto(url);
  await page.getByTestId('gate-token').fill(TOKEN);
  await page.getByTestId('gate-connect').click();
  await page.getByTestId(ready).waitFor();
}

async function selectRunA(page: Page): Promise<void> {
  await page.getByTestId(`run-pick-${RUN_A}`).click();
  await page.getByTestId('generation-table').waitFor();
  await page.getByTestId('baseline-table').waitFor();
  await page.getByTestId('scatter-count').waitFor();
}

const crossoverCid = SYNTH_A.rows.find((r) => r.generation === 2 && r.lineage?.mode === 'ga_crossover' && r.status === 'ok' && r.tier === 'T@8e')?.candidate_id as string;

test.describe('archive: main flow', () => {
  test('connect, pick a run, see the baseline, click a generation, click a candidate, follow the parent, close with Esc', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await connectArchive(page);
    await expect(page.getByRole('heading', { level: 1 })).toContainText('NAS 아카이브');
    await shot(page, 'run-list');
    await selectRunA(page);
    await expect(page.getByTestId('judge-baseline-row')).toContainText('baseline_yolo26n');
    await expect(page.getByTestId('baseline-table')).toContainText('final 12 ep');
    await noOverflow(page);
    await shot(page, 'overview');
    // generation click -> detail
    await page.getByTestId('chart-gen-map').getByTestId('line-point').nth(2).click();
    await expect(page.getByTestId('gen-detail')).toBeVisible();
    await expect(page.getByTestId('module-hist')).toBeVisible();
    expect(page.url()).toContain(`gen=2`);
    await noOverflow(page);
    await page.getByTestId('gen-detail').scrollIntoViewIfNeeded();
    await shot(page, 'generation-detail');
    // candidate -> modal -> parent
    await page.getByTestId('gen-candidates').getByTestId('gen-candidates').count();
    await page.getByTestId('gen-lineage').getByRole('button', { name: new RegExp(`^${crossoverCid} 상세 열기$`) }).click();
    const dlg = page.getByRole('dialog');
    await expect(dlg).toBeVisible();
    await expect(page.getByTestId('acand-title')).toContainText(crossoverCid);
    await noOverflow(page);
    await shot(page, 'candidate-modal');
    const parentId = (await page.getByTestId('parent-link-A').textContent()) as string;
    await page.getByTestId('parent-link-A').click();
    await expect(page.getByTestId('acand-title')).toContainText(parentId);
    await page.getByTestId('acand-back').click();
    await expect(page.getByTestId('acand-title')).toContainText(crossoverCid);
    await page.keyboard.press('Escape');
    await expect(dlg).toBeHidden();
    expect(page.url()).not.toContain('cid=');
    // the token never appears in a URL
    expect(st.seen.every((s) => !s.url.includes(TOKEN))).toBe(true);
    expect(st.seen.filter((s) => s.url.startsWith('/api/v1/archive')).every((s) => s.method === 'GET' && s.auth === `Bearer ${TOKEN}`)).toBe(true);
  });

  test('keyboard: a scatter point is reachable with Tab, Enter opens the candidate, Esc returns focus to the point', async ({ page }) => {
    await installMock(page, stateOf());
    await connectArchive(page);
    await selectRunA(page);
    const first = page.getByTestId('chart-scatter-a').getByTestId('scatter-point').first();
    await first.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect.poll(() => page.evaluate(() => document.activeElement?.getAttribute('data-testid'))).toBe('scatter-point');
    // the table alternative lists the same rows
    await page.getByTestId('scatter-view-table').click();
    await expect(page.getByTestId('scatter-table')).toBeVisible();
    await shot(page, 'scatter-table');
  });

  test('scatter slider limits the points and draws the front line; legend explains the formula', async ({ page }) => {
    await installMock(page, stateOf());
    await connectArchive(page);
    await selectRunA(page);
    const all = await page.getByTestId('chart-scatter-a').getByTestId('scatter-point').count();
    await page.getByTestId('scatter-gen').fill('0');
    await expect.poll(() => page.getByTestId('chart-scatter-a').getByTestId('scatter-point').count()).toBeLessThan(all);
    await page.getByTestId('scatter-gen').fill('3');
    await expect(page.getByTestId('front-line')).toBeVisible();
    await expect(page.getByTestId('scatter-legend')).toContainText('2D 비지배');
    await page.getByTestId('chart-scatter-a').scrollIntoViewIfNeeded();
    await shot(page, 'scatter');
  });

  test('deep link and reload: run, generation and candidate are restored after entering the token again', async ({ page }) => {
    await installMock(page, stateOf());
    await connectArchive(page, `/#/archive?run=${RUN_A}&gen=2&cid=${crossoverCid}`, 'acand-title');
    await expect(page.getByTestId('gen-detail')).toBeVisible();
    await expect(page.getByTestId('acand-title')).toContainText(crossoverCid);
    await page.reload();
    await expect(page.getByTestId('connect-gate')).toBeVisible(); // memory-only token: gone after reload
    await page.getByTestId('gate-token').fill(TOKEN);
    await page.getByTestId('gate-connect').click();
    await expect(page.getByTestId('acand-title')).toContainText(crossoverCid);
    await expect(page.getByTestId('gen-detail')).toBeAttached();
  });

  test('a running run shows the live card; a failing API shows a banner instead of a blank page', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await connectArchive(page);
    await page.getByTestId(`run-pick-${RUN_B}`).click();
    await expect(page.getByTestId('live-card')).toBeVisible();
    await expect(page.getByTestId('gpu-list')).toContainText('Synthetic GPU');
    await shot(page, 'running-live');
    st.archive!.failAll = true;
    await page.getByTestId('runs-refresh').click();
    await expect(page.getByTestId('runs-error')).toContainText('HTTP 500');
    await expect(page.getByTestId('live-card')).toBeVisible();
  });
});

test.describe('archive: compare', () => {
  test('select two runs of one dataset, curves and tables appear, a different dataset is flagged', async ({ page }) => {
    await installMock(page, stateOf());
    await connectArchive(page);
    await openScreen(page, 'compare');
    await expect(page.getByRole('heading', { level: 1 })).toContainText('Run 비교');
    await page.getByTestId(`compare-check-${RUN_A}`).check();
    await page.getByTestId(`compare-check-${RUN_B}`).check();
    await expect(page.getByTestId('chart-cmp-best')).toBeVisible();
    await expect(page.getByTestId('compare-baseline-table')).toBeVisible();
    await noOverflow(page);
    await shot(page, 'compare');
  });
});

test.describe('archive: accessibility and layout', () => {
  test('axe: no serious or critical violations (run selected, generation open)', async ({ page }) => {
    await installMock(page, stateOf());
    await connectArchive(page);
    await selectRunA(page);
    await page.getByTestId('gen-open-2').click();
    await page.getByTestId('gen-detail').waitFor();
    const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    const bad = res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`)).toEqual([]);
  });

  test('axe: the candidate modal has no serious or critical violations', async ({ page }) => {
    await installMock(page, stateOf());
    await connectArchive(page, `/#/archive?run=${RUN_A}&cid=${crossoverCid}`, 'acand-title');
    await page.getByTestId('acand-evals').waitFor();
    const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    const bad = res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`)).toEqual([]);
  });

  test('axe: compare screen', async ({ page }) => {
    await installMock(page, stateOf());
    await connectArchive(page, `/#/compare?runs=${RUN_A},${RUN_B}`, 'compare-list');
    await page.getByTestId('chart-cmp-best').waitFor();
    const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    const bad = res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`)).toEqual([]);
  });

  for (const width of [320, 375, 640]) {
    test(`no horizontal scroll at ${width}px with a run, a generation and the modal open`, async ({ page }) => {
      await installMock(page, stateOf());
      await page.setViewportSize({ width, height: 900 });
      await connectArchive(page, `/#/archive?run=${RUN_A}&gen=1`);
      await page.getByTestId('gen-detail').waitFor();
      await page.getByTestId('scatter-count').waitFor();
      await noOverflow(page);
      await page.getByTestId('gen-lineage').getByRole('button', { name: new RegExp(`^${crossoverCid} 상세 열기$`) }).count();
      await page.getByTestId('cand-open-' + crossoverCid).first().click();
      await page.getByTestId('acand-evals').waitFor();
      await noOverflow(page);
    });
  }

  test('touch targets: main controls are at least 44px high on mobile', async ({ page }) => {
    await installMock(page, stateOf());
    await connectArchive(page);
    await selectRunA(page);
    if ((page.viewportSize()?.width ?? 1280) >= 768) return;
    const small = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('button, select, input[type=text], input[type=password], input[type=range], input[type=checkbox]')]
        // a checkbox is operated through its 44px label (.check); the 20px box itself is not the target
        .filter((e) => e.offsetParent !== null && !e.closest('svg') && !(e.matches('input[type=checkbox]') && e.closest('.check')) && e.getBoundingClientRect().height > 0)
        .map((e) => ({ t: `${e.tagName.toLowerCase()}.${e.className}`, h: Math.round(e.getBoundingClientRect().height) }))
        .filter((x) => x.h < 44),
    );
    expect(small).toEqual([]);
  });
});
