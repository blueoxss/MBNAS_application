/**
 * V4-B fixes (DD-A55; real-stack evidence docs/internal/evidence/WP6/V4B/README.md):
 *  (1) CheckpointTable downloads a checkpoint by its store artifact_id when the server filled it in, else by the checkpoint
 *      id itself for a PRESENT / READY row (DD-A51 c: the gateway serves checkpoints by id on the artifact route); a row
 *      that can be downloaded never reads "저장소 미등록"; 404 ARTIFACT_NOT_READY reads "아직 저장소에 없음".
 *  (2) The structure graph is a named group, not role="img": an img's children are presentational, but the block nodes
 *      inside are focusable buttons (axe nested-interactive). Keyboard focus and selection stay on the nodes.
 * SYNTHETIC values only (tests/checkpoint_fixtures.ts, tests/detail_fixtures.ts).
 */
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../src/api/client';
import { CandidateView, checkpointDownloadId } from '../src/components/CandidateView';
import { ModelStructure } from '../src/components/ModelStructure';
import { CK_CID, CK_IDS, CK_STORE_BYTES, fixtureCheckpointDetail, fixtureStoreObjects } from './checkpoint_fixtures';
import { sha256Hex } from './config_fixtures';
import { syntheticStructureReport } from './detail_fixtures';
import { cand, resetCounter } from './fixtures';

beforeEach(resetCounter);
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState(null, '', '#/server'); });

const RUN = 'r';

/** ApiClient whose fetch answers the candidate detail and the store objects of tests/checkpoint_fixtures.ts. */
function storeClient(): { client: ApiClient; paths: string[] } {
  const paths: string[] = [];
  const objects = fixtureStoreObjects();
  const fetchImpl = async (input: RequestInfo | URL): Promise<Response> => {
    const p = new URL(String(input), 'http://x').pathname;
    paths.push(p);
    if (p === `/api/v1/runs/${RUN}/candidates/${CK_CID}`) return new Response(JSON.stringify(fixtureCheckpointDetail(CK_CID, RUN)), { status: 200, headers: { 'content-type': 'application/json' } });
    const m = new RegExp(`^/api/v1/runs/${RUN}/artifacts/([^/]+)$`).exec(p);
    const hit = m ? objects[decodeURIComponent(m[1])] : undefined;
    if (hit && 'bytes' in hit) return new Response(hit.bytes, { status: 200, headers: { 'content-type': 'application/octet-stream', 'x-artifact-sha256': sha256Hex(hit.bytes) } });
    if (hit) return new Response(JSON.stringify({ code: hit.code, message: hit.message, location: 'store' }), { status: hit.status, headers: { 'content-type': 'application/json' } });
    return new Response(JSON.stringify({ code: 'NOT_FOUND', message: 'unmocked' }), { status: 404, headers: { 'content-type': 'application/json' } });
  };
  return { client: new ApiClient({ origin: '', token: 'T', pageOrigin: 'http://x', fetchImpl: fetchImpl as unknown as typeof fetch }), paths };
}

const rowOf = (table: HTMLElement, id: string): HTMLElement =>
  within(table).getAllByTestId('checkpoint-id').find((n) => n.textContent === id)?.closest('tr') as HTMLElement;

describe('checkpointDownloadId (DD-A51 c)', () => {
  it('prefers the store artifact_id, falls back to the checkpoint id for PRESENT / READY, else nothing', () => {
    expect(checkpointDownloadId({ id: 'ck-1', state: 'PRESENT', artifact_id: 'ck-1' })).toEqual({ id: 'ck-1', via: 'artifact_id' });
    expect(checkpointDownloadId({ id: 'ck-1', state: 'PRESENT', artifact_id: 'art-store-9' })).toEqual({ id: 'art-store-9', via: 'artifact_id' });
    expect(checkpointDownloadId({ id: 'ck-1', state: 'PRESENT', artifact_id: null })).toEqual({ id: 'ck-1', via: 'checkpoint_id' });
    expect(checkpointDownloadId({ id: 'ck-1', state: 'PRESENT' })).toEqual({ id: 'ck-1', via: 'checkpoint_id' }); // older server: no member
    expect(checkpointDownloadId({ id: 'ck-1', state: 'READY' as never, artifact_id: null })).toEqual({ id: 'ck-1', via: 'checkpoint_id' });
    expect(checkpointDownloadId({ id: 'ck-1', state: 'PRUNED', artifact_id: null })).toBeNull();
    expect(checkpointDownloadId({ id: 'ck-1', state: 'MISSING', artifact_id: null })).toBeNull();
    expect(checkpointDownloadId({ id: 'ck-1', state: 'PRUNED', artifact_id: 'ck-1' })).toEqual({ id: 'ck-1', via: 'artifact_id' }); // the store holds it
  });
});

