/**
 * WP-W1 pass 2: the web views against REAL native (non-YAML) mbnas-structure/2 documents produced by WP-S2 code.
 *
 * `structure_native_server_fixture.json` is not hand written: it holds `native_structure_for_arch()` of the registry models
 * rf-detr-nano and etri-controlled-cnn-transformer (real CPU build + forward hooks, imgsz 384), the declaration-only
 * documents `describe_registry_model()` returns for them (what GET /registry/{id}/structure sends when nothing is saved)
 * and real `compare_structures()` output. Generator and run log: evidence_impl/WPW1/gen_native_fixture.py, pass2/gen_native_fixture.txt.
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import type { ApiClient } from '../src/api/client';
import { ModelComparison } from '../src/components/ModelComparison';
import { CandidateView } from '../src/components/CandidateView';
import { ModelStructure, StructureSummary } from '../src/components/ModelStructure';
import { diffStructureDocuments, structureEdges, structureNodes } from '../src/lib/structure';
import { cand } from './fixtures';
import fixture from './structure_native_server_fixture.json';

type Doc = Record<string, unknown> & { blocks: Record<string, any>[]; edges: Record<string, any>[]; edges_unobserved: Record<string, any>[] };
const docs = fixture.documents as unknown as Record<string, Doc>;
const nano = docs.rfdetr_nano_checked, etri = docs.etri_checked;
afterEach(() => { cleanup(); window.history.replaceState(null, '', '/'); });

describe('real RF-DETR Nano document (S2 output)', () => {
  it('shows one button per module, labelled by native path, and no YAML-position label', () => {
    render(<ModelStructure blocks={nano.blocks} modelKey="nano" report={nano} />);
    expect(nano.blocks).toHaveLength(15);
    nano.blocks.forEach((block, index) => {
      const button = screen.getByTestId(`gene-${index}`);
      expect(button).toHaveTextContent(block.native_path);
    });
    expect(screen.getByTestId('model-structure').textContent).not.toMatch(/backbone\[\d\]|head\[\d\]|neck\[\d\]|row\[\d\]|from \d|YAML 위치/);
    expect(screen.getByTestId('basis-backbone')).toHaveTextContent('section_basis: native:backbone.0 / native:backbone');
  });

  it('draws exactly the report edges (integer endpoints resolve through the block position), styled by connection_source', () => {
    render(<ModelStructure blocks={nano.blocks} modelKey="nano" report={nano} />);
    expect(screen.queryByText(/해결할 수 없는 연결/)).toBeNull();
    const drawn = [...screen.getByTestId('structure-graph').querySelectorAll('g[data-source][data-target]')];
    expect(drawn).toHaveLength(nano.edges.length);
    expect(drawn.map((g) => g.getAttribute('data-connection'))).toEqual(nano.edges.map((e) => e.connection_source));
    expect(new Set(drawn.map((g) => g.getAttribute('data-connection')))).toEqual(new Set(['observed', 'native_fixed']));
    // the block-id of each drawn edge is the block at the recorded position
    expect(drawn.map((g) => [g.getAttribute('data-source'), g.getAttribute('data-target')]))
      .toEqual(nano.edges.map((e) => [e.source == null ? 'INPUT' : nano.blocks[e.source].id, nano.blocks[e.target].id]));
    // no edge into a block the report does not connect (blocks 3..8 have none)
    const targets = new Set(drawn.map((g) => g.getAttribute('data-target')));
    for (const i of [3, 4, 5, 6, 7, 8]) expect(targets.has(nano.blocks[i].id)).toBe(false);
  });

  it('lists every unobserved module under the graph', () => {
    render(<ModelStructure blocks={nano.blocks} modelKey="nano" report={nano} />);
    const items = within(screen.getByTestId('edges-unobserved')).getAllByTestId('edge-unobserved-item');
    expect(items).toHaveLength(nano.edges_unobserved.length);
    expect(nano.edges_unobserved.length).toBeGreaterThan(0);
    nano.edges_unobserved.forEach((item, i) => { expect(items[i]).toHaveTextContent(item.module_path); expect(items[i]).toHaveTextContent(item.reason.slice(0, 30)); });
  });

  it('decoder layer: repeats from dec_layers (declared 2, observed 2, no scale), bases, inputs with source paths, no shared layer', () => {
    render(<ModelStructure blocks={nano.blocks} modelKey="nano" report={nano} />);
    fireEvent.click(screen.getByTestId('gene-10'));
    const block = screen.getByTestId('selected-block');
    expect(block).toHaveTextContent('rf:transformer.decoder.layers.0@');
    expect(block).toHaveTextContent('module_class (관측)');
    expect(block).toHaveTextContent('TransformerDecoderLayer');
    expect(screen.getByTestId('repeats-declared')).toHaveTextContent('2');
    expect(screen.getByTestId('repeats-scaled')).toHaveTextContent('해당 없음');
    expect(screen.getByTestId('repeats-observed')).toHaveTextContent('2');
    expect(screen.getByTestId('repeats-basis')).toHaveTextContent('dec_layers');
    expect(screen.getByTestId('repeats-basis')).toHaveTextContent('len(transformer.decoder.layers)');
    expect(screen.getByTestId('repeats-basis')).toHaveTextContent('transformer.decoder.layers (0번째)');
    const inputs = screen.getByTestId('block-inputs');
    expect(inputs).toHaveTextContent('row 9 · transformer.decoder.ref_point_head');
    expect(inputs.querySelectorAll('tbody tr').length).toBe(nano.blocks[10].effective.inputs.length);
    expect(screen.getByTestId('internal-layers').querySelectorAll('tr[data-shared="true"]')).toHaveLength(0);
    expect(screen.getByTestId('internal-layers').querySelectorAll('tr[data-shared="false"]')).toHaveLength(nano.blocks[10].internal_layers.length);
  });

  it('the backbone encoder block is a 4-output module (multiple outputs are recorded, not collapsed)', () => {
    render(<ModelStructure blocks={nano.blocks} modelKey="nano" report={nano} />);
    fireEvent.click(screen.getByTestId('gene-0'));
    expect(screen.getByTestId('repeats-observed')).toHaveTextContent('4');
    expect(screen.getByTestId('repeats-basis')).toHaveTextContent('len(forward output feature maps)');
    expect(screen.getByTestId('block-inputs')).toHaveTextContent('INPUT (모델 입력)');
  });

  it('check table keeps NOT_CHECKED rows (yaml_rows, merge_conditions) as 기록 없음 and the summary counts them', () => {
    render(<><StructureSummary report={nano} /><ModelStructure blocks={nano.blocks} modelKey="nano" report={nano} /></>);
    expect(screen.getByTestId('check-level-yaml_rows')).toHaveTextContent('기록 없음');
    expect(screen.getByTestId('check-level-merge_conditions')).toHaveTextContent('기록 없음');
    expect(screen.getByTestId('check-level-reference_forward')).toHaveTextContent('forward');
    expect(screen.getByTestId('summary-backend')).toHaveTextContent('rfdetr');
    expect(screen.getByTestId('summary-representation')).toHaveTextContent('native_modules');
    expect(screen.getByTestId('summary-evidence-level')).toHaveTextContent('최저 증거 수준: built');
    expect(screen.getByTestId('summary-evidence-level')).toHaveTextContent('미검사 4');
    expect(screen.getByTestId('structure-summary')).toHaveTextContent('forward_hook · cpu · imgsz 384');
  });
});

describe('real ETRI hybrid document (S2 output)', () => {
  it('draws the 14 recorded edges; the position buffer is connected by a native_fixed (dashed) edge', () => {
    render(<ModelStructure blocks={etri.blocks} modelKey="etri" report={etri} />);
    const drawn = [...screen.getByTestId('structure-graph').querySelectorAll('g[data-source][data-target]')];
    expect(drawn).toHaveLength(etri.edges.length);
    expect(etri.edges.length).toBe(14);
    expect(screen.getByTestId('edges-unobserved')).toHaveTextContent('기록된 관측되지 않은 구간이 없습니다');
    const dashed = drawn.filter((g) => g.getAttribute('data-connection') === 'native_fixed');
    expect(dashed.length).toBeGreaterThan(0);
    expect(dashed.every((g) => g.querySelector('path')?.getAttribute('stroke-dasharray') === '6 4')).toBe(true);
  });

  it('a stem block: 8 repeats from a Sequential container, internal layers, no scale', () => {
    render(<ModelStructure blocks={etri.blocks} modelKey="etri" report={etri} />);
    fireEvent.click(screen.getByTestId('gene-3'));
    expect(screen.getByTestId('selected-block')).toHaveTextContent('etri:embedding.layers.3@');
    expect(screen.getByTestId('repeats-declared')).toHaveTextContent('8');
    expect(screen.getByTestId('repeats-observed')).toHaveTextContent('8');
    expect(screen.getByTestId('repeats-basis')).toHaveTextContent('2*log2(patch) conv blocks');
    expect(screen.getByTestId('block-inputs')).toHaveTextContent('row 2 · embedding.layers.2');
    expect(screen.getByTestId('internal-layers')).toHaveTextContent('기록된 내부 layer (3');
  });
});

describe('declaration-only native documents (GET /registry/{id}/structure with nothing saved)', () => {
  for (const key of ['rfdetr_nano_declared', 'etri_declared']) {
    it(`${key}: UNVERIFIED, no blocks, says so and does not invent a graph or call it a FAIL`, () => {
      const doc = docs[key];
      expect(doc.status).toBe('UNVERIFIED');
      render(<><StructureSummary report={doc} /><ModelStructure blocks={doc.blocks} modelKey={key} report={doc} /></>);
      expect(screen.getByTestId('structure-declared-only-notice')).toBeInTheDocument();
      expect(screen.getByTestId('model-structure-empty')).toBeInTheDocument();
      expect(screen.queryByTestId('structure-graph')).toBeNull();
      expect(screen.queryByTestId('structure-fail-notice')).toBeNull();
      expect(screen.getByTestId('summary-structure-status')).toHaveTextContent('UNVERIFIED');
      expect(screen.getByTestId('summary-evidence-source')).toHaveTextContent('declared_configuration');
      expect(screen.getByTestId('summary-evidence-level')).toHaveTextContent('기록 없음');
      expect(screen.getByTestId('structure-summary')).toHaveTextContent('관측 기록 없음');
    });
  }
});

describe('native candidate detail (CandidateView) with a GENE_REPRESENTATION_NOT_APPLICABLE document', () => {
  it('shows the unsupported notice (not a FAIL) for the real declaration-only shape with that code', () => {
    const doc = { ...docs.rfdetr_nano_declared, diagnostics: [{ code: 'GENE_REPRESENTATION_NOT_APPLICABLE', position: null, path: null, cause: 'the rfdetr backend is built from code modules', suggestion: 'Run the explicit CPU structure check.' }] };
    render(<ModelStructure blocks={doc.blocks} modelKey="n" report={doc} />);
    expect(screen.getByTestId('structure-unobserved-notice')).toHaveTextContent('구조 관측 없음 / 지원되지 않음');
    expect(screen.queryByTestId('structure-fail-notice')).toBeNull();
  });

  it('header and graph for a saved native check', async () => {
    const c = cand({ candidate_id: 'rf-1', name: 'rf-1-model' } as never);
    const detail = { candidate_id: 'rf-1', name: 'rf-1-model', blocks: nano.blocks, evaluations: [], candidate_status: 'BUILD_VALID', structure_status: 'PASS', structure: { ...nano, evidence_source: 'saved_structure_check' } };
    render(<CandidateView c={c} run={null} runId="r" all={[c]} client={{ candidateDetail: async () => detail } as unknown as ApiClient} />);
    expect(await screen.findByTestId('summary-backend')).toHaveTextContent('rfdetr');
    expect(screen.getByTestId('summary-representation')).toHaveTextContent('native_modules');
    expect(screen.getByTestId('summary-evidence-source')).toHaveTextContent('saved_structure_check');
    expect(screen.getAllByTestId('edge-unobserved-item')).toHaveLength(nano.edges_unobserved.length);
  });
});

describe('parity with the server compare_structures on native documents', () => {
  for (const pair of fixture.pairs) {
    it(`${pair.left} -> ${pair.right}`, () => {
      expect(diffStructureDocuments(docs[pair.left], docs[pair.right])).toEqual(pair.expected);
    });
  }

  it('the edges the view resolves from the integer endpoints are the edges of the real report (nothing added)', () => {
    for (const doc of [nano, etri]) {
      const nodes = structureNodes(doc.blocks, 'x', { representation: doc.representation, schema: doc.schema });
      const position = new Map(nodes.map((n) => [n.id, n.position]));
      const resolved = structureEdges(nodes, doc.edges).map((e) => [e.source === null ? null : position.get(e.source), position.get(e.target)]);
      expect(resolved).toEqual(doc.edges.map((e) => [e.source, e.target]));
    }
  });
});

describe('comparison of two real native documents', () => {
  it('lists the server-shaped edge diff and names the native identity fields that differ (the server fields are null for native blocks)', async () => {
    const c = cand({ candidate_id: 'n-a', name: 'etri-model' });
    const other = cand({ candidate_id: 'n-b', name: 'nano-model' });
    const client = { candidateDetail: async () => ({ candidate_id: 'n-b', name: 'nano-model', blocks: nano.blocks, parents: [], structure: nano }) } as unknown as ApiClient;
    render(<ModelComparison c={c} blocks={etri.blocks} all={[c, other]} client={client} runId="r" structure={etri} />);
    await userEvent.setup().selectOptions(screen.getByLabelText('비교할 모델'), 'n-b');
    await screen.findByTestId('edge-diff');
    const expected = fixture.pairs.find((p) => p.left === 'etri_checked' && p.right === 'rfdetr_nano_checked')!.expected;
    expect(within(screen.getByTestId('edges-added')).getByRole('heading')).toHaveTextContent(`(${expected.edges_added.length})`);
    expect(within(screen.getByTestId('edges-removed')).getByRole('heading')).toHaveTextContent(`(${expected.edges_removed.length})`);
    expect(screen.getByTestId('comparison-kind')).toHaveTextContent('두 모델 구조 비교');
    const table = within(screen.getByRole('region', { name: '추가·제거·변경 및 연결 차이' })).getByRole('table');
    expect(table).toHaveTextContent('블록 순서');
    expect(table).toHaveTextContent('native_path, module_class');
    expect(table).toHaveTextContent('embedding.layers.0');
    expect(table).toHaveTextContent('backbone.0.encoder');
  });
});
