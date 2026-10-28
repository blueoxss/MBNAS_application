/**
 * DD-44 (REQ-9.03): in-page lightbox over ArtifactImage - same artifact URL, no extra fetch / re-inference, zoom buttons, Esc,
 * focus return, original new-tab link and the recorded sha256 stay.
 * DD-45 (REQ-9.07): ModelComparison shows the recorded run conditions of two candidates of a console run, 기록 없음 for
 * missing values, 조건 상이 for differing recorded values, 미평가 for unevaluated candidates (no estimate).
 */
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '../src/api/client';
import { ApiClient as RealClient } from '../src/api/client';
import { ArtifactImage, LIGHTBOX_SCALES, stepScale } from '../src/components/ArtifactImage';
import { evaluationConditionDifferences, evaluationConditions, ModelComparison } from '../src/components/ModelComparison';
import { artifactSet, cand, resetCounter } from './fixtures';

beforeEach(resetCounter);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('stepScale', () => {
  it('walks the fixed steps, treats fit as the natural size and clamps at both ends', () => {
    expect(stepScale(1, 1)).toBe(1.5);
    expect(stepScale(1, -1)).toBe(0.75);
    expect(stepScale('fit', 1)).toBe(1.5);
    expect(stepScale(LIGHTBOX_SCALES[LIGHTBOX_SCALES.length - 1], 1)).toBe(4);
    expect(stepScale(0.25, -1)).toBe(0.25);
    expect(stepScale(1.2, 1)).toBe(1.5); // an off-grid value moves to the next larger step
    expect(stepScale(1.2, -1)).toBe(1);
  });
});

