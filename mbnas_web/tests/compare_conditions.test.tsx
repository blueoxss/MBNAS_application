/** DD-33 (WP-W1): per-run conditions table on the Compare screen. Server data is mocked (ArchiveCompareRun.conditions is written by WP-S5). */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { App } from '../src/App';
import { CONDITION_KEYS, conditionDifferences, conditionValue } from '../src/screens/Compare';
import type { ArchiveCompareRun } from '../src/archive/types';
import { AppProvider } from '../src/state/context';
import { installArchiveFetch, newFixtureState, RUN_A, RUN_B } from './archive_fixtures';

let restore: () => void = () => undefined;
afterEach(() => { cleanup(); restore(); window.history.replaceState(null, '', '#/server'); });

type Conditions = Record<(typeof CONDITION_KEYS)[number], string | number | null>;
const base: Conditions = { dataset: 'synthetic-ds-1', split: 'val', stage: 'final@12e', epochs_total: 12, protocol_hash: 'sha256:aaaa', device: 'cuda:0', precision: 'fp16', checkpoint: '/runs/a/best.pt' };
const run = (id: string, conditions: Partial<Conditions> | null | undefined): ArchiveCompareRun => ({ id, ...(conditions === undefined ? {} : { conditions: conditions === null ? null : { ...base, ...conditions } }) }) as unknown as ArchiveCompareRun;

describe('conditionDifferences', () => {
  it('flags only pairs whose recorded values differ, per key', () => {
    const d = conditionDifferences([run('a', {}), run('b', { epochs_total: 90, device: 'cpu' }), run('c', {})]);
    expect(d.pairs).toEqual([{ a: 'a', b: 'b', keys: ['epochs_total', 'device'] }, { a: 'b', b: 'c', keys: ['epochs_total', 'device'] }]);
    expect(d.unknown).toEqual([]);
  });
  it('never infers: a null on either side is neither equal nor different and is listed as unknown', () => {
    const d = conditionDifferences([run('a', { precision: null, checkpoint: null }), run('b', { precision: 'fp32' })]);
    expect(d.pairs).toEqual([]);
    expect(d.unknown).toEqual(['precision', 'checkpoint']);
  });
  it('a run without conditions (older server) is all unknown, never different', () => {
    const d = conditionDifferences([run('a', undefined), run('b', {})]);
    expect(d.pairs).toEqual([]);
    expect(d.unknown).toEqual([...CONDITION_KEYS]);
    expect(conditionValue(run('a', null), 'dataset')).toBeNull();
    expect(conditionValue(run('a', { epochs_total: 0 }), 'epochs_total')).toBe(0); // zero is a recorded value
  });
});

async function mount(conditionsFor: (id: string) => Partial<Conditions> | null | undefined): Promise<void> {
  window.history.replaceState(null, '', `#/compare?runs=${RUN_A},${RUN_B}`);
  const api = installArchiveFetch(newFixtureState());
  restore = api.restore;
  const inner = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const response = await inner(input, init);
    if (!new URL(String(input), 'http://x').pathname.endsWith('/archive/compare')) return response;
    const body = await response.json() as { runs: { id: string }[] };
    body.runs = body.runs.map((r) => { const c = conditionsFor(r.id); return c === undefined ? r : { ...r, conditions: c === null ? null : { ...base, ...c } }; });
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
  }) as typeof fetch;
  const user = userEvent.setup();
  render(<AppProvider><App /></AppProvider>);
  await user.type(screen.getByLabelText('API 토큰'), 'TEST-TOKEN');
  await user.click(screen.getByTestId('gate-connect'));
  await screen.findByTestId('compare-conditions-table');
}

describe('Compare screen: 실행 조건', () => {
  it('shows every condition per run, 기록 없음 for null, and 조건 상이 when recorded values differ', async () => {
    await mount((id) => id === RUN_A ? {} : { epochs_total: 90, device: null, checkpoint: '/runs/b/best.pt' });
    const table = screen.getByTestId('compare-conditions-table');
    expect(within(table).getAllByRole('columnheader').map((h) => (h.textContent ?? '').replace('⇅', ''))).toEqual(['Run', ...CONDITION_KEYS]);
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('synthetic-ds-1');
    expect(rows[0]).toHaveTextContent('cuda:0');
    expect(rows[1]).toHaveTextContent('90');
    expect(within(rows[1]).getByText('기록 없음')).toBeInTheDocument(); // device null
    const warning = screen.getByTestId('compare-conditions-differ');
    expect(warning).toHaveTextContent('조건 상이');
    expect(warning).toHaveTextContent(`${RUN_A} ↔ ${RUN_B} (epochs_total, checkpoint)`); // device is null on one side: not compared
    expect(screen.getByTestId('compare-conditions-unknown')).toHaveTextContent('device');
    // WPS5 request: device/precision are the latency protocol's values, stage is free text
    expect(screen.getByTestId('compare-conditions-note')).toHaveTextContent('latency 측정 protocol');
  });

  it('no warning when the recorded conditions agree', async () => {
    await mount(() => ({}));
    expect(screen.queryByTestId('compare-conditions-differ')).toBeNull();
    expect(screen.queryByTestId('compare-conditions-unknown')).toBeNull();
  });

  it('a server without conditions shows 기록 없음 everywhere and no warning (nothing is inferred)', async () => {
    await mount(() => undefined);
    const rows = within(screen.getByTestId('compare-conditions-table')).getAllByRole('row').slice(1);
    for (const row of rows) expect(within(row).getAllByText('기록 없음')).toHaveLength(CONDITION_KEYS.length);
    expect(screen.queryByTestId('compare-conditions-differ')).toBeNull();
    await waitFor(() => expect(screen.getByTestId('compare-conditions-unknown')).toHaveTextContent(CONDITION_KEYS.join(', ')));
  });
});
