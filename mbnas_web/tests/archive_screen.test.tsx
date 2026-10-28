import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { ArchiveScatter } from '../src/components/archive/ArchiveScatter';
import { AppProvider, ARCHIVE_POLL_MS } from '../src/state/context';
import { bigRows, fixtureBaselines, fixtureRun, fixtureRunList, installArchiveFetch, newFixtureState, RUN_A, RUN_B, RUN_C, SYNTH_A, type FixtureState } from './archive_fixtures';
import type { ArchiveRunSummary } from '../src/archive/types';

let restore: () => void = () => undefined;
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:synthetic-plot') as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  cleanup();
  restore();
  vi.useRealTimers();
  window.history.replaceState(null, '', '#/server');
});

function mount(state: FixtureState, hash = '#/archive', hook?: Parameters<typeof installArchiveFetch>[1]): ReturnType<typeof installArchiveFetch> {
  window.history.replaceState(null, '', hash);
  const api = installArchiveFetch(state, hook);
  restore = api.restore;
  render(
    <AppProvider>
      <App />
    </AppProvider>,
  );
  return api;
}

async function connect(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText('API 토큰'), 'TEST-TOKEN');
  await user.click(screen.getByTestId('gate-connect'));
  await screen.findByTestId('run-table');
}

describe('connection gate and run list', () => {
  it('shows the gate first, sends no archive request before connecting, and keeps the token out of storage and URL', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const user = userEvent.setup();
    const api = mount(newFixtureState());
    expect(screen.getByTestId('connect-gate')).toBeInTheDocument();
    expect(screen.queryByTestId('run-table')).toBeNull();
    expect(api.calls.filter((c) => c.path.includes('/archive/'))).toEqual([]);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('NAS 아카이브');
    await connect(user);
    expect(screen.queryByTestId('connect-gate')).toBeNull();
    const arch = api.calls.filter((c) => c.path.startsWith('/api/v1/archive/'));
    expect(arch.length).toBeGreaterThan(0);
    expect(arch.every((c) => c.method === 'GET' && c.headers.Authorization === 'Bearer TEST-TOKEN')).toBe(true);
    expect(setItem).not.toHaveBeenCalled();
    expect(window.location.href).not.toContain('TEST-TOKEN');
    setItem.mockRestore();
  });

  it('lists the runs with status text, filters by dataset and status, and warns about unreachable roots', async () => {
    const user = userEvent.setup();
    mount(newFixtureState());
    await connect(user);
    const table = screen.getByTestId('run-table');
    expect(within(table).getByTestId(`run-pick-${RUN_A}`)).toBeInTheDocument();
    expect(within(table).getByTestId(`run-pick-${RUN_B}`)).toBeInTheDocument();
    expect(within(table).getAllByTestId('status-text').map((e) => e.textContent).sort()).toEqual(['? 알 수 없음'.replace('? 알 수 없음', '✔ 완료'), '▶ 실행 중', '✖ 실패'].sort());
    expect(screen.getByTestId('roots-missing')).toHaveTextContent('/synthetic/missing-root');
    await user.selectOptions(screen.getByTestId('filter-dataset'), 'synthetic-dataset-two');
    expect(within(screen.getByTestId('run-table')).queryByTestId(`run-pick-${RUN_A}`)).toBeNull();
    expect(within(screen.getByTestId('run-table')).getByTestId(`run-pick-${RUN_C}`)).toBeInTheDocument();
    await user.selectOptions(screen.getByTestId('filter-dataset'), '');
    await user.selectOptions(screen.getByTestId('filter-status'), 'running');
    expect(within(screen.getByTestId('run-table')).getAllByRole('button', { name: /선택$/ })).toHaveLength(1);
  });

  it('a failing run list shows an error banner and the page stays usable', async () => {
    const user = userEvent.setup();
    const st = newFixtureState({ failAll: true });
    mount(st);
    await user.type(screen.getByLabelText('API 토큰'), 'TEST-TOKEN');
    await user.click(screen.getByTestId('gate-connect'));
    expect(await screen.findByTestId('runs-error')).toHaveTextContent('HTTP 500');
    st.failAll = false;
    await user.click(screen.getByTestId('runs-refresh'));
    expect(await screen.findByTestId(`run-pick-${RUN_A}`)).toBeInTheDocument();
    expect(screen.queryByTestId('runs-error')).toBeNull();
  });
});

