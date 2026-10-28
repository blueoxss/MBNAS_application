import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { isPolicyLocked, AppProvider, canRun, validationKey } from '../src/state/context';
import { fixtureSnapshot, PROTOCOL_HASH, RUN_ID, fixtureValidateOk } from './fixtures';
import { installMockApi, type MockCall } from './mockapi';

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

async function connect(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText(/Bearer token/), 'TEST-TOKEN');
  await user.click(screen.getByTestId('connect-btn'));
  await screen.findByText(/status: ok/);
}

describe('policy lock rule', () => {
  it('locks in every non-terminal run state and only those', () => {
    for (const s of ['QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED', 'RESUMING', 'CANCEL_REQUESTED'] as const) expect(isPolicyLocked({ status: s })).toBe(true);
    for (const s of ['COMPLETED', 'FAILED', 'CANCELLED'] as const) expect(isPolicyLocked({ status: s })).toBe(false);
    expect(isPolicyLocked(null)).toBe(false);
  });
  it('canRun requires a valid Validate of the current unchanged state', () => {
    const v = { status: 'done' as const, response: fixtureValidateOk({}), requestKey: validationKey('', { a: 1 }), error: null, httpStatus: 200 };
    expect(canRun(v, validationKey('', { a: 1 }))).toBe(true);
    expect(canRun(v, validationKey('', { a: 2 }))).toBe(false); // dirty
    expect(canRun(v, validationKey('https://other', { a: 1 }))).toBe(false); // origin changed
    expect(canRun({ ...v, response: { ...v.response, valid: false } }, validationKey('', { a: 1 }))).toBe(false);
    expect(canRun({ ...v, status: 'pending' }, validationKey('', { a: 1 }))).toBe(false);
  });
});

