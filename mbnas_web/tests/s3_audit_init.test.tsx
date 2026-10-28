import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ApiClient } from '../src/api/client';
import { CandidateView } from '../src/components/CandidateView';
import { initializationHelp } from '../src/screens/Settings';
import auditFixture from './fixtures_audit_etri_mixed.json';
import { cand } from './fixtures';

afterEach(cleanup);

function clientWith(audit: Record<string, unknown> | undefined): ApiClient {
  return {
    candidateDetail: async () => ({ candidate_id: 'c', blocks: [], lineage: {}, evaluations: [], candidate_status: 'EVALUATED', audit }),
  } as unknown as ApiClient;
}

async function renderAudit(audit: Record<string, unknown> | undefined): Promise<HTMLElement> {
  const c = cand();
  render(<CandidateView c={c} run={null} runId="r" all={[c]} client={clientWith(audit)} />);
  return audit ? await screen.findByTestId('candidate-audit') : await screen.findByTestId('candidate-view');
}

describe('S3-F3 initialization help from registry init_modes', () => {
  const modes = [
    ['yolo11s', ['scratch', 'pretrained_native']],
    ['rf-detr-small', ['scratch', 'pretrained_native']],
    ['etri-hybrid', ['scratch']],
  ] as const;
  it('does not claim RF-DETR is native-pretrained only; states random-init scratch and the separate native group', () => {
    const t = initializationHelp(modes);
    expect(t).not.toMatch(/native pretrained only/i);
    expect(t).toMatch(/rf-detr-small: scratch is a true random init/);
    expect(t).toMatch(/native_pretrained comparison group/);
    expect(t).toMatch(/scratch only: etri-hybrid/);
  });
  it('without registry data it stays generic', () => {
    expect(initializationHelp([])).toMatch(/Connect to the server/);
    expect(initializationHelp([])).not.toMatch(/only\)/);
  });
});

describe('S3-F4 candidate architecture audit', () => {
  it('renders label, backbone estimate vs built, neck, detect conv count, variant, fallback with text badges', async () => {
    const el = await renderAudit(auditFixture as Record<string, unknown>);
    const t = el.textContent ?? '';
    expect(t).toContain('hybrid_backbone with CNN detection head');
    expect(within(el).getByTestId('audit-backbone')).toHaveTextContent(/estimate hybrid_backbone.*built hybrid_backbone/);
    expect(within(el).getByTestId('audit-backbone-match')).toHaveTextContent('MATCH');
    expect(within(el).getByTestId('audit-neck')).toHaveTextContent('hybrid_neck');
    expect(within(el).getByTestId('audit-detect-convs')).toHaveTextContent('48');
    expect(within(el).getByTestId('audit-variant')).toHaveTextContent('etri_mixed');
    expect(within(el).getByTestId('audit-fallback')).toHaveTextContent('none');
    expect(t).not.toMatch(/full transformer/i);
  });
  it('flags a backbone mismatch, shows fallback_reason, and prints full Transformer only when true', async () => {
    const a = JSON.parse(JSON.stringify(auditFixture)) as Record<string, any>;
    a.config_estimate.backbone_family = 'cnn_backbone';
    a.fallback_reason = 'native_fallback: mixed pool exhausted';
    let el = await renderAudit(a);
    expect(within(el).getByTestId('audit-backbone-match')).toHaveTextContent('MISMATCH');
    expect(within(el).getByTestId('audit-fallback')).toHaveTextContent('native_fallback: mixed pool exhausted');
    expect(el.textContent).not.toMatch(/full transformer/i);
    cleanup();
    a.built.full_transformer = true;
    el = await renderAudit(a);
    expect(el.textContent).toMatch(/full Transformer/);
  });
  it('shows an explicit absent state when the server returns no audit', async () => {
    const el = await renderAudit(undefined);
    expect(await screen.findByTestId('candidate-audit-missing')).toBeInTheDocument();
    expect(el.textContent).not.toMatch(/full transformer/i);
  });
});
