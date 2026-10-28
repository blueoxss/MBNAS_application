import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../src/api/client';
import type { GeneratedCandidate, TrainingRequest, CandidateDetail } from '../src/api/types';
import { progressScalars } from '../src/components/TrainingProgress';
import { candidateRecordLabel } from '../src/components/CandidateModal';
import { CandidateView } from '../src/components/CandidateView';
import { TrainingPanel, trainingProtocol } from '../src/components/TrainingPanel';
import { getIn, setIn, type Policy } from '../src/lib/policy';
import { evaluationEnabled, inGenerationOrder } from '../src/lib/generation';
import { validatePolicy } from '../src/lib/validate';
import { GeneratedTable } from '../src/screens/Monitor';
import { Settings } from '../src/screens/Settings';
import { AppProvider, useApp, type AppModel } from '../src/state/context';
import { initialRunState, runReducer } from '../src/state/reducer';
import { fixtureSnapshot, fixturePresets, cand, fixtureDesignExampleProtocol } from './fixtures';

/** A full protocol: the synthetic DEFAULT_BASELINE14 server preset (the console embeds no template any more, DD-A8). */
const defaultPolicy = fixtureDesignExampleProtocol;

afterEach(() => { cleanup(); window.history.replaceState(null, '', '/#/settings'); });

describe('generation-only settings', () => {
  it('defaults ON and ignores preserved evaluation-only settings when OFF', () => {
    expect(evaluationEnabled(defaultPolicy())).toBe(true);
    expect(evaluationEnabled({ console: { search: {} } })).toBe(true);
    const p = { console: { search: { evaluation_enabled: false, nc: 3, candidate_count: 2 }, paths: { output_root: '/allowed/output' } }, rungs: [], training: { requested_input_size: [640, 640] } };
    // DD-17: no error/warning; exactly one info-level "preserved, not validated" notice.
    expect(validatePolicy(p).filter((x) => x.severity !== 'info')).toEqual([]);
    expect(validatePolicy(p).filter((x) => x.severity === 'info').map((x) => x.message)).toEqual(['성능 평가 OFF: 평가 설정 값은 보존되며 검증하지 않습니다']);
    expect(validatePolicy(setIn(p, 'console.search.nc', 0)).some((x) => x.field === 'console.search.nc')).toBe(true);
    expect(validatePolicy(setIn(p, 'console.search.max_attempts', 0)).some((x) => x.field === 'console.search.max_attempts')).toBe(true);
  });

  it('checkbox OFF disables evaluation controls without erasing saved weights or recipe', () => {
    let state: Policy = {};
    let model: AppModel | null = null;
    function Probe(): JSX.Element { const app = useApp(); state = app.policy; model = app; return <Settings />; }
    render(<AppProvider><Probe /></AppProvider>);
    // the editor starts empty (DD-A8): load the server preset first, as a connected console does
    act(() => (model as unknown as AppModel).applyServerPreset(fixturePresets()[0]));
    const before = structuredClone(getIn(state, 'rungs'));
    expect(before).toHaveLength(3);
    expect(screen.getByTestId('evaluation-enabled')).toBeChecked();
    fireEvent.click(screen.getByTestId('evaluation-enabled'));
    expect(getIn(state, 'console.search.evaluation_enabled')).toBe(false);
    expect(screen.getByTestId('rung-0-w-ap')).toBeDisabled();
    expect(getIn(state, 'rungs')).toEqual(before);
    // DD-17: evaluation-only path fields stay rendered, disabled, with their preserved values (output_root stays editable).
    expect(screen.getByTestId('path-dataset_yaml')).toBeDisabled();
    expect(screen.getByTestId('path-search_val_root')).toBeDisabled();
    expect(screen.getByTestId('path-output_root')).toBeEnabled();
    fireEvent.click(screen.getByTestId('evaluation-enabled'));
    expect(screen.getByTestId('rung-0-w-ap')).toBeEnabled();
    expect(screen.getByTestId('path-dataset_yaml')).toBeEnabled();
    expect(getIn(state, 'rungs')).toEqual(before);
  });
});

const generated = (id: string, order: number): GeneratedCandidate => ({ id, candidate_id: id, run_id: 'generated-run', name: id, family: 'yolo11', arm: 'IndependentRandom', generation_order: order, structure_status: 'PASS', evaluation_status: 'NOT_EVALUATED', training_status: 'UNTRAINED', ap: null, recall: null, latency_ms: null, fitness: null, parameters: null });

