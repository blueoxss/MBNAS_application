/**
 * Hardening browser tests (real Chromium, SYNTHETIC mocked API): keyboard-only flow, 422 rendering, idempotency
 * variants, policy lock transitions, SSE resync, artifact state machine, token/origin hygiene, zoom reflow,
 * reduced motion and axe with an open dialog. Runs on desktop-1280 and mobile-375.
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { CDN, connectAndLoad, installMock, openScreen, sse, stateOf, TOKEN, captureEvidence } from './mock';
import { cand, RUN_ID } from '../tests/fixtures';
import { syntheticGeneratedDetail } from '../tests/detail_fixtures';
import type { Artifact } from '../src/api/types';

const shot = async (page: Page, name: string): Promise<void> => {
  const h = await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio);
  await captureEvidence(page, `evidence/${test.info().project.name}-${name}.png`, h < 12000);
};

/** Strict overflow check: nothing outside a scroll wrapper may extend past the viewport (body has overflow-x hidden). */
const noOverflow = async (page: Page): Promise<void> => {
  const bad = await page.evaluate(() => {
    const cw = document.documentElement.clientWidth;
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>('body *')) {
      if (el.closest('.table-wrap, .scroll-x, [data-scroll], svg, .sr-only, [inert]')) continue;
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

const SCREENS = ['archive', 'compare', 'monitor', 'settings', 'lineage', 'history', 'server'] as const;

const json = (route: import('@playwright/test').Route, status: number, body: unknown): Promise<void> =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

const art = (id: string, kind: Artifact['kind'], over: Partial<Artifact>): Artifact => ({
  id,
  kind,
  url: `${CDN}/ok-${kind}.png`,
  auth_mode: 'signed_url',
  sample_id: 'synthetic-sample-777',
  evaluation_id: 'synthetic-ev-hard',
  state: 'READY',
  matching: { counts: { tp: 1, fp: 0, fn: 0, gt: 1 }, sample_recall: 1, rule: 'greedy_conf_desc_iou_v1', iou: 0.5, conf: 0.25 } as Artifact['matching'],
  ...over,
});

/** A candidate whose four artifacts are produced by `mk`. */
function candWith(name: string, mk: (kind: Artifact['kind']) => Artifact): ReturnType<typeof cand> {
  return cand({ id: `synthetic-ev-${name}`, candidate_id: `synthetic-cand-${name}`, name, generation: 0, artifacts: (['gt', 'tp', 'fn', 'fp'] as const).map((k) => ({ ...mk(k), evaluation_id: `synthetic-ev-${name}` })) });
}

async function openCandidate(page: Page, name: string): Promise<void> {
  await openScreen(page, 'monitor');
  await page.getByTestId('candidate-table').getByRole('button', { name: new RegExp(name) }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
}

async function connect(page: Page): Promise<void> {
  await page.goto('/#/server');
  await page.getByLabel(/Bearer token/).fill(TOKEN);
  await page.getByTestId('connect-btn').click();
}

test.describe('keyboard-only', () => {
  test('Tab alone reaches a chart point, Enter opens the dialog, Esc closes and focus returns', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    await expect(page.getByTestId('chart-fitness')).toBeVisible();
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await page.evaluate(() => window.scrollTo(0, 0));
    let id: string | null = null;
    for (let i = 0; i < 250 && id === null; i++) {
      await page.keyboard.press('Tab');
      id = await page.evaluate(() => {
        const a = document.activeElement as HTMLElement | null;
        return a?.getAttribute('data-testid') === 'chart-point' ? a.getAttribute('data-eval-id') : null;
      });
    }
    expect(id, 'a chart point is reachable by Tab').not.toBeNull();
    // the focus indicator of an SVG point is its .ring child (opacity 1 only on :focus-visible, 3px stroke)
    const ring = await page.evaluate(() => {
      const r = (document.activeElement as Element).querySelector('.ring');
      const s = r ? getComputedStyle(r) : null;
      return { opacity: s ? parseFloat(s.opacity) : 0, stroke: s ? parseFloat(s.strokeWidth) : 0 };
    });
    expect(ring.opacity, 'focus ring visible on the focused point').toBe(1);
    expect(ring.stroke).toBeGreaterThanOrEqual(2);
    await page.keyboard.press('Enter');
    const dlg = page.getByRole('dialog');
    await expect(dlg).toBeVisible();
    expect(await dlg.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    for (let i = 0; i < 40; i++) await page.keyboard.press('Tab'); // focus trap
    expect(await dlg.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(dlg).toBeHidden();
    expect(await page.evaluate(() => (document.activeElement as HTMLElement).getAttribute('data-eval-id'))).toBe(id);
  });

  test('the chart has a table alternative listing the same candidates', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    await openScreen(page, 'monitor');
    const points = await page.getByTestId('chart-fitness').getByTestId('chart-point').count();
    const rows = await page.getByTestId('points-table').locator('tbody tr').count();
    expect(points).toBeGreaterThan(0);
    expect(rows).toBeGreaterThanOrEqual(points);
    await expect(page.getByTestId('points-table').locator('caption')).not.toHaveCount(0);
  });
});

test.describe('validate and run edge cases', () => {
  test('a 422 Validate answer is rendered as an error table and keeps Run disabled', async ({ page }) => {
    const st = stateOf({
      validateReply: {
        status: 422,
        body: {
          code: 'PROTOCOL_INVALID',
          message: 'synthetic: protocol rejected',
          errors: [
            { field: 'rungs[1].epochs', code: 'EPOCHS_NOT_INCREASING', message: 'synthetic: epochs must increase' },
            { field: 'hard_constraints.latency_ms', code: 'UNKNOWN_FIELD', message: 'synthetic: unknown field' },
          ],
        },
      },
    });
    await installMock(page, st);
    await connect(page);
    await page.getByTestId('validate-btn').click();
    const errs = page.getByTestId('validate-errors');
    await expect(errs).toContainText('EPOCHS_NOT_INCREASING');
    await expect(errs).toContainText('rungs[1].epochs');
    await expect(errs).toContainText('UNKNOWN_FIELD');
    await expect(page.getByTestId('validation-result')).toContainText('INVALID');
    await expect(page.getByTestId('run-btn')).toBeDisabled();
    await shot(page, 'validate-422');
    const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
    expect(res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
  });

  test('a 422 without an errors list still shows a visible error, never a blank card', async ({ page }) => {
    await installMock(page, stateOf({ validateReply: { status: 422, body: { code: 'PROTOCOL_INVALID', message: 'synthetic: no errors list' } } }));
    await connect(page);
    await page.getByTestId('validate-btn').click();
    await expect(page.getByText('no errors list').first()).toBeVisible();
    await expect(page.getByTestId('run-btn')).toBeDisabled();
  });

  test('a 5xx on Run keeps the same Idempotency-Key; a later 2xx releases it', async ({ page }) => {
    const st = stateOf({ runResponses: [503, 503, 200] });
    await installMock(page, st);
    await connect(page);
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('run-btn')).toBeEnabled();
    for (let i = 0; i < 2; i++) {
      await page.getByTestId('run-btn').click();
      await expect(page.getByTestId('run-btn')).toContainText('Retry Run (same Idempotency-Key)');
    }
    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('submit-message')).toContainText('Run created');
    const posts = st.seen.filter((s) => s.method === 'POST' && s.url === '/api/v1/runs');
    expect(posts).toHaveLength(3);
    expect(new Set(posts.map((p) => p.idem)).size).toBe(1);
    expect(posts.every((p) => (p.body as { idempotency_key: string }).idempotency_key === p.idem)).toBe(true);
  });

  test('a definitive 422 on Run releases the key: the next attempt uses a new one', async ({ page }) => {
    const st = stateOf({ runResponses: [422, 200] });
    await installMock(page, st);
    await connect(page);
    await page.getByTestId('validate-btn').click();
    await expect(page.getByTestId('run-btn')).toBeEnabled();
    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('submit-message')).toContainText('422');
    await expect(page.getByTestId('run-btn')).not.toContainText('Retry Run');
    await page.getByTestId('run-btn').click();
    await expect(page.getByTestId('submit-message')).toContainText('Run created');
    const posts = st.seen.filter((s) => s.method === 'POST' && s.url === '/api/v1/runs');
    expect(posts).toHaveLength(2);
    expect(posts[0].idem).toBeTruthy();
    expect(posts[1].idem).not.toBe(posts[0].idem);
  });
});

test.describe('policy lock follows the run status', () => {
  for (const status of ['QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED', 'RESUMING', 'CANCEL_REQUESTED'] as const) {
    test(`nonterminal ${status} locks every policy control`, async ({ page }) => {
      await installMock(page, stateOf({ runStatus: status }));
      await connectAndLoad(page);
      await openScreen(page, 'settings');
      await expect(page.getByTestId('settings-fieldset')).toHaveAttribute('data-locked', 'true');
      const enabled = await page.getByTestId('settings-fieldset').locator('input:enabled, select:enabled, textarea:enabled').count();
      expect(enabled, 'no editable control while locked').toBe(0);
    });
  }
  for (const status of ['COMPLETED', 'FAILED', 'CANCELLED'] as const) {
    test(`terminal ${status} unlocks the policy editor`, async ({ page }) => {
      await installMock(page, stateOf({ runStatus: status }));
      await connect(page);
      await page.getByText('status: ok').waitFor();
      await page.getByTestId('run-id-input').fill(RUN_ID);
      await page.getByTestId('load-run-btn').click();
      await page.getByTestId('export-btn').waitFor();
      await openScreen(page, 'settings');
      await expect(page.getByTestId('lock-banner')).toHaveCount(0);
      await expect(page.getByTestId('budget')).toBeEnabled();
    });
  }
});

test.describe('SSE resync', () => {
  test('snapshot.required triggers a snapshot refetch', async ({ page }) => {
    const st = stateOf({ eventScript: [sse([{ seq: 6, type: 'snapshot.required', payload: {} }])] });
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'history');
    await expect(page.getByTestId('recoveries')).toContainText('snapshot.required');
    expect(st.seen.filter((s) => s.url.endsWith('/snapshot')).length).toBeGreaterThanOrEqual(2);
  });

  test('a seq jump is never applied: the log row behind the gap does not appear', async ({ page }) => {
    const st = stateOf({ eventScript: [sse([{ seq: 12, type: 'log', payload: { message: 'jumped-event' } }])] });
    await installMock(page, st);
    await connectAndLoad(page);
    await openScreen(page, 'history');
    await expect(page.getByTestId('recoveries')).toContainText('gap');
    await openScreen(page, 'monitor');
    await expect(page.getByTestId('event-log')).not.toContainText('#12');
  });
});

test.describe('artifact state machine', () => {
  test('EXPIRED -> one refresh -> READY shows the image from the fresh signed URL without credentials', async ({ page }) => {
    const st = stateOf();
    st.extraCandidates = [candWith('refreshable-candidate', (k) => art(`rf-${k}`, k, k === 'fn' ? { url: `${CDN}/expired.png?sig=old`, expires_at: '2000-01-01T00:00:00Z' } : {}))];
    st.custom = async (route, p) => {
      if (!p.endsWith('/rf-fn/refresh')) return false;
      await json(route, 200, art('rf-fn', 'fn', { url: `${CDN}/fresh.png?sig=new`, expires_at: '2999-01-01T00:00:00Z' }));
      return true;
    };
    await installMock(page, st);
    await connectAndLoad(page);
    await openCandidate(page, 'refreshable-candidate');
    const fn = page.getByTestId('artifact-fn');
    await expect(fn).toHaveAttribute('data-state', 'READY');
    await fn.scrollIntoViewIfNeeded(); // lazy image: load starts when it is in view
    await expect(fn).toHaveAttribute('data-loaded', 'true');
    await expect.poll(() => fn.locator('img').evaluate((i) => (i as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    expect(st.seen.filter((s) => s.url.endsWith('/rf-fn/refresh'))).toHaveLength(1);
    const cdn = st.seen.filter((s) => s.url.startsWith(CDN));
    expect(cdn.some((s) => s.url.includes('fresh.png'))).toBe(true);
    expect(cdn.some((s) => s.url.includes('expired.png'))).toBe(false); // the expired link is never fetched
    expect(cdn.every((s) => s.auth === undefined)).toBe(true);
    await shot(page, 'artifact-refreshed');
  });

  test('a refresh that fails with 403 ends in ACCESS_DENIED, with 5xx in EXPIRED (never a spinner)', async ({ page }) => {
    const st = stateOf();
    const expired = (k: Artifact['kind']): Artifact => art(`rx-${k}`, k, { url: `${CDN}/expired.png?sig=old`, expires_at: '2000-01-01T00:00:00Z' });
    st.extraCandidates = [candWith('refresh-fails-candidate', (k) => (k === 'gt' || k === 'tp' ? expired(k) : art(`rx-${k}`, k, {})))];
    st.custom = async (route, p) => {
      if (p.endsWith('/rx-gt/refresh')) {
        await json(route, 403, { code: 'ACCESS_DENIED', message: 'synthetic' });
        return true;
      }
      if (p.endsWith('/rx-tp/refresh')) {
        await json(route, 500, { code: 'INTERNAL', message: 'synthetic' });
        return true;
      }
      return false;
    };
    await installMock(page, st);
    await connectAndLoad(page);
    await openCandidate(page, 'refresh-fails-candidate');
    await expect(page.getByTestId('artifact-gt')).toHaveAttribute('data-state', 'ACCESS_DENIED');
    await expect(page.getByTestId('artifact-tp')).toHaveAttribute('data-state', 'EXPIRED');
    await expect(page.locator('[data-state="LOADING"]')).toHaveCount(0);
  });

  test('LOAD_FAILED: bearer fetch 500, a broken signed image after one refresh, and a foreign-origin bearer URL', async ({ page }) => {
    const st = stateOf();
    const foreign = 'https://evil.synthetic.test';
    st.extraCandidates = [
      candWith('load-failed-candidate', (k) => {
        if (k === 'gt') return art('lf-gt', k, { auth_mode: 'bearer_fetch', url: `/api/v1/runs/${RUN_ID}/artifacts/lf-gt` });
        if (k === 'tp') return art('lf-tp', k, { url: `${CDN}/broken.png` });
        if (k === 'fn') return art('lf-fn', k, { auth_mode: 'bearer_fetch', url: `${foreign}/steal.png` });
        return art('lf-fp', k, {});
      }),
    ];
    const foreignSeen: (string | undefined)[] = [];
    await page.route(`${foreign}/**`, async (route) => {
      foreignSeen.push(route.request().headers()['authorization']);
      await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('x') });
    });
    st.custom = async (route, p) => {
      if (p.endsWith('/lf-gt')) {
        await json(route, 500, { code: 'INTERNAL', message: 'synthetic' });
        return true;
      }
      if (p.endsWith('/lf-tp/refresh')) {
        await json(route, 200, art('lf-tp', 'tp', { url: `${CDN}/broken.png` }));
        return true;
      }
      return false;
    };
    await installMock(page, st);
    await connectAndLoad(page);
    await openCandidate(page, 'load-failed-candidate');
    for (const k of ['gt', 'tp', 'fn']) await expect(page.getByTestId(`artifact-${k}`)).toHaveAttribute('data-state', 'LOAD_FAILED');
    await expect(page.getByTestId('artifact-fp')).toHaveAttribute('data-state', 'READY');
    await expect(page.getByTestId('artifact-gt').getByRole('button', { name: 'Retry' })).toBeVisible();
    expect(foreignSeen, 'the foreign origin is never contacted with the bearer artifact URL').toEqual([]);
    expect(st.seen.some((s) => s.url.includes(TOKEN))).toBe(false);
    await shot(page, 'artifact-load-failed');
  });

  test('DD-44 lightbox: 확대 opens a modal with the same image, zoom steps change the scale, Esc closes it and returns focus to the 확대 button', async ({ page }) => {
    const st = stateOf();
    st.extraCandidates = [candWith('lightbox-candidate', (k) => art(`lb-${k}`, k, {}))];
    await installMock(page, st);
    await connectAndLoad(page);
    await openCandidate(page, 'lightbox-candidate');
    const gt = page.getByTestId('artifact-gt');
    await expect(gt).toHaveAttribute('data-state', 'READY');
    await gt.scrollIntoViewIfNeeded();
    await expect(gt).toHaveAttribute('data-loaded', 'true');
    // Card caption (375 px): the 원본 새 탭 link stays on one line and a ' · ' separates it from the sample id.
    const capLink = gt.getByRole('link', { name: '원본 새 탭' });
    await expect(capLink).toHaveCSS('white-space', 'nowrap');
    const capLinkBox = await capLink.boundingBox();
    const zoomBtnBox = await page.getByTestId('artifact-zoom-gt').boundingBox();
    expect(capLinkBox && zoomBtnBox).toBeTruthy();
    expect(capLinkBox!.height).toBeLessThanOrEqual(zoomBtnBox!.height + 1);
    expect(await gt.locator('figcaption').innerText()).toMatch(/원본 새 탭 · sample \S+$/);
    const zoom = page.getByTestId('artifact-zoom-gt');
    await zoom.click();
    const box = page.getByRole('dialog').filter({ has: page.getByTestId('lightbox-image') });
    await expect(box).toBeVisible();
    await expect(page.getByTestId('lightbox-image')).toBeVisible();
    await expect.poll(() => page.getByTestId('lightbox-image').evaluate((i) => (i as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    await expect(page.getByTestId('lightbox-scale')).toHaveText('100 %');
    await page.getByTestId('lightbox-zoom-in').click();
    await expect(page.getByTestId('lightbox-scale')).toHaveText('150 %');
    await expect(page.getByTestId('lightbox-original')).toHaveAttribute('href', /^https?:/);
    // The 닫기 label stays on one line even at 375 px (it used to wrap): its box is not taller than the single-line zoom button.
    const closeBox = await page.getByTestId('lightbox-close').boundingBox();
    const zoomInBox = await page.getByTestId('lightbox-zoom-in').boundingBox();
    expect(closeBox && zoomInBox).toBeTruthy();
    expect(closeBox!.height).toBeLessThanOrEqual(zoomInBox!.height + 1);
    await expect(page.getByTestId('lightbox-close')).toHaveCSS('white-space', 'nowrap');
    await shot(page, 'artifact-lightbox');
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('lightbox-image')).toHaveCount(0);
    await expect(zoom).toBeFocused();
  });
});

test.describe('DD-45 recorded conditions of two console-run candidates', () => {
  test('choosing a second model lists both rows conditions, flags the differing recorded ones and never invents a missing one', async ({ page }) => {
    const left = cand({ id: 'synthetic-ev-cond-left', candidate_id: 'synthetic-cand-cond-left', name: 'conditions-left-candidate', rung: 'L0', precision: 'FP16', split_hash: 'sha256:synthetic-split-1', protocol_hash: 'synthetic-protocol-1', checkpoint_sha256: 'c'.repeat(64) });
    const right = cand({ id: 'synthetic-ev-cond-right', candidate_id: 'synthetic-cand-cond-right', name: 'conditions-right-candidate', rung: 'L1', precision: 'FP32', split_hash: 'sha256:synthetic-split-1', protocol_hash: undefined, checkpoint_sha256: null });
    const st = stateOf();
    st.extraCandidates = [left, right];
    st.details = Object.fromEntries([left, right].map((row) => [row.candidate_id, { ...syntheticGeneratedDetail(row.candidate_id, RUN_ID), name: row.name, evaluations: [row], evaluation_status: 'EVALUATED' }]));
    await installMock(page, st);
    await connectAndLoad(page);
    await openCandidate(page, 'conditions-left-candidate');
    await page.getByLabel('비교할 모델').selectOption('synthetic-cand-cond-right');
    const box = page.getByTestId('eval-conditions');
    await expect(box).toBeVisible();
    await expect(box.getByRole('row').filter({ hasText: 'rung' })).toContainText('상이');
    await expect(box.getByRole('row').filter({ hasText: 'precision' })).toContainText('상이');
    await expect(box.getByRole('row').filter({ hasText: 'protocol_hash' })).toContainText('기록 없음');
    await expect(page.getByTestId('eval-conditions-differ')).toContainText('조건 상이');
    await expect(page.getByTestId('eval-conditions-unknown')).toContainText('checkpoint sha256');
    // viewport capture, not an element capture: the table sits inside a fixed-position, scrollable dialog, and an
    // element capture composed the wrong region on the 375 viewport (S06_fix1 round 1). Align the box top with the viewport top first.
    await box.evaluate((el) => el.scrollIntoView({ block: 'start', inline: 'nearest' }));
    await expect(box.getByRole('table')).toBeInViewport();
    await page.screenshot({ path: `evidence/${test.info().project.name}-model-comparison-conditions.png`, fullPage: false });
  });
});

test.describe('token and origin hygiene', () => {
  test('the token is only in memory: not in storage, cookies, URL or query strings, and gone after reload', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await connectAndLoad(page);
    for (const id of SCREENS) await openScreen(page, id);
    const dump = async (): Promise<string> =>
      page.evaluate(async () => {
        const dbs = (await indexedDB.databases?.()) ?? [];
        return JSON.stringify({ l: { ...localStorage }, s: { ...sessionStorage }, c: document.cookie, u: location.href, n: window.name, dbs: dbs.map((d) => d.name) });
      });
    expect(await dump()).not.toContain(TOKEN);
    expect(st.seen.every((s) => !s.url.includes(TOKEN))).toBe(true);
    expect(st.seen.every((s) => !JSON.stringify(s.body ?? '').includes(TOKEN))).toBe(true);
    await page.reload();
    expect(await dump()).not.toContain(TOKEN);
    await page.goto('/#/server');
    await expect(page.getByLabel(/Bearer token/)).toHaveValue('');
    expect(await page.getByLabel(/Bearer token/).getAttribute('type')).toBe('password');
  });

  test('a page that is not connected sends no bearer value to the API', async ({ page }) => {
    const st = stateOf();
    await installMock(page, st);
    await page.goto('/#/monitor');
    await page.waitForTimeout(300);
    expect(st.seen.filter((s) => s.auth !== undefined)).toEqual([]);
  });
});

test.describe('reflow and zoom', () => {
  test('200% zoom (640 CSS px at device scale 2): no element overflows on any screen', async ({ page }) => {
    await installMock(page, stateOf());
    await page.setViewportSize({ width: 640, height: 400 });
    await connectAndLoad(page);
    for (const id of SCREENS) {
      await openScreen(page, id);
      await noOverflow(page);
    }
    await shot(page, 'zoom-200');
  });

  test('400% zoom emulated with CSS zoom on body at 640: no horizontal page scroll', async ({ page }) => {
    await installMock(page, stateOf());
    await page.setViewportSize({ width: 640, height: 400 });
    await connectAndLoad(page);
    await page.evaluate(() => {
      document.body.style.zoom = '2';
    });
    for (const id of SCREENS) {
      await openScreen(page, id);
      await noOverflow(page);
    }
    await shot(page, 'zoom-400');
  });

  test('text-only zoom 200% (root font-size) does not break the layout', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    await page.evaluate(() => {
      document.documentElement.style.fontSize = '200%';
    });
    for (const id of SCREENS) {
      await openScreen(page, id);
      await noOverflow(page);
    }
  });

  test('strict overflow check at the native viewport on all screens', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    for (const id of SCREENS) {
      await openScreen(page, id);
      await noOverflow(page);
    }
  });
});

test.describe('motion', () => {
  test('prefers-reduced-motion: reduce removes transitions and animations', async ({ page }) => {
    await installMock(page, stateOf());
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await connectAndLoad(page);
    expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);
    const offenders = await page.evaluate(() => {
      const out: string[] = [];
      const dur = (v: string): number => Math.max(...v.split(',').map((x) => parseFloat(x) * (x.trim().endsWith('ms') ? 1 : 1000)));
      for (const el of document.querySelectorAll<HTMLElement>('body *')) {
        const s = getComputedStyle(el);
        if (dur(s.transitionDuration) > 1 || dur(s.animationDuration) > 1) out.push(`${el.tagName}.${el.className}:${s.transitionDuration}/${s.animationDuration}`);
        if (s.scrollBehavior === 'smooth') out.push(`smooth:${el.tagName}`);
      }
      return out.slice(0, 5);
    });
    expect(offenders).toEqual([]);
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    const btn = await page.evaluate(() => getComputedStyle(document.querySelector('button.btn') as Element).transitionDuration);
    expect(btn, 'motion is allowed again when the user does not ask for reduction').not.toMatch(/^0(\.0+)?s$/);
  });
});

