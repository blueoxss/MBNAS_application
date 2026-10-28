/**
 * Round 4 (C07-D1, C07-D2), SYNTHETIC values only:
 *  - D1: a block-table row of the candidate detail opens the SAME block detail as its structure-graph node and its gene
 *    button: same `selected-block` content, same `?block=` and the "구조·gene" tab (`model_tab=structure`); rows without a
 *    structure node (or a native-module document) keep a plain Pos cell, never a dead button.
 *  - D2: the "학습 완료 · 미평가" badge title names its case (validation OFF explicit training vs. a CPU runner that trained
 *    but could not evaluate, EVALUATION_REQUIRES_CUDA) with a generic fallback; the Monitor table and the detail share it.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { App } from '../src/App';
import type { ApiClient } from '../src/api/client';
import type { Candidate, Run } from '../src/api/types';
import { CandidateView, structureBlockIdsByPosition } from '../src/components/CandidateView';
import { EVALUATION_REQUIRES_CUDA, TRAINED_NOT_EVALUATED, trainedNotEvaluatedTitle } from '../src/lib/evaluation';
import { setIn } from '../src/lib/policy';
import { AppProvider } from '../src/state/context';
import { SYNTHETIC_CONCAT_INDEX, SYNTHETIC_DETECT_INDEX, syntheticContractBlocks, syntheticStructureReport } from './detail_fixtures';
import { cand, fixtureSnapshot, resetCounter, RUN_ID } from './fixtures';
import { installMockApi } from './mockapi';

let restore: () => void = () => undefined;
afterEach(() => {
  cleanup();
  restore();
  restore = () => undefined;
  window.history.replaceState(null, '', '/');
});
beforeEach(() => {
  resetCounter();
  window.history.replaceState(null, '', '/');
});

const blockId = (i: number): string => `block-${String(i).padStart(4, '0')}`;
const query = (): URLSearchParams => new URL(window.location.href).searchParams;
const I = SYNTHETIC_CONCAT_INDEX;

function stub(detail: unknown): ApiClient {
  return { candidateDetail: async () => detail } as unknown as ApiClient;
}

/** A searched candidate whose rows are the nine synthetic YAML rows; its detail carries the mbnas-structure/2 report. */
async function renderCandidate(report: Record<string, unknown> = syntheticStructureReport(), blocks = syntheticContractBlocks()): Promise<Candidate> {
  const c = cand({ candidate_id: 'synthetic-blk-1', name: 'synthetic-blk-1-model', blocks });
  render(<CandidateView c={c} run={null} runId="r" all={[c]} client={stub({ candidate_id: c.candidate_id, blocks, evaluations: [], structure: report, structure_status: 'PASS', evaluation_status: 'EVALUATED' })} />);
  // the detail replaces the row-derived nodes (`<cid>#<pos>`) by the report's block ids
  await waitFor(() => expect(screen.getByTestId('summary-structure-status')).toHaveTextContent('구조: PASS'));
  return c;
}

