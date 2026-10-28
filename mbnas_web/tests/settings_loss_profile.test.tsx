/** REQ-7.02: the training loss profile is displayed separately from the fitness weights (and is read-only there). */
import { act, cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Settings } from '../src/screens/Settings';
import { AppProvider, useApp, type AppModel } from '../src/state/context';
import { fixturePresets } from './fixtures';

afterEach(() => { cleanup(); window.history.replaceState(null, '', '/#/settings'); });

describe('loss profile vs fitness weights', () => {
  it('shows loss_profile and change_loss_weights_at_promotion in their own section, apart from the rung fitness weights', () => {
    let model: AppModel | null = null;
    function Probe(): JSX.Element { model = useApp(); return <Settings />; }
    render(<AppProvider><Probe /></AppProvider>);
    // the editor starts empty (DD-A8, no embedded template): load the synthetic DEFAULT_BASELINE14 server preset first
    act(() => (model as unknown as AppModel).applyServerPreset(fixturePresets()[0]));
    const heading = screen.getByText(/Loss profile \(separate from fitness weights; read-only mapping\)/);
    const section = heading.parentElement as HTMLElement;
    expect(within(section).getByText('loss_profile')).toBeInTheDocument();
    expect(within(section).getByText('model_version_native_pinned_and_exported')).toBeInTheDocument();
    expect(within(section).getByText('change loss weights at promotion')).toBeInTheDocument();
    expect(within(section).getByText(/Loss weights shape gradients; fitness weights score candidates\. They are never mixed\./)).toBeInTheDocument();
    // the editable fitness weights are other controls, not inside the loss section
    const fitnessInput = screen.getByTestId('rung-0-w-ap');
    expect(section.contains(fitnessInput)).toBe(false);
  });
});
