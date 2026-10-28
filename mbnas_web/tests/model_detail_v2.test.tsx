/**
 * WP-W1: common model detail for mbnas-structure/2 (header, native rendering, unobserved list, repeats columns,
 * download hashes, UNVERIFIED notice vs FAIL, edge diff).
 *
 * YAML documents are REAL server output (tests/structure_v2_server_fixture.json, see structure_parity.test.ts). The native
 * (RF-DETR-like) document is SYNTHETIC: WP-S2 had not produced a native report when this was written, so it follows
 * CONTRACTS_v2 section 1 by hand (module paths and the observed/native_fixed split are invented for the test).
 */
import { webcrypto, createHash } from 'node:crypto';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '../src/api/client';
import { CandidateView } from '../src/components/CandidateView';
import { ModelComparison } from '../src/components/ModelComparison';
import { ModelStructure, StructureSummary } from '../src/components/ModelStructure';
import { cand } from './fixtures';
import catalogFixture from './block_catalog_server_fixture.json';
import fixture from './structure_v2_server_fixture.json';

type Doc = Record<string, unknown> & { blocks: Record<string, unknown>[] };
const docs = fixture.documents as unknown as Record<string, Doc>;

beforeAll(() => {
  // jsdom ships no crypto.subtle; Node's WebCrypto is the same API the browser provides
  if (!globalThis.crypto?.subtle) Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/'); });

const withApiFields = (doc: Doc, evidence_source: string): Doc => ({ ...doc, evidence_source });
const NATIVE: Doc = {
  schema: 'mbnas-structure/2', backend: 'rfdetr', representation: 'native_modules', status: 'PASS', evidence_source: 'saved_structure_check',
  yaml_hash: null, flops: null, flops_status: 'NOT_MEASURED', flops_source: null, lineage: null, lineage_status: 'NOT_APPLICABLE',
  observation: { method: 'forward_hook', device: 'cpu', imgsz: 64, fx_traceable: false, dinov2_weights: 'skipped', checked_at: '2026-10-22T00:00:00Z' },
  checks: [
    { name: 'yaml_rows', status: 'NOT_CHECKED', evidence_level: null }, { name: 'cpu_build', status: 'PASS', evidence_level: 'built' },
    { name: 'reference_forward', status: 'PASS', evidence_level: 'forward' }, { name: 'merge_conditions', status: 'NOT_CHECKED', evidence_level: null },
  ],
  blocks: [
    { id: 'block-0000', block_id: 'block-0000', stable_id: 'rf:backbone.encoder.layer.0@aaaabbbbcccc', gene_index: null, yaml_path: null, from: null, args: null, module: null, module_class: 'Dinov2Layer', wrapper: null, wrapped_module: null,
      native_path: 'backbone.encoder.layer.0', section: 'backbone', section_basis: 'native:backbone', connection_source: 'native_fixed', repeats: null,
      repeats_detail: { declared: 12, scaled: null, observed: 12 }, declared: { repeats: 12, channels: 384 }, effective: { repeats: 12, repeats_scaled: null, params: 1774848, inputs: [{ source: null, c: 3, h: 64, w: 64 }] },
      internal_layers: [{ path: 'attention.qkv', module: 'Linear', parameters: [['weight', [1152, 384]]], attributes: {}, shared: false }, { path: 'act', module: 'GELU', parameters: [], attributes: {}, shared: true }], internal_mapping_status: 'RECORDED' },
    { id: 'block-0001', block_id: 'block-0001', stable_id: 'rf:transformer.decoder.layers.1@aaaabbbbcccc', gene_index: null, yaml_path: null, from: null, args: null, module: null, module_class: 'DecoderLayer', wrapper: null, wrapped_module: null,
      native_path: 'transformer.decoder.layers.1', section: 'transformer', section_basis: 'native:transformer', connection_source: 'observed', repeats: null,
      repeats_detail: { declared: 3, scaled: null, observed: null }, declared: { repeats: 3, channels: 256 }, effective: { repeats: null, repeats_scaled: null, params: null }, internal_layers: [], internal_mapping_status: 'UNVERIFIED' },
    { id: 'block-0002', block_id: 'block-0002', stable_id: 'rf:class_embed@aaaabbbbcccc', gene_index: null, yaml_path: null, from: null, args: null, module: null, module_class: 'Linear', wrapper: null, wrapped_module: null,
      native_path: 'class_embed', section: 'head', section_basis: 'native:class_embed', connection_source: 'observed', repeats: null,
      repeats_detail: { declared: null, scaled: null, observed: null }, effective: {}, internal_layers: [], internal_mapping_status: 'UNVERIFIED' },
  ],
  edges: [
    { source: null, target: 'block-0000', from_ref: null, connection_source: 'native_fixed' },
    { source: 'block-0000', target: 'block-0001', from_ref: null, connection_source: 'observed' },
  ],
  edges_unobserved: [{ module_path: 'transformer.decoder.layers.1.cross_attn', reason: 'inputs not seen by forward hooks (inside the decoder)' }],
  diagnostics: [],
};

describe('summary header (CONTRACTS_v2 section 3)', () => {
  it('shows backend, representation, evidence source, the lowest evidence level and the structure badge', () => {
    render(<StructureSummary report={withApiFields(docs.base, 'saved_structure_check')} />);
    expect(screen.getByTestId('summary-backend')).toHaveTextContent('backend: ultralytics');
    expect(screen.getByTestId('summary-representation')).toHaveTextContent('yaml_genes');
    expect(screen.getByTestId('summary-evidence-source')).toHaveTextContent('saved_structure_check');
    expect(screen.getByTestId('summary-evidence-level')).toHaveTextContent('최저 증거 수준: declared'); // yaml_rows is a declared-level check
    expect(screen.getByTestId('summary-structure-status')).toHaveTextContent('구조: PASS');
    expect(screen.getByTestId('structure-summary')).toHaveTextContent('imgsz 64');
  });

  it('a declaration-only document says there is no observation and no evidence level', () => {
    const declared = { ...docs.declared_only, status: 'UNVERIFIED', evidence_source: 'declared_configuration', checks: [{ name: 'cpu_build', status: 'NOT_CHECKED', evidence_level: null }] };
    render(<StructureSummary report={declared} />);
    expect(screen.getByTestId('summary-evidence-level')).toHaveTextContent('기록 없음');
    expect(screen.getByTestId('structure-summary')).toHaveTextContent('관측 기록 없음');
    expect(screen.getByTestId('summary-structure-status')).toHaveTextContent('UNVERIFIED');
  });

  it('a v1 document keeps working: v2-only keys are optional and reported as not recorded', () => {
    render(<StructureSummary report={{ schema: 'mbnas-structure/1', status: 'PASS', checks: [{ name: 'cpu_build', status: 'PASS' }] }} />);
    expect(screen.getByTestId('summary-backend')).toHaveTextContent('v1 문서(미기록)');
    expect(screen.getByTestId('summary-representation')).toHaveTextContent('v1 문서(미기록)');
    expect(screen.getByTestId('summary-evidence-level')).toHaveTextContent('기록 없음');
  });

  it('CandidateView header: generation seed and order, evaluation badge, structure badge and DD-5 badge', async () => {
    const c = cand({ candidate_id: 'gen-1', name: 'gen-1-model', status: 'NOT_EVALUATED' as never, search_seed: 7, generation_order: 3, origin: 'random_generation' } as never);
    const detail = {
      candidate_id: 'gen-1', name: 'gen-1-model', blocks: [], evaluations: [], candidate_status: 'BUILD_VALID', evaluation_status: 'NOT_EVALUATED', structure_status: 'PASS',
      audit: { generation: { index: 3, attempt: 4, seed: 7, requested_method: ['GA'], effective_method: 'RandomGeneration', duplicate_scope: 'per_seed', execution: 'CPU', worker_id: 'w-1' } },
      structure: withApiFields(docs.base, 'saved_structure_check'),
    };
    render(<CandidateView c={c} run={null} runId="r" all={[c]} client={{ candidateDetail: async () => detail } as unknown as ApiClient} />);
    expect(await screen.findByTestId('summary-backend')).toHaveTextContent('ultralytics');
    expect(screen.getByTestId('summary-generation')).toHaveTextContent('seed 7 / generation_order 3 (시도 4)');
    expect(screen.getByTestId('structure-summary')).toHaveTextContent('RandomGeneration / CPU · duplicate_scope per_seed');
    expect(screen.getByTestId('summary-evaluation-status')).toHaveTextContent('평가: NOT_EVALUATED');
    expect(screen.getByTestId('summary-evidence-source')).toHaveTextContent('saved_structure_check');
  });

  it('CandidateView shows 학습 완료 · 미평가 for an explicit validation-OFF training row (DD-5) and the raw status otherwise', async () => {
    const trained = cand({ candidate_id: 't-1', name: 't-1-model', status: 'SEARCH_COMPLETE', performance_status: 'NOT_EVALUATED', training_mode: 'scratch' } as never);
    const client = { candidateDetail: async () => ({ candidate_id: 't-1', blocks: [], evaluations: [], candidate_status: 'SEARCH_COMPLETE' }) } as unknown as ApiClient;
    const { unmount } = render(<CandidateView c={trained} run={null} runId="r" all={[trained]} client={client} />);
    await screen.findByTestId('summary-backend');
    expect(screen.getByTestId('trained-not-evaluated')).toHaveTextContent('학습 완료 · 미평가');
    unmount();
    const plain = cand({ candidate_id: 'p-1', name: 'p-1-model', status: 'SEARCH_COMPLETE' });
    render(<CandidateView c={plain} run={null} runId="r" all={[plain]} client={client} />);
    await screen.findByTestId('summary-backend');
    expect(screen.queryByTestId('trained-not-evaluated')).toBeNull();
  });
});

describe('native backend rendering', () => {
  it('shows the native path and never a YAML-position label', () => {
    render(<ModelStructure blocks={NATIVE.blocks} modelKey="native" report={NATIVE} />);
    const view = screen.getByTestId('model-structure');
    expect(view).toHaveTextContent('backbone.encoder.layer.0');
    expect(view).toHaveTextContent('transformer.decoder.layers.1');
    expect(view.textContent).not.toMatch(/backbone\[\d\]|head\[\d\]|neck\[\d\]|row\[\d\]|from \d|YAML 위치/);
    expect(screen.getByTestId('gene-0')).toHaveTextContent('Dinov2Layer');
    expect(screen.getByTestId('gene-0')).toHaveTextContent('backbone.encoder.layer.0');
    fireEvent.click(screen.getByTestId('gene-0'));
    const block = screen.getByTestId('selected-block');
    expect(block).toHaveTextContent('stable_id');
    expect(block).toHaveTextContent('rf:backbone.encoder.layer.0@aaaabbbbcccc');
    expect(block).toHaveTextContent('native path');
    expect(block).toHaveTextContent('section_basis');
    expect(block).toHaveTextContent('native:backbone');
    expect(block).toHaveTextContent('module_class (관측)');
    expect(block).not.toHaveTextContent('YAML 위치');
  });

  it('draws only recorded edges, styled by connection_source, with a legend of all three kinds', () => {
    render(<ModelStructure blocks={NATIVE.blocks} modelKey="native" report={NATIVE} />);
    const legend = screen.getByTestId('connection-legend');
    expect(legend).toHaveTextContent('yaml_from · 실선');
    expect(legend).toHaveTextContent('native_fixed · 점선');
    expect(legend).toHaveTextContent('observed · 2중선');
    const edges = [...screen.getByTestId('structure-graph').querySelectorAll('g[data-source][data-target]')];
    expect(edges.map((g) => [g.getAttribute('data-source'), g.getAttribute('data-target'), g.getAttribute('data-connection')])).toEqual([
      ['INPUT', 'block-0000', 'native_fixed'],
      ['block-0000', 'block-0001', 'observed'],
    ]);
    // dashed for native_fixed, two strokes for observed, one solid stroke would be yaml_from
    expect(edges[0].querySelectorAll('path')).toHaveLength(1);
    expect(edges[0].querySelector('path')?.getAttribute('stroke-dasharray')).toBe('6 4');
    expect(edges[1].querySelectorAll('path')).toHaveLength(2);
    // block-0002 has no recorded edge: nothing is drawn into it (no chain by order)
    expect(edges.some((g) => g.getAttribute('data-target') === 'block-0002')).toBe(false);
  });

  it('lists the modules whose connections were not observed under "관측되지 않은 구간"', () => {
    render(<ModelStructure blocks={NATIVE.blocks} modelKey="native" report={NATIVE} />);
    const section = screen.getByTestId('edges-unobserved');
    expect(within(section).getByRole('heading', { name: '관측되지 않은 구간' })).toBeInTheDocument();
    const items = within(section).getAllByTestId('edge-unobserved-item');
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent('transformer.decoder.layers.1.cross_attn');
    expect(items[0]).toHaveTextContent('inside the decoder');
    // the list sits below the graph
    const graph = screen.getByTestId('structure-graph');
    expect(graph.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('with no edge and no unobserved entry it says so instead of inventing a chain', () => {
    render(<ModelStructure blocks={NATIVE.blocks} modelKey="native-empty" report={{ ...NATIVE, edges: [], edges_unobserved: [] }} />);
    expect(screen.getByTestId('no-recorded-edges')).toBeInTheDocument();
    expect(screen.getByTestId('edges-unobserved')).toHaveTextContent('기록된 관측되지 않은 구간이 없습니다');
    expect(screen.getByTestId('structure-graph').querySelectorAll('g[data-source]')).toHaveLength(0);
  });

  it('YAML documents have no unobserved section when the list is empty', () => {
    render(<ModelStructure blocks={docs.base.blocks} modelKey="yaml" report={docs.base} />);
    expect(screen.queryByTestId('edges-unobserved')).toBeNull();
    for (const kind of ['yaml_from', 'native_fixed', 'observed']) expect(screen.getByTestId(`legend-${kind}`)).toBeInTheDocument();
    const edges = [...screen.getByTestId('structure-graph').querySelectorAll('g[data-source][data-target]')];
    expect(edges).toHaveLength((docs.base.edges as unknown[]).length);
    expect(new Set(edges.map((g) => g.getAttribute('data-connection')))).toEqual(new Set(['yaml_from']));
  });
});

describe('block detail', () => {
  it('repeats are three separate columns: declared, scale (formula), observed', () => {
    render(<ModelStructure blocks={docs.base.blocks} modelKey="base" report={docs.base} />);
    fireEvent.click(screen.getByTestId('gene-1'));
    expect(screen.getByTestId('repeats-declared')).toHaveTextContent('1');
    expect(screen.getByTestId('repeats-scaled')).toHaveTextContent('1');
    expect(screen.getByTestId('repeats-observed')).toHaveTextContent('1');
    const heads = within(screen.getByTestId('repeats-columns')).getAllByRole('columnheader').map((h) => h.textContent);
    expect(heads).toEqual(['선언 (D)', 'scale (R, depth 공식 파생)', '관측 (O)']);
    expect(screen.getByTestId('selected-block')).not.toHaveTextContent('스케일 적용 반복 수');
  });

  it('an unobserved repeat count is 미관측, never replaced by the formula value', () => {
    const unverified = { ...docs.declared_only, status: 'UNVERIFIED', evidence_source: 'declared_configuration' };
    render(<ModelStructure blocks={unverified.blocks} modelKey="declared" report={unverified} />);
    fireEvent.click(screen.getByTestId('gene-1'));
    expect(screen.getByTestId('repeats-scaled')).toHaveTextContent('1'); // the formula is shown in its own column
    expect(screen.getByTestId('repeats-observed')).toHaveTextContent('미관측');
    expect(screen.getByTestId('structure-declared-only-notice')).toBeInTheDocument();
  });

  it('a v1 document shows its depth-scaled value under scale and nothing under observed', () => {
    const v1 = { schema: 'mbnas-structure/1', status: 'PASS', blocks: [{ id: 'block-0000', position: 0, module: 'C3k2', section: 'backbone', yaml_path: 'backbone[0]', from: -1, repeats: 2, args: [64], effective: { repeats: 1, c_out: 64 } }], edges: [] };
    render(<ModelStructure blocks={v1.blocks} modelKey="v1" report={v1} />);
    expect(screen.getByTestId('repeats-declared')).toHaveTextContent('2');
    expect(screen.getByTestId('repeats-scaled')).toHaveTextContent('1');
    expect(screen.getByTestId('repeats-observed')).toHaveTextContent('미관측');
    expect(screen.getByTestId('selected-block')).toHaveTextContent('YAML 위치');
  });

  it('shows the inputs table per source, Detect head fields, and the shared mark of internal layers', () => {
    render(<ModelStructure blocks={docs.base.blocks} modelKey="base" report={docs.base} />);
    fireEvent.click(screen.getByTestId('gene-4')); // the Concat row: two sources
    const inputs = within(screen.getByTestId('block-inputs'));
    expect(inputs.getAllByRole('row')).toHaveLength(3); // header + two sources
    expect(inputs.getAllByRole('row')[1]).toHaveTextContent('row 0');
    expect(inputs.getAllByRole('row')[2]).toHaveTextContent('row 3');
    fireEvent.click(screen.getByTestId('gene-5')); // Detect
    expect(screen.getByTestId('head-strides')).toHaveTextContent('[2,4]');
    expect(screen.getByTestId('head-nl')).toHaveTextContent('2');
    expect(screen.getByTestId('head-end2end')).toHaveTextContent('false');
    fireEvent.click(screen.getByTestId('gene-1'));
    const layers = screen.getByTestId('internal-layers');
    expect(layers).toHaveTextContent('공유 1');
    const rows = within(layers).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.getAttribute('data-shared'))).toEqual(['false', 'false', 'true']);
    expect(within(rows[2]).getByText('공유')).toBeInTheDocument();
  });

  it('shows stable_id, native_path, module_class, section_basis and the per-check evidence levels', () => {
    render(<ModelStructure blocks={docs.base.blocks} modelKey="base" report={docs.base} />);
    fireEvent.click(screen.getByTestId('gene-3'));
    const block = screen.getByTestId('selected-block');
    expect(block).toHaveTextContent(/ul:head\[0\]@dc03a61745c4/);
    expect(block).toHaveTextContent('model.3');
    expect(block).toHaveTextContent('derived:non-detection head row -> neck');
    expect(block).toHaveTextContent('yaml_from');
    expect(screen.getByTestId('check-level-merge_conditions')).toHaveTextContent('forward');
    expect(screen.getByTestId('check-level-yaml_rows')).toHaveTextContent('declared');
    expect(screen.getByTestId('check-level-cpu_build')).toHaveTextContent('built');
    expect(screen.getByTestId('basis-neck')).toHaveTextContent('derived:non-detection head row -> neck');
  });
});

describe('UNVERIFIED (no structure observation / unsupported) is not a FAIL', () => {
  const unsupported = (code: string, status = 'UNVERIFIED'): Doc => ({ schema: 'mbnas-structure/2', backend: 'rfdetr', representation: 'native_modules', status, blocks: [], edges: [], diagnostics: [{ code, position: null, path: null, cause: 'x', suggestion: null }], checks: [] } as unknown as Doc);

  it.each(['GENE_REPRESENTATION_NOT_APPLICABLE', 'STRUCTURE_RUNTIME_UNAVAILABLE'])('%s with UNVERIFIED shows the dedicated notice and no failure banner', (code) => {
    const doc = unsupported(code);
    render(<ModelStructure blocks={doc.blocks} modelKey="u" report={doc} />);
    const notice = screen.getByTestId('structure-unobserved-notice');
    expect(notice).toHaveTextContent('구조 관측 없음 / 지원되지 않음');
    expect(notice).toHaveTextContent('(FAIL)이 아니며');
    expect(notice).toHaveTextContent(code);
    expect(screen.queryByTestId('structure-fail-notice')).toBeNull();
  });

  it('UNVERIFIED with another code (STRUCTURE_CHECK_NOT_RECORDED) shows neither notice', () => {
    const doc = unsupported('STRUCTURE_CHECK_NOT_RECORDED');
    render(<ModelStructure blocks={doc.blocks} modelKey="u" report={doc} />);
    expect(screen.queryByTestId('structure-unobserved-notice')).toBeNull();
    expect(screen.queryByTestId('structure-fail-notice')).toBeNull();
  });

  it('a real FAIL document shows the failure notice and its diagnostics, not the unsupported notice', () => {
    render(<ModelStructure blocks={docs.changed.blocks} modelKey="fail" report={docs.changed} />);
    expect(screen.getByTestId('structure-fail-notice')).toHaveTextContent('구조 검사 실패 (FAIL)');
    expect(screen.queryByTestId('structure-unobserved-notice')).toBeNull();
    expect(screen.getByTestId('structure-status')).toHaveTextContent('FAIL');
    expect(screen.getByText(/MERGE_INPUT_MISMATCH/)).toBeInTheDocument();
  });

  it('even with the unsupported code, status FAIL is never shown as "no observation"', () => {
    const doc = unsupported('GENE_REPRESENTATION_NOT_APPLICABLE', 'FAIL');
    render(<ModelStructure blocks={doc.blocks} modelKey="u" report={doc} />);
    expect(screen.queryByTestId('structure-unobserved-notice')).toBeNull();
    expect(screen.getByTestId('structure-fail-notice')).toBeInTheDocument();
  });

  it('the notice also shows when only status and diagnostics are passed (candidate view props)', () => {
    render(<ModelStructure blocks={[]} modelKey="u" status="UNVERIFIED" diagnostics={[{ code: 'STRUCTURE_RUNTIME_UNAVAILABLE' }]} />);
    expect(screen.getByTestId('structure-unobserved-notice')).toBeInTheDocument();
    expect(screen.getByTestId('model-structure-empty')).toBeInTheDocument();
  });
});

describe('downloaded file hashes', () => {
  const YAML = 'nc: 2\nbackbone:\n  - [-1, 1, Conv, [8, 3, 2]]\nhead: []\n';
  const sha = (text: string): string => createHash('sha256').update(text, 'utf8').digest('hex');

  async function renderDownload(registered: string | undefined): Promise<{ blobs: Blob[] }> {
    const c = cand({ candidate_id: 'dl-1', name: 'dl-1-model' });
    const detail = {
      candidate_id: 'dl-1', name: 'dl-1-model', blocks: [], evaluations: [], candidate_status: 'EVALUATED', architecture_yaml: YAML,
      artifacts: registered ? [{ id: 'arch-1', kind: 'architecture', url: '/x', sha256: registered, metadata: { candidate_id: 'dl-1', configuration_kind: 'execution' } }] : [],
      structure: { ...withApiFields(docs.base, 'saved_structure_check') },
    };
    const blobs: Blob[] = [];
    Object.assign(URL, { createObjectURL: vi.fn((blob: Blob) => { blobs.push(blob); return 'blob:synthetic'; }), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    render(<CandidateView c={c} run={null} runId="r" all={[c]} client={{ candidateDetail: async () => detail } as unknown as ApiClient} />);
    await screen.findByTestId('summary-backend');
    fireEvent.click(screen.getByText(/실제 보유 구성 YAML/));
    fireEvent.click(await screen.findByTestId('download-model-yaml'));
    await screen.findByTestId('download-hash');
    return { blobs };
  }

  it('computes SHA-256 of the downloaded bytes, matches the registered one and says the canonical yaml_hash differs on purpose', async () => {
    const { blobs } = await renderDownload(sha(YAML));
    expect(blobs).toHaveLength(1);
    expect(await blobs[0].text()).toBe(YAML);
    expect(screen.getByTestId('hash-file')).toHaveTextContent(sha(YAML));
    expect(screen.getByTestId('hash-registered')).toHaveTextContent(sha(YAML));
    expect(screen.getByTestId('hash-canonical')).toHaveTextContent(String(docs.base.yaml_hash).replace('sha256:', ''));
    expect(screen.getByTestId('hash-registered-verdict')).toHaveTextContent('일치합니다');
    expect(screen.getByTestId('hash-canonical-verdict')).toHaveTextContent('다른 것은 정상');
    expect(screen.getByTestId('download-hash')).toHaveTextContent('두 해시는 정의가 다릅니다');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('a file sha256 that differs from the registered artifact sha256 is shown as an error', async () => {
    await renderDownload('f'.repeat(64));
    expect(screen.getByTestId('hash-file')).toHaveTextContent(sha(YAML));
    const verdict = screen.getByTestId('hash-registered-verdict');
    expect(verdict).toHaveTextContent('오류');
    expect(verdict.closest('[role="alert"]')).not.toBeNull();
  });

  it('without a registered sha256 the comparison is reported as not possible (no value invented)', async () => {
    await renderDownload(undefined);
    expect(screen.getByTestId('hash-registered')).toHaveTextContent('등록값 없음');
    expect(screen.getByTestId('hash-registered-verdict')).toHaveTextContent('대조하지 못했습니다');
  });

  it('without WebCrypto it says so and prints no hash', async () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
    Object.defineProperty(globalThis, 'crypto', { value: {}, configurable: true });
    try {
      await renderDownload(sha(YAML));
      expect(screen.getByTestId('hash-unavailable')).toHaveTextContent('WebCrypto');
      expect(screen.getByTestId('hash-file')).toHaveTextContent('—');
      expect(screen.queryByTestId('hash-registered-verdict')).toBeNull();
    } finally { if (original) Object.defineProperty(globalThis, 'crypto', original); }
  });
});

describe('model comparison', () => {
  async function compare(parentChild: boolean): Promise<void> {
    const c = cand({ candidate_id: 'cmp-a', name: 'cmp-a', parents: parentChild ? ['cmp-b'] : [] });
    const other = cand({ candidate_id: 'cmp-b', name: 'cmp-b' });
    const client = { candidateDetail: async () => ({ candidate_id: 'cmp-b', name: 'cmp-b', blocks: [], parents: [], structure: docs.longer }) } as unknown as ApiClient;
    render(<ModelComparison c={c} blocks={docs.base.blocks} all={[c, other]} client={client} runId="r" structure={docs.base} />);
    await userEvent.setup().selectOptions(screen.getByLabelText('비교할 모델'), 'cmp-b');
    await screen.findByTestId('edge-diff');
  }

  it('lists edges added and removed (server-shaped diff) for a two-model comparison', async () => {
    await compare(false);
    expect(screen.getByTestId('comparison-kind')).toHaveTextContent('두 모델 구조 비교');
    const added = within(screen.getByTestId('edges-added'));
    const removed = within(screen.getByTestId('edges-removed'));
    expect(added.getByRole('heading')).toHaveTextContent('(3)');
    expect(removed.getByRole('heading')).toHaveTextContent('(2)');
    expect(screen.getByTestId('edges-added')).toHaveTextContent('row 5 → row 6 (from_ref 5)');
    expect(screen.getByTestId('edges-removed')).toHaveTextContent('row 4 → row 5 (from_ref 4)');
    // the changed-field column carries the server `fields`
    expect(within(screen.getByRole('region', { name: '추가·제거·변경 및 연결 차이' })).getByRole('table')).toHaveTextContent('module, from, args');
  });

  it('keeps 부모·자식 apart from 두 모델 when the server recorded the parent relation', async () => {
    await compare(true);
    expect(screen.getByTestId('comparison-kind')).toHaveTextContent('부모·자식 구조 비교');
  });

  it('says so when one side has no edge record instead of computing edges from nothing', async () => {
    const c = cand({ candidate_id: 'cmp-a', name: 'cmp-a' });
    const other = cand({ candidate_id: 'cmp-b', name: 'cmp-b' });
    const client = { candidateDetail: async () => ({ candidate_id: 'cmp-b', name: 'cmp-b', blocks: [], structure: { blocks: docs.longer.blocks } }) } as unknown as ApiClient;
    render(<ModelComparison c={c} blocks={docs.base.blocks} all={[c, other]} client={client} runId="r" structure={docs.base} />);
    await userEvent.setup().selectOptions(screen.getByLabelText('비교할 모델'), 'cmp-b');
    await waitFor(() => expect(screen.getByTestId('edge-diff-unavailable')).toBeInTheDocument());
    expect(screen.queryByTestId('edge-diff')).toBeNull();
  });
});

describe('design-origin catalog (DD-38, real server documents of tests/block_catalog_server_fixture.json)', () => {
  const catalogDocs = catalogFixture.documents as unknown as Record<string, Doc>;

  it('CandidateView header shows the same origin distribution as the structure view, from catalog_summary', async () => {
    const c = cand({ candidate_id: 'org-1', name: 'org-1-model' });
    const detail = { candidate_id: 'org-1', name: 'org-1-model', blocks: [], evaluations: [], candidate_status: 'BUILD_VALID', structure_status: 'PASS',
      structure: withApiFields(catalogDocs.checked, 'saved_structure_check') };
    render(<CandidateView c={c} run={null} runId="r" all={[c]} client={{ candidateDetail: async () => detail } as unknown as ApiClient} />);
    const header = await screen.findByTestId('candidate-origin-distribution');
    expect(header).toHaveTextContent('구성 블록 출처 분포: YOLOv5 60% · YOLOv8 40%');
    expect(within(screen.getByTestId('candidate-structure-summary')).getByTestId('candidate-origin-distribution')).toBe(header);
    expect(screen.getByTestId('model-origin-distribution')).toHaveTextContent('YOLOv5 60% · YOLOv8 40%');
  });

  it('a candidate whose structure predates the catalog has no distribution line in the header', async () => {
    const c = cand({ candidate_id: 'org-2', name: 'org-2-model' });
    const detail = { candidate_id: 'org-2', name: 'org-2-model', blocks: [], evaluations: [], candidate_status: 'BUILD_VALID', structure_status: 'PASS',
      structure: withApiFields(docs.base, 'saved_structure_check') };
    render(<CandidateView c={c} run={null} runId="r" all={[c]} client={{ candidateDetail: async () => detail } as unknown as ApiClient} />);
    await screen.findByTestId('summary-backend');
    expect(screen.queryByTestId('candidate-origin-distribution')).toBeNull();
    expect(screen.queryByTestId('model-origin-distribution')).toBeNull();
  });
});