describe('selected run', () => {
  async function openA(): Promise<ReturnType<typeof userEvent.setup>> {
    const user = userEvent.setup();
    mount(newFixtureState());
    await connect(user);
    await user.click(screen.getByTestId(`run-pick-${RUN_A}`));
    await screen.findByTestId('overview');
    return user;
  }

  it('baseline card highlights the judge baseline with its tier, lists reference baselines and the anchor, missing as dash', async () => {
    await openA();
    await screen.findByTestId('baseline-table');
    const judge = screen.getByTestId('judge-baseline-row');
    expect(judge).toHaveTextContent('baseline_yolo26n');
    expect(judge).toHaveTextContent('판정 기준선');
    const table = screen.getByTestId('baseline-table');
    const judgeTr = judge.closest('tr') as HTMLElement;
    expect(within(judgeTr).getByText('final 12 ep')).toBeInTheDocument();
    expect(within(judgeTr).getByText('0.4939')).toBeInTheDocument();
    expect(within(judgeTr).getByText('2,375,031')).toBeInTheDocument();
    expect(within(table).getByTestId('anchor-row').closest('tr')).toHaveTextContent('T@8e');
    // the failed reference baseline has no metrics: dashes, never 0
    const failed = within(table).getByText('baseline_yolo26n_p2').closest('tr') as HTMLElement;
    expect(failed).toHaveTextContent('—');
    expect(failed.textContent).not.toMatch(/0\.0000/);
    expect(screen.getByTestId('baseline-summary')).toHaveTextContent('baseline_yolo26n');
    expect(window.location.hash).toBe(`#/archive?run=${RUN_A}`);
  });

  it('overview: status, verdicts, stage timeline, gates with FAIL and WARN first, roles', async () => {
    await openA();
    const ov = screen.getByTestId('overview');
    expect(within(ov).getByTestId('status-text')).toHaveTextContent('완료');
    expect(within(ov).getByText(/notebook PASS/)).toBeInTheDocument();
    expect(within(ov).getAllByRole('listitem').length).toBeGreaterThanOrEqual(3); // stage timeline items
    expect(screen.getByTestId('stage-timeline')).toHaveTextContent('P10-1');
    const gates = screen.getByTestId('gate-table');
    const names = within(gates).getAllByRole('row').slice(1).map((r) => r.textContent ?? '');
    expect(names[0]).toContain('FAIL');
    expect(names[1]).toContain('WARN');
    expect(screen.getByTestId('gates')).toHaveAttribute('open'); // FAIL / WARN present: expanded
    expect(within(ov).getByTestId('roles')).toHaveTextContent('BEST_ACCURACY');
    expect(screen.getByTestId('budget')).toBeInTheDocument();
  });

  it('generation table shows the deltas and the lineage modes; the baseline delta is footnoted as a different tier', async () => {
    await openA();
    const table = await screen.findByTestId('generation-table');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent('seeded_anchor 6');
    expect(rows[1]).toHaveTextContent('ga_crossover');
    // generation 0 has no previous generation: its delta is a dash, not 0
    expect(within(rows[0]).getAllByText(/\(—\)/).length).toBeGreaterThan(0);
    expect(screen.getByText(/티어가 달라 참고용/)).toBeInTheDocument();
    expect(screen.getByTestId('chart-gen-map')).toBeInTheDocument();
    expect(screen.getByTestId('ref-baseline-map')).toHaveTextContent('final 12 ep');
  });

  it('clicking a generation opens the detail panel (hash gen=), with improvements, new fp, lineage and the module histogram delta', async () => {
    const user = await openA();
    await user.click(await screen.findByTestId('gen-open-2'));
    const panel = await screen.findByTestId('gen-detail');
    expect(window.location.hash).toBe(`#/archive?run=${RUN_A}&gen=2`);
    expect(await within(panel).findByTestId('improvements')).toBeInTheDocument();
    expect(within(panel).getByTestId('gen-candidates')).toBeInTheDocument();
    expect(within(panel).getAllByText('새 fp').length).toBeGreaterThan(0);
    expect(within(panel).getByTestId('lineage-summary')).toHaveTextContent('ga_crossover');
    const hist = within(panel).getByTestId('module-hist');
    expect(hist).toHaveTextContent('Conv');
    // delta cells are signed numbers computed against the previous generation
    const deltas = within(hist).getAllByTestId(/hist-delta-/).map((e) => e.textContent ?? '');
    expect(deltas.every((d) => /^([+-]?\d+|—)$/.test(d))).toBe(true);
    expect(within(panel).getByTestId('distributions')).toBeInTheDocument();
    expect(within(panel).getByTestId('head-mode')).toHaveTextContent('e2e');
    expect(within(panel).getByTestId('front-change')).toBeInTheDocument();
    await user.click(screen.getByTestId('gen-next'));
    await waitFor(() => expect(window.location.hash).toBe(`#/archive?run=${RUN_A}&gen=3`));
    await user.click(screen.getByTestId('gen-close'));
    expect(screen.queryByTestId('gen-detail')).toBeNull();
    expect(window.location.hash).toBe(`#/archive?run=${RUN_A}`);
  });

  it('a generation chart point is a keyboard button that selects the generation', async () => {
    const user = await openA();
    const chart = await screen.findByTestId('chart-gen-hv');
    const pt = within(chart).getAllByTestId('line-point')[1];
    expect(pt).toHaveAttribute('role', 'button');
    pt.focus();
    await user.keyboard('{Enter}');
    expect(await screen.findByTestId('gen-detail')).toBeInTheDocument();
    expect(window.location.hash).toContain('gen=1');
  });

  it('candidate table: a missing latency is a dash, failed rows keep their status, filters work, a row opens the modal', async () => {
    const user = await openA();
    const table = await screen.findByTestId('candidate-table');
    const missing = SYNTH_A.rows.find((r) => r.status === 'ok' && r.metrics?.latency_median_ms == null && r.tier === 'T@8e');
    expect(missing).toBeTruthy();
    const tr = within(table).getAllByTestId(`cand-open-${missing?.candidate_id}`)[0].closest('tr') as HTMLElement;
    expect(within(tr).getAllByText('—').length).toBeGreaterThan(0);
    expect(within(table).getAllByText('oom').length).toBe(1);
    expect(within(table).getAllByText('train_fail').length).toBe(1);
    await user.selectOptions(screen.getByTestId('cand-filter-status'), 'oom');
    expect(within(screen.getByTestId('candidate-table')).getAllByRole('row')).toHaveLength(2); // header + 1
    await user.selectOptions(screen.getByTestId('cand-filter-status'), '');
    await user.selectOptions(screen.getByTestId('cand-filter-role'), 'BEST_ACCURACY');
    const rows = within(screen.getByTestId('candidate-table')).getAllByRole('row').slice(1);
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((r) => r.textContent?.includes('BEST_ACCURACY'))).toBe(true);
    await user.click(within(rows[0]).getByRole('button'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(window.location.hash).toContain('cid=');
  });

  it('scatter: generation slider limits the points, point is a button that opens the candidate, table view lists the same rows', async () => {
    const user = await openA();
    const card = await screen.findByTestId('scatter-count');
    const all = Number(/표시 (\d+)행/.exec(card.textContent ?? '')?.[1]);
    expect(all).toBeGreaterThan(5);
    fireEvent.change(screen.getByTestId('scatter-gen'), { target: { value: '0' } });
    await waitFor(() => expect(screen.getByTestId('scatter-gen-value')).toHaveTextContent('0'));
    const g0 = Number(/표시 (\d+)행/.exec(screen.getByTestId('scatter-count').textContent ?? '')?.[1]);
    expect(g0).toBeLessThan(all);
    expect(g0).toBeGreaterThan(0);
    const a = screen.getByTestId('chart-scatter-a');
    const pts = within(a).getAllByTestId('scatter-point');
    expect(pts).toHaveLength(g0);
    expect(pts.every((p) => p.getAttribute('data-gen') === '0')).toBe(true);
    // baseline squares and the anchor star are drawn with their names
    expect(within(a).getAllByTestId('scatter-baseline').length).toBeGreaterThanOrEqual(2);
    expect(within(a).getByTestId('scatter-anchor')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('scatter-gen'), { target: { value: String(Number((screen.getByTestId('scatter-gen') as HTMLInputElement).max)) } });
    await waitFor(() => expect(screen.getByTestId('front-line')).toBeInTheDocument());
    // front points carry the ring flag
    expect(within(screen.getByTestId('chart-scatter-a')).getAllByTestId('scatter-point').some((p) => p.getAttribute('data-front') === 'true')).toBe(true);
    const target = within(screen.getByTestId('chart-scatter-a')).getAllByTestId('scatter-point')[0];
    target.focus();
    await user.keyboard('{Enter}');
    const dlg = await screen.findByRole('dialog');
    expect(dlg).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await user.click(screen.getByTestId('scatter-view-table'));
    const t = await screen.findByTestId('scatter-table');
    expect(within(t).getAllByRole('row').length - 1).toBe(all);
  });

  it('y-metric selection changes the plotted values; a tier filter switches the front to the client calculation', async () => {
    const user = await openA();
    await screen.findByTestId('scatter-count');
    expect(screen.getByTestId('scatter-legend')).toHaveTextContent('서버 산출');
    await user.selectOptions(screen.getByTestId('scatter-tier'), 'T@10e');
    expect(screen.getByTestId('scatter-legend')).toHaveTextContent('클라이언트 계산');
    await user.selectOptions(screen.getByTestId('scatter-y'), 'small_target_recall');
    expect(within(screen.getByTestId('chart-scatter-a')).queryByTestId('front-line')).toBeNull(); // front is defined on mAP50-95
    expect(screen.getByTestId('scatter-legend')).toHaveTextContent('HV는 런타임 산출값');
  });

  it('plots: the loaded plot is an image, the missing one says MISSING, the token only travels in the header', async () => {
    const api = mount(newFixtureState());
    const user = userEvent.setup();
    await connect(user);
    await user.click(screen.getByTestId(`run-pick-${RUN_A}`));
    await waitFor(() => expect(screen.getByTestId('plot-plot_pareto_map50_latency')).toHaveAttribute('data-state', 'READY'));
    expect(within(screen.getByTestId('plot-plot_pareto_map50_latency')).getByRole('img')).toHaveAttribute('src', 'blob:synthetic-plot');
    expect(screen.getByTestId('plot-plot_params_vs_map50')).toHaveAttribute('data-state', 'MISSING');
    const fileCalls = api.calls.filter((c) => c.path.includes('/files/'));
    expect(fileCalls.length).toBe(4);
    expect(fileCalls.every((c) => c.headers.Authorization === 'Bearer TEST-TOKEN' && !c.path.includes('TEST-TOKEN'))).toBe(true);
  });

  it('fidelity and confirm cards show the numbers with the non-evidence label', async () => {
    await openA();
    const f = await screen.findByTestId('fidelity');
    expect(f).toHaveTextContent('0.8095');
    expect(f).toHaveTextContent('0.6429');
    expect(f).toHaveTextContent('비증거');
    const c = await screen.findByTestId('confirm');
    expect(c).toHaveTextContent('CONFIRMED');
    expect(within(c).getByTestId('confirm-table')).toHaveTextContent('0.5200');
    // test_native of the synthetic finalist is null: a dash
    expect(within(within(c).getByTestId('confirm-table')).getByText('synthetic_final_a').closest('tr')).toHaveTextContent('—');
  });
});

describe('missing data', () => {
  it('a run without baselines, generations or candidates still renders every card with explicit messages', async () => {
    const user = userEvent.setup();
    mount(newFixtureState());
    await connect(user);
    await user.click(screen.getByTestId(`run-pick-${RUN_C}`));
    expect(await screen.findByTestId('baseline-missing')).toBeInTheDocument();
    expect(await screen.findByTestId('gens-empty')).toBeInTheDocument();
    expect(screen.getByTestId('stage-missing')).toBeInTheDocument();
    expect(screen.getByTestId('driver-records')).toHaveTextContent('stagnation>=3');
    expect(screen.getByTestId('confirm-missing')).toBeInTheDocument();
    expect(screen.getByTestId('fidelity-missing')).toBeInTheDocument();
    expect(screen.getAllByTestId('archive-warnings').map((e) => e.textContent).join(' ')).toContain('baselines_v2.json is missing');
  });

  it('an unknown run id in the deep link shows an error banner, not a blank page', async () => {
    const user = userEvent.setup();
    mount(newFixtureState(), '#/archive?run=synthetic-no-such-run');
    await connect(user);
    expect((await screen.findAllByTestId('archive-error'))[0]).toHaveTextContent('HTTP 404 ARCHIVE_RUN_NOT_FOUND');
    expect(screen.getByTestId('run-table')).toBeInTheDocument();
    expect(screen.getByTestId('baseline-error')).toBeInTheDocument(); // the baseline card is present even so
  });
});

describe('deep link restore', () => {
  it('run, generation and candidate from the hash are restored after connecting', async () => {
    const user = userEvent.setup();
    const cid = SYNTH_A.rows.find((r) => r.generation === 2 && r.status === 'ok')?.candidate_id as string;
    mount(newFixtureState(), `#/archive?run=${RUN_A}&gen=2&cid=${cid}`);
    expect(window.location.hash).toContain(cid);
    await connect(user);
    expect(await screen.findByTestId('gen-detail')).toBeInTheDocument();
    expect(await screen.findByRole('dialog')).toHaveTextContent(cid);
    expect(screen.getByTestId('acand-title')).toHaveTextContent(cid);
    await user.keyboard('{Escape}');
    await waitFor(() => expect(window.location.hash).toBe(`#/archive?run=${RUN_A}&gen=2`));
  });
});

describe('deep link before connecting', () => {
  it('does not open the candidate dialog (it would cover the token form); the gate is usable', () => {
    mount(newFixtureState(), `#/archive?run=${RUN_A}&cid=synthetic-g000c0000`);
    expect(screen.getByTestId('connect-gate')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('running run polling', () => {
  it('polls every 10 s, keeps the last data when a poll fails, retries, and stops when the run is done', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const st = newFixtureState();
    const api = mount(st);
    await connect(user);
    await user.click(screen.getByTestId(`run-pick-${RUN_B}`));
    const table = await screen.findByTestId('generation-table');
    expect(within(table).getAllByRole('row').slice(1)).toHaveLength(2);
    expect(screen.getByTestId('live-card')).toBeInTheDocument();
    expect(screen.getAllByTestId('archive-warnings').map((e) => e.textContent).join(' ')).toContain('partial line');
    const detailHits = (): number => api.calls.filter((c) => c.path === `/api/v1/archive/runs/${RUN_B}`).length;
    const before = detailHits();
    // a new generation appears
    st.bGens = 3;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARCHIVE_POLL_MS + 200);
    });
    await waitFor(() => expect(within(screen.getByTestId('generation-table')).getAllByRole('row').slice(1)).toHaveLength(3));
    expect(detailHits()).toBeGreaterThan(before);
    expect(screen.getByTestId('poll-status')).toHaveTextContent('10초마다');
    // network trouble: the table stays, an error note is shown
    st.failAll = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARCHIVE_POLL_MS + 200);
    });
    await waitFor(() => expect(screen.getByTestId('poll-error')).toBeInTheDocument());
    expect(within(screen.getByTestId('generation-table')).getAllByRole('row').slice(1)).toHaveLength(3);
    // recovery
    st.failAll = false;
    st.bGens = 4;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARCHIVE_POLL_MS + 200);
    });
    await waitFor(() => expect(within(screen.getByTestId('generation-table')).getAllByRole('row').slice(1)).toHaveLength(4));
    expect(screen.queryByTestId('poll-error')).toBeNull();
    // finished: polling stops
    st.bRunning = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARCHIVE_POLL_MS + 200);
    });
    await waitFor(() => expect(screen.queryByTestId('live-card')).toBeNull());
    const after = detailHits();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARCHIVE_POLL_MS * 3);
    });
    expect(detailHits()).toBe(after);
  });

  it('a finished run is not polled', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const api = mount(newFixtureState());
    await connect(user);
    await user.click(screen.getByTestId(`run-pick-${RUN_A}`));
    await screen.findByTestId('overview');
    const n = api.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARCHIVE_POLL_MS * 2);
    });
    expect(api.calls.length).toBe(n);
  });
});

