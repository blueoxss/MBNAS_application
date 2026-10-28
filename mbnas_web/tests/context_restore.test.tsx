/** DD-32 (WP-W1): the boot restore re-opens the same candidate after setScreen() dropped `cid`; block / model_tab must survive that, a normal open must not inherit them. */
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { AppProvider, useApp, type AppModel } from '../src/state/context';
import { cand } from './fixtures';

afterEach(() => { cleanup(); window.history.replaceState(null, '', '/#/settings'); });

function mountProbe(): () => AppModel {
  let current: AppModel | null = null;
  function Probe(): JSX.Element { current = useApp(); return <span />; }
  render(<AppProvider><Probe /></AppProvider>);
  return () => current as AppModel;
}
const query = (): URLSearchParams => new URL(window.location.href).searchParams;

describe('openCandidate({ restore: true })', () => {
  it('keeps ?block= and ?model_tab= when the hash has no cid (reload restore), and sets cid', () => {
    const app = mountProbe();
    const a = cand({ candidate_id: 'cand-a' });
    window.history.replaceState(null, '', '/?model_tab=structure&block=block-0006#/server?run=R');
    act(() => app().openCandidate(a, { restore: true }));
    expect(query().get('model_tab')).toBe('structure');
    expect(query().get('block')).toBe('block-0006');
    expect(window.location.hash).toContain('cid=cand-a');
  });

  it('without the option a candidate opened from a hash without cid still starts clean (I4-#12 unchanged)', () => {
    const app = mountProbe();
    window.history.replaceState(null, '', '/?model_tab=structure&block=block-0006#/server?run=R');
    act(() => app().openCandidate(cand({ candidate_id: 'cand-a' })));
    expect(query().get('model_tab')).toBeNull();
    expect(query().get('block')).toBeNull();
  });

  it('closeCandidate still removes them after a restore', () => {
    const app = mountProbe();
    window.history.replaceState(null, '', '/?model_tab=structure&block=block-0006#/server?run=R');
    act(() => app().openCandidate(cand({ candidate_id: 'cand-a' }), { restore: true }));
    act(() => app().closeCandidate());
    expect(window.location.search).toBe('');
    expect(window.location.hash).not.toContain('cid=');
  });
});