describe('unevaluated candidate identity and ordering', () => {
  it('preserves first-seen seed order [42, 7] without interleaving per-seed indexes', () => {
    const rows = [
      { id: '42-c0', search_seed: 42, generation_order: 0, fitness: null },
      { id: '42-c1', search_seed: 42, generation_order: 1, fitness: null },
      { id: '7-c0', search_seed: 7, generation_order: 0, fitness: null },
      { id: '7-c1', search_seed: 7, generation_order: 1, fitness: null },
    ];
    expect(inGenerationOrder(rows).map((c) => c.id)).toEqual(['42-c0', '42-c1', '7-c0', '7-c1']);
    expect(inGenerationOrder([rows[1], rows[0], rows[3], rows[2]]).map((c) => c.id)).toEqual(['42-c0', '42-c1', '7-c0', '7-c1']);
    expect(rows.map((c) => c.id)).toEqual(['42-c0', '42-c1', '7-c0', '7-c1']);
  });

  it('keeps generation records outside evaluation rows across repeat snapshots', () => {
    const snap = { ...fixtureSnapshot(), generated_candidates: [generated('second', 2), generated('first', 1)] };
    let rs = runReducer(initialRunState, { type: 'snapshot', snap, reason: 'initial', at: 't' });
    rs = runReducer(rs, { type: 'snapshot', snap, reason: 'reconnect', at: 't2' });
    expect(rs.generatedCandidates).toHaveLength(2);
    expect(Object.keys(rs.candidates)).not.toContain('first');
    expect(inGenerationOrder(rs.generatedCandidates).map((c) => c.id)).toEqual(['first', 'second']);
    const open = vi.fn(); render(<GeneratedTable rows={rs.generatedCandidates} onOpen={open} />);
    expect(screen.getByText('미평가 랜덤 후보 · 생성 순서 (성능 순위 아님)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'first' }));
    expect(open.mock.calls[0][0]).toMatchObject({ candidate_id: 'first', evaluation_status: 'NOT_EVALUATED', ap: null, fitness: null });
    expect(open.mock.calls[0][0].evaluation_hash).toBeUndefined();
  });
});

describe('shared explicit training request', () => {
  it('minimal generated source requires an explicit server recipe choice and preserves source', async () => {
    const calls: { url: string; body: unknown }[] = [];
    const presets = fixturePresets();
    const client = new ApiClient({ origin: '', token: '', fetchImpl: vi.fn(async (url, init) => {
      calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null });
      const body = String(url).endsWith('/protocols/presets') ? { presets } : { run_id: 'training-child', status: 'QUEUED' };
      return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
    }) });
    render(<TrainingPanel client={client} protocol={{ console: { search: { evaluation_enabled: false, nc: 2 }, paths: { output_root: '/allowed/out' } } }} source={{ kind: 'generated', run_id: 'generated-minimal', candidate_id: 'model' }} />);
    expect(screen.getByTestId('training-submit')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '서버 학습 recipe 불러오기' }));
    await waitFor(() => expect(screen.getByTestId('training-recipe').querySelectorAll('option').length).toBeGreaterThan(1));
    fireEvent.change(screen.getByTestId('training-recipe'), { target: { value: presets[0].id } });
    expect(screen.getByTestId('training-submit')).toBeEnabled();
    fireEvent.click(screen.getByTestId('training-submit'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/training-runs'))).toBe(true));
    const request = calls.find((call) => call.url.endsWith('/training-runs'))!.body as TrainingRequest;
    expect(request.source).toEqual({ kind: 'generated', run_id: 'generated-minimal', candidate_id: 'model' });
    expect(getIn(request.protocol, 'console.paths.output_root')).toBe('/allowed/out');
    expect(getIn(request.protocol, 'training')).toBeTruthy();
  });

  it('progress only displays recorded finite scalar metrics', () => {
    expect(progressScalars({ loss: 0.4, train_loss: 0.3, learning_rate: 0.001, throughput: Infinity, native_metrics: { 'val/box_loss': 0.5, bad: NaN }, secret: 'source text' })).toEqual({ loss: 0.4, train_loss: 0.3, learning_rate: 0.001, 'native_metrics.val/box_loss': 0.5 });
    expect(progressScalars(null)).toEqual({});
  });

  it('copies recipe, keeps generated origin, independent validation and stable retry key', async () => {
    const policy = setIn(defaultPolicy(), 'console.search.evaluation_enabled', false);
    const calls: TrainingRequest[] = [];
    const client = new ApiClient({ origin: '', token: 'in-memory-only', fetchImpl: vi.fn(async (_url, init) => {
      calls.push(JSON.parse(String(init?.body)) as TrainingRequest);
      if (calls.length === 1) throw new TypeError('temporary transport failure');
      return new Response(JSON.stringify({ run_id: 'training-child', status: 'QUEUED' }), { status: 200, headers: { 'content-type': 'application/json' } });
    }) });
    render(<TrainingPanel client={client} protocol={policy} source={{ kind: 'generated', run_id: 'origin', candidate_id: 'first' }} />);
    fireEvent.click(screen.getByTestId('training-submit'));
    await screen.findByText(/temporary transport failure/);
    fireEvent.click(screen.getByTestId('training-submit'));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[0].idempotency_key).toBe(calls[1].idempotency_key);
    expect(calls[1].source).toEqual({ kind: 'generated', run_id: 'origin', candidate_id: 'first' });
    expect(calls[1].validation_enabled).toBe(true);
    expect(getIn(policy, 'console.search.evaluation_enabled')).toBe(false);
    expect(getIn(calls[1].protocol, 'console.training.epoch_target_total')).toBe(90);
    expect(getIn(calls[1].protocol, 'training.optimizer')).toBe('AdamW');
  });

  it('does not alter search fitness weights when creating an explicit training recipe', () => {
    const source = defaultPolicy();
    const result = trainingProtocol(source, { dataset: '/data/d.yaml', output: '/runs/t', checkpoint: '', epochs: 5, batch: 2, size: 320, seed: 42, optimizer: 'SGD', lr: 0.01 });
    expect(getIn(result, 'rungs')).toEqual(getIn(source, 'rungs'));
    expect(getIn(result, 'console.training.epoch_target_total')).toBe(5);
    expect(getIn(source, 'console.training.epoch_target_total')).toBeUndefined();
  });
});


