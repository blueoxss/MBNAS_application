/**
 * WP-W2: TrainingPanel evaluate_only / resume wording, Settings OFF preserved fields, closeCandidate query cleanup,
 * Workers table telemetry columns. Synthetic values only; no server, no GPU.
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../src/api/client';
import type { Candidate, TrainingRequest, Worker } from '../src/api/types';
import { TrainingPanel, trainingProtocol } from '../src/components/TrainingPanel';
import { getIn, setIn } from '../src/lib/policy';
import { validatePolicy } from '../src/lib/validate';
import { isTrainedNotEvaluated, WorkerTable } from '../src/screens/Monitor';
import { Settings } from '../src/screens/Settings';
import { AppProvider, useApp, type AppModel } from '../src/state/context';
import { cand, fixtureDesignExampleProtocol } from './fixtures';

/** A full protocol: the synthetic DEFAULT_BASELINE14 server preset (the console embeds no template any more, DD-A8). */
const defaultPolicy = fixtureDesignExampleProtocol;

afterEach(() => { cleanup(); window.history.replaceState(null, '', '/#/settings'); });

function recordingClient(calls: TrainingRequest[]): ApiClient {
  return new ApiClient({ origin: '', token: 'in-memory-only', fetchImpl: vi.fn(async (_url, init) => {
    calls.push(JSON.parse(String(init?.body)) as TrainingRequest);
    return new Response(JSON.stringify({ run_id: 'training-child', status: 'QUEUED' }), { status: 200, headers: { 'content-type': 'application/json' } });
  }) });
}

/** Every key name used anywhere in a JSON value. */
function keysDeep(value: unknown, out = new Set<string>()): Set<string> {
  if (Array.isArray(value)) value.forEach((v) => keysDeep(v, out));
  else if (value && typeof value === 'object') for (const [k, v] of Object.entries(value)) { out.add(k); keysDeep(v, out); }
  return out;
}

