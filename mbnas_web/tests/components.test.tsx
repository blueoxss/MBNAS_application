import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { ApiClient } from '../src/api/client';
import { ArtifactImage } from '../src/components/ArtifactImage';
import { Modal } from '../src/components/Modal';
import { Nav } from '../src/components/Nav';
import { Scatter } from '../src/components/Scatter';
import { buildPlotPoints, selectCohort } from '../src/lib/cohort';
import { setIn } from '../src/lib/policy';
import { AppProvider } from '../src/state/context';
import { artifactSet, BLOCKS, cand, fixtureCandidates, fixtureGenerations, fixtureSnapshot, resetCounter, RUN_ID } from './fixtures';
import { installMockApi } from './mockapi';

const setExplicitTraining = (protocol: Record<string, unknown>): Record<string, unknown> => setIn(protocol, 'console.extra.training', { enabled: true, mode: 'scratch', validation_enabled: false });

let restore: () => void = () => undefined;
afterEach(() => {
  cleanup();
  restore();
  window.history.replaceState(null, '', '#/server');
});
beforeEach(resetCounter);

function Harness(): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <div id="root">
      <button onClick={() => setOpen(true)}>opener</button>
      {open && (
        <Modal titleId="t" onClose={() => setOpen(false)}>
          <h2 id="t">Dialog</h2>
          <button data-autofocus>first</button>
          <button>second</button>
        </Modal>
      )}
    </div>
  );
}

describe('Modal', () => {
  it('is a labelled modal dialog, traps Tab, closes with ESC and returns focus to the opener', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByText('opener');
    await user.click(opener);
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveAttribute('aria-modal', 'true');
    expect(dlg).toHaveAccessibleName('Dialog');
    expect(screen.getByText('first')).toHaveFocus();
    await user.tab();
    expect(screen.getByText('second')).toHaveFocus();
    await user.tab();
    expect(screen.getByText('first')).toHaveFocus(); // wrapped, not escaping to the page
    await user.tab({ shift: true });
    expect(screen.getByText('second')).toHaveFocus();
    expect(document.getElementById('root')).toHaveAttribute('inert');
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(document.getElementById('root')).not.toHaveAttribute('inert');
  });
});

function NestedHarness(): JSX.Element {
  const [outer, setOuter] = useState(false);
  const [inner, setInner] = useState(false);
  return (
    <div id="root">
      <button onClick={() => setOuter(true)}>open outer</button>
      {outer && (
        <Modal titleId="o" onClose={() => setOuter(false)}>
          <h2 id="o">Outer</h2>
          <button data-autofocus onClick={() => setInner(true)}>open inner</button>
          {inner && (
            <Modal titleId="i" onClose={() => setInner(false)}>
              <h2 id="i">Inner</h2>
              <button data-autofocus>inner first</button>
            </Modal>
          )}
        </Modal>
      )}
    </div>
  );
}

describe('Modal nested (candidate dialog -> image lightbox)', () => {
  it('Esc closes only the innermost modal, focus returns to the button that opened it, the page stays inert until the last one closes', async () => {
    const user = userEvent.setup();
    render(<NestedHarness />);
    await user.click(screen.getByText('open outer'));
    const openInner = screen.getByText('open inner');
    await user.click(openInner);
    expect(screen.getAllByRole('dialog')).toHaveLength(2);
    await user.keyboard('{Escape}');
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Outer');
    expect(openInner).toHaveFocus();
    expect(document.getElementById('root')).toHaveAttribute('inert');
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.getElementById('root')).not.toHaveAttribute('inert');
  });
});