describe('C07-D1: block-table row -> the same block detail as the graph node and the gene button', () => {
  it('maps YAML positions to the structure view ids (report ids, else <model>#<pos>; native documents map nothing)', () => {
    const report = syntheticStructureReport();
    const ids = structureBlockIdsByPosition(report.blocks as unknown[], 'cid', report);
    expect([...ids.entries()]).toEqual(Array.from({ length: 9 }, (_, i) => [i, blockId(i)]));
    const rows = structureBlockIdsByPosition(syntheticContractBlocks(), 'cid', {});
    expect(rows.get(I)).toBe(`cid#${I}`);
    const native = structureBlockIdsByPosition([{ block_id: 'n0', position: 0, native_path: 'model.backbone', gene_index: null }], 'cid', { representation: 'native_modules' });
    expect(native.size).toBe(0);
  });

  it('graph node, gene button and block-table row give the same selected-block content and ?block=; the row switches to model_tab=structure', async () => {
    await renderCandidate();
    const rowButton = screen.getByTestId(`block-row-${I}`);
    expect(rowButton).toHaveAttribute('data-block-id', blockId(I));
    expect(rowButton.tagName).toBe('BUTTON');
    expect(rowButton).toHaveAccessibleName(new RegExp(`^${I} · Concat`)); // the visible label starts the accessible name

    // 1. the structure graph node
    fireEvent.click(screen.getByRole('button', { name: `선택 ${I} Concat` }));
    const viaGraph = screen.getByTestId('selected-block').textContent;
    expect(viaGraph).toContain(`선택 블록 #${I} · Concat`);
    expect(query().get('block')).toBe(blockId(I));

    // 2. the gene button (after another block was selected)
    fireEvent.click(screen.getByTestId('gene-0'));
    expect(screen.getByTestId('selected-block')).toHaveTextContent('선택 블록 #0 · Conv');
    fireEvent.click(screen.getByTestId(`gene-${I}`));
    expect(screen.getByTestId('selected-block').textContent).toBe(viaGraph);
    expect(query().get('block')).toBe(blockId(I));

    // 3. the block-table row (after another block was selected); the table lives in the "전체" view
    fireEvent.click(screen.getByTestId('gene-0'));
    expect(query().get('block')).toBe(blockId(0));
    expect(screen.getByTestId('block-table')).toBeVisible();
    fireEvent.click(screen.getByTestId(`block-row-${I}`));
    await waitFor(() => expect(query().get('block')).toBe(blockId(I)));
    expect(query().get('model_tab')).toBe('structure');
    expect(screen.getByTestId('model-tab-structure')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('selected-block').textContent).toBe(viaGraph);
    expect(screen.getByTestId(`gene-${I}`)).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: `선택 ${I} Concat` })).toHaveAttribute('aria-pressed', 'true');
    // the clicked button is hidden by the tab switch: focus moves to the block detail heading
    expect(screen.getByTestId('block-table')).not.toBeVisible();
    await waitFor(() => expect(within(screen.getByTestId('selected-block')).getByRole('heading', { level: 4 })).toHaveFocus());

    // a second row click (any row) is a new request: Detect, then Concat again
    fireEvent.click(screen.getByTestId('model-tab-all'));
    fireEvent.click(screen.getByTestId(`block-row-${SYNTHETIC_DETECT_INDEX}`));
    await waitFor(() => expect(query().get('block')).toBe(blockId(SYNTHETIC_DETECT_INDEX)));
    expect(screen.getByTestId('selected-block')).toHaveTextContent(`선택 블록 #${SYNTHETIC_DETECT_INDEX} · Detect`);
    fireEvent.click(screen.getByTestId('model-tab-all'));
    fireEvent.click(screen.getByTestId(`block-row-${I}`));
    await waitFor(() => expect(query().get('block')).toBe(blockId(I)));
    expect(screen.getByTestId('selected-block').textContent).toBe(viaGraph);
  });

  it('a row whose position has no structure node keeps a plain Pos cell (no dead button)', async () => {
    const report = syntheticStructureReport();
    report.blocks = (report.blocks as Record<string, unknown>[]).filter((b) => b.position !== SYNTHETIC_DETECT_INDEX);
    await renderCandidate(report);
    const table = screen.getByTestId('block-table');
    expect(within(table).getAllByRole('button', { name: /구조·gene 탭에서 이 블록 상세 보기/ })).toHaveLength(8);
    expect(screen.queryByTestId(`block-row-${SYNTHETIC_DETECT_INDEX}`)).toBeNull();
    const detectRow = within(table).getAllByRole('row').find((tr) => tr.textContent?.includes('Detect')) as HTMLElement;
    expect(within(detectRow).getAllByRole('cell')[0]).toHaveTextContent(String(SYNTHETIC_DETECT_INDEX));
    expect(within(detectRow).queryByRole('button')).toBeNull();
  });

  it('a native-module document gets no row buttons (no YAML row <-> module correspondence)', async () => {
    const native = { schema: 'mbnas-structure/2', representation: 'native_modules', status: 'PASS', blocks: [{ block_id: 'native-0', native_path: 'model.backbone', module_class: 'Backbone', gene_index: null, position: 0 }], edges: [] };
    await renderCandidate(native);
    expect(screen.getByTestId('block-table')).toBeInTheDocument();
    expect(screen.queryAllByTestId(/^block-row-/)).toHaveLength(0);
  });
});