describe('TrainingPanel evaluate_only (I4-#3)', () => {
  it('hides the target-epoch field, explains why, and does not send epoch_target_total / epochs', async () => {
    const calls: TrainingRequest[] = [];
    // evaluate_only exists only for an official baseline (the server refuses it for candidates and user models)
    render(<TrainingPanel client={recordingClient(calls)} protocol={defaultPolicy()} />);
    fireEvent.change(screen.getByLabelText(/Baseline registry ID/), { target: { value: 'synthetic-baseline-id' } });
    // scratch: the epoch field exists
    expect(screen.getByText('목표 epoch')).toBeInTheDocument();
    expect(screen.queryByTestId('evaluate-only-epoch-help')).not.toBeInTheDocument();

    fireEvent.change(screen.getByTestId('training-mode'), { target: { value: 'evaluate_only' } });
    expect(screen.queryByText('목표 epoch')).not.toBeInTheDocument();
    expect(screen.getByTestId('evaluate-only-epoch-help')).toHaveTextContent('평가 전용: 학습 epoch 없음');
    fireEvent.click(screen.getByTestId('training-submit'));
    await waitFor(() => expect(calls).toHaveLength(1));

    const body = calls[0];
    expect(body.mode).toBe('evaluate_only');
    expect(body.source).toMatchObject({ kind: 'baseline', baseline_id: 'synthetic-baseline-id' });
    expect(body.validation_enabled).toBe(true);
    expect(getIn(body.protocol, 'console.training.epoch_target_total')).toBeUndefined();
    expect(getIn(body.protocol, 'console.training.scheduler_horizon_epochs')).toBeUndefined();
    expect(Object.keys(body)).not.toContain('epochs');
    expect(Object.keys(body)).not.toContain('epoch_target_total');
    expect(keysDeep(getIn(body.protocol, 'console.training'))).not.toContain('epochs');
    // the other explicit-training inputs are still sent
    expect(getIn(body.protocol, 'console.training.seed')).toBe(0);
    expect(getIn(body.protocol, 'training.optimizer')).toBe('AdamW');
  });

  it('keeps sending the target epoch for scratch training', async () => {
    const calls: TrainingRequest[] = [];
    render(<TrainingPanel client={recordingClient(calls)} protocol={defaultPolicy()} source={{ kind: 'search', run_id: 'origin', candidate_id: 'cand-1' }} />);
    fireEvent.click(screen.getByTestId('training-submit'));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].mode).toBe('scratch');
    expect(getIn(calls[0].protocol, 'console.training.epoch_target_total')).toBe(90);
    expect(getIn(calls[0].protocol, 'console.training.scheduler_horizon_epochs')).toBe(90);
  });

  const modeValues = (): string[] => Array.from((screen.getByTestId('training-mode') as HTMLSelectElement).options).map((o) => o.value);

  it.each(['search', 'generated'] as const)('does not offer evaluate_only for a fixed %s candidate source (server: 422 EVAL_ONLY_UNSUPPORTED)', (kind) => {
    render(<TrainingPanel client={recordingClient([])} protocol={defaultPolicy()} source={{ kind, run_id: 'origin', candidate_id: 'cand-1' }} />);
    expect(modeValues()).toEqual(['scratch', 'pretrained', 'resume']);
    expect(screen.queryByText('공식 가중치 평가 전용')).not.toBeInTheDocument();
  });

  it('offers evaluate_only for a baseline source and drops it (and a selected evaluate_only) for a user model', () => {
    render(<TrainingPanel client={recordingClient([])} protocol={defaultPolicy()} />);
    expect(modeValues()).toEqual(['scratch', 'pretrained', 'evaluate_only']);
    fireEvent.change(screen.getByTestId('training-mode'), { target: { value: 'evaluate_only' } });
    expect(screen.getByTestId('evaluate-only-epoch-help')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/모델 출처/), { target: { value: 'user' } });
    expect(modeValues()).toEqual(['scratch', 'pretrained']);
    expect(screen.getByTestId('training-mode')).toHaveValue('scratch');
    expect(screen.queryByTestId('evaluate-only-epoch-help')).not.toBeInTheDocument();
    expect(screen.getByText('목표 epoch')).toBeInTheDocument();
  });

  it('trainingProtocol(omitEpochs) also removes inherited epoch keys without touching the source policy', () => {
    const source = setIn(setIn(defaultPolicy(), 'console.training.epoch_target_total', 7), 'console.training.scheduler_horizon_epochs', 7);
    const fields = { dataset: '/d.yaml', output: '/o', checkpoint: '', epochs: 5, batch: 2, size: 320, seed: 1, optimizer: 'SGD', lr: 0.01 };
    const result = trainingProtocol(source, fields, { omitEpochs: true });
    expect(getIn(result, 'console.training.epoch_target_total')).toBeUndefined();
    expect(getIn(result, 'console.training.scheduler_horizon_epochs')).toBeUndefined();
    expect(getIn(result, 'console.training.seed')).toBe(1);
    expect(getIn(source, 'console.training.epoch_target_total')).toBe(7);
    expect(getIn(trainingProtocol(source, fields), 'console.training.epoch_target_total')).toBe(5);
  });
});