describe('Scatter', () => {
  const pts = buildPlotPoints(fixtureCandidates(), 'fitness').points.filter((p) => p.c.arm === 'Baseline');
  it('renders focusable points that open on click, Enter and Space', async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<Scatter chartId="c" title="Fitness versus latency" yLabel="Fitness" yKind="fitness" xMode="ms" points={pts} segments={[]} onOpen={onOpen} />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(pts.length);
    for (const b of buttons) {
      expect(b).toHaveAttribute('tabindex', '0');
      expect(b.getAttribute('aria-label')).toMatch(/latency .* ms, fitness .*, AP .* %/);
    }
    await user.tab();
    expect(buttons[0]).toHaveFocus();
    await user.keyboard('{Enter}');
    await user.keyboard(' ');
    await user.click(buttons[1]);
    expect(onOpen).toHaveBeenCalledTimes(3);
    expect(onOpen.mock.calls[0][0].id).toBe(pts[0].id);
    expect(onOpen.mock.calls[2][0].id).toBe(pts[1].id);
  });
  it('switches the x axis to FPS (1000 / latency) with relabelled axis and units', () => {
    const { rerender } = render(<Scatter chartId="c" title="t" yLabel="Fitness" yKind="fitness" xMode="ms" points={pts} segments={[]} onOpen={() => undefined} />);
    expect(screen.getAllByText('Latency [ms]').length).toBeGreaterThan(0);
    rerender(<Scatter chartId="c" title="t" yLabel="Fitness" yKind="fitness" xMode="fps" points={pts} segments={[]} onOpen={() => undefined} />);
    expect(screen.getAllByText(/Throughput \[FPS\] = 1000 \/ latency_ms/).length).toBeGreaterThan(0);
    expect(screen.queryAllByText('Latency [ms]')).toHaveLength(0);
  });
  it('draws exactly the segments it is given and labels the AP axis in percent', () => {
    const { container } = render(<Scatter chartId="c" title="t" yLabel="mAP50-95" yKind="ap" xMode="ms" points={pts} segments={[{ kind: 'representative', key: 'k', label: 'l', points: pts.slice(0, 2) }]} onOpen={() => undefined} />);
    expect(container.querySelectorAll('[data-line="representative"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-line="generation"]')).toHaveLength(0);
    expect(screen.getAllByText(/mAP50-95 \[%\]/).length).toBeGreaterThan(0);
  });
});

describe('Nav (mobile disclosure)', () => {
  function setMobile(matches: boolean): void {
    window.matchMedia = ((q: string) => ({ matches, media: q, addEventListener: () => undefined, removeEventListener: () => undefined, addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false, onchange: null })) as unknown as typeof window.matchMedia;
  }
  it('on mobile the menu button controls an inert list until expanded', async () => {
    setMobile(true);
    const user = userEvent.setup();
    render(<Nav screen="monitor" onSelect={() => undefined} />);
    const btn = screen.getByTestId('menu-btn');
    const nav = document.getElementById('main-nav')!;
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    expect(btn).toHaveAttribute('aria-controls', 'main-nav');
    expect(nav).toHaveAttribute('inert');
    await user.click(btn);
    expect(btn).toHaveAttribute('aria-expanded', 'true');
    expect(nav).not.toHaveAttribute('inert');
    expect(within(nav).getAllByRole('button')).toHaveLength(7);
    expect(within(nav).getByRole('button', { current: 'page' })).toHaveTextContent('탐색 모니터링');
  });
  it('labels the archive-only compare entry so it is not read as a console-run comparison', () => {
    setMobile(false);
    render(<Nav screen="monitor" onSelect={() => undefined} />);
    expect(screen.getByTestId('nav-compare')).toHaveTextContent('Archive run 비교');
  });
  it('on desktop the navigation is always available', () => {
    setMobile(false);
    render(<Nav screen="server" onSelect={() => undefined} />);
    expect(document.getElementById('main-nav')).not.toHaveAttribute('inert');
  });
});