describe('performance sanity (jsdom)', () => {
  it('renders 2,000 plotted rows in both scatter charts well inside a second-scale budget and keeps the DOM bounded', () => {
    const rows = bigRows(2000);
    const t0 = performance.now();
    render(<ArchiveScatter rows={rows} generations={[]} baselines={fixtureBaselines(RUN_A)} judgeName="baseline_yolo26n" onOpen={() => undefined} />);
    const dt = performance.now() - t0;
    expect(screen.getByTestId('scatter-count')).toHaveTextContent('표시 2000행');
    expect(within(screen.getByTestId('chart-scatter-a')).getAllByTestId('scatter-point')).toHaveLength(2000);
    // with 2,000 points only the front and the newest generation get a tab stop
    const tabbable = within(screen.getByTestId('chart-scatter-a')).getAllByTestId('scatter-point').filter((p) => p.getAttribute('tabindex') === '0');
    expect(tabbable.length).toBeLessThan(400);
    expect(dt).toBeLessThan(3000);
  });
});

describe('campaign runs (WP-S7: read-only 12-search adapter)', () => {
  // SYNTHETIC summaries shaped like the server's campaign arms: plain ArchiveRunSummary keys + additive campaign_id/space/method/seed/pair/progress
  const CID = 'vd12_synthetic_campaign';
  const arm = (space: string, method: string, seed: number, over: Record<string, unknown> = {}): ArchiveRunSummary => {
    const base = fixtureRunList().runs[1];
    return {
      ...base,
      id: `${CID}__${space}__${method}__seed_${seed}`,
      family: 'visdrone-campaign',
      path: `/synthetic/campaign/arms/${space}/${method}/seed_${seed}`,
      status: 'running',
      status_detail: 'arm process 1 alive (attempt 1; stage search)',
      campaign_id: CID,
      campaign_arm: `${space}/${method}/seed_${seed}`,
      space,
      method,
      seed,
      pair: 1,
      pair_done: false,
      campaign_arms_total: 12,
      progress: { generations_seen: 12, candidates: { total: 123, ok: 115, eval_fail: 1, degenerate: 3 }, last_eval_at: '2026-10-23T11:26:16Z' },
      ...over,
    } as ArchiveRunSummary;
  };
  const CAMPAIGN_RUNS = [
    arm('S2', 'random', 42, { status: 'done', pair: 0, pair_done: true, progress: { generations_seen: 16, candidates: { total: 200, ok: 179, eval_fail: 8, degenerate: 6 }, last_eval_at: '2026-10-22T22:00:00Z' } }),
    arm('S1', 'ga', 42),
    arm('S2', 'ga', 42, { status: 'failed', status_detail: 'BLOCKED_CONTRACT: arm blocked', pair: 0, pair_done: true, progress: null }),
    arm('S1', 'random', 42, { progress: { generations_seen: 11 } }),
  ];
  const hookWithCampaign = (url: URL): Response | undefined => {
    const json = (body: unknown): Response => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    if (url.pathname === '/api/v1/archive/runs') {
      const l = fixtureRunList();
      return json({ ...l, roots: [...l.roots, { path: '/synthetic/campaign', family: 'visdrone-campaign', exists: true, kind: 'campaign' }], runs: [...l.runs, ...CAMPAIGN_RUNS] });
    }
    const m = /^\/api\/v1\/archive\/runs\/([^/]+)$/.exec(url.pathname);
    const hit = m ? CAMPAIGN_RUNS.find((r) => r.id === decodeURIComponent(m[1])) : undefined;
    return hit ? json({ ...fixtureRun(RUN_B), ...hit }) : undefined;
  };

  it('groups the arms under their campaign (not in the plain run table), sorted space / seed / method, with status and ledger progress', async () => {
    const user = userEvent.setup();
    mount(newFixtureState(), '#/archive', hookWithCampaign);
    await connect(user);
    const plain = screen.getByTestId('run-table');
    expect(within(plain).getByTestId(`run-pick-${RUN_A}`)).toBeInTheDocument();
    expect(within(plain).queryByTestId(`run-pick-${CID}__S1__ga__seed_42`)).toBeNull(); // arms are not in the plain table
    expect(plain).toHaveAttribute('aria-label', '아카이브 run 3 / 3');
    const card = await screen.findByTestId(`campaign-${CID}`);
    expect(within(card).getByTestId(`campaign-toggle-${CID}`)).toHaveAttribute('aria-expanded', 'true');
    expect(within(card).getByTestId(`campaign-summary-${CID}`)).toHaveTextContent('arm 4 / 12');
    expect(within(card).getByTestId(`campaign-summary-${CID}`)).toHaveTextContent('실행 중 2');
    expect(within(card).getByTestId(`campaign-summary-${CID}`)).toHaveTextContent('완료 1');
    expect(within(card).getByTestId(`campaign-summary-${CID}`)).toHaveTextContent('실패 1');
    const table = within(card).getByTestId(`campaign-table-${CID}`);
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getByRole('button').textContent)).toEqual(['S1 / ga / seed 42', 'S1 / random / seed 42', 'S2 / ga / seed 42', 'S2 / random / seed 42']);
    expect(within(rows[0]).getByTestId('status-text')).toHaveTextContent('실행 중');
    expect(rows[0]).toHaveTextContent('12'); // generations seen
    expect(rows[0]).toHaveTextContent('115 / 1 / 3 / 123'); // ok / eval_fail / degenerate / total
    expect(rows[3]).toHaveTextContent('179 / 8 / 6 / 200');
    expect(rows[3]).toHaveTextContent('2026-10-22');
    expect(within(rows[2]).getByTestId('status-text')).toHaveTextContent('실패');
    // progress missing or partial -> the plain run counts (ok / total of the summary) and dashes for the rest, never an invented number
    const plainCounts = `${fixtureRunList().runs[1].candidates.ok} / — / — / ${fixtureRunList().runs[1].candidates.total}`;
    expect(rows[1]).toHaveTextContent(plainCounts);
    expect(rows[2]).toHaveTextContent(plainCounts);
  });

  it('collapses and expands a campaign', async () => {
    const user = userEvent.setup();
    mount(newFixtureState(), '#/archive', hookWithCampaign);
    await connect(user);
    await screen.findByTestId(`campaign-table-${CID}`);
    await user.click(screen.getByTestId(`campaign-toggle-${CID}`));
    expect(screen.getByTestId(`campaign-toggle-${CID}`)).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId(`campaign-table-${CID}`)).toBeNull();
    expect(screen.getByTestId(`campaign-summary-${CID}`)).toHaveTextContent('arm 4 / 12'); // the summary stays visible
    await user.click(screen.getByTestId(`campaign-toggle-${CID}`));
    expect(screen.getByTestId(`campaign-table-${CID}`)).toBeInTheDocument();
  });

  it('a campaign row opens the run by its id (hash and detail request use the full id)', async () => {
    const user = userEvent.setup();
    const api = mount(newFixtureState(), '#/archive', hookWithCampaign);
    await connect(user);
    const id = `${CID}__S2__ga__seed_42`;
    await user.click(await screen.findByTestId(`run-pick-${id}`));
    await waitFor(() => expect(window.location.hash).toBe(`#/archive?run=${id}`));
    expect(screen.getByTestId(`run-pick-${id}`)).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(api.calls.some((c) => c.path === `/api/v1/archive/runs/${id}`)).toBe(true));
    expect(await screen.findByTestId('overview')).toBeInTheDocument();
  });

  it('without campaign runs there is no campaign card and the plain table is unchanged', async () => {
    const user = userEvent.setup();
    mount(newFixtureState());
    await connect(user);
    expect(screen.queryByTestId('archive-campaigns')).toBeNull();
    expect(screen.queryByText('12-search 캠페인')).toBeNull();
    expect(screen.getByTestId('run-table')).toHaveAttribute('aria-label', '아카이브 run 3 / 3');
  });
});