describe('ArtifactImage lightbox (DD-44)', () => {
  const base = { ...artifactSet('e1')[0], url: 'https://cdn.example/gt.png?sig=1', sha256: 'a'.repeat(64) };
  const fetchSpy = vi.fn(async () => new Response('', { status: 200 }));
  const client = (): RealClient => new RealClient({ origin: '', token: 'T', pageOrigin: 'http://x', fetchImpl: fetchSpy as unknown as typeof fetch });

  it('opens from the 확대 button on the already-loaded src, zooms, shows the original link and sha256, closes with Esc and returns the focus', async () => {
    fetchSpy.mockClear();
    const user = userEvent.setup();
    render(<div id="root"><ArtifactImage artifact={base} runId="r" client={client()} label="GT" /></div>);
    const opener = await screen.findByTestId('artifact-zoom-gt');
    expect(screen.queryByRole('dialog')).toBeNull();
    await user.click(opener);
    const dialog = screen.getByRole('dialog');
    const big = within(dialog).getByTestId('lightbox-image') as HTMLImageElement;
    expect(big.getAttribute('src')).toBe('https://cdn.example/gt.png?sig=1'); // the real artifact URL, nothing else
    expect(within(dialog).getByTestId('lightbox-scale')).toHaveTextContent('100 %');
    await user.click(within(dialog).getByTestId('lightbox-zoom-in'));
    expect(within(dialog).getByTestId('lightbox-scale')).toHaveTextContent('150 %');
    await user.click(within(dialog).getByTestId('lightbox-zoom-out'));
    await user.click(within(dialog).getByTestId('lightbox-zoom-out'));
    expect(within(dialog).getByTestId('lightbox-scale')).toHaveTextContent('75 %');
    await user.click(within(dialog).getByTestId('lightbox-fit'));
    expect(within(dialog).getByTestId('lightbox-scale')).toHaveTextContent('fit');
    await user.click(within(dialog).getByTestId('lightbox-natural'));
    expect(within(dialog).getByTestId('lightbox-scale')).toHaveTextContent('100 %');
    const original = within(dialog).getByTestId('lightbox-original');
    expect(original).toHaveAttribute('href', 'https://cdn.example/gt.png?sig=1');
    expect(original).toHaveAttribute('target', '_blank');
    expect(original.getAttribute('rel')).toContain('noopener');
    expect(within(dialog).getByTestId('lightbox-meta')).toHaveTextContent('a'.repeat(64));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(opener).toHaveFocus();
    expect(fetchSpy).not.toHaveBeenCalled(); // signed_url: neither the card nor the lightbox goes through the API client
  });

  it('opens from a click on the image itself, keeps the new-tab link on the card, and says 기록 없음 without a sha256', async () => {
    const user = userEvent.setup();
    render(<div id="root"><ArtifactImage artifact={{ ...base, sha256: undefined }} runId="r" client={client()} label="GT" /></div>);
    const img = await screen.findByRole('img');
    expect(screen.getByRole('link', { name: '원본 새 탭' })).toHaveAttribute('target', '_blank');
    // 375 px caption: the link never wraps and a ' · ' separates it from the sample id (it used to read '원본 새 / 탭sample …')
    expect(screen.getByRole('link', { name: '원본 새 탭' })).toHaveStyle({ whiteSpace: 'nowrap' });
    expect(screen.getByTestId('artifact-gt').querySelector('figcaption')?.textContent).toMatch(/원본 새 탭 · sample \S+$/);
    await user.click(img);
    expect(screen.getByTestId('lightbox-meta')).toHaveTextContent('artifact sha256: 기록 없음');
    expect(screen.getByTestId('lightbox-close')).toHaveStyle({ whiteSpace: 'nowrap' }); // 375 px: the label must not wrap onto two lines
    await user.click(screen.getByTestId('lightbox-close'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a lightbox image that fails to load is reported as LOAD_FAILED text', async () => {
    const user = userEvent.setup();
    render(<div id="root"><ArtifactImage artifact={base} runId="r" client={client()} label="GT" /></div>);
    await user.click(await screen.findByTestId('artifact-zoom-gt'));
    const big = screen.getByTestId('lightbox-image');
    big.dispatchEvent(new Event('error'));
    expect(await screen.findByText(/LOAD_FAILED/, { selector: 'p' })).toBeInTheDocument();
  });
});

describe('evaluation conditions helpers (DD-45)', () => {
  it('reads recorded values only; empty strings and missing fields are null', () => {
    const row = cand({ split_hash: 'sha256:s1', protocol_hash: '', rung: 'L1', epochs: 6, device: 'cuda:0', precision: 'FP16', checkpoint_sha256: null });
    expect(evaluationConditions(row, 'ds.yaml')).toEqual({ dataset_yaml: 'ds.yaml', split_hash: 'sha256:s1', rung: 'L1', epochs: 6, protocol_hash: null, device: 'cuda:0', precision: 'FP16', checkpoint_sha256: null });
    expect(evaluationConditions(null).rung).toBeNull();
  });
  it('a null on either side is unknown, never different', () => {
    const a = evaluationConditions(cand({ rung: 'L0', epochs: 3, protocol_hash: 'p1' }));
    const b = evaluationConditions(cand({ rung: 'L1', epochs: 3, protocol_hash: undefined }));
    const d = evaluationConditionDifferences(a, b);
    expect(d.differing).toEqual(['rung']);
    expect(d.unknown).toContain('protocol_hash');
    expect(d.unknown).not.toContain('epochs');
  });
});

describe('ModelComparison recorded conditions (DD-45)', () => {
  async function mount(left: Parameters<typeof cand>[0], right: Parameters<typeof cand>[0], rightDetail: Record<string, unknown> = {}): Promise<void> {
    const c = cand({ candidate_id: 'cmp-a', name: 'cmp-a', ...left });
    const other = cand({ candidate_id: 'cmp-b', name: 'cmp-b', ...right });
    const client = { candidateDetail: async () => ({ candidate_id: 'cmp-b', name: 'cmp-b', blocks: [], parents: [], structure: {}, evaluations: [other], ...rightDetail }) } as unknown as ApiClient;
    render(<ModelComparison c={c} blocks={[]} all={[c, other]} client={client} runId="r" structure={null} datasetYaml="/data/ds/data.yaml" />);
    await userEvent.setup().selectOptions(screen.getByLabelText('비교할 모델'), 'cmp-b');
    await screen.findByTestId('eval-conditions');
  }

  it('shows both rows conditions, flags differing recorded values and lists missing ones as 기록 없음', async () => {
    await mount(
      { rung: 'L0', epochs: 3, split_hash: 'sha256:s1', protocol_hash: 'p1', device: 'cuda:0', precision: 'FP16', checkpoint_sha256: 'c'.repeat(64) },
      { rung: 'L1', epochs: 3, split_hash: 'sha256:s1', protocol_hash: undefined, device: 'cuda:0', precision: 'FP32', checkpoint_sha256: null },
    );
    const box = screen.getByTestId('eval-conditions');
    const table = within(box).getByRole('table');
    const rowOf = (label: string): HTMLElement => within(table).getAllByRole('row').find((r) => r.textContent?.includes(label)) as HTMLElement;
    expect(rowOf('dataset yaml')).toHaveTextContent('/data/ds/data.yaml');
    expect(rowOf('rung')).toHaveTextContent('L0');
    expect(rowOf('rung')).toHaveTextContent('L1');
    expect(rowOf('rung')).toHaveTextContent('상이');
    expect(rowOf('protocol_hash')).toHaveTextContent('기록 없음');
    expect(rowOf('protocol_hash')).not.toHaveTextContent('상이');
    expect(rowOf('누적 epoch')).not.toHaveTextContent('상이');
    const warn = screen.getByTestId('eval-conditions-differ');
    expect(warn).toHaveTextContent('조건 상이');
    expect(warn).toHaveTextContent('rung');
    expect(warn).toHaveTextContent('precision');
    expect(warn).not.toHaveTextContent('device');
    expect(screen.getByTestId('eval-conditions-unknown')).toHaveTextContent('protocol_hash');
    expect(screen.getByTestId('eval-conditions-unknown')).toHaveTextContent('checkpoint sha256');
  });

  it('no warning when the recorded conditions agree; evaluated metrics are the recorded values', async () => {
    const same = { rung: 'L1', epochs: 6, split_hash: 'sha256:s1', protocol_hash: 'p1', device: 'cuda:0', precision: 'FP16', checkpoint_sha256: 'c'.repeat(64) } as const;
    await mount({ ...same, ap: 0.25 }, { ...same, ap: 0.5 });
    expect(screen.queryByTestId('eval-conditions-differ')).toBeNull();
    const ap = within(screen.getByTestId('eval-conditions')).getAllByRole('row').find((r) => r.textContent?.includes('결과 · AP')) as HTMLElement;
    expect(ap).toHaveTextContent('25.00 %');
    expect(ap).toHaveTextContent('50.00 %');
  });

  it('an unevaluated candidate shows 미평가 for every result and no number is invented', async () => {
    await mount({ ap: 0.25 }, { status: 'BUILD_VALID', ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null }, { evaluation_status: 'NOT_EVALUATED' });
    const rows = within(screen.getByTestId('eval-conditions')).getAllByRole('row').filter((r) => r.textContent?.includes('결과 ·'));
    expect(rows).toHaveLength(5);
    for (const row of rows) expect(within(row).getAllByRole('cell')[2]).toHaveTextContent('미평가');
  });

  it('shows nothing before a second model is chosen', () => {
    const c = cand({ candidate_id: 'cmp-a' });
    render(<ModelComparison c={c} blocks={[]} all={[c]} client={null} runId="r" structure={null} />);
    expect(screen.queryByTestId('eval-conditions')).toBeNull();
  });
});
