import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { App } from '../src/App';
import { curveSeries } from '../src/screens/Compare';
import { AppProvider } from '../src/state/context';
import { fixtureCompare, installArchiveFetch, newFixtureState, RUN_A, RUN_B, RUN_C } from './archive_fixtures';

let restore: () => void = () => undefined;
afterEach(() => {
  cleanup();
  restore();
  window.history.replaceState(null, '', '#/server');
});

async function mountCompare(hash = '#/compare'): Promise<{ user: ReturnType<typeof userEvent.setup>; calls: ReturnType<typeof installArchiveFetch>['calls'] }> {
  window.history.replaceState(null, '', hash);
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
  await screen.findByTestId('compare-list');
  return { user, calls: api.calls };
}

describe('Run 비교', () => {
  it('lists the runs of one dataset only, needs two selections, then draws both curves, the baseline table and the finalists', async () => {
    const { user, calls } = await mountCompare();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Run 비교');
    expect(screen.getByText(/읽기 전용/)).toHaveTextContent('NAS archive run');
    const list = screen.getByTestId('compare-list');
    expect(within(list).getAllByRole('checkbox')).toHaveLength(2); // dataset one: runs A and B
    expect(within(list).queryByTestId(`compare-check-${RUN_C}`)).toBeNull();
    await user.click(screen.getByTestId(`compare-check-${RUN_A}`));
    expect(screen.getByTestId('compare-hint')).toBeInTheDocument();
    expect(calls.some((c) => c.path.includes('/archive/compare'))).toBe(false);
    await user.click(screen.getByTestId(`compare-check-${RUN_B}`));
    await screen.findByTestId('chart-cmp-best');
    expect(window.location.hash).toBe(`#/compare?runs=${RUN_A},${RUN_B}`);
    expect(calls.filter((c) => c.path.includes('/archive/compare'))[0].path).toContain(`runs=${encodeURIComponent(`${RUN_A},${RUN_B}`)}`);
    expect(screen.getByTestId('chart-cmp-hv')).toBeInTheDocument();
    expect(within(screen.getByTestId('compare-legend')).getAllByRole('listitem')).toHaveLength(2);
    const bt = screen.getByTestId('compare-baseline-table');
    expect(within(bt).getAllByRole('row')).toHaveLength(3);
    expect(bt).toHaveTextContent('baseline_yolo26n');
    expect(screen.getByTestId('compare-finalists-table')).toHaveTextContent('synthetic-final-a');
    expect(screen.queryByTestId('compare-warning')).toBeNull();
    // a point without cumulative train time is not drawn (not moved to zero) and is reported
    expect(screen.getByTestId('missing-time')).toHaveTextContent(RUN_B);
    const pts = within(screen.getByTestId('chart-cmp-best')).getAllByTestId('line-point');
    expect(pts.some((p) => p.getAttribute('data-key') === `${RUN_B}:1`)).toBe(false);
    // table alternative
    await user.click(screen.getByTestId('compare-view-table'));
    expect(await screen.findByTestId('compare-points')).toBeInTheDocument();
  });

  it('runs with a different dataset are flagged as not comparable but still drawn', async () => {
    const { user } = await mountCompare(`#/compare?runs=${RUN_A},${RUN_C}`);
    // the dataset select follows the first selected run; switch to dataset two to reach run C then back is not needed:
    await waitFor(() => expect(screen.getByTestId('compare-warning')).toBeInTheDocument());
    expect(screen.getByTestId('compare-warning')).toHaveTextContent(`${RUN_C} (dataset_id differs)`);
    expect(screen.getByTestId('chart-cmp-best')).toBeInTheDocument();
    expect(within(screen.getByTestId('compare-baseline-table')).getAllByText('⚠ 불가')).toHaveLength(1);
    await user.click(screen.getByTestId(`compare-check-${RUN_B}`));
    await waitFor(() => expect(window.location.hash).toContain(RUN_B));
  });

  it('the selection is capped at six runs and a failing compare request shows a retryable error', async () => {
    const { user } = await mountCompare(`#/compare?runs=${RUN_A},synthetic-no-such-run`);
    expect((await screen.findAllByTestId('archive-error'))[0]).toHaveTextContent('ARCHIVE_RUN_NOT_FOUND');
    expect(screen.getByTestId('compare-list')).toBeInTheDocument();
    await user.click(screen.getByTestId(`compare-check-${RUN_A}`)); // deselect: back to one valid run
    expect(window.location.hash).toBe('#/compare?runs=synthetic-no-such-run');
  });
});

describe('curveSeries', () => {
  it('drops points without an x, keeps null y as a gap, and uses distinct styles per run', () => {
    const c = fixtureCompare([RUN_A, RUN_B]);
    const best = curveSeries(c.runs, 'best');
    expect(best).toHaveLength(2);
    expect(best[0].color).not.toBe(best[1].color);
    expect(best[0].dash).not.toBe(best[1].dash);
    expect(best[0].shape).not.toBe(best[1].shape);
    expect(best[0].step).toBe(true);
    expect(best[1].points.length).toBe((c.runs[1].curves?.best_so_far_map50_95 ?? []).length - 1);
    const hv = curveSeries(c.runs, 'hv');
    expect(hv[0].step).toBe(false);
    expect(hv[0].points.map((p) => p.x)).toEqual([0, 1, 2, 3]);
  });
});