describe('Monitor with a loaded run', () => {
  async function loadMonitor(opts: Parameters<typeof installMockApi>[0] = {}): Promise<ReturnType<typeof userEvent.setup>> {
    const api = installMockApi(opts);
    restore = api.restore;
    const user = userEvent.setup();
    render(
      <AppProvider>
        <App />
      </AppProvider>,
    );
    await user.type(screen.getByLabelText(/Bearer token/), 'T');
    await user.click(screen.getByTestId('connect-btn'));
    await screen.findByText(/status: ok/);
    await user.type(screen.getByTestId('run-id-input'), RUN_ID);
    await user.click(screen.getByTestId('load-run-btn'));
    if (opts.snapshot) await screen.findByTestId('export-btn'); // custom snapshot: the run status may not allow Pause
    else await waitFor(() => expect(screen.getByTestId('pause-btn')).toBeEnabled());
    await user.click(screen.getByTestId('nav-monitor'));
    return user;
  }

  it('Workers table shows vram_mb / power_w / sm_clock_mhz when reported and a dash otherwise (I4-#9)', async () => {
    const snapshot = fixtureSnapshot();
    snapshot.workers = [
      { ...snapshot.workers[0], id: 'w-telemetry', vram_mb: 20480, power_w: 187.25, sm_clock_mhz: 1410 },
      { ...snapshot.workers[0], id: 'w-none', vram_mb: null },
    ];
    await loadMonitor({ snapshot });
    const table = screen.getByTestId('worker-table');
    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent ?? '');
    const col = (label: string): number => headers.findIndex((h) => h.includes(label));
    const cells = (id: string): string[] => within(within(table).getByText(id).closest('tr') as HTMLElement).getAllByRole('cell').map((c) => c.textContent ?? '');
    expect(cells('w-telemetry')[col('VRAM [MB]')]).toBe('20480');
    expect(cells('w-telemetry')[col('Power [W]')]).toBe('187.3');
    expect(cells('w-telemetry')[col('SM clock [MHz]')]).toBe('1410');
    expect(cells('w-none')[col('VRAM [MB]')]).toBe('—');
    expect(cells('w-none')[col('Power [W]')]).toBe('—');
  });

  it('explicit validation-OFF training row shows 학습 완료 · 미평가, other SEARCH_COMPLETE rows keep the raw status (DD-5)', async () => {
    const snapshot = fixtureSnapshot('COMPLETED');
    const run = snapshot.run;
    snapshot.run = { ...run, resolved_protocol: setExplicitTraining(run.resolved_protocol as Record<string, unknown>) as never };
    snapshot.candidates = [
      cand({ candidate_id: 'trained-off', name: 'trained-off', status: 'SEARCH_COMPLETE', ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null, performance_status: 'NOT_EVALUATED', training_mode: 'scratch' } as never),
      cand({ candidate_id: 'plain-complete', name: 'plain-complete', status: 'SEARCH_COMPLETE' }),
    ];
    await loadMonitor({ snapshot });
    const table = screen.getByTestId('candidate-table');
    const rowOf = (name: string): HTMLElement => within(table).getByRole('button', { name: new RegExp(name) }).closest('tr') as HTMLElement;
    expect(rowOf('trained-off')).toHaveTextContent('학습 완료 · 미평가');
    expect(rowOf('trained-off')).not.toHaveTextContent('SEARCH_COMPLETE');
    expect(rowOf('plain-complete')).toHaveTextContent('SEARCH_COMPLETE');
    expect(rowOf('plain-complete')).not.toHaveTextContent('학습 완료 · 미평가');
  });

  it('generation-OFF run with reason_code GENERATION_EMPTY / GENERATION_PARTIAL shows the partial banner text (DD-3)', async () => {
    for (const [code, text] of [['GENERATION_EMPTY', '유효 후보가 0개'], ['GENERATION_PARTIAL', '요청 후보 수에 못 미쳤습니다']] as const) {
      const snapshot = fixtureSnapshot('COMPLETED');
      snapshot.run = { ...snapshot.run, reason_code: code, resolved_protocol: setIn(snapshot.run.resolved_protocol as Record<string, unknown>, 'console.search.evaluation_enabled', false) as never };
      await loadMonitor({ snapshot });
      expect(screen.getByTestId('generation-reason-banner')).toHaveTextContent(code);
      expect(screen.getByTestId('generation-reason-banner')).toHaveTextContent(text);
      cleanup(); restore(); window.history.replaceState(null, '', '#/server');
    }
    const snapshot = fixtureSnapshot('COMPLETED');
    snapshot.run = { ...snapshot.run, reason_code: null, resolved_protocol: setIn(snapshot.run.resolved_protocol as Record<string, unknown>, 'console.search.evaluation_enabled', false) as never };
    await loadMonitor({ snapshot });
    expect(screen.queryByTestId('generation-reason-banner')).not.toBeInTheDocument();
  });

  it('draws two charts with the points of one cohort, a cohort-guard notice, and an equivalent table', async () => {
    await loadMonitor();
    const snap = fixtureSnapshot();
    const expectedAp = selectCohort(buildPlotPoints(snap.candidates, 'ap').points, null);
    const fit = within(screen.getByTestId('chart-fitness')).getAllByTestId('chart-point');
    const ap = within(screen.getByTestId('chart-ap')).getAllByTestId('chart-point');
    expect(ap).toHaveLength(expectedAp.inCohort.length);
    expect(fit).toHaveLength(expectedAp.inCohort.length);
    expect(within(screen.getByTestId('points-table')).getAllByRole('row')).toHaveLength(expectedAp.inCohort.length + 1);
    // failed / training / unresolved rows are not points
    expect(screen.queryByText('ga-g1-failed', { selector: 'g title' })).not.toBeInTheDocument();
    expect(screen.getByTestId('cohort-guard')).toHaveTextContent(/UNRESOLVED_COHORT/);
    // dotted and solid lines exist for closed/consecutive generations; baseline line exists
    const charts = screen.getByTestId('chart-fitness');
    expect(charts.querySelectorAll('[data-line="baseline"]').length).toBeGreaterThan(0);
    expect(charts.querySelectorAll('[data-line="generation"]').length).toBe(3); // GA g0, GA g1, Random g0 (open g2 has none)
    expect(charts.querySelectorAll('[data-line="representative"]').length).toBe(1);
    // open generation 2 is listed as open in the progress table
    expect(within(screen.getByTestId('generation-table')).getAllByText(/open/).length).toBeGreaterThan(0);
  });

  it('opens the candidate dialog with Enter on a focused point and restores focus on ESC', async () => {
    const user = await loadMonitor();
    const point = within(screen.getByTestId('chart-fitness')).getAllByTestId('chart-point')[0];
    point.focus();
    await user.keyboard('{Enter}');
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText(/Identity/)).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(point).toHaveFocus();
  });

  it('shows costs in GPU-hours with raw seconds, and a dash (not 0) for unmeasured values', async () => {
    await loadMonitor();
    const costs = screen.getByTestId('costs');
    expect(within(costs).getByText('1.500 GPU-h')).toHaveAttribute('title', expect.stringContaining('5400'));
    expect(within(costs).getAllByText('—').length).toBeGreaterThanOrEqual(3); // final / failed / retry / profile
    expect(within(costs).queryByText('0.000 GPU-h')).not.toBeInTheDocument();
  });

  it('the FPS toggle relabels both axes and table header', async () => {
    const user = await loadMonitor();
    await user.click(screen.getByTestId('x-fps'));
    expect(screen.getAllByText(/Throughput \[FPS\]/).length).toBeGreaterThanOrEqual(2);
    expect(within(screen.getByTestId('points-table')).getByText(/FPS \[1000\/latency\]/)).toBeInTheDocument();
  });

  it('modal shows lineage with parent A/B badges, the public block source kinds and YAML rows (no owner-only repair trace) and 4 artifact panels', async () => {
    const user = await loadMonitor();
    // candidate ga-g1-1 has 2 parents and BLOCKS
    const row = within(screen.getByTestId('candidate-table')).getByRole('button', { name: /ga-g1-1/ });
    await user.click(row);
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByTestId('lineage-op')).toHaveTextContent('crossover (2 parents)');
    expect(within(dlg).getByText('Parent A')).toBeInTheDocument();
    expect(within(dlg).getByText('Parent B')).toBeInTheDocument();
    const blocks = within(dlg).getByTestId('block-table');
    for (const t of ['A', 'B', 'M (mutation)']) expect(within(blocks).getAllByText(t).length).toBeGreaterThan(0);
    // contract 1.5.0 / DD-A19: the repaired row keeps the kind it was repaired from (B) and the YAML row is shown;
    // repair kind, repair reason, old name and before/after are owner-only: no column and no text for them
    expect(within(blocks).getAllByText('B')).toHaveLength(2);
    expect(within(blocks).getByText(/module=Conv · from=\[-1,2\] · repeats=1 · args=\[64,1,1\] · scope=head/)).toBeInTheDocument();
    expect(within(blocks).getAllByText('mutate')).toHaveLength(1);
    for (const gone of [/R \(repair\)/, /Repair reason/, /old_name/, /^Before$/, /^After$/]) expect(within(blocks).queryByText(gone)).toBeNull();
    expect(BLOCKS).toHaveLength(4);
    expect(within(dlg).getByText(/Metrics \(raw/)).toBeInTheDocument();
    // DD-A8 / WP-6 R1: only the server's fitness is shown; the "Client recompute" row is gone
    expect(within(dlg).getByText(/Fitness \(server\)/)).toBeInTheDocument();
    expect(within(dlg).queryByText(/Client recompute/)).toBeNull();
  });
});

