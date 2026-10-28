/**
 * WP-6B / F14 (DD-A23) and WP-6 §1-8: the Lineage screen lists the generated candidates of an evaluation-OFF run next to the
 * evaluated rows, with the "UNTRAINED · UNEVALUATED" badge (never as a measured row); the candidate view names architecture
 * and checkpoint files by id + sha256 instead of a host path, downloads them by id, and maps a failed download by its
 * Error.code (DD-A39: ARTIFACT_NOT_READY / LOAD_FAILED), not by the HTTP status. SYNTHETIC values only.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { ApiClient, ApiError } from '../src/api/client';
import type { CandidateDetail } from '../src/api/types';
import { CandidateView } from '../src/components/CandidateView';
import { ARTIFACT_ERROR_STATE, loadOnce, stateFromError } from '../src/lib/artifacts';
import { UNTRAINED_UNEVALUATED } from '../src/lib/evaluation';
import { generatedAsCandidate } from '../src/lib/generation';
import { lineageKey, lineageRows } from '../src/screens/Lineage';
import { AppProvider } from '../src/state/context';
import { sha256Hex } from './config_fixtures';
import { GEN_CID, syntheticGeneratedDetail, syntheticGeneratedRows } from './detail_fixtures';
import { artifactSet, cand, fixtureSnapshot, resetCounter, RUN_ID } from './fixtures';
import { installMockApi } from './mockapi';

let restore: () => void = () => undefined;
beforeEach(resetCounter);
afterEach(() => { cleanup(); restore(); vi.restoreAllMocks(); window.history.replaceState(null, '', '#/server'); });

describe('Lineage rows include the generated candidates (F14)', () => {
  it('appends generated candidates after the evaluated rows, once, without hiding or duplicating an evaluated candidate', () => {
    const evaluated = fixtureSnapshot().candidates;
    const generated = syntheticGeneratedRows();
    const rows = lineageRows(evaluated, generated);
    expect(rows).toHaveLength(evaluated.length + generated.length);
    expect(rows.slice(evaluated.length).map((r) => r.candidate_id)).toEqual(generated.map((g) => g.candidate_id));
    // a generated candidate that also has an evaluation row is shown as that evaluation only
    const twin = { ...generated[0], candidate_id: evaluated[3].candidate_id };
    expect(lineageRows(evaluated, [twin])).toHaveLength(evaluated.length);
    // keys: evaluation id for evaluated rows, generated:<candidate_id> for generated candidates (no collision)
    expect(lineageKey(evaluated[3])).toBe(evaluated[3].id);
    expect(lineageKey(generatedAsCandidate(generated[0]))).toBe(`generated:${generated[0].candidate_id}`);
  });

  it('the Lineage table badges every generated candidate UNTRAINED · UNEVALUATED, distinct from the evaluated rows', async () => {
    const snapshot = { ...fixtureSnapshot(), generated_candidates: syntheticGeneratedRows() };
    const api = installMockApi({ snapshot, details: { [GEN_CID]: syntheticGeneratedDetail() } });
    restore = api.restore;
    const user = userEvent.setup();
    render(<AppProvider><App /></AppProvider>);
    await user.type(screen.getByLabelText(/Bearer token/), 'T');
    await user.click(screen.getByTestId('connect-btn'));
    await screen.findByText(/status: ok/);
    await user.type(screen.getByTestId('run-id-input'), RUN_ID);
    await user.click(screen.getByTestId('load-run-btn'));
    await waitFor(() => expect(screen.getByTestId('pause-btn')).toBeEnabled());
    await user.click(screen.getByTestId('nav-lineage'));
    const table = await screen.findByTestId('lineage-table');
    const generated = syntheticGeneratedRows();
    const rowOf = (name: string): HTMLElement => within(table).getByRole('button', { name }).closest('tr') as HTMLElement;
    for (const g of generated) {
      const row = rowOf(g.name);
      expect(within(row).getByTestId('lineage-untrained')).toHaveTextContent(UNTRAINED_UNEVALUATED);
      expect(row).toHaveTextContent('random (0 parents)'); // the random generator drew it (no detail needed)
      expect(row).toHaveTextContent('미평가'); // no fitness for a generated candidate
      expect(within(row).queryByTestId('lineage-status')).toBeNull();
    }
    expect(within(table).getAllByTestId('lineage-untrained')).toHaveLength(generated.length);
    // evaluated rows keep their own status badge and never get the UNTRAINED badge
    const evaluatedRow = rowOf('ga-g0-0');
    expect(within(evaluatedRow).getByTestId('lineage-status')).toHaveTextContent('EVALUATED');
    expect(within(evaluatedRow).queryByTestId('lineage-untrained')).toBeNull();
    expect(screen.getByTestId('lineage-generated-count')).toHaveTextContent(`생성 후보 ${generated.length}개`);
    // opening a generated candidate shows the generated record (no evaluation) with the same badge
    await user.click(within(table).getByRole('button', { name: `${GEN_CID}-model` }));
    const view = await screen.findByTestId('candidate-view');
    expect(within(view).getByTestId('untrained-unevaluated')).toHaveTextContent(UNTRAINED_UNEVALUATED);
    expect(view).toHaveTextContent('candidate record (evaluation 없음)');
  });
});

// ---- files by id + sha256 ---------------------------------------------------------------------------------------------

const ARCH_YAML = 'nc: 2\nbackbone: []\nhead: []\n';
const CKPT_BYTES = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]);

function detailWithFiles(cid: string): CandidateDetail {
  return {
    candidate_id: cid, run_id: 'r', name: cid, arm: 'GA', family: 'GA-mixed', architecture_hash: 'sha256:' + 'c'.repeat(64), blocks: [], parents: [], evaluations: [], candidate_status: 'EVALUATED',
    architecture_path: 'architectures/cand.yaml',
    artifacts: [{ id: 'art-arch-1', kind: 'architecture', url: '/api/v1/runs/r/artifacts/art-arch-1', sha256: sha256Hex(ARCH_YAML), metadata: { candidate_id: cid } }],
    checkpoints: [
      { id: 'ck-0001', kind: 'training_full', epoch: 10, rung: 'L0', sha256: sha256Hex(CKPT_BYTES), state: 'PRESENT', bytes: CKPT_BYTES.length, artifact_id: 'art-ck-1' },
      { id: 'ck-0002', kind: 'weights_only', epoch: 10, rung: 'L0', sha256: 'f'.repeat(64), state: 'PRUNED', artifact_id: null },
    ],
  };
}

function filesClient(artifact: (id: string) => Response): { client: ApiClient; paths: string[] } {
  const paths: string[] = [];
  const fetchImpl = async (input: RequestInfo | URL): Promise<Response> => {
    const p = new URL(String(input), 'http://x').pathname;
    paths.push(p);
    if (p === '/api/v1/runs/r/candidates/files-1') return new Response(JSON.stringify(detailWithFiles('files-1')), { status: 200, headers: { 'content-type': 'application/json' } });
    const m = /^\/api\/v1\/runs\/r\/artifacts\/([^/]+)$/.exec(p);
    if (m) return artifact(decodeURIComponent(m[1]));
    return new Response(JSON.stringify({ code: 'NOT_FOUND', message: 'unmocked' }), { status: 404, headers: { 'content-type': 'application/json' } });
  };
  return { client: new ApiClient({ origin: '', token: 'T', pageOrigin: 'http://x', fetchImpl: fetchImpl as unknown as typeof fetch }), paths };
}
const errorResponse = (status: number, code: string): Response => new Response(JSON.stringify({ code, message: `synthetic ${code}`, location: 'store' }), { status, headers: { 'content-type': 'application/json' } });

describe('candidate view: architecture and checkpoint files by id + sha256 (no host path)', () => {
  it('shows the architecture artifact id + sha256 and the checkpoint ids + sha256, never a path', async () => {
    const { client } = filesClient(() => errorResponse(404, 'NOT_FOUND'));
    const c = cand({ candidate_id: 'files-1', checkpoint_sha256: 'e'.repeat(64), architecture_path: 'architectures/cand.yaml', checkpoint_path: 'checkpoints/cand.pt' });
    render(<CandidateView c={c} run={null} runId="r" all={[c]} client={client} />);
    expect(await screen.findByTestId('architecture-artifact-id')).toHaveTextContent('art-arch-1');
    expect(screen.getByTestId('architecture-sha256')).toHaveTextContent(sha256Hex(ARCH_YAML));
    expect(screen.getByTestId('row-checkpoint-sha256')).toHaveTextContent('e'.repeat(64));
    const table = screen.getByTestId('checkpoint-table');
    expect(within(table).getAllByTestId('checkpoint-id').map((n) => n.textContent)).toEqual(['ck-0001', 'ck-0002']);
    expect(within(table).getAllByTestId('checkpoint-sha256')[0]).toHaveTextContent(sha256Hex(CKPT_BYTES));
    expect(table).toHaveTextContent('저장소 미등록'); // the pruned row has no store id: no download offered
    const section = screen.getByText(/Architecture and checkpoint \(id \+ sha256\)/).closest('section') as HTMLElement;
    for (const gone of ['architecture_path', 'checkpoint_path', 'architectures/cand.yaml', 'checkpoints/cand.pt']) expect(section).not.toHaveTextContent(gone);
  });

  it('downloads by artifact id and verifies the bytes against the recorded sha256', async () => {
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:synthetic'), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const { client, paths } = filesClient((id) => id === 'art-ck-1'
      ? new Response(CKPT_BYTES, { status: 200, headers: { 'content-type': 'application/octet-stream', 'x-artifact-sha256': sha256Hex(CKPT_BYTES) } })
      : new Response(ARCH_YAML, { status: 200, headers: { 'content-type': 'application/x-yaml', 'x-artifact-sha256': sha256Hex(ARCH_YAML) } }));
    const c = cand({ candidate_id: 'files-1' });
    const user = userEvent.setup();
    render(<CandidateView c={c} run={null} runId="r" all={[c]} client={client} />);
    await user.click(await screen.findByTestId('checkpoint-download-ck-0001-btn'));
    const st = await screen.findByTestId('checkpoint-download-ck-0001-state');
    expect(st).toHaveAttribute('data-state', 'READY');
    expect(st).toHaveTextContent('sha256 일치');
    expect(st).toHaveTextContent(`${CKPT_BYTES.length} bytes · ${sha256Hex(CKPT_BYTES)}`);
    await user.click(screen.getByTestId('architecture-download-btn'));
    expect(await screen.findByTestId('architecture-download-state')).toHaveTextContent('sha256 일치');
    expect(paths).toContain('/api/v1/runs/r/artifacts/art-ck-1');
    expect(paths).toContain('/api/v1/runs/r/artifacts/art-arch-1');
  });

  it.each([
    [404, 'ARTIFACT_NOT_READY', 'NOT_READY', '아직 저장소에 없음'],
    [409, 'LOAD_FAILED', 'LOAD_FAILED', '기록 sha256과 다르거나'],
    [403, 'ACCESS_DENIED', 'ACCESS_DENIED', '권한 없음'],
  ])('a failed download %i %s is shown as %s (by code)', async (status, code, state, text) => {
    const { client } = filesClient(() => errorResponse(status, code));
    const c = cand({ candidate_id: 'files-1' });
    const user = userEvent.setup();
    render(<CandidateView c={c} run={null} runId="r" all={[c]} client={client} />);
    await user.click(await screen.findByTestId('checkpoint-download-ck-0001-btn'));
    const st = await screen.findByTestId('checkpoint-download-ck-0001-state');
    expect(st).toHaveAttribute('data-state', state);
    expect(st).toHaveTextContent(text);
    expect(st).toHaveTextContent(code);
  });
});

describe('artifact download states by Error.code (DD-A39), the status only for an unknown code', () => {
  const err = (status: number, code: string): ApiError => new ApiError('http', 'm', status, { code, message: 'm' });
  it('the code decides: 404 ARTIFACT_NOT_READY is NOT_READY (not MISSING), 409 LOAD_FAILED is LOAD_FAILED (not NOT_READY)', () => {
    expect(stateFromError(err(404, 'ARTIFACT_NOT_READY'))).toBe('NOT_READY');
    expect(stateFromError(err(409, 'LOAD_FAILED'))).toBe('LOAD_FAILED');
    expect(stateFromError(err(404, 'NOT_FOUND'))).toBe('MISSING');
    expect(stateFromError(err(401, 'AUTH_REQUIRED'))).toBe('ACCESS_DENIED');
    expect(stateFromError(err(410, 'EXPIRED'))).toBe('EXPIRED');
    expect(Object.keys(ARTIFACT_ERROR_STATE)).toEqual(expect.arrayContaining(['ARTIFACT_NOT_READY', 'LOAD_FAILED']));
  });
  it('an unknown code falls back to the status (older servers), and no code at all too', () => {
    expect(stateFromError(err(404, 'SOMETHING_ELSE'))).toBe('MISSING');
    expect(stateFromError(err(409, 'SOMETHING_ELSE'))).toBe('NOT_READY');
    expect(stateFromError(new ApiError('http', 'm', 500, null))).toBe('LOAD_FAILED');
  });
  it('a diagnostic image (bearer fetch) follows the same rule and carries the code', async () => {
    const art = { ...artifactSet('e1', 'bearer_fetch')[0], url: '/api/v1/runs/r/artifacts/a' };
    const mk = (status: number, code: string): ApiClient => new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: (async () => errorResponse(status, code)) as unknown as typeof fetch });
    expect(await loadOnce(art, { client: mk(404, 'ARTIFACT_NOT_READY'), runId: 'r' })).toEqual({ state: 'NOT_READY', code: 'ARTIFACT_NOT_READY' });
    expect(await loadOnce(art, { client: mk(409, 'LOAD_FAILED'), runId: 'r' })).toEqual({ state: 'LOAD_FAILED', code: 'LOAD_FAILED' });
  });
});
