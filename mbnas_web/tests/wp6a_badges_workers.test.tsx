/**
 * WP-6A: UNTRAINED · UNEVALUATED badge (DD-A23; training_status of contract 1.5.0 with a graceful 1.4.x fallback),
 * DD-5 wording kept, Worker runner_id / device_id columns (contract 1.5.0, dash when absent), and the Monitor shows the
 * server's generation representative without a client-side rule badge (R6). SYNTHETIC values only.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { App } from '../src/App';
import type { ApiClient } from '../src/api/client';
import type { Candidate, Worker } from '../src/api/types';
import { CandidateView } from '../src/components/CandidateView';
import { isUntrainedUnevaluated, trainingStatusOf, TRAINED_NOT_EVALUATED, UNTRAINED_UNEVALUATED } from '../src/lib/evaluation';
import { GeneratedTable, WorkerTable } from '../src/screens/Monitor';
import { AppProvider } from '../src/state/context';
import { syntheticGeneratedDetail, syntheticGeneratedRows } from './detail_fixtures';
import { cand, fixtureSnapshot, gen, resetCounter, RUN_ID } from './fixtures';
import { installMockApi } from './mockapi';

let restore: () => void = () => undefined;
afterEach(() => {
  cleanup();
  restore();
  window.history.replaceState(null, '', '#/server');
});
beforeEach(resetCounter);

describe('training_status / evaluation_status -> UNTRAINED · UNEVALUATED', () => {
  it('uses training_status when the server sends it (contract 1.5.0)', () => {
    expect(trainingStatusOf({ training_status: 'UNTRAINED', status: 'QUEUED' })).toBe('UNTRAINED');
    expect(isUntrainedUnevaluated({ training_status: 'UNTRAINED', status: 'QUEUED', evaluation_status: 'NOT_EVALUATED' })).toBe(true);
    expect(trainingStatusOf({ training_status: 'TRAINED', status: 'SEARCH_COMPLETE' })).toBe('TRAINED');
    expect(isUntrainedUnevaluated({ training_status: 'TRAINED', status: 'SEARCH_COMPLETE' })).toBe(false);
    // a reported UNTRAINED row that is nevertheless evaluated is not "unevaluated"
    expect(isUntrainedUnevaluated({ training_status: 'UNTRAINED', evaluation_status: 'EVALUATED' })).toBe(false);
  });

  it('falls back for contract 1.4.x: a generated candidate without an evaluation is UNTRAINED; other rows are not guessed', () => {
    // the fixture is a 1.5.0 GeneratedCandidate (training_status UNTRAINED); a contract 1.4.3 server sent the same row without it
    const { training_status: reported, ...row } = syntheticGeneratedRows()[0];
    expect(reported).toBe('UNTRAINED');
    expect('training_status' in row).toBe(false);
    expect(trainingStatusOf(row)).toBe('UNTRAINED');
    expect(isUntrainedUnevaluated(row)).toBe(true);
    expect(isUntrainedUnevaluated({ ...row, status: 'NOT_EVALUATED', epochs: 0 })).toBe(true); // the row the table opens
    const evaluated = cand({ status: 'EVALUATED' });
    expect(trainingStatusOf(evaluated)).toBeNull();
    expect(isUntrainedUnevaluated(evaluated)).toBe(false);
    expect(isUntrainedUnevaluated(cand({ status: 'SEARCH_COMPLETE' }))).toBe(false); // unknown: no badge invented
  });

  it('GeneratedTable shows the badge on every generated row and keeps the NOT_EVALUATED text', () => {
    const rows = syntheticGeneratedRows();
    render(<GeneratedTable rows={rows} onOpen={() => undefined} />);
    const table = screen.getByTestId('generated-candidates');
    expect(within(table).getAllByText(UNTRAINED_UNEVALUATED)).toHaveLength(rows.length);
    expect(within(table).getAllByText('NOT_EVALUATED')).toHaveLength(rows.length);
    expect(within(table).getByRole('columnheader', { name: '학습 · 성능 평가' })).toBeInTheDocument();
  });

  it('GeneratedTable shows the reported statuses instead when a 1.5.0 row says TRAINED', () => {
    const [row] = syntheticGeneratedRows();
    render(<GeneratedTable rows={[{ ...row, training_status: 'TRAINED' } as never]} onOpen={() => undefined} />);
    const table = screen.getByTestId('generated-candidates');
    expect(within(table).queryByText(UNTRAINED_UNEVALUATED)).toBeNull();
    expect(table).toHaveTextContent('TRAINED / NOT_EVALUATED');
  });

  it('CandidateView: generated candidate -> UNTRAINED · UNEVALUATED; DD-5 row -> 학습 완료 · 미평가; evaluated row -> neither', async () => {
    const [row] = syntheticGeneratedRows();
    const opened = { ...row, status: 'NOT_EVALUATED', epochs: 0 } as unknown as Candidate; // what GeneratedTable passes on
    const client = (detail: unknown): ApiClient => ({ candidateDetail: async () => detail }) as unknown as ApiClient;
    const one = render(<CandidateView c={opened} run={null} runId="r" all={[opened]} client={client(syntheticGeneratedDetail(row.candidate_id))} />);
    expect(await screen.findByTestId('untrained-unevaluated')).toHaveTextContent(UNTRAINED_UNEVALUATED);
    expect(screen.queryByTestId('trained-not-evaluated')).toBeNull();
    one.unmount();

    const trained = cand({ candidate_id: 't-1', name: 't-1-model', status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED', training_mode: 'scratch' } as never);
    const two = render(<CandidateView c={trained} run={null} runId="r" all={[trained]} client={client({ candidate_id: 't-1', blocks: [], evaluations: [] })} />);
    expect(await screen.findByTestId('trained-not-evaluated')).toHaveTextContent(TRAINED_NOT_EVALUATED);
    expect(TRAINED_NOT_EVALUATED).toBe('학습 완료 · 미평가'); // DD-5 wording unchanged
    expect(screen.queryByTestId('untrained-unevaluated')).toBeNull();
    two.unmount();

    const evaluated = cand({ candidate_id: 'e-1', name: 'e-1-model' });
    render(<CandidateView c={evaluated} run={null} runId="r" all={[evaluated]} client={client({ candidate_id: 'e-1', blocks: [], evaluations: [] })} />);
    await screen.findByTestId('candidate-view');
    expect(screen.queryByTestId('untrained-unevaluated')).toBeNull();
    expect(screen.queryByText(/Client recompute/)).toBeNull(); // DD-A8
  });

  it('CandidateView takes training_status from the candidate detail when the row lacks it (1.5.0 detail, any origin)', async () => {
    const queued = cand({ candidate_id: 'q-1', name: 'q-1-model', status: 'QUEUED' as never, ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null });
    const detail = { candidate_id: 'q-1', blocks: [], evaluations: [], training_status: 'UNTRAINED', evaluation_status: 'NOT_EVALUATED' };
    render(<CandidateView c={queued} run={null} runId="r" all={[queued]} client={{ candidateDetail: async () => detail } as unknown as ApiClient} />);
    expect(await screen.findByTestId('untrained-unevaluated')).toHaveTextContent(UNTRAINED_UNEVALUATED);
  });

  it('CandidateView: a GA row a CPU runner trained but could not evaluate (EVALUATION_REQUIRES_CUDA) -> 학습 완료 · 미평가 (DD-A63 a)', async () => {
    const searchRun = { resolved_protocol: { console: { search: { arms: ['GA'], evaluation_enabled: true } } } } as never;
    const cpuTrained = cand({ candidate_id: 'g-1', name: 'g-1-model', arm: 'GA', status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED', reason_code: 'EVALUATION_REQUIRES_CUDA', training_status: 'TRAINED', ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null, latency_source: 'pending' } as never);
    render(<CandidateView c={cpuTrained} run={searchRun} runId="r" all={[cpuTrained]} client={{ candidateDetail: async () => ({ candidate_id: 'g-1', blocks: [], evaluations: [] }) } as unknown as ApiClient} />);
    expect(await screen.findByTestId('trained-not-evaluated')).toHaveTextContent(TRAINED_NOT_EVALUATED);
    expect(TRAINED_NOT_EVALUATED).toBe('학습 완료 · 미평가'); // DD-5 wording kept
    expect(screen.queryByTestId('untrained-unevaluated')).toBeNull();
  });
});

describe('Workers table: runner_id / device_id (contract 1.5.0)', () => {
  it('shows the core-issued ids when present and a dash for an older server', () => {
    const workers = [
      { id: 'w-new', status: 'busy', device: 'cuda:0', runner_id: 'runner-synthetic-01', device_id: 'device-synthetic-0a' },
      { id: 'w-old', status: 'idle', device: 'cuda:1' },
    ] as unknown as Worker[];
    render(<WorkerTable workers={workers} />);
    const table = screen.getByTestId('worker-table');
    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent ?? '');
    const col = (label: string): number => headers.findIndex((h) => h.includes(label));
    const cells = (id: string): string[] => within(within(table).getByText(id).closest('tr') as HTMLElement).getAllByRole('cell').map((c) => c.textContent ?? '');
    expect(col('Runner ID')).toBeGreaterThan(-1);
    expect(col('Device ID')).toBeGreaterThan(-1);
    expect(cells('w-new')[col('Runner ID')]).toBe('runner-synthetic-01');
    expect(cells('w-new')[col('Device ID')]).toBe('device-synthetic-0a');
    expect(cells('w-old')[col('Runner ID')]).toBe('—');
    expect(cells('w-old')[col('Device ID')]).toBe('—');
  });
});

describe('Monitor: the generation representative is the server value only (R6)', () => {
  it('no "differs from client rule" badge even when the highest-fitness visible row is another candidate', async () => {
    const snapshot = fixtureSnapshot();
    // GA gen 0: the highest-fitness visible row is ga-g0-0 (recorded server values of tests/fixtures.ts); the server names ga-g0-3.
    // The removed client rule would have flagged this as "differs from client rule"; the console now shows the server value only.
    const fit = (cid: string): number => snapshot.candidates.find((c) => c.candidate_id === cid)?.fitness as number;
    expect(fit('synthetic-ga-g0-0')).toBeGreaterThan(fit('synthetic-ga-g0-3'));
    snapshot.generations = [gen({ generation: 0, representative_candidate_id: 'synthetic-ga-g0-3', representative_evaluation_id: 'synthetic-ev-ga-g0-3', planned_count: 5, evaluated_count: 5 })];
    const api = installMockApi({ snapshot });
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
    await waitFor(() => expect(screen.getByTestId('pause-btn')).toBeEnabled());
    await user.click(screen.getByTestId('nav-monitor'));
    const table = screen.getByTestId('generation-table');
    expect(within(table).getByRole('columnheader', { name: 'Representative (server)' })).toBeInTheDocument();
    expect(within(table).getByText('synthetic-ev-ga-g0-3')).toBeInTheDocument();
    expect(screen.queryByText(/differs from client rule/)).toBeNull();
  });
});
