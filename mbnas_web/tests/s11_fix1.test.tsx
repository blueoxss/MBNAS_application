import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { App } from '../src/App';
import { AppProvider } from '../src/state/context';
import { installMockApi } from './mockapi';

let restore: () => void = () => undefined;
afterEach(() => {
  cleanup();
  restore();
  window.history.replaceState(null, '', '#/server');
});
beforeEach(() => window.history.replaceState(null, '', '#/server'));

describe('S11-F2 unknown run id', () => {
  it('shows an inline role=alert in the Load run card and does not enter the loaded state', async () => {
    const api = installMockApi();
    restore = api.restore;
    render(<AppProvider><App /></AppProvider>);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Bearer token/), 'TEST-TOKEN');
    await user.click(screen.getByTestId('connect-btn'));
    await screen.findByText(/status: ok/);
    await user.type(screen.getByTestId('run-id-input'), 'run-000000000000');
    await user.click(screen.getByTestId('load-run-btn'));
    const alert = await screen.findByTestId('load-run-error');
    expect(alert).toHaveAttribute('role', 'alert');
    expect(alert.textContent).toMatch(/404|not found|no such run/i);
    expect(alert.closest('.card')).toContainElement(screen.getByTestId('load-run-btn'));
    await waitFor(() => expect(screen.queryByText(/A run is loaded/)).toBeNull());
    expect(screen.queryByText(/editor shows the loaded run/)).toBeNull();
    expect(within(alert).queryByText(/stream/i)).toBeNull();
  });
});

describe('S11-F4 / S11-F5 / S11-F1 css', () => {
  const css = readFileSync('src/styles.css', 'utf8');
  it('btn-sm has a 24px minimum hit area with spacing', () => {
    const rule = /^\.btn-sm\s*\{([^}]*)\}/m.exec(css)![1];
    expect(rule).toMatch(/min-height:\s*(2[4-9]|[3-9]\d)px/);
    expect(rule).toMatch(/min-width:\s*(2[4-9]|[3-9]\d)px/);
    expect(rule).toMatch(/margin:\s*2px/);
  });
  it('device targets reason column keeps a min width inside the scroll wrapper', () => {
    expect(css).toMatch(/device-targets-table[^{]*\{[^}]*min-width/);
  });
  it('health commit hash wraps', async () => {
    const api = installMockApi();
    restore = api.restore;
    render(<AppProvider><App /></AppProvider>);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Bearer token/), 'TEST-TOKEN');
    await user.click(screen.getByTestId('connect-btn'));
    await screen.findByText(/status: ok/);
    expect(screen.getByTestId('health-commit')).toHaveClass('wrap-any');
  });
});