describe('TrainingPanel resume banner (DD-9)', () => {
  it('states that external full-state import is unsupported and only the latest full-state of a paused run resumes', () => {
    render(<TrainingPanel client={null} protocol={defaultPolicy()} source={{ kind: 'search', run_id: 'origin', candidate_id: 'cand-1' }} />);
    expect(screen.queryByText(/외부 checkpoint를 새 run으로 가져오는 기능은 지원하지 않습니다/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId('training-mode'), { target: { value: 'resume' } });
    const banner = screen.getByText(/기존 학습 run의 원래 protocol과 최신 full-state를 사용합니다/);
    expect(banner).toHaveTextContent('외부 checkpoint를 새 run으로 가져오는 기능은 지원하지 않습니다(기존 paused run의 최신 full-state만 재개)');
  });
});

describe('Settings with performance evaluation OFF (DD-17)', () => {
  function mountSettings(): { app: () => AppModel } {
    let current: AppModel | null = null;
    function Probe(): JSX.Element { current = useApp(); return <Settings />; }
    render(<AppProvider><Probe /></AppProvider>);
    return { app: () => current as AppModel };
  }

  it('renders the evaluation paths disabled with their preserved values, restores them on ON, and shows one info notice', () => {
    const { app } = mountSettings();
    fireEvent.change(screen.getByTestId('path-dataset_yaml'), { target: { value: '/srv/preserved/data.yaml' } });
    fireEvent.change(screen.getByTestId('path-final_eval_root'), { target: { value: '/srv/preserved/test' } });
    fireEvent.click(screen.getByTestId('evaluation-enabled'));
    expect(getIn(app().policy, 'console.search.evaluation_enabled')).toBe(false);

    for (const f of ['dataset_yaml', 'train_root', 'search_val_root', 'final_eval_root']) expect(screen.getByTestId(`path-${f}`)).toBeDisabled();
    expect(screen.getByTestId('path-output_root')).toBeEnabled();
    expect(screen.getByTestId('path-dataset_yaml')).toHaveValue('/srv/preserved/data.yaml');
    expect(screen.getByTestId('path-final_eval_root')).toHaveValue('/srv/preserved/test');
    expect(getIn(app().policy, 'console.paths.dataset_yaml')).toBe('/srv/preserved/data.yaml');
    expect(screen.getByTestId('path-dataset_yaml-group')).toHaveAttribute('data-preserved', 'true');
    expect(screen.getAllByText(/성능 평가 OFF: 이 값은 보존되며/).length).toBe(4);
    // single info-level notice in the pre-check card
    const notice = screen.getByTestId('issue-info-list');
    expect(within(notice).getAllByRole('listitem')).toHaveLength(1);
    expect(notice).toHaveTextContent('성능 평가 OFF: 평가 설정 값은 보존되며 검증하지 않습니다');

    fireEvent.click(screen.getByTestId('evaluation-enabled'));
    expect(screen.getByTestId('path-dataset_yaml')).toBeEnabled();
    expect(screen.getByTestId('path-dataset_yaml')).toHaveValue('/srv/preserved/data.yaml');
    expect(screen.queryByTestId('issue-info-list')).not.toBeInTheDocument();
  });

  it('validatePolicy OFF branch does not validate preserved evaluation values but still validates generation fields', () => {
    const base = setIn(setIn(defaultPolicy(), 'console.search.evaluation_enabled', false), 'console.search.nc', 3);
    const broken = setIn(setIn(setIn(base, 'console.paths.dataset_yaml', 'relative/not-absolute.yaml'), 'rungs.0.fitness_weights.ap', 99), 'console.paths.output_root', '/allowed/out');
    const issues = validatePolicy(broken);
    expect(issues.filter((i) => i.severity !== 'info')).toEqual([]);
    expect(issues.filter((i) => i.severity === 'info')).toHaveLength(1);
    expect(validatePolicy(setIn(broken, 'console.search.nc', 0)).some((i) => i.severity === 'error' && i.field === 'console.search.nc')).toBe(true);
    // ON: the same broken preserved values are validated again
    expect(validatePolicy(setIn(broken, 'console.search.evaluation_enabled', true)).some((i) => i.severity === 'error')).toBe(true);
    expect(validatePolicy(setIn(broken, 'console.search.evaluation_enabled', true)).some((i) => i.severity === 'info')).toBe(false);
  });
});

describe('closeCandidate query cleanup (I4-#12)', () => {
  function mountProbe(): { app: () => AppModel } {
    let current: AppModel | null = null;
    function Probe(): JSX.Element { current = useApp(); return <span />; }
    render(<AppProvider><Probe /></AppProvider>);
    return { app: () => current as AppModel };
  }
  const query = (): URLSearchParams => new URL(window.location.href).searchParams;

  it('removes block and model_tab with cid; a different candidate does not inherit them; the same cid keeps them', () => {
    const { app } = mountProbe();
    const a = cand({ candidate_id: 'cand-a' });
    const b = cand({ candidate_id: 'cand-b' });
    window.history.replaceState(null, '', '/?block=block-0007&model_tab=head#/monitor');
    // restoring the same candidate after a reload keeps its tab and block
    act(() => app().openCandidate(a));
    expect(window.location.hash).toContain('cid=cand-a');
    expect(query().get('model_tab')).toBeNull(); // first open of cand-a: URL state belonged to no candidate -> cleared
    window.history.replaceState(null, '', `/?block=block-0007&model_tab=head${window.location.hash}`);
    act(() => app().openCandidate(a));
    expect(query().get('model_tab')).toBe('head');
    expect(query().get('block')).toBe('block-0007');
    // switching to another candidate drops both
    act(() => app().openCandidate(b as Candidate));
    expect(window.location.hash).toContain('cid=cand-b');
    expect(query().get('model_tab')).toBeNull();
    expect(query().get('block')).toBeNull();
    // closing removes cid, block and model_tab (also from the hash query), keeps the screen path
    window.history.replaceState(null, '', `/?block=block-0003&model_tab=neck${window.location.hash}&model_tab=neck`);
    act(() => app().closeCandidate());
    expect(window.location.search).toBe('');
    expect(window.location.hash).not.toContain('cid=');
    expect(window.location.hash).not.toContain('model_tab');
    expect(window.location.hash).toMatch(/^#\/[a-z]+$/);
  });
});

describe('Workers table telemetry (I4-#9)', () => {
  const workers: Worker[] = [
    { id: 'w-full', status: 'busy', device: 'cuda:0', utilization_percent: 91, vram_mb: 40960.4, power_w: 215.55, sm_clock_mhz: 1755, epoch: 3, epoch_target_total: 10 },
    { id: 'w-null', status: 'idle', device: 'cuda:1', utilization_percent: null, vram_mb: null, power_w: null, sm_clock_mhz: null },
    { id: 'w-missing', status: 'idle', device: 'cuda:2' },
  ];
  it('shows vram_mb, power_w and sm_clock_mhz columns; null or missing is a dash, never 0', () => {
    render(<WorkerTable workers={workers} />);
    const table = screen.getByTestId('worker-table');
    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent ?? '');
    for (const label of ['VRAM [MB]', 'Power [W]', 'SM clock [MHz]']) expect(headers.some((h) => h.includes(label))).toBe(true);
    const cellsOf = (id: string): string[] => {
      const row = within(table).getByText(id).closest('tr') as HTMLElement;
      return within(row).getAllByRole('cell').map((c) => c.textContent ?? '');
    };
    const col = (label: string): number => headers.findIndex((h) => h.includes(label));
    const full = cellsOf('w-full');
    expect(full[col('VRAM [MB]')]).toBe('40960');
    expect(full[col('Power [W]')]).toBe('215.6');
    expect(full[col('SM clock [MHz]')]).toBe('1755');
    for (const id of ['w-null', 'w-missing']) {
      const cells = cellsOf(id);
      for (const label of ['VRAM [MB]', 'Power [W]', 'SM clock [MHz]']) expect(cells[col(label)]).toBe('—');
    }
  });
});

describe('DD-5 trained-not-evaluated detection', () => {
  const explicitRun = { resolved_protocol: { console: { extra: { training: { enabled: true, mode: 'scratch', validation_enabled: false } } } } } as never;
  const searchRun = { resolved_protocol: { console: { search: { arms: ['GA'] } } } } as never;
  it('requires SEARCH_COMPLETE + performance_status NOT_EVALUATED inside an explicit training run', () => {
    const row = { status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED' };
    expect(isTrainedNotEvaluated(row, explicitRun)).toBe(true);
    expect(isTrainedNotEvaluated({ ...row, training_mode: 'scratch' }, null)).toBe(true);
    expect(isTrainedNotEvaluated(row, searchRun)).toBe(false); // search run: keep the raw status
    expect(isTrainedNotEvaluated({ ...row, status: 'EVALUATED' }, explicitRun)).toBe(false);
    expect(isTrainedNotEvaluated({ status: 'SEARCH_COMPLETE' }, explicitRun)).toBe(false);
    expect(isTrainedNotEvaluated({ ...row, performance_status: 'EVALUATED' }, explicitRun)).toBe(false);
  });
  it('DD-A63 (a): a search-arm row a CPU runner trained but could not evaluate (EVALUATION_REQUIRES_CUDA) gets the same label', () => {
    const cpuRow = { status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED', reason_code: 'EVALUATION_REQUIRES_CUDA', arm: 'GA', training_status: 'TRAINED' };
    expect(isTrainedNotEvaluated(cpuRow, searchRun)).toBe(true);
    expect(isTrainedNotEvaluated(cpuRow, null)).toBe(true);
    const { training_status: _reported, ...noTrainingStatus } = cpuRow; // a row without training_status is not guessed UNTRAINED
    expect(isTrainedNotEvaluated({ ...noTrainingStatus, arm: 'IndependentRandom' }, searchRun)).toBe(true);
    expect(isTrainedNotEvaluated({ ...cpuRow, training_status: 'UNTRAINED' }, searchRun)).toBe(false); // server says untrained
    expect(isTrainedNotEvaluated({ ...cpuRow, reason_code: 'NOT_EVALUATED' }, searchRun)).toBe(false); // fallback reason: raw status
    expect(isTrainedNotEvaluated({ ...cpuRow, reason_code: null }, searchRun)).toBe(false); // the DD-5 search-run case above
    expect(isTrainedNotEvaluated({ ...cpuRow, status: 'EVAL_FAILED' }, searchRun)).toBe(false); // e.g. ARCHITECTURE_HASH_MISMATCH
    expect(isTrainedNotEvaluated({ ...cpuRow, performance_status: 'EVALUATED' }, searchRun)).toBe(false);
  });
});
