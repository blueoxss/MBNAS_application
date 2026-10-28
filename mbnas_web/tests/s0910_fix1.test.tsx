/** Session 2 fix 1 (S8-F5 web, S9-F1, S9-F2, S10-F2, S10-F3, S10-F4, gate op "in"). */
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ArtifactPanel, CandidateView, lineageOperation } from '../src/components/CandidateView';
import { GateChecks } from '../src/components/CandidateView';
import { buildPlotPoints, cohortKey, cohortLabel, facetOptions, selectCohort } from '../src/lib/cohort';
import { generationGuides, skippedForCohort, skippedHelpText } from '../src/lib/lines';
import { artifactSet, BLOCKS, cand, gen, resetCounter } from './fixtures';

beforeEach(resetCounter);
afterEach(cleanup);

describe('S8-F5 postprocess_runtime', () => {
  it('is a cohort field: different implementations never share a cohort or label', () => {
    const a = cand({ postprocess_runtime: 'torchvision_cuda_nms' });
    const b = cand({ postprocess_runtime: 'python_loop' });
    expect(cohortKey(a)).not.toBe(cohortKey(b));
    expect(cohortLabel(a)).not.toBe(cohortLabel(b));
    expect(cohortLabel(b)).toContain('python_loop');
    expect(cohortKey(cand())).toBe(cohortKey(cand()));
    const sel = selectCohort(buildPlotPoints([a, b, cand({ postprocess_runtime: 'python_loop' })], 'ap').points, null);
    expect(sel.groups).toHaveLength(2);
    expect(facetOptions([a, b], 'postprocess')).toEqual(['python_loop', 'torchvision_cuda_nms']);
  });
  it('CandidateView shows it next to latency source / scope', () => {
    render(<CandidateView c={cand({ postprocess_runtime: 'python_loop' })} run={null} runId="r" all={[]} client={null} />);
    const row = screen.getByText('Latency source / scope').closest('tr, div') as HTMLElement;
    expect(row.textContent).toContain('measured / forward_and_postprocess');
    expect(screen.getByTestId('postprocess-runtime').textContent).toBe('python_loop');
  });
});

describe('gate op "in"', () => {
  it('renders a list threshold and pass/fail', () => {
    render(<GateChecks gate={{ eligible: false, reason_code: 'LATENCY_SOURCE', checks: [{ name: 'latency_source', passed: false, value: 'unverified_proxy', threshold: ['measured', 'calibrated_proxy'], op: 'in' }, { name: 'ap', passed: true, value: 0.3, threshold: 0.1, op: '>=' }] }} />);
    const rows = screen.getAllByTestId('gate-check');
    expect(rows[0].textContent).toContain('latency_source');
    expect(rows[0].textContent).toContain('unverified_proxy in [measured, calibrated_proxy]');
    expect(rows[0].textContent).toContain('FAIL');
    expect(rows[1].textContent).toContain('0.3 >= 0.1');
    expect(rows[1].textContent).toContain('PASS');
  });
});

describe('S9-F1 skipped generation help', () => {
  it('counts only generations of the selected cohort with the real reason', () => {
    const rows = [
      cand({ id: 'a', generation: 0, rung: 'L0', latency_ms: 30 }),
      cand({ id: 'b', generation: 0, rung: 'L0', latency_ms: 40 }),
      cand({ id: 'c', generation: 1, rung: 'L1', latency_ms: 30, epoch_target_total: 20 }),
    ];
    const all = buildPlotPoints(rows, 'ap').points;
    const sel = selectCohort(all, all[0].cohort);
    const gens = [gen({ generation: 0, rung: 'L0', planned_count: 2, evaluated_count: 2 }), gen({ generation: 1, rung: 'L1', planned_count: 1, evaluated_count: 1 })];
    const g = generationGuides(sel.inCohort, gens, all);
    expect(g.skipped).toHaveLength(1); // the L1 generation is a TOO_FEW_POINTS of the other cohort
    expect(skippedForCohort(g.skipped, sel.inCohort)).toHaveLength(0);
    expect(skippedHelpText(skippedForCohort(g.skipped, sel.inCohort))).toBe('');
    const sel2 = selectCohort(all, all[2].cohort);
    const g2 = generationGuides(sel2.inCohort, gens, all);
    const sk = skippedForCohort(g2.skipped, sel2.inCohort);
    expect(sk.map((s) => s.reason)).toEqual(['TOO_FEW_POINTS']);
    expect(skippedHelpText(sk)).toBe('1 generation(s) without a dotted line: 1 too few points (needs 2).');
  });
  it('states open and mixed-cohort reasons separately', () => {
    const t = skippedHelpText([{ key: 'a', reason: 'OPEN_GENERATION' }, { key: 'b', reason: 'MIXED_COHORT' }, { key: 'c', reason: 'MIXED_COHORT' }]);
    expect(t).toContain('1 open (not yet complete)');
    expect(t).toContain('2 spanning several cohorts');
    expect(t).not.toContain('too few');
  });
});