describe('ArtifactImage states', () => {
  const base = artifactSet('e1')[0];
  const mkClient = (f: (u: string, init: RequestInit) => Promise<Response>, origin = '') => new ApiClient({ origin, token: 'T', pageOrigin: 'http://x', fetchImpl: f as unknown as typeof fetch });
  const json = (status: number): Response => new Response(JSON.stringify({ code: 'X', message: 'm' }), { status, headers: { 'content-type': 'application/json' } });

  it.each([
    [{ state: 'NOT_READY' as const }, 'NOT_READY'],
    [{ state: 'ACCESS_DENIED' as const }, 'ACCESS_DENIED'],
    [{ state: 'MISSING' as const }, 'MISSING'],
    [{ state: 'LOAD_FAILED' as const }, 'LOAD_FAILED'],
  ])('server-reported state %j is shown as text', async (over, text) => {
    render(<ArtifactImage artifact={{ ...base, ...over }} runId="r" client={mkClient(async () => json(500))} label="GT" />);
    expect(await screen.findByText(text, { exact: false })).toBeInTheDocument();
    expect(screen.getByTestId('artifact-gt')).toHaveAttribute('data-state', text);
  });

  it('bearer_fetch HTTP statuses 403 / 404 / 409 end in ACCESS_DENIED / MISSING / NOT_READY (no endless spinner)', async () => {
    for (const [status, state] of [[403, 'ACCESS_DENIED'], [404, 'MISSING'], [409, 'NOT_READY']] as const) {
      const { unmount } = render(<ArtifactImage artifact={{ ...base, auth_mode: 'bearer_fetch', url: '/api/v1/runs/r/artifacts/a' }} runId="r" client={mkClient(async () => json(status))} label="GT" />);
      await waitFor(() => expect(screen.getByTestId('artifact-gt')).toHaveAttribute('data-state', state));
      unmount();
    }
  });

  it('EXPIRED tries the refresh endpoint once and then shows EXPIRED', async () => {
    const f = vi.fn(async (_u: string, _i: RequestInit) => new Response(JSON.stringify({ ...base, state: 'EXPIRED', expires_at: '2000-01-01T00:00:00Z' }), { status: 200, headers: { 'content-type': 'application/json' } }));
    render(<ArtifactImage artifact={{ ...base, state: 'EXPIRED' }} runId="r" client={mkClient(f)} label="GT" />);
    await waitFor(() => expect(screen.getByTestId('artifact-gt')).toHaveAttribute('data-state', 'EXPIRED'));
    expect(f).toHaveBeenCalledTimes(1);
    expect(f.mock.calls[0][0]).toContain('/refresh');
  });

  it('signed_url renders a plain <img> without credentials; bearer_fetch renders a blob: URL and revokes it on unmount', async () => {
    const created: string[] = [];
    const revoked: string[] = [];
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => { const u = `blob:test-${created.length}`; created.push(u); return u; }, revokeObjectURL: (u: string) => revoked.push(u) }));
    try {
      const f = vi.fn(async (_u: string, _i: RequestInit) => new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { 'content-type': 'image/png' } }));
      const signed = render(<ArtifactImage artifact={{ ...base, auth_mode: 'signed_url', url: 'https://cdn.example/x.png?sig=1' }} runId="r" client={mkClient(f)} label="GT" />);
      const img = (await screen.findByRole('img')) as HTMLImageElement;
      expect(img.getAttribute('src')).toBe('https://cdn.example/x.png?sig=1');
      expect(img.getAttribute('referrerpolicy')).toBe('no-referrer');
      expect(f).not.toHaveBeenCalled();
      signed.unmount();

      const bearer = render(<ArtifactImage artifact={{ ...base, auth_mode: 'bearer_fetch', url: '/api/v1/runs/r/artifacts/a' }} runId="r" client={mkClient(f)} label="GT" />);
      const img2 = (await screen.findByRole('img')) as HTMLImageElement;
      expect(img2.getAttribute('src')).toBe('blob:test-0');
      expect((f.mock.calls[0][1].headers as Record<string, string>).Authorization).toBe('Bearer T');
      bearer.unmount();
      expect(revoked).toContain('blob:test-0');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('changing the artifact revokes the previous blob URL', async () => {
    const revoked: string[] = [];
    let n = 0;
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => `blob:b${n++}`, revokeObjectURL: (u: string) => revoked.push(u) }));
    try {
      const f = async (): Promise<Response> => new Response(new Uint8Array([1]), { status: 200, headers: { 'content-type': 'image/png' } });
      const client = mkClient(f);
      const a = { ...base, auth_mode: 'bearer_fetch' as const, url: '/api/v1/runs/r/artifacts/a' };
      const { rerender } = render(<ArtifactImage artifact={a} runId="r" client={client} label="GT" />);
      await screen.findByRole('img');
      await act(async () => {
        rerender(<ArtifactImage artifact={{ ...a, url: '/api/v1/runs/r/artifacts/b' }} runId="r" client={client} label="GT" />);
      });
      await waitFor(() => expect(revoked).toContain('blob:b0'));
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('shows N/A sample recall when the sample has no ground truth', async () => {
    const { ArtifactPanel } = await import('../src/components/CandidateView');
    const c = cand({ id: 'e1', artifacts: artifactSet('e1').map((a) => ({ ...a, matching: { counts: { tp: 0, fp: 2, fn: 0, gt: 0 }, sample_recall: null } })) });
    render(<ArtifactPanel c={c} runId="r" client={mkClient(async () => json(404))} />);
    expect(screen.getByText(/N\/A/)).toBeInTheDocument();
    expect(screen.getByText(/0 \/ 2 \/ 0 \/ 0|0 \/ 2 \/ 0 \/ 0/)).toBeInTheDocument();
  });
});

describe('fixtures sanity', () => {
  it('fixture generation counts satisfy the invariant used by the UI', () => {
    for (const g of fixtureGenerations().filter((x) => x.complete)) {
      expect((g.evaluated_count ?? 0) + (g.failed_count ?? 0) + (g.not_admitted_count ?? 0) + (g.pending_count ?? 0)).toBe(g.planned_count);
    }
  });
});