describe('CheckpointTable downloads (V4-B defect 1)', () => {
  it('offers "id로 다운로드" for every row the gateway can serve; only the pruned row without a store id reads 저장소 미등록', async () => {
    const { client } = storeClient();
    const c = cand({ candidate_id: CK_CID });
    render(<CandidateView c={c} run={null} runId={RUN} all={[c]} client={client} />);
    const table = await screen.findByTestId('checkpoint-table');
    for (const id of [CK_IDS.byCheckpointId, CK_IDS.byStoreId, CK_IDS.notInStore]) {
      const row = rowOf(table, id);
      expect(within(row).getByTestId(`checkpoint-download-${id}-btn`)).toHaveTextContent('id로 다운로드');
      expect(row).not.toHaveTextContent('저장소 미등록');
    }
    expect(within(table).getByTestId(`checkpoint-store-${CK_IDS.byCheckpointId}`)).toHaveAttribute('data-via', 'checkpoint_id');
    expect(within(table).getByTestId(`checkpoint-store-${CK_IDS.byStoreId}`)).toHaveAttribute('data-via', 'artifact_id');
    expect(within(table).getByTestId(`checkpoint-store-${CK_IDS.byStoreId}`)).toHaveTextContent(CK_IDS.byStoreId);
    const pruned = rowOf(table, CK_IDS.pruned);
    expect(pruned).toHaveTextContent('저장소 미등록');
    expect(within(pruned).queryByRole('button')).toBeNull();
    expect(within(table).getAllByText(/저장소 미등록/)).toHaveLength(1);
  });

  it('a PRESENT row with artifact_id null downloads by its checkpoint id and verifies the bytes against the recorded sha256', async () => {
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:synthetic'), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const { client, paths } = storeClient();
    const c = cand({ candidate_id: CK_CID });
    const user = userEvent.setup();
    render(<CandidateView c={c} run={null} runId={RUN} all={[c]} client={client} />);
    await user.click(await screen.findByTestId(`checkpoint-download-${CK_IDS.byCheckpointId}-btn`));
    const st = await screen.findByTestId(`checkpoint-download-${CK_IDS.byCheckpointId}-state`);
    expect(st).toHaveAttribute('data-state', 'READY');
    expect(st).toHaveTextContent('sha256 일치');
    const bytes = CK_STORE_BYTES[CK_IDS.byCheckpointId];
    expect(st).toHaveTextContent(`${new TextEncoder().encode(bytes).length} bytes · ${sha256Hex(bytes)}`);
    expect(paths).toContain(`/api/v1/runs/${RUN}/artifacts/${CK_IDS.byCheckpointId}`);
    // the row whose store id was filled in is requested by that id
    await user.click(screen.getByTestId(`checkpoint-download-${CK_IDS.byStoreId}-btn`));
    expect(await screen.findByTestId(`checkpoint-download-${CK_IDS.byStoreId}-state`)).toHaveTextContent('sha256 일치');
    expect(paths).toContain(`/api/v1/runs/${RUN}/artifacts/${CK_IDS.byStoreId}`);
    expect(paths.filter((p) => p.includes('/artifacts/'))).toHaveLength(2); // nothing fetched for the other rows
  });

  it('404 ARTIFACT_NOT_READY (bytes not transferred yet) reads 아직 저장소에 없음 with the code', async () => {
    const { client } = storeClient();
    const c = cand({ candidate_id: CK_CID });
    const user = userEvent.setup();
    render(<CandidateView c={c} run={null} runId={RUN} all={[c]} client={client} />);
    await user.click(await screen.findByTestId(`checkpoint-download-${CK_IDS.notInStore}-btn`));
    const st = await screen.findByTestId(`checkpoint-download-${CK_IDS.notInStore}-state`);
    expect(st).toHaveAttribute('data-state', 'NOT_READY');
    expect(st).toHaveTextContent('아직 저장소에 없음');
    expect(st).toHaveTextContent('ARTIFACT_NOT_READY');
    expect(st).not.toHaveTextContent('서버에 없음'); // not MISSING: the code decides, not the 404 status
  });
});

describe('structure graph semantics (V4-B defect 2, axe nested-interactive)', () => {
  /** ARIA roles whose children are presentational (WAI-ARIA 1.2): a focusable descendant inside one is nested-interactive. */
  const PRESENTATIONAL_CHILDREN = ['img', 'button', 'checkbox', 'radio', 'switch', 'tab', 'progressbar', 'slider', 'separator', 'math', 'meter', 'scrollbar', 'menuitemcheckbox', 'menuitemradio'];
  const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';

  it('the graph is a named group (not an img) and no presentational-children role contains a focusable node', () => {
    const report = syntheticStructureReport();
    render(<ModelStructure blocks={report.blocks as unknown[]} modelKey="ck-graph" report={report} />);
    const graph = screen.getByTestId('structure-graph');
    expect(graph).toHaveAttribute('role', 'group');
    expect(graph.getAttribute('aria-label')).toMatch(/YAML from 연결 그래프/);
    expect(screen.getByRole('group', { name: /YAML from 연결 그래프/ })).toBe(graph);
    const section = screen.getByTestId('model-structure');
    const offenders = [...section.querySelectorAll<HTMLElement | SVGElement>('[role]')]
      .filter((el) => PRESENTATIONAL_CHILDREN.includes(el.getAttribute('role') ?? '') && el.querySelector(FOCUSABLE) !== null)
      .map((el) => `${el.tagName.toLowerCase()}[role=${el.getAttribute('role')}]`);
    expect(offenders).toEqual([]);
  });

  it('every gene node stays keyboard-reachable and Enter / Space select it', async () => {
    const report = syntheticStructureReport();
    const user = userEvent.setup();
    render(<ModelStructure blocks={report.blocks as unknown[]} modelKey="ck-graph-keys" report={report} />);
    const nodes = [...screen.getByTestId('structure-graph').querySelectorAll<SVGGElement>('g[role="button"]')];
    expect(nodes).toHaveLength((report.blocks as unknown[]).length);
    expect(nodes.every((n) => n.getAttribute('tabindex') === '0' && /^선택 \d+ /.test(n.getAttribute('aria-label') ?? ''))).toBe(true);
    nodes[2].focus();
    expect(nodes[2]).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(nodes[2]).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('selected-block')).toHaveTextContent('선택 블록 #2');
    nodes[4].focus();
    await user.keyboard(' ');
    expect(nodes[4]).toHaveAttribute('aria-pressed', 'true');
    expect(nodes[2]).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('selected-block')).toHaveTextContent('선택 블록 #4');
  });
});