describe('S9-F2 cohort labels', () => {
  it('differ for cohorts that differ only by a hash or comparison group, and show short hashes', () => {
    const base = cand();
    for (const v of [{ evaluation_hash: 'sha256:' + '1'.repeat(64) }, { objective_hash: 'sha256:' + '2'.repeat(64) }, { split_hash: 'sha256:' + '3'.repeat(64) }, { protocol_hash: 'sha256:' + '4'.repeat(64) }, { comparison_group: 'sha256:' + '5'.repeat(64) }]) {
      expect(cohortLabel(cand(v))).not.toBe(cohortLabel(base));
    }
    const l = cohortLabel(cand({ evaluation_hash: 'sha256:' + '1'.repeat(64) }));
    expect(l).toContain('eval 11111111');
    expect(l).toContain('group ');
    expect(l).toContain('split ');
    expect(l).toContain('protocol ');
    expect(l).toContain('objective ');
  });
});

describe('S10-F2 diagnostic panel hashes', () => {
  it('shows the eval checkpoint from matching, the training state sha and the split name with the hash', () => {
    const arts = artifactSet('e1').map((a) => ({ ...a, split_hash: 'sha256:splithash', matching: { counts: { tp: 1, fp: 0, fn: 0, gt: 1 }, checkpoint_sha256: 'EVALCKPT', split: 'search_val' } }));
    const c = cand({ id: 'e1', checkpoint_sha256: 'TRAINSTATE', artifacts: arts });
    render(<ArtifactPanel c={c} runId="r" client={null} />);
    const kv = screen.getByTestId('artifact-panel');
    expect(within(kv).getByText('eval checkpoint sha256').parentElement?.textContent).toContain('EVALCKPT');
    expect(within(kv).getByText('training state sha256').parentElement?.textContent).toContain('TRAINSTATE');
    expect(within(kv).queryByText('checkpoint sha256')).toBeNull();
    const split = within(kv).getByText('split').parentElement?.textContent ?? '';
    expect(split).toContain('search_val');
    expect(split).toContain('sha256:splithash');
  });
});

describe('S10-F3 one operation label', () => {
  it('0-parent random for any random proposal', () => {
    expect(lineageOperation({ parents: [], arm: 'GA' }).op).toBe('random');
    expect(lineageOperation({ parents: [], arm: 'GA' }, 'random').text).toContain('random');
    expect(lineageOperation({ parents: [], arm: 'IndependentRandom' }).op).toBe('random');
    expect(lineageOperation({ parents: [], arm: 'Baseline' }).op).toBe('baseline');
  });
  it('mutation for children with mutated blocks even without the server operation (Lineage table)', () => {
    const mutated = { parents: ['p'], arm: 'GA' as const, blocks: [...BLOCKS, { ...BLOCKS[0], source_kind: 'mutation' }] };
    expect(lineageOperation(mutated).op).toBe('mutation');
    expect(lineageOperation({ parents: ['p'], arm: 'GA', blocks: BLOCKS.filter((b) => b.source_kind !== 'mutation') }).op).toBe('clone');
  });
});

describe('S10-F4 retention badge', () => {
  it('shows pruned-by-retention next to checkpoint_path', () => {
    render(<CandidateView c={cand({ checkpoint_state: 'PRUNED' })} run={null} runId="r" all={[]} client={null} />);
    expect(screen.getByTestId('checkpoint-state').textContent).toMatch(/pruned by retention/i);
  });
  it('MISSING and PRESENT are distinguished, absent shows nothing', () => {
    const { unmount } = render(<CandidateView c={cand({ checkpoint_state: 'MISSING' })} run={null} runId="r" all={[]} client={null} />);
    expect(screen.getByTestId('checkpoint-state').textContent).toMatch(/missing/i);
    unmount();
    render(<CandidateView c={cand()} run={null} runId="r" all={[]} client={null} />);
    expect(screen.queryByTestId('checkpoint-state')).toBeNull();
  });
});
