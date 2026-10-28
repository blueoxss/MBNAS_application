/**
 * Final retraining (contract v1.2.0): idempotency key reuse, form checks, error classification, the Final Retrain
 * panel flow, FINAL_RETRAIN run presentation (kind, parent link, finalists), own final cohort, lineage 'final',
 * parent/child History, worker status vocabulary, GA crossover / benchmark postprocess settings, defaulted diff rows.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { ApiClient, ApiError } from '../src/api/client';
import { lineageOperation } from '../src/components/CandidateView';
import { applyFacetFilters, buildPlotPoints, cohortKey, cohortLabel, isFinalRow, selectCohort, UNRESOLVED_COHORT } from '../src/lib/cohort';
import { canFinalRetrain, checkFinalForm, classifyFinalError, defaultTopK, NO_FINALIST_TEXT, relatedRuns, runKind } from '../src/lib/finalretrain';
import { getIn, setIn, unsetIn } from '../src/lib/policy';
import { finalRetrainFingerprint, IdempotencyStore, submitFinalRetrain } from '../src/lib/runflow';
import { checkSearch } from '../src/lib/validate';
import { isDefaulted } from '../src/screens/Server';
import { WorkerStatus, WORKER_STATUS } from '../src/screens/Monitor';
import { AppProvider } from '../src/state/context';
import { baselineRow, cand, FINAL_RUN_ID, finalRow, fixtureCandidates, fixtureFinalRun, fixtureFinalSnapshot, fixtureRun, fixtureSnapshot, fixtureValidateOk, resetCounter, RUN_ID } from './fixtures';
import { installMockApi, type MockCall } from './mockapi';

const json = (status: number, body: unknown): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

beforeEach(resetCounter);

describe('final retrain form and run kind helpers', () => {
  it('panel is offered only for a COMPLETED SEARCH run', () => {
    expect(canFinalRetrain({ kind: 'SEARCH', status: 'COMPLETED' })).toBe(true);
    expect(canFinalRetrain({ kind: undefined, status: 'COMPLETED' })).toBe(true); // older server without kind: search run
    for (const s of ['QUEUED', 'RUNNING', 'PAUSED', 'FAILED', 'CANCELLED'] as const) expect(canFinalRetrain({ kind: 'SEARCH', status: s })).toBe(false);
    expect(canFinalRetrain({ kind: 'FINAL_RETRAIN', status: 'COMPLETED' })).toBe(false);
    expect(canFinalRetrain(null)).toBe(false);
    expect(runKind(null)).toBeNull();
    expect(runKind({ kind: 'FINAL_RETRAIN' })).toBe('FINAL_RETRAIN');
  });
  it('top-k default comes from the run resolved final_retraining', () => {
    expect(defaultTopK(fixtureRun('COMPLETED'))).toBe(2);
    expect(defaultTopK({ resolved_protocol: undefined })).toBeNull();
    expect(defaultTopK({ resolved_protocol: { final_retraining: { top_k_per_arm_per_seed: 0 } } as never })).toBeNull();
  });
  it('budget is required and > 0; top-k is an optional integer >= 1; reason is trimmed or omitted', () => {
    expect(checkFinalForm({ topK: 2, budget: null, reason: '' }).budgetError).toMatch(/Required/);
    for (const b of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) expect(checkFinalForm({ topK: 2, budget: b, reason: '' }).ok).toBe(false);
    expect(checkFinalForm({ topK: 0, budget: 1, reason: '' }).topKError).toBeTruthy();
    expect(checkFinalForm({ topK: 1.5, budget: 1, reason: '' }).ok).toBe(false);
    expect(checkFinalForm({ topK: 2, budget: 0.5, reason: '  smoke  ' }).request).toEqual({ budget_gpu_hours: 0.5, top_k_per_arm_per_seed: 2, reason: 'smoke' });
    expect(checkFinalForm({ topK: null, budget: 0.5, reason: ' ' }).request).toEqual({ budget_gpu_hours: 0.5 });
  });
  it('409 codes are told apart; 5xx / network are retryable', () => {
    const e = (status: number, code: string): ApiError => new ApiError('http', `m-${code}`, status, { code, message: `m-${code}` });
    expect(classifyFinalError(e(409, 'NO_FINALIST'))).toEqual({ kind: 'NO_FINALIST', message: 'L2 finalist 없음 (하위 rung 대체 없음)' });
    expect(NO_FINALIST_TEXT).toBe('L2 finalist 없음 (하위 rung 대체 없음)');
    expect(classifyFinalError(e(409, 'INVALID_STATE')).kind).toBe('INVALID_STATE');
    expect(classifyFinalError(e(409, 'BUDGET_INVALID')).kind).toBe('BUDGET_INVALID');
    expect(classifyFinalError(e(409, 'IDEMPOTENCY_CONFLICT')).kind).toBe('IDEMPOTENCY_CONFLICT');
    const msgs = new Set(['NO_FINALIST', 'INVALID_STATE', 'BUDGET_INVALID', 'IDEMPOTENCY_CONFLICT'].map((c) => classifyFinalError(e(409, c)).message));
    expect(msgs.size).toBe(4);
    expect(classifyFinalError(e(422, 'X')).kind).toBe('VALIDATION');
    expect(classifyFinalError(e(503, 'BUSY')).kind).toBe('RETRYABLE');
    expect(classifyFinalError(new ApiError('timeout', 'slow')).kind).toBe('RETRYABLE');
  });
});

describe('POST /runs/{id}/final-retrain idempotency (same rules as Run)', () => {
  const input = { parentRunId: RUN_ID, request: { budget_gpu_hours: 0.5, top_k_per_arm_per_seed: 2 } };

  it('reuses ONE key across timeout / 5xx / network retries and releases it after 2xx', async () => {
    const seen: { url: string; header: string; body: string }[] = [];
    const answers: (() => Response | Promise<Response>)[] = [
      () => new Promise<Response>(() => undefined), // never answers -> client timeout
      () => json(503, { code: 'BUSY', message: 'later' }),
      () => {
        throw new TypeError('Failed to fetch');
      },
      () => json(200, { run_id: FINAL_RUN_ID, status: 'QUEUED', parent_run_id: RUN_ID, finalist_count: 2 }),
    ];
    let n = 0;
    const fetchImpl = vi.fn(async (u: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string) as { idempotency_key: string };
      seen.push({ url: u, header: (init.headers as Record<string, string>)['Idempotency-Key'], body: body.idempotency_key });
      return answers[n++]();
    });
    const client = new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: fetchImpl as unknown as typeof fetch, timeoutMs: 15 });
    let counter = 0;
    const store = new IdempotencyStore(() => `fkey-${++counter}`);
    const outs = [];
    for (let i = 0; i < 4; i++) outs.push(await submitFinalRetrain(client, store, input));
    expect(outs.map((o) => o.kind)).toEqual(['retryable', 'retryable', 'retryable', 'created']);
    expect(new Set(seen.map((s) => s.header))).toEqual(new Set(['fkey-1']));
    expect(seen.every((s) => s.header === s.body)).toBe(true);
    expect(seen.every((s) => s.url === `/api/v1/runs/${RUN_ID}/final-retrain`)).toBe(true);
    expect(counter).toBe(1);
    expect(store.has(finalRetrainFingerprint(input))).toBe(false); // released after 2xx
    const again = await submitFinalRetrain(client, store, input).catch(() => null);
    expect(again).not.toBeNull();
    expect(counter).toBe(2); // a later identical request gets a fresh key
  });

  it('409 and 422 are definitive: key released; 401 keeps it', async () => {
    for (const [status, kind, held] of [[409, 'conflict', false], [422, 'rejected', false], [401, 'retryable', true]] as const) {
      const client = new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: (async () => json(status, { code: 'NO_FINALIST', message: 'm' })) as unknown as typeof fetch });
      const store = new IdempotencyStore(() => 'k');
      const o = await submitFinalRetrain(client, store, input);
      expect(o.kind).toBe(kind);
      expect(store.has(finalRetrainFingerprint(input))).toBe(held);
    }
  });

  it('a different payload (budget) is a different request and gets a different key', async () => {
    const client = new ApiClient({ origin: '', token: 't', pageOrigin: 'http://x', fetchImpl: (async () => json(503, { code: 'BUSY', message: 'x' })) as unknown as typeof fetch });
    let c = 0;
    const store = new IdempotencyStore(() => `k${++c}`);
    const a = await submitFinalRetrain(client, store, input);
    const b = await submitFinalRetrain(client, store, { ...input, request: { ...input.request, budget_gpu_hours: 1 } });
    expect([a.key, b.key]).toEqual(['k1', 'k2']);
  });
});

describe('final cohort', () => {
  it('final rows (comparison_group final / rung FINAL / arm Final) never share a cohort with search rows', () => {
    const search = fixtureCandidates().filter((c) => c.name !== 'ga-missing-batch');
    const finals = [finalRow({ latency_ms: 40, ap: 0.3 }), finalRow({ latency_ms: 60, ap: 0.35 })];
    const keys = new Set(search.map(cohortKey));
    for (const f of finals) {
      expect(isFinalRow(f)).toBe(true);
      expect(cohortKey(f)).not.toBe(UNRESOLVED_COHORT);
      expect(keys.has(cohortKey(f))).toBe(false);
    }
    expect(cohortKey(finals[0])).toBe(cohortKey(finals[1]));
    const { points } = buildPlotPoints([...search, ...finals], 'ap');
    const sel = selectCohort(points, cohortKey(finals[0]));
    expect(sel.inCohort).toHaveLength(2);
    expect(sel.inCohort.every((p) => p.c.arm === 'Final')).toBe(true);
    expect(sel.hiddenOtherCohort).toBe(points.length - 2);
    // and the other way round: the largest (search) cohort never draws a Final point
    const def = selectCohort(points, null);
    expect(def.inCohort.some((p) => p.c.arm === 'Final')).toBe(false);
    expect(cohortLabel(finals[0])).toMatch(/FINAL/);
    expect(cohortLabel(search[0])).not.toMatch(/FINAL/);
  });
  it('each field alone separates the final cohort from an otherwise identical search row', () => {
    const base = cand();
    expect(cohortKey(cand({ comparison_group: 'final' }))).not.toBe(cohortKey(base));
    expect(cohortKey(cand({ rung: 'FINAL' }))).not.toBe(cohortKey(base));
  });
  it('a Final row needs no search seed to be plottable, baselines still do not', () => {
    expect(cohortKey(finalRow({ search_seed: undefined }))).not.toBe(UNRESOLVED_COHORT);
    expect(buildPlotPoints([finalRow({ search_seed: undefined })], 'ap').points).toHaveLength(1);
    expect(cohortKey(baselineRow('b', 10, 0.1, 5))).not.toBe(UNRESOLVED_COHORT);
  });
  it('the arm facet can pick the Final arm out of a mixed row set', () => {
    const { points } = buildPlotPoints([cand(), finalRow()], 'ap');
    expect(applyFacetFilters(points, { arm: 'Final' })).toHaveLength(1);
  });
});

describe('final lineage', () => {
  it("operation 'final' has the source candidate as its single parent", () => {
    const row = finalRow({ parents: ['synthetic-ga-g1-2'] });
    expect(lineageOperation(row).op).toBe('final');
    expect(lineageOperation(cand({ parents: ['p'] }), 'final').op).toBe('final'); // server-declared
    expect(lineageOperation(cand({ parents: ['p'] }), 'mutation').op).toBe('mutation');
    expect(lineageOperation(cand({ parents: ['p'] })).op).toBe('clone'); // unchanged for search rows
    expect(lineageOperation(cand({ parents: ['a', 'b'] })).op).toBe('crossover');
  });
});

describe('parent / child runs', () => {
  it('lists only explicit server facts', () => {
    const run = fixtureRun('COMPLETED');
    const child = fixtureFinalRun();
    expect(relatedRuns({ run: child, events: [] })).toEqual([{ relation: 'parent', id: RUN_ID, source: 'run.parent_run_id' }]);
    const rel = relatedRuns({
      run,
      runs: [child, { ...fixtureRun(), id: 'unrelated', parent_run_id: null }],
      events: [{ seq: 7, type: 'run.updated', payload: { id: 'ev-child', status: 'QUEUED', parent_run_id: RUN_ID } }, { seq: 8, type: 'log', payload: { message: 'hello' } }],
      createdChildId: 'made-here',
    });
    expect(rel.map((r) => `${r.relation}:${r.id}`).sort()).toEqual(['child:ev-child', 'child:made-here', `child:${FINAL_RUN_ID}`]);
    expect(relatedRuns({ run: null, events: [] })).toEqual([]);
    expect(relatedRuns({ run, runs: [child, child], events: [] })).toHaveLength(1); // deduplicated
  });
});

describe('policy helpers for optional fields', () => {
  it('unsetIn removes only the key and leaves the rest untouched', () => {
    const p = setIn({}, 'console.search.crossover_rate', 0.5);
    const q = unsetIn(setIn(p, 'console.search.mutation_rate', 0.1), 'console.search.crossover_rate');
    expect(getIn(q, 'console.search.crossover_rate')).toBeUndefined();
    expect('crossover_rate' in (getIn(q, 'console.search') as object)).toBe(false);
    expect(getIn(q, 'console.search.mutation_rate')).toBe(0.1);
    expect(unsetIn({ a: 1 }, 'x.y.z')).toEqual({ a: 1 });
  });
  it('client pre-check: crossover_rate within [0,1]; postprocess conf/iou within [0,1], max_det integer >= 1', () => {
    const mk = (search: object, benchmark: object): object => ({ console: { search: { arms: ['GA'], seeds: [1], budget_gpu_hours: 1, ...search }, benchmark } });
    const fields = (p: object): string[] => checkSearch(p).map((i) => i.field);
    expect(fields(mk({ crossover_rate: 0.5 }, { postprocess: { conf: 0.25, iou: 0.7, max_det: 300 } }))).toEqual([]);
    expect(fields(mk({ crossover_rate: 1.5 }, {}))).toContain('console.search.crossover_rate');
    expect(fields(mk({ crossover_rate: -0.1 }, {}))).toContain('console.search.crossover_rate');
    expect(fields(mk({}, { postprocess: { conf: 2 } }))).toContain('console.benchmark.postprocess.conf');
    expect(fields(mk({}, { postprocess: { iou: -1 } }))).toContain('console.benchmark.postprocess.iou');
    expect(fields(mk({}, { postprocess: { max_det: 0 } }))).toContain('console.benchmark.postprocess.max_det');
    expect(fields(mk({}, { postprocess: { max_det: 1.5 } }))).toContain('console.benchmark.postprocess.max_det');
  });
  it('resolved_diff rows without a requested value are "defaulted"', () => {
    expect(isDefaulted({ resolved: 0.5 })).toBe(true);
    expect(isDefaulted({ requested: null, resolved: 300 })).toBe(true);
    expect(isDefaulted({ requested: 0.3, resolved: 0.3 })).toBe(false);
    expect(isDefaulted({ requested: 0.3 })).toBe(false);
  });
});

describe('worker status vocabulary', () => {
  it('idle / busy / offline / lost render as text with a glyph (not colour only)', () => {
    for (const s of ['idle', 'busy', 'offline', 'lost']) {
      const { unmount } = render(<WorkerStatus status={s} />);
      expect(screen.getByText(s, { exact: false })).toHaveTextContent(new RegExp(`${WORKER_STATUS[s].glyph} ${s}`));
      unmount();
    }
    render(<WorkerStatus status="BUSY" />); // legacy upper case still maps
    expect(screen.getByText(/busy/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------------------
// App-level flow
// ---------------------------------------------------------------------------------------------------
let restore: () => void = () => undefined;
afterEach(() => {
  cleanup();
  restore();
  window.history.replaceState(null, '', '#/server');
});
beforeEach(() => window.history.replaceState(null, '', '#/server'));

function mount(opts: Parameters<typeof installMockApi>[0] = {}): { calls: MockCall[] } {
  const api = installMockApi(opts);
  restore = api.restore;
  render(
    <AppProvider>
      <App />
    </AppProvider>,
  );
  return { calls: api.calls };
}

async function connectAndLoad(user: ReturnType<typeof userEvent.setup>, id = RUN_ID): Promise<void> {
  await user.type(screen.getByLabelText(/Bearer token/), 'TEST-TOKEN');
  await user.click(screen.getByTestId('connect-btn'));
  await screen.findByText(/status: ok/);
  await user.type(screen.getByTestId('run-id-input'), id);
  await user.click(screen.getByTestId('load-run-btn'));
  await screen.findByTestId('export-btn');
}

describe('Final Retrain panel', () => {
  it('is absent while the search run is RUNNING and present once COMPLETED', async () => {
    const user = userEvent.setup();
    mount();
    await connectAndLoad(user);
    expect(screen.queryByTestId('final-retrain-panel')).toBeNull();
    cleanup();
    restore();
    mount({ snapshot: fixtureSnapshot('COMPLETED', 5) });
    await connectAndLoad(user);
    const panel = await screen.findByTestId('final-retrain-panel');
    expect(within(panel).getByTestId('final-topk')).toHaveValue(2); // default from the resolved final_retraining
    expect(within(panel).getByTestId('final-budget')).toHaveValue(null);
    expect(within(panel).getByTestId('final-run-btn')).toBeDisabled(); // budget required
  });

  it('requires budget > 0, then POSTs body + header with one key and shows child run id, finalist_count and a load action', async () => {
    const user = userEvent.setup();
    const { calls } = mount({ snapshot: fixtureSnapshot('COMPLETED', 5) });
    await connectAndLoad(user);
    const btn = await screen.findByTestId('final-run-btn');
    await user.type(screen.getByTestId('final-budget'), '0');
    expect(btn).toBeDisabled();
    expect((await screen.findAllByText(/greater than 0/)).length).toBeGreaterThan(0);
    await user.clear(screen.getByTestId('final-budget'));
    await user.type(screen.getByTestId('final-budget'), '0.5');
    await user.type(screen.getByTestId('final-reason'), 'smoke final');
    expect(btn).toBeEnabled();
    await user.click(btn);
    await waitFor(() => expect(screen.getByTestId('final-child-id')).toHaveTextContent(FINAL_RUN_ID));
    expect(screen.getByTestId('final-finalist-count')).toHaveTextContent('2');
    const post = calls.find((c) => c.method === 'POST' && c.path === `/api/v1/runs/${RUN_ID}/final-retrain`)!;
    expect(post.headers['Idempotency-Key']).toBe((post.body as { idempotency_key: string }).idempotency_key);
    expect(post.body).toMatchObject({ budget_gpu_hours: 0.5, top_k_per_arm_per_seed: 2, reason: 'smoke final' });

    // load the child run: kind, parent link, finalists table
    await user.click(screen.getByTestId('final-load-child'));
    await waitFor(() => expect(screen.getByTestId('run-kind-row')).toHaveTextContent('FINAL_RETRAIN'));
    expect(screen.getByTestId('parent-run-link')).toHaveTextContent(RUN_ID);
    const table = await screen.findByTestId('finalists-table');
    const heads = within(table).getAllByRole('columnheader').map((h) => h.textContent ?? '');
    for (const h of ['Candidate', 'Source evaluation', 'Arm', 'Seed', 'AP', 'Latency', 'Selection rule']) expect(heads.some((x) => x.startsWith(h)), h).toBe(true);
    expect(within(table).getByText('synthetic-ev-ga-g1-2')).toBeInTheDocument();
    expect(screen.queryByTestId('final-retrain-panel')).toBeNull(); // a FINAL_RETRAIN run cannot be final-retrained again
    // load parent goes back to the search run
    await user.click(screen.getByTestId('load-parent-btn'));
    await waitFor(() => expect(screen.getByTestId('final-retrain-panel')).toBeInTheDocument());
  });

  it('timeout/5xx keeps the same Idempotency-Key for the retry, which then succeeds', async () => {
    const user = userEvent.setup();
    const keys: string[] = [];
    let n = 0;
    mount({
      snapshot: fixtureSnapshot('COMPLETED', 5),
      onFinalRetrain: (call) => {
        keys.push(call.headers['Idempotency-Key']);
        return ++n === 1 ? json(504, { code: 'BUSY', message: 'upstream timeout' }) : json(200, { run_id: FINAL_RUN_ID, status: 'QUEUED', parent_run_id: RUN_ID, finalist_count: 1 });
      },
    });
    await connectAndLoad(user);
    await user.type(await screen.findByTestId('final-budget'), '1');
    await user.click(screen.getByTestId('final-run-btn'));
    await waitFor(() => expect(screen.getByTestId('final-error')).toHaveTextContent(/reuse Idempotency-Key/));
    expect(screen.getByTestId('final-run-btn')).toHaveTextContent(/Retry Final Retrain \(same Idempotency-Key\)/);
    await user.click(screen.getByTestId('final-run-btn'));
    await waitFor(() => expect(screen.getByTestId('final-child-id')).toBeInTheDocument());
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });

  it.each([
    ['NO_FINALIST', 'L2 finalist 없음 (하위 rung 대체 없음)'],
    ['INVALID_STATE', /parent run must be COMPLETED/],
    ['BUDGET_INVALID', /Budget rejected/],
    ['IDEMPOTENCY_CONFLICT', /Idempotency conflict/],
  ] as const)('409 %s is rendered distinctly', async (code, text) => {
    const user = userEvent.setup();
    mount({ snapshot: fixtureSnapshot('COMPLETED', 5), onFinalRetrain: () => json(409, { code, message: `server says ${code}` }) });
    await connectAndLoad(user);
    await user.type(await screen.findByTestId('final-budget'), '1');
    await user.click(screen.getByTestId('final-run-btn'));
    const err = await screen.findByTestId('final-error');
    expect(err).toHaveAttribute('data-error-kind', code);
    expect(err).toHaveTextContent(text);
    expect(err).toHaveTextContent(code);
    expect(screen.queryByTestId('final-child-id')).toBeNull();
    // 409 is definitive: the button is a plain Start again (key released), not a retry
    expect(screen.getByTestId('final-run-btn')).toHaveTextContent('Start final retraining');
  });
});

describe('loaded FINAL_RETRAIN run', () => {
  it('Monitor header shows kind and parent link; the final cohort is drawn alone; History lists the parent', async () => {
    const user = userEvent.setup();
    mount({ snapshot: fixtureFinalSnapshot(), snapshots: {} });
    await connectAndLoad(user, FINAL_RUN_ID);
    await user.click(screen.getByTestId('nav-monitor'));
    expect(await screen.findByText(/kind: FINAL_RETRAIN/)).toBeInTheDocument();
    expect(screen.getByTestId('parent-run-link')).toHaveTextContent(RUN_ID);
    expect(await screen.findByTestId('final-cohort-note')).toHaveTextContent(/never mixed with search cohorts/);
    expect(screen.getByTestId('cohort-select')).toHaveTextContent(/FINAL/);
    expect(screen.getByTestId('finalists-table')).toBeInTheDocument();
    await user.click(screen.getByTestId('nav-history'));
    const rel = await screen.findByTestId('related-runs');
    expect(rel).toHaveTextContent('parent');
    expect(rel).toHaveTextContent(RUN_ID);
  });

  it('NO_FINALIST child (empty finalists + reason) reports it and never fills in a lower rung', async () => {
    const user = userEvent.setup();
    mount({ snapshots: { [FINAL_RUN_ID]: fixtureFinalSnapshot('COMPLETED', []) } });
    await connectAndLoad(user, FINAL_RUN_ID);
    expect(await screen.findByTestId('no-finalist')).toHaveTextContent(NO_FINALIST_TEXT);
  });

  it('candidate modal shows lineage operation final with the source candidate as its single parent', async () => {
    const user = userEvent.setup();
    mount({ snapshots: { [FINAL_RUN_ID]: fixtureFinalSnapshot() } });
    await connectAndLoad(user, FINAL_RUN_ID);
    await user.click(screen.getByTestId('nav-lineage'));
    await user.click((await screen.findAllByRole('button', { name: /final-synthetic-ga-g1-2/ }))[0]);
    expect(await screen.findByTestId('lineage-op')).toHaveTextContent(/final/);
    const parents = screen.getAllByTestId('lineage-parent');
    expect(parents).toHaveLength(1);
    expect(parents[0]).toHaveTextContent(/Source candidate/);
    expect(parents[0]).toHaveTextContent('synthetic-ga-g1-2');
  });
});

describe('Settings: crossover rate and benchmark postprocess', () => {
  it('edits write crossover_rate and postprocess; clearing removes the keys; help text names the AP floor', async () => {
    const user = userEvent.setup();
    const { calls } = mount();
    await user.type(screen.getByLabelText(/Bearer token/), 'T');
    await user.click(screen.getByTestId('connect-btn'));
    await screen.findByText(/status: ok/);
    await user.click(screen.getByTestId('nav-settings'));
    const cr = screen.getByTestId('crossover-rate');
    await user.type(cr, '0.75');
    for (const id of ['pp-conf', 'pp-iou', 'pp-maxdet']) expect(document.getElementById(screen.getByTestId(id).getAttribute('aria-describedby') as string)).toHaveTextContent('latency operating point; AP evaluation floor stays 0.001');
    await user.type(screen.getByTestId('pp-conf'), '0.3');
    await user.type(screen.getByTestId('pp-iou'), '0.6');
    await user.type(screen.getByTestId('pp-maxdet'), '100');
    await user.click(screen.getByTestId('nav-server'));
    await user.click(screen.getByTestId('validate-btn'));
    await screen.findByTestId('validation-result');
    const body = calls.filter((c) => c.path === '/api/v1/protocols/validate').pop()!.body as { protocol: Record<string, unknown> };
    expect(getIn(body.protocol, 'console.search.crossover_rate')).toBe(0.75);
    expect(getIn(body.protocol, 'console.benchmark.postprocess')).toEqual({ conf: 0.3, iou: 0.6, max_det: 100 });
    // clear everything again: the keys disappear instead of becoming null
    await user.click(screen.getByTestId('nav-settings'));
    for (const id of ['crossover-rate', 'pp-conf', 'pp-iou', 'pp-maxdet']) await user.clear(screen.getByTestId(id));
    await user.click(screen.getByTestId('nav-server'));
    await user.click(screen.getByTestId('validate-btn'));
    await waitFor(() => expect(calls.filter((c) => c.path === '/api/v1/protocols/validate')).toHaveLength(2));
    const body2 = calls.filter((c) => c.path === '/api/v1/protocols/validate').pop()!.body as { protocol: Record<string, unknown> };
    expect('crossover_rate' in (getIn(body2.protocol, 'console.search') as object)).toBe(false);
    expect('postprocess' in (getIn(body2.protocol, 'console.benchmark') as object)).toBe(false);
  });

  it('an out-of-range crossover rate is flagged client-side', async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByTestId('nav-settings'));
    await user.type(screen.getByTestId('crossover-rate'), '1.5');
    expect((await screen.findAllByText(/Crossover rate must be within \[0, 1\]/)).length).toBeGreaterThan(0); // field error and pre-check list
  });

  it('the Validate diff table shows defaulted values with a badge', async () => {
    const user = userEvent.setup();
    const api = installMockApi();
    restore = api.restore;
    const orig = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === 'string' ? input : input.toString(), 'http://localhost');
      if (url.pathname === '/api/v1/protocols/validate') {
        const v = fixtureValidateOk({ a: 1 });
        return json(200, { ...v, resolved_diff: [{ field: 'console.search.crossover_rate', resolved: 0.5, reason: 'default' }, { field: 'console.benchmark.postprocess.max_det', requested: 100, resolved: 100 }] });
      }
      return orig(input, init);
    }) as unknown as typeof fetch;
    render(
      <AppProvider>
        <App />
      </AppProvider>,
    );
    await user.click(screen.getByTestId('connect-btn'));
    await screen.findByText(/status: ok/);
    await user.click(screen.getByTestId('validate-btn'));
    const table = await screen.findByTestId('resolved-diff');
    const rows = within(table).getAllByRole('row');
    const crossover = rows.find((r) => r.textContent?.includes('crossover_rate'))!;
    expect(crossover).toHaveTextContent('not set');
    expect(crossover).toHaveTextContent('0.5');
    expect(crossover).toHaveTextContent('default applied');
    expect(rows.find((r) => r.textContent?.includes('max_det'))).not.toHaveTextContent('default applied');
  });

  it('S4-F4: an INVALID Validate result never renders "Requested vs resolved", even if the reply carries a diff', async () => {
    const user = userEvent.setup();
    const api = installMockApi();
    restore = api.restore;
    const orig = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === 'string' ? input : input.toString(), 'http://localhost');
      if (url.pathname === '/api/v1/protocols/validate') {
        return json(200, {
          valid: false,
          errors: [{ field: 'rungs[1].epochs_added', code: 'EPOCHS_ADDED_MANUAL', message: 'added epochs are computed from the cumulative targets; remove the manual value 5' }],
          warnings: [],
          protocol_hash: null,
          resolved: null,
          capabilities: {},
          resolved_diff: [{ field: 'rungs[1].epochs_added', requested: 5, resolved: 20, reason: 'cumulative target minus previous rung target (computed, never entered)' }],
          validation_id: null,
        });
      }
      return orig(input, init);
    }) as unknown as typeof fetch;
    render(
      <AppProvider>
        <App />
      </AppProvider>,
    );
    await user.click(screen.getByTestId('connect-btn'));
    await screen.findByText(/status: ok/);
    await user.click(screen.getByTestId('validate-btn'));
    const result = await screen.findByTestId('validation-result');
    expect(result).toHaveTextContent('INVALID');
    expect(screen.getByTestId('validate-errors')).toHaveTextContent('EPOCHS_ADDED_MANUAL');
    expect(screen.queryByTestId('resolved-diff')).toBeNull();
    expect(result).not.toHaveTextContent('Requested vs resolved');
    expect(screen.getByTestId('run-btn')).toBeDisabled();
  });
});
