import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { AppProvider } from '../src/state/context';
import { installArchiveFetch, newFixtureState, RUN_A, SYNTH_A } from './archive_fixtures';

let restore: () => void = () => undefined;
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:synthetic') as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  cleanup();
  restore();
  window.history.replaceState(null, '', '#/server');
});

async function openCandidate(cid: string): Promise<ReturnType<typeof userEvent.setup>> {
  window.history.replaceState(null, '', `#/archive?run=${RUN_A}`);
  const api = installArchiveFetch(newFixtureState());
  restore = api.restore;
  const user = userEvent.setup();
  render(
    <AppProvider>
      <App />
    </AppProvider>,
  );
  await user.type(screen.getByLabelText('API 토큰'), 'TEST-TOKEN');
  await user.click(screen.getByTestId('gate-connect'));
  const btn = (await screen.findAllByTestId(`cand-open-${cid}`))[0];
  await user.click(btn);
  await screen.findByRole('dialog');
  await screen.findByTestId('acand-evals');
  return user;
}

const crossoverCid = SYNTH_A.rows.find((r) => r.generation === 2 && r.lineage?.mode === 'ga_crossover' && r.status === 'ok' && r.tier === 'T@8e')?.candidate_id as string;
const finalRow = SYNTH_A.rows.find((r) => r.is_final);
const failedRow = SYNTH_A.rows.find((r) => r.status === 'oom');

describe('archive candidate modal', () => {
  it('shows identity, per-tier evaluations, latency breakdown, architecture, lineage, checkpoint and provenance', async () => {
    await openCandidate(crossoverCid);
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveAttribute('aria-modal', 'true');
    expect(dlg).toHaveAttribute('aria-labelledby', 'acand-title');
    expect(screen.getByTestId('acand-title')).toHaveTextContent(crossoverCid);
    // tier rows: T@8e and (if promoted) T@10e, each with its own label
    const evals = within(screen.getByTestId('acand-evals'));
    expect(evals.getAllByText('T@8e').length).toBeGreaterThan(0);
    expect(within(dlg).getByText('latency 분해')).toBeInTheDocument();
    expect(within(dlg).getByText(/forward_only/)).toBeInTheDocument();
    expect(within(dlg).getByText(/t_post/)).toBeInTheDocument();
    expect(within(dlg).getByText(/head_mode/)).toBeInTheDocument();
    expect(within(dlg).getByTestId('stage-table')).toBeInTheDocument();
    expect(within(dlg).getByTestId('module-hist')).toBeInTheDocument();
    await userEvent.setup().click(within(dlg).getByText('yaml 보기'));
    expect(within(dlg).getByTestId('yaml-block')).toHaveTextContent(`synthetic yaml for ${crossoverCid}`);
    expect(within(dlg).getByTestId('parent-A')).toBeInTheDocument();
    expect(within(dlg).getByTestId('parent-B')).toBeInTheDocument();
    expect(within(dlg).getAllByTestId('parent-diff').length).toBe(2);
    expect(within(dlg).getByTestId('checkpoints')).toHaveTextContent('best.pt');
    expect(within(dlg).getByText('jsonl')).toBeInTheDocument();
  });

  it('a parent card navigates to that candidate; the back button returns', async () => {
    const user = await openCandidate(crossoverCid);
    const link = screen.getByTestId('parent-link-A');
    const parentId = link.textContent as string;
    expect(parentId).toMatch(/^synthetic-g001/);
    await user.click(link);
    await waitFor(() => expect(screen.getByTestId('acand-title')).toHaveTextContent(parentId));
    expect(window.location.hash).toContain(`cid=${parentId}`);
    await screen.findByTestId('acand-evals');
    await user.click(screen.getByTestId('acand-back'));
    await waitFor(() => expect(screen.getByTestId('acand-title')).toHaveTextContent(crossoverCid));
    expect(screen.queryByTestId('acand-back')).toBeNull();
  });

  it('Esc closes the modal, removes cid from the hash and returns focus to the opener', async () => {
    const user = await openCandidate(crossoverCid);
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(window.location.hash).toBe(`#/archive?run=${RUN_A}`);
    expect(document.activeElement).toBe(screen.getAllByTestId(`cand-open-${crossoverCid}`)[0]);
  });

  it('Tab stays inside the dialog', async () => {
    const user = await openCandidate(crossoverCid);
    const dlg = screen.getByRole('dialog');
    for (let i = 0; i < 40; i++) {
      await user.tab();
      expect(dlg.contains(document.activeElement)).toBe(true);
    }
    for (let i = 0; i < 10; i++) {
      await user.tab({ shift: true });
      expect(dlg.contains(document.activeElement)).toBe(true);
    }
  });

  it('a failed candidate shows its status and error, dashes for metrics, and says the architecture is missing', async () => {
    expect(failedRow).toBeTruthy();
    await openCandidate(failedRow?.candidate_id as string);
    const dlg = screen.getByRole('dialog');
    expect(within(dlg).getByTestId('acand-error')).toHaveTextContent('CUDA out of memory');
    expect(within(dlg).getByTestId('arch-missing')).toBeInTheDocument();
    expect(within(dlg).getAllByText('—').length).toBeGreaterThan(3);
    expect(within(screen.getByTestId('acand-evals')).queryByText('0.0000')).toBeNull();
    expect(within(dlg).getByTestId('checkpoints')).toHaveTextContent('없음');
  });

  it('a final-tier candidate carries its role badge and the final row', async () => {
    expect(finalRow).toBeTruthy();
    await openCandidate(finalRow?.candidate_id as string);
    const dlg = screen.getByRole('dialog');
    expect(within(dlg).getAllByText(/★ (BEST_ACCURACY|PARETO_KNEE)/).length).toBeGreaterThan(0);
    expect(within(screen.getByTestId('acand-evals')).getByText(/final@12e/)).toBeInTheDocument();
    expect(within(dlg).getByText(/Δ vs baseline/)).toBeInTheDocument();
  });

  it('an unknown candidate id (deep link) shows the 404 error inside the dialog and can be closed', async () => {
    window.history.replaceState(null, '', `#/archive?run=${RUN_A}&cid=synthetic-no-such-cand`);
    const api = installArchiveFetch(newFixtureState());
    restore = api.restore;
    const user = userEvent.setup();
    render(
      <AppProvider>
        <App />
      </AppProvider>,
    );
    await user.type(screen.getByLabelText('API 토큰'), 'TEST-TOKEN');
    await user.click(screen.getByTestId('gate-connect'));
    const dlg = await screen.findByRole('dialog');
    expect(await within(dlg).findByTestId('archive-error')).toHaveTextContent('HTTP 404 ARCHIVE_NOT_FOUND');
    await user.click(screen.getByTestId('acand-close'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByTestId('run-table')).toBeInTheDocument();
  });
});
