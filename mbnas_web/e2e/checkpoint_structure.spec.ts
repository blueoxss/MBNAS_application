/**
 * V4-B fixes (DD-A55) in a real Chromium against the SYNTHETIC mocked gateway (e2e/mock.ts): the candidate dialog of a
 * detail with a structure report and four checkpoint rows (tests/checkpoint_fixtures.ts).
 *  - axe (wcag2a/aa, 2.1aa) on the structure graph and on the whole dialog: no serious / critical violation; in particular
 *    no `nested-interactive` (the graph was `svg role="img"` around focusable gene nodes, ModelStructure.tsx); the nodes
 *    stay keyboard-reachable (Tab) and selectable (Enter).
 *  - CheckpointTable: a PRESENT row with artifact_id null is downloaded by its checkpoint id (DD-A51 c) and the saved
 *    file hashes to the recorded sha256; a row with artifact_id by that id; 404 ARTIFACT_NOT_READY reads
 *    "아직 저장소에 없음"; only the PRUNED row without a store id reads "저장소 미등록".
 *  - C07-D1 (round 4): a block-table row opens the same block detail as its graph node and its gene button (same
 *    selected-block text, same `?block=`), switches to the "구조·gene" tab (`model_tab=structure`) and moves focus to the
 *    block detail; axe finds no serious / nested-interactive issue on the table with its row buttons.
 * Screenshots: evidence/<project>-ckpt-*.png. Values are synthetic.
 */