test.describe('accessibility with overlays', () => {
  const serious = async (page: Page): Promise<string[]> => {
    const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    return res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`);
  };

  test('axe: open candidate dialog (lineage + artifacts)', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    await openCandidate(page, 'happy-artifacts-candidate');
    await expect(page.getByTestId('artifact-gt')).toHaveAttribute('data-state', 'READY');
    expect(await serious(page)).toEqual([]);
  });

  test('axe: artifact failure states in the dialog', async ({ page }) => {
    await installMock(page, stateOf());
    await connectAndLoad(page);
    await openCandidate(page, 'artifact-states-candidate');
    await expect(page.getByTestId('artifact-gt')).toHaveAttribute('data-state', 'ACCESS_DENIED');
    expect(await serious(page)).toEqual([]);
  });

  test('axe: settings before connecting (empty editor + connect banner)', async ({ page }) => {
    await installMock(page, stateOf());
    await page.goto('/#/settings');
    await expect(page.getByTestId('settings-connect-banner')).toBeVisible();
    expect(await serious(page)).toEqual([]);
  });

  test('axe: settings with visible client-side validation errors', async ({ page }) => {
    await installMock(page, stateOf());
    await connect(page); // the policy comes from the server preset applied on connect (DD-A21); no embedded template
    await page.getByText('status: ok').waitFor();
    await openScreen(page, 'settings');
    await page.getByTestId('rung-0-w-latency_reward').fill('0.08');
    await expect(page.getByTestId('issue-list')).toContainText('WEIGHTS_SUM_NOT_ONE');
    expect(await serious(page)).toEqual([]);
  });
});