describe('native training image gallery', () => {
  it('labels unmeasured training records without implying an evaluation', () => {
    expect(candidateRecordLabel({ status: 'SEARCH_COMPLETE', evaluation_status: 'NOT_EVALUATED' })).toBe('기록');
    expect(candidateRecordLabel({ status: 'TRAINING' })).toBe('기록');
    expect(candidateRecordLabel({ status: 'EVALUATED', evaluation_status: 'NOT_EVALUATED' })).toBe('기록');
    expect(candidateRecordLabel({ status: 'EVALUATED', evaluation_status: 'EVALUATED' })).toBe('evaluation');
  });
  it('shows actual image MIME artifacts from candidate detail, excluding other JSON/source files', async () => {
    const candidate = cand({ candidate_id: 'synthetic-native-candidate', artifacts: [] });
    const detail = {
      candidate_id: candidate.candidate_id, name: candidate.name, evaluation_status: 'NOT_EVALUATED', evaluations: [], blocks: [],
      artifacts: [
        { id: 'native-image', kind: 'other', mime_type: 'image/jpeg', url: '/synthetic/native/train_batch0.jpg', auth_mode: 'signed_url', metadata: { category: 'training_batch', filename: 'train_batch0.jpg', candidate_id: candidate.candidate_id, stage: 'stage_e0_e2', batch_index: 0, epoch: null, checkpoint_id: null } },
        { id: 'private-json', kind: 'other', mime_type: 'application/json', url: '/synthetic/not-image.json', metadata: { filename: 'not-image.json' } },
        { id: 'private-source', kind: 'other', mime_type: 'text/x-python', url: '/synthetic/not-image.py', metadata: { filename: 'not-image.py' } },
        { id: 'other-candidate-image', kind: 'other', mime_type: 'image/png', url: '/synthetic/other.png', metadata: { candidate_id: 'unrelated-candidate', filename: 'unrelated.png' } },
      ],
    } as unknown as CandidateDetail;
    const client = new ApiClient({ origin: '', token: '', fetchImpl: vi.fn(async () => new Response(JSON.stringify(detail), { headers: { 'content-type': 'application/json' } })) });
    render(<CandidateView c={candidate} run={{ ...fixtureSnapshot().run, resolved_protocol: setIn(fixtureSnapshot().run.resolved_protocol as Policy, 'console.extra.training.validation_enabled', false) as never }} runId="synthetic-native-run" all={[candidate]} client={client} />);
    const image = await screen.findByRole('img', { name: /train_batch0.jpg · training_batch/ });
    expect(image).toHaveAttribute('src', '/synthetic/native/train_batch0.jpg');
    expect(screen.getByText('성능 미평가 · NOT_EVALUATED')).toBeInTheDocument();
    const gallery = screen.getByTestId('native-artifact-gallery');
    expect(gallery.querySelectorAll('img')).toHaveLength(1);
    expect(gallery).toHaveTextContent('validation 미실행: native plot의 val 항목·0은 측정값이 아니며 성능 수치로 해석하지 않습니다.');
    expect(gallery).toHaveTextContent('epoch 미기록 · checkpoint 미기록');
    expect(gallery).not.toHaveTextContent('not-image.json');
    expect(gallery).not.toHaveTextContent('not-image.py');
    expect(gallery).not.toHaveTextContent('unrelated.png');
  });
});
