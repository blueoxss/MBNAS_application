/**
 * WP-6B (contract 1.5.0, F5a/F5d/F5e) in a real Chromium against the SYNTHETIC mocked gateway (e2e/mock.ts):
 * upload a model YAML from the browser -> upload_id + source_sha256 -> server CPU structure check by upload_id -> training
 * request by upload_id -> the candidate's "구성" tab: revision table -> diff of two revisions (both sha256 recomputed in the
 * browser and checked) -> download of the exact stored bytes (sha256 of the saved file = the revision's source_sha256).
 * Also: the tab is hidden with a note when the gateway has no revision routes, and upload rejections are shown by code.
 * Screenshots: evidence/<project>-cfg-*.png. Bytes and hashes are synthetic (tests/config_fixtures.ts).
 */
import AxeBuilder from '@axe-core/playwright';
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { captureEvidence, connectAndLoad, installMock, openScreen, sha256Of, stateOf, type MockState } from './mock';
import { CFG_CID, EXECUTED_YAML, fixtureRevisionBytes, fixtureRevisions, ORIGINAL_YAML, REVISION_IDS } from '../tests/config_fixtures';

/** The viewport around the element of interest (scrolled into view): an open dialog is a fixed overlay and the Settings page is long. */
const shot = async (page: Page, name: string, viewport = false): Promise<void> => {
  const h = await page.evaluate(() => document.documentElement.scrollHeight * window.devicePixelRatio);
  await captureEvidence(page, `evidence/${test.info().project.name}-cfg-${name}.png`, !viewport && h < 12000);
};
const serious = async (page: Page): Promise<string[]> => {
  const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  return res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`);
};

function revisionState(over: Partial<MockState> = {}): MockState {
  return stateOf({ revisions: { [CFG_CID]: fixtureRevisions() }, revisionBytes: fixtureRevisionBytes(), ...over });
}

async function userModelSource(page: Page): Promise<void> {
  await openScreen(page, 'settings');
  await page.getByLabel('모델 출처').selectOption('user');
}

async function openCfgCandidate(page: Page): Promise<ReturnType<Page['getByRole']>> {
  await openScreen(page, 'monitor');
  await page.getByTestId('candidate-table').getByRole('button', { name: /ga-g1-1/ }).click();
  const dlg = page.getByRole('dialog');
  await expect(dlg.getByTestId('candidate-view')).toBeVisible();
  return dlg;
}

test.describe('model configuration: upload -> check -> revisions -> diff -> download (F5)', () => {
  test('the full flow against the mocked gateway, with every sha256 checked in the browser', async ({ page }) => {
    const st = revisionState();
    await installMock(page, st);
    await connectAndLoad(page);

    // 1) upload from the browser: raw body, file name in ?filename=
    await userModelSource(page);
    await expect(page.getByLabel(/모델 구성 YAML 서버 절대경로/)).toHaveCount(0); // the server path is an advanced option only
    await page.getByTestId('model-upload-input').setInputFiles({ name: 'synthetic-model.yaml', mimeType: 'application/x-yaml', buffer: Buffer.from(ORIGINAL_YAML) });
    const result = page.getByTestId('model-upload-result');
    const hex = sha256Of(ORIGINAL_YAML);
    await expect(result.getByTestId('upload-id')).toHaveText(`upl-${hex}`);
    await expect(result.getByTestId('upload-sha256')).toHaveText(`sha256:${hex}`);
    await expect(result.getByTestId('upload-local-sha256')).toHaveText(hex); // WebCrypto in the page hashed the same bytes
    await expect(result.getByTestId('upload-verdict')).toContainText('일치');
    const upload = st.seen.filter((s) => s.method === 'POST' && s.url.startsWith('/api/v1/uploads/model-config'));
    expect(upload).toHaveLength(1);
    expect(upload[0].url).toBe('/api/v1/uploads/model-config?filename=synthetic-model.yaml');
    expect(st.uploads?.[hex]?.bytes.toString('utf8')).toBe(ORIGINAL_YAML); // the gateway received the exact bytes
    await result.scrollIntoViewIfNeeded();
    await shot(page, 'upload', true);

    // 2) server CPU structure check of the uploaded bytes (by upload_id, never a path)
    await page.getByTestId('registry-structure-open').click();
    await expect(page.getByTestId('registry-structure-subject')).toHaveText(`synthetic-model.yaml · upl-${hex}`);
    await page.getByTestId('registry-structure-check').click();
    await expect(page.getByTestId('registry-structure-result')).toBeVisible();
    await expect(page.getByTestId('structure-status')).toHaveText(/PASS/);
    await expect(page.getByTestId('structure-source-sha256')).toHaveText(hex);
    await expect(page.getByTestId('structure-source-verdict')).toContainText('일치');
    const check = st.seen.filter((s) => s.method === 'POST' && s.url === '/api/v1/structure-check');
    expect(check.map((s) => s.body)).toEqual([{ upload_id: `upl-${hex}`, model_name: 'synthetic-model.yaml' }]);
    await shot(page, 'structure-check', true);
    await page.getByTestId('registry-structure-close').click();

    // 3) the training request names the upload, not a server path
    await page.getByTestId('training-submit').click();
    await expect(page.getByTestId('training-source-sha')).toContainText(`synthetic-model.yaml source_sha256 sha256:${hex}`);
    await page.getByTestId('training-source-sha').scrollIntoViewIfNeeded();
    await shot(page, 'training-request', true);
    const train = st.seen.filter((s) => s.method === 'POST' && s.url === '/api/v1/training-runs');
    expect(train).toHaveLength(1);
    expect((train[0].body as { source: unknown }).source).toEqual({ kind: 'user', upload_id: `upl-${hex}`, model_name: 'synthetic-model.yaml' });
    expect(JSON.stringify(train[0].body)).not.toContain('model_path');
    expect(await serious(page)).toEqual([]);

    // 4) the candidate's configuration revisions
    const dlg = await openCfgCandidate(page);
    await dlg.getByTestId('model-tab-config').click();
    const table = dlg.getByTestId('revision-table');
    await expect(table.getByTestId('revision-kind')).toHaveText(['original', 'selected', 'executed']);
    await expect(table.getByTestId('revision-source-sha256').nth(2)).toHaveText(`sha256:${sha256Of(EXECUTED_YAML)}`);
    await expect(table.getByTestId('revision-parent').nth(2)).toContainText(`#2 selected (${REVISION_IDS.selected})`);

    // 5) diff: selected vs executed by default; both sides' bytes hashed in the browser and matched to the record
    await expect(dlg.getByTestId('diff-left')).toHaveValue(REVISION_IDS.selected);
    await expect(dlg.getByTestId('diff-right')).toHaveValue(REVISION_IDS.executed);
    await dlg.getByTestId('diff-run').click();
    await expect(dlg.getByTestId('diff-summary')).toHaveText(/\+2 행 \/ −2 행/);
    await expect(dlg.getByTestId('diff-sha-A')).toHaveText(sha256Of(ORIGINAL_YAML));
    await expect(dlg.getByTestId('diff-sha-B')).toHaveText(sha256Of(EXECUTED_YAML));
    await expect(dlg.getByTestId('diff-verdict-A')).toHaveAttribute('data-verdict', 'match');
    await expect(dlg.getByTestId('diff-verdict-B')).toHaveAttribute('data-verdict', 'match');
    const lines = dlg.getByTestId('diff-lines');
    await expect(lines.locator('tr[data-op="del"]')).toHaveCount(2);
    await expect(lines.locator('tr[data-op="add"]').first()).toContainText('nc: 3');
    await dlg.getByTestId('diff-summary').scrollIntoViewIfNeeded();
    await shot(page, 'revisions-diff', true);
    expect(await serious(page)).toEqual([]);

    // 6) download the exact stored bytes of the executed revision: the saved file hashes to its source_sha256
    const row = dlg.getByTestId(`revision-download-${REVISION_IDS.executed}`);
    const [download] = await Promise.all([page.waitForEvent('download'), row.getByTestId('revision-download').click()]);
    expect(download.suggestedFilename()).toBe(`${CFG_CID}-executed-${REVISION_IDS.executed}.yaml`);
    const saved = readFileSync(await download.path());
    expect(saved.toString('utf8')).toBe(EXECUTED_YAML);
    expect(sha256Of(saved)).toBe(sha256Of(EXECUTED_YAML));
    await expect(row.getByTestId('revision-download-verdict')).toHaveAttribute('data-verdict', 'match');
    await row.scrollIntoViewIfNeeded();
    await shot(page, 'download', true);
  });

  test('a gateway without the revision routes: the "구성" tab is hidden and a note says why', async ({ page }) => {
    await installMock(page, stateOf()); // no revisions: GET .../config-revisions answers 404 NOT_FOUND
    await connectAndLoad(page);
    const dlg = await openCfgCandidate(page);
    await expect(dlg.getByTestId('config-tab-note')).toHaveAttribute('data-state', 'unsupported');
    await expect(dlg.getByTestId('config-tab-note')).toContainText('404 NOT_FOUND');
    await expect(dlg.getByTestId('model-tab-config')).toHaveCount(0);
    await expect(dlg.getByTestId('model-tab-structure')).toBeVisible();
    await shot(page, 'revisions-unsupported', true);
  });

  test('upload refusals: a non-YAML name is not sent; a server 413 is shown by its code', async ({ page }) => {
    const st = revisionState();
    await installMock(page, st);
    await connectAndLoad(page);
    await userModelSource(page);
    await page.getByTestId('model-upload-input').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('nc: 2\n') });
    await expect(page.getByTestId('model-upload-error')).toHaveAttribute('data-code', 'UPLOAD_TYPE_UNSUPPORTED');
    expect(st.seen.filter((s) => s.url.startsWith('/api/v1/uploads/'))).toHaveLength(0);
    st.uploadReply = { status: 413, body: { code: 'PAYLOAD_TOO_LARGE', message: 'model config larger than 1048576 bytes', location: 'gateway' } };
    await page.getByTestId('model-upload-input').setInputFiles({ name: 'big.yaml', mimeType: 'application/x-yaml', buffer: Buffer.from(ORIGINAL_YAML) });
    await expect(page.getByTestId('model-upload-error')).toHaveAttribute('data-code', 'PAYLOAD_TOO_LARGE');
    await expect(page.getByTestId('model-upload-error')).toContainText('location gateway');
    await expect(page.getByTestId('training-submit')).toBeDisabled();
    await page.getByTestId('model-upload-error').scrollIntoViewIfNeeded();
    await shot(page, 'upload-refused', true);
  });
});
