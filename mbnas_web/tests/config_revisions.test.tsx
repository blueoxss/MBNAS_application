/**
 * WP-6B / F5d-F5e (contract 1.5.0): the "구성" tab of the candidate view — the revision table of
 * GET .../config-revisions, the line diff of two revisions' STORED BYTES (GET .../config-revisions/{id}/yaml, both sha256
 * recomputed and checked), the exact-byte download, and the graceful fallback (tab hidden + note) when the gateway answers
 * 404. Pure diff helpers are unit-tested first. Synthetic bytes, fetch-level mock, no server.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../src/api/client';
import type { ModelConfigRevision } from '../src/api/types';
import { CandidateView } from '../src/components/CandidateView';
import { bytesVerdict, decodeYaml, defaultDiffPair, lineDiff, MAX_CELLS, parentLabel, revisionFileName, revisionLabel, splitLines } from '../src/lib/configRevisions';
import { CFG_CID, EXECUTED_ARCH_HASH, EXECUTED_YAML, fixtureRevisionBytes, fixtureRevisions, ORIGINAL_YAML, REVISION_IDS, sha256Hex } from './config_fixtures';
import { cand, resetCounter } from './fixtures';

beforeEach(resetCounter);
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/'); });

// ---- pure helpers ---------------------------------------------------------------------------------------------------

describe('lineDiff (line diff of two YAML texts)', () => {
  it('identical texts: every line is context, nothing added or removed', () => {
    const d = lineDiff(ORIGINAL_YAML, ORIGINAL_YAML);
    expect(d.identical).toBe(true);
    expect(d.added + d.removed).toBe(0);
    expect(d.lines.every((l) => l.op === 'same')).toBe(true);
    expect(d.lines).toHaveLength(splitLines(ORIGINAL_YAML).lines.length);
  });
  it('a changed line is a removal followed by an addition, at the right line numbers, with context around it', () => {
    const d = lineDiff(ORIGINAL_YAML, EXECUTED_YAML);
    expect([d.added, d.removed, d.identical]).toEqual([2, 2, false]);
    const changed = d.lines.filter((l) => l.op !== 'same').map((l) => `${l.op}:${l.left ?? '-'}:${l.right ?? '-'}:${l.text}`);
    expect(changed).toEqual(['del:2:-:nc: 2', 'add:-:2:nc: 3', 'del:6:-:  - [-1, 2, C2f, [64, True]]', 'add:-:6:  - [-1, 2, C2f, [96, True]]']);
    expect(d.lines[0]).toEqual({ op: 'same', text: '# SYNTHETIC model configuration (test fixture, not a research result)', left: 1, right: 1 });
  });
  it('insertions and deletions keep the common lines aligned (LCS), not shifted', () => {
    const d = lineDiff('a\nb\nc\nd\n', 'a\nx\nb\nd\ny\n');
    expect(d.lines.map((l) => `${l.op === 'same' ? ' ' : l.op === 'add' ? '+' : '-'}${l.text}`)).toEqual([' a', '+x', ' b', '-c', ' d', '+y']);
    expect([d.added, d.removed]).toEqual([2, 1]);
  });
  it('CRLF vs LF and a missing final newline are byte differences and are reported', () => {
    const crlf = lineDiff('a\nb\n', 'a\r\nb\n');
    expect(crlf.identical).toBe(false);
    expect(crlf.lines.filter((l) => l.op !== 'same').map((l) => l.text)).toEqual(['a', 'a\r']);
    const eol = lineDiff('a\nb\n', 'a\nb');
    expect(eol.added + eol.removed).toBe(0);
    expect(eol.finalNewlineDiffers).toBe(true);
    expect(eol.identical).toBe(false);
  });
  it('an empty side is all additions / all removals', () => {
    expect(lineDiff('', 'a\nb\n')).toMatchObject({ added: 2, removed: 0 });
    expect(lineDiff('a\n', '')).toMatchObject({ added: 0, removed: 1 });
  });
  it('a changed middle larger than the alignment budget is shown as a whole replacement (coarse), never truncated', () => {
    const n = Math.ceil(Math.sqrt(MAX_CELLS)) + 1;
    const a = Array.from({ length: n }, (_, i) => `a${i}`).join('\n'), b = Array.from({ length: n }, (_, i) => `b${i}`).join('\n');
    const d = lineDiff(`head\n${a}\ntail\n`, `head\n${b}\ntail\n`);
    expect(d.coarse).toBe(true);
    expect([d.removed, d.added]).toEqual([n, n]);
    expect(d.lines[0]).toMatchObject({ op: 'same', text: 'head' });
    expect(d.lines[d.lines.length - 1]).toMatchObject({ op: 'same', text: 'tail' });
  });
});

describe('revision labels, default pair, file name, decoding and the bytes verdict', () => {
  const revs = fixtureRevisions().revisions;
  it('labels by position and kind; a parent outside the list is named as such', () => {
    expect(revs.map((r) => revisionLabel(revs, r))).toEqual(['#1 original', '#2 selected', '#3 executed']);
    expect(parentLabel(revs, REVISION_IDS.original)).toBe(`#1 original (${REVISION_IDS.original})`);
    expect(parentLabel(revs, null)).toBe('—');
    expect(parentLabel(revs, 'rev-elsewhere')).toBe('rev-elsewhere (목록에 없음)');
  });
  it('the diff opens on selected vs executed, else the last two, else the only one', () => {
    expect(defaultDiffPair(revs)).toEqual([REVISION_IDS.selected, REVISION_IDS.executed]);
    const noExec: ModelConfigRevision[] = [revs[0], { ...revs[1], kind: 'generated' }];
    expect(defaultDiffPair(noExec)).toEqual([REVISION_IDS.original, REVISION_IDS.selected]);
    expect(defaultDiffPair([revs[0]])).toEqual([REVISION_IDS.original, REVISION_IDS.original]);
    expect(defaultDiffPair([])).toBeNull();
  });
  it('the download file name is safe and names candidate, kind and revision', () => {
    expect(revisionFileName('synthetic-ga-g1-1', revs[2])).toBe(`synthetic-ga-g1-1-executed-${REVISION_IDS.executed}.yaml`);
    expect(revisionFileName('../a b/c', { kind: 'original', revision_id: 'r:1' })).toBe('a_b_c-original-r_1.yaml');
  });
  it('decodes UTF-8 exactly and says so when the bytes are not UTF-8', () => {
    expect(decodeYaml(new TextEncoder().encode('모델: 1\n'))).toEqual({ text: '모델: 1\n', exact: true });
    expect(decodeYaml(new Uint8Array([0x6e, 0x63, 0xff, 0x0a])).exact).toBe(false);
  });
  it('bytes verdict: the received bytes must hash to the recorded source_sha256 (and to the header when sent)', () => {
    const h = sha256Hex('x');
    expect(bytesVerdict(h, h, `sha256:${h}`).matches).toBe(true);
    expect(bytesVerdict(h, null, `sha256:${h}`).matches).toBe(true);
    expect(bytesVerdict(h, 'f'.repeat(64), `sha256:${h}`).matches).toBe(false);
    expect(bytesVerdict(h, h, `sha256:${'0'.repeat(64)}`).matches).toBe(false);
    expect(bytesVerdict(null, h, `sha256:${h}`).matches).toBeNull(); // the header alone proves nothing
    expect(bytesVerdict(h, h, null).matches).toBeNull();
  });
});

// ---- the "구성" tab ---------------------------------------------------------------------------------------------------

interface Served { status: number; body: string | Uint8Array; headers?: Record<string, string> }
function serve(handler: (path: string) => Served | undefined): { client: ApiClient; paths: string[] } {
  const paths: string[] = [];
  const fetchImpl = async (input: RequestInfo | URL): Promise<Response> => {
    const url = new URL(String(input), 'http://x');
    paths.push(url.pathname);
    const hit = handler(url.pathname) ?? { status: 404, body: JSON.stringify({ code: 'NOT_FOUND', message: 'unmocked' }), headers: { 'content-type': 'application/json' } };
    return new Response(hit.body as BodyInit, { status: hit.status, headers: hit.headers ?? { 'content-type': 'application/json' } });
  };
  return { client: new ApiClient({ origin: '', token: 'T', pageOrigin: 'http://x', fetchImpl: fetchImpl as unknown as typeof fetch }), paths };
}
const jsonOf = (status: number, body: unknown): Served => ({ status, body: JSON.stringify(body) });

const BASE = `/api/v1/runs/r/candidates/${CFG_CID}`;
function standardServer(over: { revisions?: Served; bytes?: Record<string, { bytes: string; sha256?: string }> } = {}): ReturnType<typeof serve> {
  const bytes = over.bytes ?? fixtureRevisionBytes();
  return serve((p) => {
    if (p === BASE) return jsonOf(200, { candidate_id: CFG_CID, name: 'ga-g1-1', blocks: [], parents: [], evaluations: [], candidate_status: 'EVALUATED' });
    if (p === `${BASE}/config-revisions`) return over.revisions ?? jsonOf(200, fixtureRevisions());
    const m = new RegExp(`^${BASE}/config-revisions/([^/]+)/yaml$`).exec(p);
    if (m) {
      const hit = bytes[decodeURIComponent(m[1])];
      if (!hit) return jsonOf(404, { code: 'ARTIFACT_NOT_READY', message: 'synthetic: not stored' });
      return { status: 200, body: hit.bytes, headers: { 'content-type': 'application/x-yaml', ...(hit.sha256 ? { 'x-artifact-sha256': hit.sha256 } : {}) } };
    }
    return undefined;
  });
}

function mount(client: ApiClient): void {
  const c = cand({ candidate_id: CFG_CID, name: 'ga-g1-1', id: 'synthetic-ev-ga-g1-1' });
  render(<CandidateView c={c} run={null} runId="r" all={[c]} client={client} />);
}

async function openConfigTab(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  await user.click(await screen.findByTestId('model-tab-config'));
  return screen.findByTestId('config-revisions');
}

describe('"구성" tab: revision table', () => {
  it('lists every revision with kind, source_sha256, canonical_hash, architecture_hash, created_at and parent', async () => {
    const { client, paths } = standardServer();
    const user = userEvent.setup();
    mount(client);
    const panel = await openConfigTab(user);
    expect(paths).toContain(`${BASE}/config-revisions`);
    const rows = within(within(panel).getByTestId('revision-table')).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(3);
    const revs = fixtureRevisions().revisions;
    rows.forEach((row, i) => {
      expect(within(row).getByTestId('revision-kind')).toHaveTextContent(revs[i].kind);
      expect(within(row).getByTestId('revision-id')).toHaveTextContent(revs[i].revision_id);
      expect(within(row).getByTestId('revision-source-sha256')).toHaveTextContent(revs[i].source_sha256);
      expect(within(row).getByTestId('revision-canonical-hash')).toHaveTextContent(String(revs[i].canonical_hash));
      expect(within(row).getByTestId('revision-created-at')).toHaveTextContent(revs[i].created_at);
    });
    expect(within(rows[0]).getByTestId('revision-architecture-hash')).toHaveTextContent('빌드 전 · null');
    expect(within(rows[2]).getByTestId('revision-architecture-hash')).toHaveTextContent(EXECUTED_ARCH_HASH);
    expect(within(rows[0]).getByTestId('revision-parent')).toHaveTextContent('—');
    expect(within(rows[2]).getByTestId('revision-parent')).toHaveTextContent(`#2 selected (${REVISION_IDS.selected})`);
    // the canonical hash and the bytes hash are explained as different definitions
    expect(panel).toHaveTextContent('두 값이 다른 것은 정상');
    expect(within(panel).getByTestId('revision-export-hint')).toHaveTextContent('GET /runs/{run_id}/export');
  });
});

describe('"구성" tab: diff of two revisions (stored bytes, both sha256 checked)', () => {
  it('opens on selected vs executed and shows the changed lines with both sides\' sha256 verified', async () => {
    const { client } = standardServer();
    const user = userEvent.setup();
    mount(client);
    const panel = await openConfigTab(user);
    expect(within(panel).getByTestId('diff-left')).toHaveValue(REVISION_IDS.selected);
    expect(within(panel).getByTestId('diff-right')).toHaveValue(REVISION_IDS.executed);
    await user.click(within(panel).getByTestId('diff-run'));
    const summary = await within(panel).findByTestId('diff-summary');
    expect(summary).toHaveTextContent('+2 행 / −2 행');
    expect(summary).toHaveAttribute('data-identical', 'false');
    expect(within(panel).getByTestId('diff-sha-A')).toHaveTextContent(sha256Hex(ORIGINAL_YAML));
    expect(within(panel).getByTestId('diff-sha-B')).toHaveTextContent(sha256Hex(EXECUTED_YAML));
    expect(within(panel).getByTestId('diff-verdict-A')).toHaveAttribute('data-verdict', 'match');
    expect(within(panel).getByTestId('diff-verdict-B')).toHaveAttribute('data-verdict', 'match');
    const lines = within(within(panel).getByTestId('diff-lines')).getAllByRole('row').slice(1);
    const changed = lines.filter((r) => r.getAttribute('data-op') !== 'same').map((r) => `${r.getAttribute('data-op')} ${r.textContent}`);
    expect(changed).toEqual(['del 2−nc: 2', 'add 2+nc: 3', 'del 6−  - [-1, 2, C2f, [64, True]]', 'add 6+  - [-1, 2, C2f, [96, True]]']);
  });

  it('any two revisions can be compared; the same bytes are reported as identical (sha256 equal)', async () => {
    const { client } = standardServer();
    const user = userEvent.setup();
    mount(client);
    const panel = await openConfigTab(user);
    await user.selectOptions(within(panel).getByTestId('diff-left'), REVISION_IDS.original);
    await user.selectOptions(within(panel).getByTestId('diff-right'), REVISION_IDS.selected);
    await user.click(within(panel).getByTestId('diff-run'));
    expect(await within(panel).findByTestId('diff-summary')).toHaveTextContent('저장 바이트가 같습니다 (sha256 동일)');
    // original vs executed in the other direction
    await user.selectOptions(within(panel).getByTestId('diff-left'), REVISION_IDS.executed);
    await user.selectOptions(within(panel).getByTestId('diff-right'), REVISION_IDS.original);
    await user.click(within(panel).getByTestId('diff-run'));
    await waitFor(() => expect(within(panel).getByTestId('diff-summary')).toHaveTextContent('+2 행 / −2 행'));
  });

  it('stored bytes that do not hash to the recorded source_sha256 are flagged on that side', async () => {
    const bytes = fixtureRevisionBytes();
    const tampered = EXECUTED_YAML.replace('nc: 3', 'nc: 4');
    bytes[REVISION_IDS.executed] = { bytes: tampered, sha256: sha256Hex(tampered) }; // the header matches the bytes, the record does not
    const { client } = standardServer({ bytes });
    const user = userEvent.setup();
    mount(client);
    const panel = await openConfigTab(user);
    await user.click(within(panel).getByTestId('diff-run'));
    expect(await within(panel).findByTestId('diff-verdict-B')).toHaveAttribute('data-verdict', 'mismatch');
    expect(within(panel).getByTestId('diff-verdict-A')).toHaveAttribute('data-verdict', 'match');
  });

  it('a revision whose bytes are not stored yet reports the server code', async () => {
    const bytes = fixtureRevisionBytes();
    delete (bytes as Record<string, unknown>)[REVISION_IDS.executed];
    const { client } = standardServer({ bytes });
    const user = userEvent.setup();
    mount(client);
    const panel = await openConfigTab(user);
    await user.click(within(panel).getByTestId('diff-run'));
    expect(await within(panel).findByTestId('diff-error')).toHaveTextContent('ARTIFACT_NOT_READY');
  });
});

describe('"구성" tab: exact-byte download', () => {
  it('downloads the stored bytes of a revision unchanged and checks them against source_sha256', async () => {
    const blobs: { blob: Blob; name: string }[] = [];
    let lastUrlBlob: Blob | null = null;
    Object.assign(URL, { createObjectURL: vi.fn((b: Blob) => { lastUrlBlob = b; return 'blob:synthetic'; }), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { if (lastUrlBlob) blobs.push({ blob: lastUrlBlob, name: this.download }); });
    const { client } = standardServer();
    const user = userEvent.setup();
    mount(client);
    const panel = await openConfigTab(user);
    const row = within(panel).getByTestId(`revision-download-${REVISION_IDS.executed}`);
    await user.click(within(row).getByTestId('revision-download'));
    expect(await within(row).findByTestId('revision-download-verdict')).toHaveAttribute('data-verdict', 'match');
    expect(blobs).toHaveLength(1);
    expect(blobs[0].name).toBe(`${CFG_CID}-executed-${REVISION_IDS.executed}.yaml`);
    expect(await blobs[0].blob.text()).toBe(EXECUTED_YAML);
    expect(row).toHaveTextContent(`${Buffer.byteLength(EXECUTED_YAML)} bytes · sha256 ${sha256Hex(EXECUTED_YAML)}`);
  });

  it('the displayed (canonical) text download is labelled separately from the exact-byte download', async () => {
    const { client } = standardServer();
    const user = userEvent.setup();
    mount(client);
    const panel = await openConfigTab(user);
    expect(within(panel).getAllByTestId('revision-download')[0]).toHaveTextContent('원문 바이트 다운로드');
    expect(within(panel).getByTestId('revision-export-hint')).toHaveTextContent('화면에 표시한 구성 YAML 다운로드');
    expect(within(panel).getByTestId('revision-export-hint')).toHaveTextContent('표시본(canonical 텍스트)');
  });
});

describe('"구성" tab fallback (gateway without the revision routes)', () => {
  it('404 NOT_FOUND: the tab is hidden and a note explains why', async () => {
    const { client } = standardServer({ revisions: jsonOf(404, { code: 'NOT_FOUND', message: 'Not Found', location: 'gateway' }) });
    mount(client);
    const note = await screen.findByTestId('config-tab-note');
    expect(note).toHaveAttribute('data-state', 'unsupported');
    expect(note).toHaveTextContent('구성 개정 API');
    expect(note).toHaveTextContent('404 NOT_FOUND');
    expect(screen.queryByTestId('model-tab-config')).toBeNull();
    expect(screen.queryByTestId('config-tab')).toBeNull();
    expect(screen.getByTestId('model-tab-structure')).toBeInTheDocument(); // the other tabs stay
  });
  it('404 CANDIDATE_NOT_FOUND: hidden as "no record for this candidate"', async () => {
    const { client } = standardServer({ revisions: jsonOf(404, { code: 'CANDIDATE_NOT_FOUND', message: 'no such candidate', location: 'core' }) });
    mount(client);
    const note = await screen.findByTestId('config-tab-note');
    expect(note).toHaveAttribute('data-state', 'not_found');
    expect(note).toHaveTextContent('개정 기록이 없어');
    expect(screen.queryByTestId('model-tab-config')).toBeNull();
  });
  it('a remembered model_tab=config falls back to the full view while the server offers no revisions', async () => {
    window.history.replaceState(null, '', '/?model_tab=config');
    const { client } = standardServer({ revisions: jsonOf(404, { code: 'NOT_FOUND', message: 'Not Found' }) });
    mount(client);
    await screen.findByTestId('config-tab-note');
    expect(screen.getByText('Identity').closest('section')).not.toHaveAttribute('hidden');
  });
  it('a 5xx keeps the tab with the error and a retry that loads the table', async () => {
    let fail = true;
    const { client } = serve((p) => {
      if (p === `${BASE}/config-revisions`) return fail ? jsonOf(503, { code: 'CORE_UNAVAILABLE', message: 'core is down', location: 'gateway' }) : jsonOf(200, fixtureRevisions());
      if (p === BASE) return jsonOf(200, { candidate_id: CFG_CID, name: 'ga-g1-1', blocks: [], parents: [], evaluations: [], candidate_status: 'EVALUATED' });
      return undefined;
    });
    const user = userEvent.setup();
    mount(client);
    await user.click(await screen.findByTestId('model-tab-config'));
    expect(await screen.findByTestId('config-revisions-error')).toHaveTextContent('CORE_UNAVAILABLE');
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }));
    expect(await screen.findByTestId('revision-table')).toBeInTheDocument();
  });
});