describe('Server screen flow', () => {
  it('connects, shows registry status/reason, and never persists the token', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const user = userEvent.setup();
    const { calls } = mount();
    await connect(user);
    const table = screen.getByTestId('registry-table');
    expect(within(table).getByText('BLOCKED_SOURCE')).toBeInTheDocument();
    expect(within(table).getByText(/weights download blocked/)).toBeInTheDocument();
    expect(calls[0].headers.Authorization).toBe('Bearer TEST-TOKEN');
    expect(setItem).not.toHaveBeenCalled();
    expect(window.location.href).not.toContain('TEST-TOKEN');
    expect(JSON.stringify({ ...localStorage })).not.toContain('TEST-TOKEN');
    setItem.mockRestore();
  });

  it('shows a clear connection error and keeps working', async () => {
    const user = userEvent.setup();
    const api = installMockApi();
    restore = api.restore;
    (globalThis.fetch as unknown as { mockImplementation: (f: unknown) => void }).mockImplementation(async () => {
      throw new TypeError('Failed to fetch');
    });
    render(
      <AppProvider>
        <App />
      </AppProvider>,
    );
    await user.click(screen.getByTestId('connect-btn'));
    expect(await screen.findByTestId('connection-error')).toHaveTextContent(/Cannot reach the server/);
  });

  it('rejects a plain-http remote origin before any request', async () => {
    const user = userEvent.setup();
    const { calls } = mount();
    await user.type(screen.getByTestId('origin-input'), 'http://nas.example.org');
    expect(screen.getByText(/Only https/)).toBeInTheDocument();
    expect(screen.getByTestId('connect-btn')).toBeDisabled();
    expect(calls).toHaveLength(0);
  });

  it('Run is enabled only after a valid Validate and disabled again when the editor changes', async () => {
    const user = userEvent.setup();
    const { calls } = mount();
    await connect(user);
    expect(screen.getByTestId('run-btn')).toBeDisabled();
    await user.click(screen.getByTestId('validate-btn'));
    await screen.findByTestId('validation-result');
    expect(screen.getByTestId('protocol-hash')).toHaveTextContent(PROTOCOL_HASH);
    expect(screen.getByTestId('resolved-diff')).toBeInTheDocument();
    expect(screen.getByTestId('run-btn')).toBeEnabled();

    // edit the policy on the Settings screen -> dirty -> Run disabled until re-validated
    await user.click(screen.getByTestId('nav-settings'));
    const budget = screen.getByTestId('budget');
    await user.clear(budget);
    await user.type(budget, '5');
    await user.click(screen.getByTestId('nav-server'));
    expect(screen.getByTestId('run-btn')).toBeDisabled();
    expect(screen.getByText(/Changed since Validate/)).toBeInTheDocument();
    await user.click(screen.getByTestId('validate-btn'));
    await waitFor(() => expect(screen.getByTestId('run-btn')).toBeEnabled());

    await user.click(screen.getByTestId('run-btn'));
    await waitFor(() => expect(screen.getByTestId('submit-message')).toHaveTextContent(`Run created: ${RUN_ID}`));
    const post = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/runs')!;
    expect(post.headers['Idempotency-Key']).toBe((post.body as { idempotency_key: string }).idempotency_key);
    expect((post.body as { protocol_hash: string }).protocol_hash).toBe(PROTOCOL_HASH);
    expect((post.body as { validation_id: string }).validation_id).toBe('synthetic-validation-1');
    // the created run is loaded and its resolved protocol replaces the editor: not reported as an unsaved edit
    await screen.findByText(/editor shows the loaded run's resolved protocol/);
    expect(screen.queryByText(/Changed since Validate/)).toBeNull();
    expect(screen.getByTestId('run-disabled-reason')).toHaveTextContent('A run is loaded');
  });

  it('a timeout answer keeps the same Idempotency-Key for the retry; 409 shows the conflict', async () => {
    const user = userEvent.setup();
    const keys: string[] = [];
    let n = 0;
    mount({
      onRun: async (call) => {
        keys.push(call.headers['Idempotency-Key']);
        n += 1;
        if (n === 1) return new Response('{"code":"BUSY","message":"upstream timeout"}', { status: 504 });
        return new Response(JSON.stringify({ code: 'IDEMPOTENCY_CONFLICT', message: 'same key, different payload' }), { status: 409, headers: { 'content-type': 'application/json' } });
      },
    });
    await connect(user);
    await user.click(screen.getByTestId('validate-btn'));
    await screen.findByTestId('validation-result');
    await user.click(screen.getByTestId('run-btn'));
    await waitFor(() => expect(screen.getByTestId('submit-message')).toHaveTextContent(/same Idempotency-Key|Idempotency-Key/));
    expect(screen.getByTestId('run-btn')).toHaveTextContent(/Retry Run \(same Idempotency-Key\)/);
    await user.click(screen.getByTestId('run-btn'));
    await waitFor(() => expect(screen.getByTestId('submit-message')).toHaveTextContent(/409 conflict/));
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });
});

describe('policy lock in the UI', () => {
  it('a loaded RUNNING run locks the Settings screen and offers a fork; a COMPLETED run does not', async () => {
    const user = userEvent.setup();
    mount();
    await connect(user);
    await user.type(screen.getByTestId('run-id-input'), RUN_ID);
    await user.click(screen.getByTestId('load-run-btn'));
    await waitFor(() => expect(screen.getByTestId('pause-btn')).toBeEnabled());
    await user.click(screen.getByTestId('nav-settings'));
    expect(screen.getByTestId('lock-banner')).toBeInTheDocument();
    expect(screen.getByTestId('settings-fieldset')).toHaveAttribute('data-locked', 'true');
    expect(screen.getByTestId('budget')).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /Fork as new version/ }));
    expect(screen.queryByTestId('lock-banner')).not.toBeInTheDocument();
    expect(screen.getByTestId('settings-fieldset')).toHaveAttribute('data-locked', 'false');
  });

  it('a COMPLETED run leaves the policy editable', async () => {
    const user = userEvent.setup();
    mount({ snapshot: fixtureSnapshot('COMPLETED', 5) });
    await connect(user);
    await user.type(screen.getByTestId('run-id-input'), RUN_ID);
    await user.click(screen.getByTestId('load-run-btn'));
    await waitFor(() => expect(screen.getByTestId('export-btn')).toBeEnabled());
    await user.click(screen.getByTestId('nav-settings'));
    expect(screen.queryByTestId('lock-banner')).not.toBeInTheDocument();
    expect(screen.getByTestId('settings-fieldset')).toHaveAttribute('data-locked', 'false');
  });
});