describe('C07-D2: "학습 완료 · 미평가" badge title', () => {
  const offRun = { resolved_protocol: { console: { extra: { training: { enabled: true, mode: 'scratch', validation_enabled: false } } } } } as unknown as Run;
  const searchRun = { resolved_protocol: { console: { search: { arms: ['GA'], evaluation_enabled: true } } } } as unknown as Run;

  it('names the case: CPU runner without CUDA, validation OFF explicit training, generic fallback', () => {
    const cuda = trainedNotEvaluatedTitle({ status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED', reason_code: EVALUATION_REQUIRES_CUDA }, searchRun);
    expect(cuda).toContain('runner에 CUDA가 없습니다');
    expect(cuda).toContain(`reason_code ${EVALUATION_REQUIRES_CUDA}`);
    expect(cuda).toContain('checkpoint는 유지');
    expect(cuda).not.toMatch(/validation OFF/);
    // reason code wins even inside an explicit training run (validation ON, CPU runner)
    expect(trainedNotEvaluatedTitle({ reason_code: EVALUATION_REQUIRES_CUDA }, offRun)).toBe(cuda);

    const off = trainedNotEvaluatedTitle({ status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED', training_mode: 'scratch' }, offRun);
    expect(off).toContain('validation OFF 명시 학습');
    expect(off).toContain('SEARCH_COMPLETE + performance_status NOT_EVALUATED');
    expect(off).not.toMatch(/CUDA/);

    const generic = trainedNotEvaluatedTitle({ status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED', training_mode: 'scratch' }, null);
    expect(generic).toContain('성능 평가 기록이 없습니다');
    expect(generic).not.toMatch(/validation OFF|CUDA/);
    expect(trainedNotEvaluatedTitle({ reason_code: 'SOME_CODE' }, null)).toContain('reason_code SOME_CODE');
  });

  it('candidate detail: the CPU NOT_EVALUATED row and the validation-OFF row get their own title; the badge text is unchanged', async () => {
    const cpuRow = cand({ candidate_id: 'synthetic-g-1', name: 'synthetic-g-1-model', arm: 'GA', status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED', reason_code: EVALUATION_REQUIRES_CUDA, training_status: 'TRAINED', ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null, latency_source: 'pending' } as never);
    const one = render(<CandidateView c={cpuRow} run={searchRun} runId="r" all={[cpuRow]} client={stub({ candidate_id: 'synthetic-g-1', blocks: [], evaluations: [] })} />);
    const badge = (await screen.findByTestId('trained-not-evaluated')).parentElement as HTMLElement;
    expect(screen.getByTestId('trained-not-evaluated')).toHaveTextContent(TRAINED_NOT_EVALUATED);
    expect(badge).toHaveAttribute('title', trainedNotEvaluatedTitle(cpuRow, searchRun));
    expect(badge.getAttribute('title')).toContain('runner에 CUDA가 없습니다');
    one.unmount();

    const offRow = cand({ candidate_id: 'synthetic-t-1', name: 'synthetic-t-1-model', status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED', training_mode: 'scratch', ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null } as never);
    render(<CandidateView c={offRow} run={offRun} runId="r" all={[offRow]} client={stub({ candidate_id: 'synthetic-t-1', blocks: [], evaluations: [] })} />);
    const offBadge = (await screen.findByTestId('trained-not-evaluated')).parentElement as HTMLElement;
    expect(offBadge.getAttribute('title')).toContain('validation OFF 명시 학습');
  });

  it('Monitor candidate table uses the same title per row', async () => {
    const snapshot = fixtureSnapshot('COMPLETED');
    snapshot.run = { ...snapshot.run, resolved_protocol: setIn(snapshot.run.resolved_protocol as Record<string, unknown>, 'console.search.evaluation_enabled', true) as never };
    const cpuRow = cand({ candidate_id: 'synthetic-cpu-1', name: 'synthetic-cpu-1', arm: 'GA', status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED', reason_code: EVALUATION_REQUIRES_CUDA, training_status: 'TRAINED', ap: null, recall: null, latency_ms: null, size_mb: null, fitness: null, latency_source: 'pending' } as never);
    snapshot.candidates = [cpuRow, cand({ candidate_id: 'synthetic-done-1', name: 'synthetic-done-1' })];
    const api = installMockApi({ snapshot });
    restore = api.restore;
    window.history.replaceState(null, '', '#/server'); // the App's connection screen (as in components.test.tsx)
    const user = userEvent.setup();
    render(<AppProvider><App /></AppProvider>);
    await user.type(screen.getByLabelText(/Bearer token/), 'T');
    await user.click(screen.getByTestId('connect-btn'));
    await screen.findByText(/status: ok/);
    await user.type(screen.getByTestId('run-id-input'), RUN_ID);
    await user.click(screen.getByTestId('load-run-btn'));
    await screen.findByTestId('export-btn');
    await user.click(screen.getByTestId('nav-monitor'));
    const table = await screen.findByTestId('candidate-table');
    const row = within(table).getByRole('button', { name: /synthetic-cpu-1/ }).closest('tr') as HTMLElement;
    const badge = within(row).getByText(TRAINED_NOT_EVALUATED);
    expect(badge.closest('[title]')).toHaveAttribute('title', trainedNotEvaluatedTitle(cpuRow, snapshot.run));
    expect(badge.closest('[title]')?.getAttribute('title')).toContain('runner에 CUDA가 없습니다');
  });
});
