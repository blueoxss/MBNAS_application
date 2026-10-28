/**
 * WP-6 R2/R3 (DD-A8 / DD-A21): the console embeds no protocol template. The editor starts empty; a successful connection
 * loads the server presets automatically and, while the editor is untouched, applies DEFAULT_BASELINE14 with the visible
 * label "server preset DEFAULT_BASELINE14 (design example)". Edits, a chosen preset or a loaded run are never replaced.
 * SYNTHETIC mock API (tests/mockapi.ts, tests/fixtures.ts); nothing here is a measured value.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { getIn, rungsOf } from '../src/lib/policy';
import { AppProvider, AUTO_PRESET_LABEL, EDITED_POLICY_LABEL, NO_POLICY_LABEL, useApp, type AppModel } from '../src/state/context';
import { fixtureDesignExampleProtocol, RUN_ID } from './fixtures';
import { installMockApi, type MockCall } from './mockapi';

let restore: () => void = () => undefined;
afterEach(() => {
  cleanup();
  restore();
  window.history.replaceState(null, '', '#/server');
});
beforeEach(() => window.history.replaceState(null, '', '#/server'));

function mount(): { calls: MockCall[]; app: () => AppModel } {
  const api = installMockApi();
  restore = api.restore;
  let model: AppModel | null = null;
  function Probe(): JSX.Element {
    model = useApp();
    return <App />;
  }
  render(
    <AppProvider>
      <Probe />
    </AppProvider>,
  );
  return { calls: api.calls, app: () => model as unknown as AppModel };
}

async function connect(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByTestId('nav-server'));
  await user.type(screen.getByLabelText(/Bearer token/), 'TEST-TOKEN');
  await user.click(screen.getByTestId('connect-btn'));
  await screen.findByText(/status: ok/);
}

describe('no embedded template before connecting', () => {
  it('starts with an empty policy, says so on Settings and offers no client template', async () => {
    const user = userEvent.setup();
    const { app, calls } = mount();
    expect(app().policy).toEqual({});
    expect(app().presetLabel).toBe(NO_POLICY_LABEL);
    await user.click(screen.getByTestId('nav-settings'));
    expect(screen.getByTestId('settings-connect-banner')).toHaveTextContent('Not connected: the experiment policy comes from the server');
    expect(screen.getByTestId('policy-label')).toHaveTextContent(NO_POLICY_LABEL);
    expect(within(screen.getByTestId('rung-table')).queryAllByRole('row')).toHaveLength(1); // header row only
    const select = screen.getByLabelText('Preset') as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([`current: ${NO_POLICY_LABEL}`]);
    expect(screen.getByTestId('baselines-empty')).toBeInTheDocument();
    // nothing to pre-check yet: a neutral note instead of "rungs must be a non-empty list"
    expect(app().clientIssues).toEqual([]);
    expect(screen.getByTestId('issues-empty-policy')).toHaveTextContent('Nothing to pre-check: no policy is loaded yet');
    expect(calls).toHaveLength(0); // nothing is requested before the user connects
  });
});

describe('connect -> presets -> DEFAULT_BASELINE14', () => {
  it('loads the presets automatically and applies DEFAULT_BASELINE14 with a visible label before reporting ok', async () => {
    const user = userEvent.setup();
    const { app, calls } = mount();
    await connect(user);
    expect(calls.map((c) => c.path)).toEqual(['/api/v1/health', '/api/v1/protocols/presets']);
    expect(app().policy).toEqual(fixtureDesignExampleProtocol());
    expect(app().presetLabel).toBe(AUTO_PRESET_LABEL);
    expect(AUTO_PRESET_LABEL).toBe('server preset DEFAULT_BASELINE14 (design example)');
    expect(app().presetAutoApplied).toBe(true);
    // visible on the Server screen next to Validate ...
    expect(screen.getByTestId('policy-label')).toHaveTextContent(AUTO_PRESET_LABEL);
    expect(screen.getByTestId('policy-auto-applied')).toHaveTextContent('auto-applied on connect');
    // ... and on Settings, where the 3-rung example and the server's baseline14 order are shown
    await user.click(screen.getByTestId('nav-settings'));
    expect(screen.getByTestId('policy-label')).toHaveTextContent(AUTO_PRESET_LABEL);
    expect(screen.queryByTestId('settings-connect-banner')).toBeNull();
    expect(screen.getByTestId('rung-0-added')).toHaveTextContent('+10');
    expect(screen.getByTestId('rung-2-added')).toHaveTextContent('+60');
    expect(screen.getByText('Baseline14 (fixed order of the server preset DEFAULT_BASELINE14)')).toBeInTheDocument();
    expect(screen.getByTestId('baseline-etri-controlled-cnn-transformer')).toBeChecked();
    // Validate sends exactly the auto-applied preset protocol
    await user.click(screen.getByTestId('nav-server'));
    await user.click(screen.getByTestId('validate-btn'));
    await screen.findByTestId('validation-result');
    const body = calls.find((c) => c.path === '/api/v1/protocols/validate')?.body as { protocol: Record<string, unknown> };
    expect(rungsOf(body.protocol).map((r) => r.epoch_target_total)).toEqual([10, 30, 90]);
    expect(screen.getByTestId('run-btn')).toBeEnabled();
  });

  it('an edit made before connecting is kept: no auto-apply over it, and the label says it is a manual edit', async () => {
    const user = userEvent.setup();
    const { app } = mount();
    await user.click(screen.getByTestId('nav-settings'));
    await user.type(screen.getByTestId('path-output_root'), '/srv/out/mine');
    expect(app().presetLabel).toBe(EDITED_POLICY_LABEL);
    await connect(user);
    expect(getIn(app().policy, 'console.paths.output_root')).toBe('/srv/out/mine');
    expect(rungsOf(app().policy)).toEqual([]);
    expect(app().presetAutoApplied).toBe(false);
    expect(app().presets.data?.map((p) => p.id)).toEqual(['DEFAULT_BASELINE14', 'PILOT_SMOKE']); // still loaded for the picker
    expect(screen.getByTestId('policy-label')).toHaveTextContent(EDITED_POLICY_LABEL);
  });

  it('an edit after the auto-apply clears the "auto-applied" badge; a chosen preset gets its own label', async () => {
    const user = userEvent.setup();
    const { app } = mount();
    await connect(user);
    await user.click(screen.getByTestId('nav-settings'));
    const budget = screen.getByTestId('budget');
    await user.clear(budget);
    await user.type(budget, '5');
    expect(app().presetAutoApplied).toBe(false);
    expect(screen.queryByTestId('policy-auto-applied')).toBeNull();
    await user.selectOptions(screen.getByLabelText('Preset'), 'PILOT_SMOKE');
    expect(app().presetLabel).toBe('Pilot smoke (server preset PILOT_SMOKE)');
    expect(rungsOf(app().policy).map((r) => r.epoch_target_total)).toEqual([2, 4, 6]);
    expect(screen.getAllByText('PILOT · SMOKE · not research values').length).toBeGreaterThan(0);
  });

  it('a reconnect never replaces a loaded run (its resolved protocol stays in the editor)', async () => {
    const user = userEvent.setup();
    const { app, calls } = mount();
    await connect(user);
    await user.type(screen.getByTestId('run-id-input'), RUN_ID);
    await user.click(screen.getByTestId('load-run-btn'));
    await waitFor(() => expect(app().presetLabel).toBe(`run ${RUN_ID} resolved protocol`));
    const loaded = app().policy;
    expect(rungsOf(loaded)).toHaveLength(1);
    await user.click(screen.getByTestId('connect-btn'));
    await waitFor(() => expect(calls.filter((c) => c.path === '/api/v1/protocols/presets')).toHaveLength(2));
    await screen.findByText(/status: ok/);
    expect(app().policy).toEqual(loaded);
    expect(app().presetLabel).toBe(`run ${RUN_ID} resolved protocol`);
  });

  it('a presets failure does not fail the connection: the editor stays empty and Settings says why', async () => {
    const user = userEvent.setup();
    const { app } = mount();
    const inner = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/api/v1/protocols/presets')
        ? new Response(JSON.stringify({ code: 'INTERNAL', message: 'synthetic preset failure' }), { status: 500, headers: { 'content-type': 'application/json' } })
        : inner(input, init),
    ) as unknown as typeof fetch;
    await connect(user);
    expect(app().policy).toEqual({});
    expect(app().presets.status).toBe('error');
    await user.click(screen.getByTestId('nav-settings'));
    expect(screen.getByTestId('settings-empty-banner')).toHaveTextContent('the server presets could not be loaded');
  });

  it('Validate is disabled while the connection (health + presets) is in progress', async () => {
    const user = userEvent.setup();
    mount();
    const inner = globalThis.fetch;
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => { release = r; });
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/api/v1/protocols/presets')) await gate;
      return inner(input, init);
    }) as unknown as typeof fetch;
    await user.type(screen.getByLabelText(/Bearer token/), 'TEST-TOKEN');
    await user.click(screen.getByTestId('connect-btn'));
    await waitFor(() => expect(screen.getByTestId('connect-btn')).toHaveTextContent('Connecting…'));
    expect(screen.getByTestId('validate-btn')).toBeDisabled();
    release();
    await screen.findByText(/status: ok/);
    expect(screen.getByTestId('validate-btn')).toBeEnabled();
    expect(screen.getByTestId('policy-label')).toHaveTextContent(AUTO_PRESET_LABEL);
  });

  it('a server without DEFAULT_BASELINE14 leaves the editor empty (nothing is substituted)', async () => {
    const user = userEvent.setup();
    const { app } = mount();
    const inner = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/api/v1/protocols/presets')
        ? new Response(JSON.stringify({ presets: [] }), { status: 200, headers: { 'content-type': 'application/json' } })
        : inner(input, init),
    ) as unknown as typeof fetch;
    await connect(user);
    expect(app().policy).toEqual({});
    expect(app().presetLabel).toBe(NO_POLICY_LABEL);
    await user.click(screen.getByTestId('nav-settings'));
    expect(screen.getByTestId('settings-empty-banner')).toHaveTextContent('No policy is loaded. Choose a server preset below');
  });
});
