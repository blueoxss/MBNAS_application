/** Mobile-only checks (375px): menu disclosure, vertical stacking. SYNTHETIC mocked API. */
import { expect, test } from '@playwright/test';
import { captureEvidence, connectAndLoad, installMock, openScreen, RUN_ID, stateOf, TOKEN } from './mock';

test('menu disclosure: closed list is inert and hidden, button toggles aria-expanded, selecting closes the menu', async ({ page }) => {
  await installMock(page, stateOf());
  await page.goto('/#/server');
  const btn = page.getByTestId('menu-btn');
  await expect(btn).toBeVisible();
  await expect(btn).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('#main-nav')).toHaveAttribute('inert', '');
  await expect(page.getByTestId('nav-monitor')).toBeHidden();
  await captureEvidence(page, 'evidence/mobile-375-menu-closed.png', false);
  await btn.click();
  await expect(btn).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByTestId('nav-monitor')).toBeVisible();
  const box = (await page.getByTestId('nav-monitor').boundingBox())!;
  expect(box.height).toBeGreaterThanOrEqual(44);
  await captureEvidence(page, 'evidence/mobile-375-menu-open.png', false);
  await page.getByTestId('nav-settings').click();
  await expect(btn).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('실험 설정');
});

test('cards stack vertically and chart points are touch-sized', async ({ page }) => {
  await installMock(page, stateOf());
  await connectAndLoad(page);
  await page.getByTestId('menu-btn').click();
  await page.getByTestId('nav-monitor').click();
  const cards = page.locator('main .card');
  const n = await cards.count();
  let prevBottom = -1;
  for (let i = 0; i < n; i++) {
    const b = (await cards.nth(i).boundingBox())!;
    expect(b.y + 1).toBeGreaterThanOrEqual(prevBottom - 1);
    expect(b.width).toBeLessThanOrEqual(375);
    prevBottom = b.y + b.height;
  }
  const hit = await page.getByTestId('chart-point').first().locator('.hit').evaluate((el) => parseFloat(getComputedStyle(el).r));
  expect(hit * 2).toBeGreaterThanOrEqual(44);
  const h = await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio);
  await captureEvidence(page, 'evidence/mobile-375-monitor-full.png', h < 12000);
});

test('menu disclosure works from the keyboard: Enter/Space toggle, Tab enters the list, Escape closes and refocuses the button', async ({ page }) => {
  await installMock(page, stateOf());
  await page.goto('/#/server');
  const btn = page.getByTestId('menu-btn');
  await btn.focus();
  await page.keyboard.press('Enter');
  await expect(btn).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#main-nav')).not.toHaveAttribute('inert', '');
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => document.activeElement?.getAttribute('data-testid'))).toBe('nav-archive');
  await page.keyboard.press('Escape');
  await expect(btn).toHaveAttribute('aria-expanded', 'false');
  await expect(btn).toBeFocused();
  await expect(page.locator('#main-nav')).toHaveAttribute('inert', '');
  await page.keyboard.press('Space');
  await expect(btn).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter'); // fourth item (archive, compare, monitor, settings): Settings
  await expect(page.getByRole('heading', { level: 1 })).toContainText('실험 설정');
  await expect(btn).toHaveAttribute('aria-expanded', 'false');
});

test('S11-F1: no horizontal page scroll on any screen with a 71-char commit_sha, and a tap on Load by id hits that button', async ({ page }) => {
  const sha = `sha256:${'a1b2c3d4'.repeat(8)}`.slice(0, 71);
  expect(sha).toHaveLength(71);
  await installMock(page, stateOf({ healthOverride: { commit_sha: sha } }));
  await page.goto('/#/server');
  await page.getByLabel(/Bearer token/).fill(TOKEN);
  await page.getByTestId('connect-btn').click();
  await page.getByText('status: ok').waitFor();
  await expect(page.getByTestId('health-commit')).toHaveText(sha);
  const noScroll = async (label: string): Promise<void> => {
    const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bw: document.body.scrollWidth }));
    expect(m.sw, `${label}: documentElement scrollWidth`).toBeLessThanOrEqual(m.cw);
    expect(m.bw, `${label}: body scrollWidth`).toBeLessThanOrEqual(m.cw);
  };
  await noScroll('server (connected)');
  // device-targets stays readable inside its own scroll wrapper
  const wrap = page.getByTestId('device-targets-table');
  if (await wrap.count()) {
    const w = await wrap.locator('table').evaluate((t) => t.getBoundingClientRect().width);
    expect(w).toBeGreaterThanOrEqual(400);
  }
  // tap hits Load by id, not a neighbour
  await page.getByTestId('run-id-input').fill(RUN_ID);
  const btn = page.getByTestId('load-run-btn');
  await btn.scrollIntoViewIfNeeded();
  const hit = await btn.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const t = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return !!t && (t === el || el.contains(t));
  });
  expect(hit).toBe(true);
  await btn.tap();
  await page.getByTestId('pause-btn').waitFor();
  await noScroll('server (run loaded)');
  for (const id of ['archive', 'compare', 'monitor', 'settings', 'lineage', 'history'] as const) {
    await openScreen(page, id);
    await noScroll(id);
  }
  await openScreen(page, 'server');
  await noScroll('server (again)');
});