import AxeBuilder from '@axe-core/playwright';
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { captureEvidence, connectAndLoad, installMock, openScreen, RUN_ID, sha256Of, stateOf, type MockState } from './mock';
import { CK_CID, CK_IDS, CK_STORE_BYTES, fixtureCheckpointDetail, fixtureStoreObjects } from '../tests/checkpoint_fixtures';
import { SYNTHETIC_CONCAT_INDEX, syntheticContractBlocks } from '../tests/detail_fixtures';

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21aa'];
const serious = (violations: { id: string; impact?: string | null; nodes: { target: unknown[] }[] }[]): string[] =>
  violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.impact} ${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`);
const shot = async (page: Page, name: string): Promise<void> => captureEvidence(page, `evidence/${test.info().project.name}-ckpt-${name}.png`, false);

function checkpointState(over: Partial<MockState> = {}): MockState {
  return stateOf({ details: { [CK_CID]: fixtureCheckpointDetail() }, storeObjects: fixtureStoreObjects(), ...over });
}

async function openCheckpointCandidate(page: Page): Promise<ReturnType<Page['getByRole']>> {
  await openScreen(page, 'monitor');
  await page.getByTestId('candidate-table').getByRole('button', { name: /ga-g1-2/ }).click();
  const dlg = page.getByRole('dialog');
  await expect(dlg.getByTestId('candidate-view')).toBeVisible();
  return dlg;
}

test.describe('candidate detail: structure graph a11y + checkpoint download by id (V4-B fix)', () => {
  test('axe: the structure graph is a named group with keyboard-focusable nodes, no nested-interactive', async ({ page }) => {
    await installMock(page, checkpointState());
    await connectAndLoad(page);
    const dlg = await openCheckpointCandidate(page);
    await dlg.getByTestId('model-tab-structure').click();
    const graph = dlg.getByTestId('structure-graph');
    await expect(graph).toBeVisible();
    await expect(graph).toHaveAttribute('role', 'group');
    await expect(dlg.getByRole('group', { name: /YAML from 연결 그래프/ })).toBeVisible();
    await expect(dlg.locator('svg[role="img"][data-testid="structure-graph"]')).toHaveCount(0);

    const onGraph = await new AxeBuilder({ page }).include('[data-testid="structure-graph"]').withTags(TAGS).analyze();
    expect(onGraph.violations.map((v) => `${v.impact} ${v.id}`)).toEqual([]);
    const whole = await new AxeBuilder({ page }).withTags(TAGS).analyze();
    expect(serious(whole.violations)).toEqual([]);
    expect(whole.violations.map((v) => v.id)).not.toContain('nested-interactive');

    // keyboard: the gene nodes keep their focus order (Tab) and Enter selects the focused node
    const nodes = graph.locator('g[role="button"]');
    await expect(nodes).toHaveCount(9);
    await nodes.nth(2).focus();
    await expect(nodes.nth(2)).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(nodes.nth(3)).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(nodes.nth(3)).toHaveAttribute('aria-pressed', 'true');
    await expect(dlg.getByTestId('selected-block')).toContainText('선택 블록 #3');
    await graph.scrollIntoViewIfNeeded();
    await shot(page, 'structure-graph');
  });

  test('block table: a row opens the same block detail as the graph node and the gene button (?block=, model_tab=structure)', async ({ page }) => {
    await installMock(page, checkpointState({ details: { [CK_CID]: { ...fixtureCheckpointDetail(), blocks: syntheticContractBlocks() } } }));
    await connectAndLoad(page);
    const dlg = await openCheckpointCandidate(page);
    const I = SYNTHETIC_CONCAT_INDEX;
    const wanted = `block-${String(I).padStart(4, '0')}`;
    const block = (): string | null => new URL(page.url()).searchParams.get('block');
    const tab = (): string | null => new URL(page.url()).searchParams.get('model_tab');
    const rowButton = dlg.getByTestId(`block-row-${I}`);
    await expect(rowButton).toHaveAttribute('data-block-id', wanted); // the detail (report block ids) has loaded
    await expect(dlg.getByTestId('block-table').getByRole('button', { name: /구조·gene 탭에서 이 블록 상세 보기/ })).toHaveCount(9);
    const onTable = await new AxeBuilder({ page }).include('[data-testid="block-table"]').withTags(TAGS).analyze();
    expect(serious(onTable.violations)).toEqual([]);
    expect(onTable.violations.map((v) => v.id)).not.toContain('nested-interactive');

    // 1. the structure graph node
    const selected = dlg.getByTestId('selected-block');
    await dlg.getByTestId('structure-graph').locator('g[role="button"]').nth(I).click();
    await expect(selected).toContainText(`선택 블록 #${I} · Concat`);
    await expect.poll(block).toBe(wanted);
    const viaGraph = await selected.textContent();

    // 2. the gene button (after another block was selected)
    await dlg.getByTestId('gene-0').click();
    await expect(selected).toContainText('선택 블록 #0');
    await dlg.getByTestId(`gene-${I}`).click();
    await expect(selected).toContainText(`선택 블록 #${I} · Concat`);
    expect(await selected.textContent()).toBe(viaGraph);
    await expect.poll(block).toBe(wanted);

    // 3. the block-table row (after another block was selected)
    await dlg.getByTestId('gene-0').click();
    await expect.poll(block).toBe('block-0000');
    await rowButton.click();
    await expect.poll(tab).toBe('structure');
    await expect.poll(block).toBe(wanted);
    await expect(dlg.getByTestId('model-tab-structure')).toHaveAttribute('aria-pressed', 'true');
    await expect(selected).toContainText(`선택 블록 #${I} · Concat`);
    expect(await selected.textContent()).toBe(viaGraph);
    await expect(dlg.getByTestId(`gene-${I}`)).toHaveAttribute('aria-pressed', 'true');
    await expect(dlg.getByTestId('block-table')).toBeHidden();
    await expect(selected.getByRole('heading', { level: 4 })).toBeFocused();
    await shot(page, 'block-table-link');
  });

  test('checkpoint table: PRESENT + artifact_id null downloads by checkpoint id; NOT_READY and PRUNED are explicit', async ({ page }) => {
    const st = checkpointState();
    await installMock(page, st);
    await connectAndLoad(page);
    const dlg = await openCheckpointCandidate(page);
    const table = dlg.getByTestId('checkpoint-table');
    await expect(table).toBeVisible();
    const row = (id: string) => table.locator('tbody tr').filter({ has: page.getByTestId(`checkpoint-store-${id}`) });

    // every downloadable row offers the button and never reads 저장소 미등록; the pruned row does, without a button
    for (const id of [CK_IDS.byCheckpointId, CK_IDS.byStoreId, CK_IDS.notInStore]) {
      await expect(row(id).getByTestId(`checkpoint-download-${id}-btn`)).toBeVisible();
      await expect(row(id)).not.toContainText('저장소 미등록');
    }
    await expect(table.getByTestId(`checkpoint-store-${CK_IDS.byCheckpointId}`)).toHaveAttribute('data-via', 'checkpoint_id');
    await expect(table.getByTestId(`checkpoint-store-${CK_IDS.byStoreId}`)).toHaveAttribute('data-via', 'artifact_id');
    await expect(row(CK_IDS.pruned)).toContainText('저장소 미등록');
    await expect(row(CK_IDS.pruned).getByRole('button')).toHaveCount(0);

    // PRESENT + artifact_id null: requested by the checkpoint id; the saved file hashes to the recorded sha256
    const bytes = CK_STORE_BYTES[CK_IDS.byCheckpointId];
    const [download] = await Promise.all([page.waitForEvent('download'), row(CK_IDS.byCheckpointId).getByTestId(`checkpoint-download-${CK_IDS.byCheckpointId}-btn`).click()]);
    expect(download.suggestedFilename()).toBe(`${CK_CID}-${CK_IDS.byCheckpointId}.pt`);
    const saved = readFileSync(await download.path());
    expect(sha256Of(saved)).toBe(sha256Of(bytes));
    const ok = dlg.getByTestId(`checkpoint-download-${CK_IDS.byCheckpointId}-state`);
    await expect(ok).toHaveAttribute('data-state', 'READY');
    await expect(ok).toContainText('sha256 일치');
    expect(st.seen.filter((s) => s.method === 'GET' && s.url.includes('/artifacts/')).map((s) => s.url)).toEqual([`/api/v1/runs/${RUN_ID}/artifacts/${CK_IDS.byCheckpointId}`]);

    // the store id the core filled in
    const [second] = await Promise.all([page.waitForEvent('download'), row(CK_IDS.byStoreId).getByTestId(`checkpoint-download-${CK_IDS.byStoreId}-btn`).click()]);
    expect(sha256Of(readFileSync(await second.path()))).toBe(sha256Of(CK_STORE_BYTES[CK_IDS.byStoreId]));
    await expect(dlg.getByTestId(`checkpoint-download-${CK_IDS.byStoreId}-state`)).toContainText('sha256 일치');

    // bytes not transferred yet: 404 ARTIFACT_NOT_READY -> 아직 저장소에 없음 (not "missing")
    await row(CK_IDS.notInStore).getByTestId(`checkpoint-download-${CK_IDS.notInStore}-btn`).click();
    const notReady = dlg.getByTestId(`checkpoint-download-${CK_IDS.notInStore}-state`);
    await expect(notReady).toHaveAttribute('data-state', 'NOT_READY');
    await expect(notReady).toContainText('아직 저장소에 없음');
    await expect(notReady).toContainText('ARTIFACT_NOT_READY');

    const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
    expect(serious(res.violations)).toEqual([]);
    await table.scrollIntoViewIfNeeded();
    await shot(page, 'checkpoint-table');
  });
});
