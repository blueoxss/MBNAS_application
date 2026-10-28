import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ModelStructure } from '../src/components/ModelStructure';
import { compareStructure, diffStructureDocuments, hashVerdict, hexDigest, lowestEvidenceLevel, repeatsDetailOf, structureEdges, structureKind, structureNodes, unobservedCodes } from '../src/lib/structure';

const blocks = [
  { position: 0, name: 'Conv', params: { from: -1, repeats: 1, args: [16, 3, 2], scope: 'backbone' } },
  { position: 1, name: 'C2PSA', params: { from: -1, repeats: 3, args: [16], scope: 'backbone', effective: { params: 1234 } } },
  { position: 2, name: 'Concat', params: { from: [-1, 0], repeats: 1, args: [1], scope: 'head' } },
  { position: 3, name: 'Detect', params: { from: [1, -1], repeats: 1, args: ['nc'], scope: 'head' } },
];

describe('recorded model structure', () => {
  it('resolves relative, absolute, multi-input, skip and detection edges without flattening them', () => {
    const nodes = structureNodes(blocks, 'candidate-x');
    expect(nodes.map((node) => node.id)).toEqual(['candidate-x#0', 'candidate-x#1', 'candidate-x#2', 'candidate-x#3']);
    expect(nodes.map((node) => node.yamlPath)).toEqual(['backbone[0]', 'backbone[1]', 'head[0]', 'head[1]']);
    expect(structureEdges(nodes).map((edge) => [edge.source, edge.target, edge.reference, edge.state])).toEqual([
      [null, 'candidate-x#0', -1, 'input'],
      ['candidate-x#0', 'candidate-x#1', -1, 'connected'],
      ['candidate-x#1', 'candidate-x#2', -1, 'connected'],
      ['candidate-x#0', 'candidate-x#2', 0, 'connected'],
      ['candidate-x#1', 'candidate-x#3', 1, 'connected'],
      ['candidate-x#2', 'candidate-x#3', -1, 'connected'],
    ]);
  });

  it('keeps missing or invalid references unresolved and does not invent a chain', () => {
    const nodes = structureNodes([{ name: 'Conv' }, { name: 'Add', from: [-9, 1, 7] }], 'missing');
    expect(structureEdges(nodes)).toHaveLength(3);
    expect(structureEdges(nodes).every((edge) => edge.state === 'unresolved')).toBe(true);
    expect(nodes[0].effective).toEqual({});
    expect(nodes[0].internalLayers).toEqual([]);
  });

  it('uses explicit service IDs, YAML mapping and recorded internal layers', () => {
    const nodes = structureNodes([{ id: 'block-0000', section: 'neck', yaml_path: 'head[4]', module: 'C3', from: -1, repeats: 3, args: [64], effective: { repeats: 2, params: 556 }, internal_layers: [{ path: 'model.0.cv1', params: 24 }] }], 'service');
    expect(nodes[0]).toMatchObject({ id: 'block-0000', scope: 'neck', yamlPath: 'head[4]', repeats: 3, effective: { repeats: 2, params: 556 } });
    expect(nodes[0].internalLayers).toHaveLength(1);
  });

  it('diffs configuration and connections by position without claiming gene ancestry', () => {
    const before = structureNodes(blocks, 'before');
    const after = structureNodes([...blocks.slice(0, 2), { ...blocks[2], params: { ...blocks[2].params, from: [-1, 1] } }], 'after');
    expect(compareStructure(before, after)).toEqual([{ position: 0, change: 'same' }, { position: 1, change: 'same' }, { position: 2, change: 'changed' }, { position: 3, change: 'removed' }]);
  });

  it('labels operator classes and synchronizes selected gene with block details', () => {
    expect(structureKind('C2PSA')).toBe('Transformer');
    expect(structureKind('Concat')).toBe('Merge');
    render(<ModelStructure blocks={blocks} modelKey="candidate-x" status="UNVERIFIED" />);
    expect(screen.getByTestId('structure-status').textContent).toBe('UNVERIFIED');
    fireEvent.click(screen.getByTestId('gene-1'));
    expect(screen.getByTestId('selected-block').textContent).toContain('선택 블록 #1 · C2PSA');
    expect(screen.getByTestId('selected-block').textContent).toContain('1234');
    expect(screen.getByTestId('selected-block').textContent).toContain('내부 layer 대응은 기록되지 않았습니다');
    expect(new URL(window.location.href).searchParams.get('block')).toBe('candidate-x#1');
  });

  it('does not mix up declared, scaled and observed repeats (v2) and keeps the v1 meaning for v1 documents', () => {
    const v2 = structureNodes([{ id: 'block-0000', module: 'C3k2', repeats: 2, repeats_detail: { declared: 2, scaled: 1, observed: 1 }, effective: { repeats: 1, repeats_scaled: 1 } }], 'v2', { schema: 'mbnas-structure/2' });
    expect(v2[0].repeatsDetail).toMatchObject({ declared: 2, scaled: 1, observed: 1 });
    // UNVERIFIED v2 document: the formula value stays "scaled", observed is null and is never replaced by it
    const unverified = structureNodes([{ id: 'block-0000', module: 'C3k2', repeats: 2, repeats_detail: { declared: 2, scaled: 1, observed: null }, effective: { repeats: null, repeats_scaled: 1 } }], 'u', { schema: 'mbnas-structure/2' });
    expect(unverified[0].repeatsDetail).toMatchObject({ declared: 2, scaled: 1, observed: null });
    // v1: effective.repeats WAS the depth-scaled value, nothing was observed
    const v1 = structureNodes([{ id: 'block-0000', module: 'C3k2', repeats: 2, effective: { repeats: 1 } }], 'v1', { schema: 'mbnas-structure/1' });
    expect(v1[0].repeatsDetail).toMatchObject({ declared: 2, scaled: 1, observed: null });
    // v2 key present but repeats_detail missing: still observed from effective.repeats, scaled from repeats_scaled
    expect(repeatsDetailOf({}, { repeats: 3, repeats_scaled: 2 }, 4, 'mbnas-structure/2')).toMatchObject({ declared: 4, scaled: 2, observed: 3 });
  });

  it('native blocks (no gene, no YAML row) get native labels and edges only from the report', () => {
    const blocks = [
      { id: 'block-0000', gene_index: null, yaml_path: null, from: null, native_path: 'backbone.0', module_class: 'Conv2d', section: 'backbone', section_basis: 'native:backbone', connection_source: 'native_fixed' },
      { id: 'block-0001', gene_index: null, yaml_path: null, from: null, native_path: 'transformer.decoder.layers.1', module_class: 'DecoderLayer', section: 'transformer', section_basis: 'native:transformer', connection_source: 'observed' },
      { id: 'block-0002', gene_index: null, yaml_path: null, from: null, native_path: 'head.cls', module_class: 'Linear', section: 'head', section_basis: 'native:head' },
    ];
    const nodes = structureNodes(blocks, 'rf', { representation: 'native_modules' });
    expect(nodes.every((n) => n.native)).toBe(true);
    expect(nodes.map((n) => n.label)).toEqual(['backbone.0', 'transformer.decoder.layers.1', 'head.cls']);
    expect(nodes.map((n) => n.name)).toEqual(['Conv2d', 'DecoderLayer', 'Linear']);
    // without the explicit representation the gene_index:null + no YAML row shape is recognized as native too
    expect(structureNodes(blocks, 'rf').every((n) => n.native)).toBe(true);
    // no report edges -> no edges at all (nothing chained by order)
    expect(structureEdges(nodes)).toEqual([]);
    const edges = structureEdges(nodes, [
      { source: 'INPUT', target: 'block-0000', connection_source: 'native_fixed' },
      { source: 'block-0000', target: 'block-0001' },
      { source: 1, target: 2, connection_source: 'observed' },
      { source: 'block-0099', target: 'block-0002' },
    ]);
    expect(edges.map((e) => [e.source, e.target, e.state, e.connection])).toEqual([
      [null, 'block-0000', 'input', 'native_fixed'],
      ['block-0000', 'block-0001', 'connected', 'observed'], // edge carries none: the target block's connection_source
      ['block-0001', 'block-0002', 'connected', 'observed'],
      [null, 'block-0002', 'unresolved', null],
    ]);
  });

  it('keeps yaml documents on their own `from` edges even when the report lists edges too', () => {
    const nodes = structureNodes(blocks, 'x');
    const doubled = structureEdges(nodes, [{ source: 0, target: 1, from_ref: -1 }]);
    expect(doubled).toHaveLength(structureEdges(nodes).length);
    expect(doubled.every((e) => e.connection === 'yaml_from')).toBe(true);
  });

  it('lowest evidence level only counts checks that ran', () => {
    expect(lowestEvidenceLevel([{ status: 'PASS', evidence_level: 'forward' }, { status: 'PASS', evidence_level: 'built' }, { status: 'PASS', evidence_level: 'declared' }])).toBe('declared');
    expect(lowestEvidenceLevel([{ status: 'PASS', evidence_level: 'forward' }, { status: 'PASS', evidence_level: 'derived_from_forward' }])).toBe('derived_from_forward');
    expect(lowestEvidenceLevel([{ status: 'NOT_CHECKED', evidence_level: 'declared' }, { status: 'PASS', evidence_level: 'built' }])).toBe('built');
    expect(lowestEvidenceLevel([{ status: 'NOT_CHECKED', evidence_level: null }])).toBeNull();
    expect(lowestEvidenceLevel(undefined)).toBeNull();
  });

  it('separates "no structure observation / unsupported" (UNVERIFIED + two codes) from FAIL', () => {
    const diag = (code: string): unknown[] => [{ code }];
    expect(unobservedCodes('UNVERIFIED', diag('GENE_REPRESENTATION_NOT_APPLICABLE'))).toEqual(['GENE_REPRESENTATION_NOT_APPLICABLE']);
    expect(unobservedCodes('UNVERIFIED', diag('STRUCTURE_RUNTIME_UNAVAILABLE'))).toEqual(['STRUCTURE_RUNTIME_UNAVAILABLE']);
    expect(unobservedCodes('FAIL', diag('GENE_REPRESENTATION_NOT_APPLICABLE'))).toEqual([]);
    expect(unobservedCodes('UNVERIFIED', diag('STRUCTURE_CHECK_NOT_RECORDED'))).toEqual([]);
    expect(unobservedCodes('PASS', undefined)).toEqual([]);
  });

  it('hash verdict: file vs registered is an error when it differs, file vs canonical differing is expected', () => {
    const file = 'a'.repeat(64), other = 'b'.repeat(64);
    expect(hexDigest(`sha256:${file.toUpperCase()}`)).toBe(file);
    expect(hexDigest('not-a-hash')).toBeNull();
    expect(hashVerdict(file, file, `sha256:${other}`)).toMatchObject({ fileMatchesRegistered: true, fileEqualsCanonical: false });
    expect(hashVerdict(file, other, `sha256:${other}`)).toMatchObject({ fileMatchesRegistered: false });
    expect(hashVerdict(null, file, file)).toMatchObject({ fileMatchesRegistered: null, fileEqualsCanonical: null });
    expect(hashVerdict(file, null, null)).toMatchObject({ fileMatchesRegistered: null, fileEqualsCanonical: null, registered: null });
  });

  it('server-shaped diff lists edges that only one document has', () => {
    const left = { blocks: [{ id: 'block-0000', module: 'Conv', from: -1, repeats: 1, args: [8] }, { id: 'block-0001', module: 'Conv', from: -1, repeats: 1, args: [8] }], edges: [{ source: -1, target: 0, from_ref: -1 }, { source: 0, target: 1, from_ref: -1 }] };
    const right = { blocks: [...left.blocks.slice(0, 1), { id: 'block-0001', module: 'Conv', from: 0, repeats: 1, args: [8] }, { id: 'block-0002', module: 'Conv', from: -1, repeats: 1, args: [8] }], edges: [{ source: -1, target: 0, from_ref: -1 }, { source: 0, target: 1, from_ref: 0 }, { source: 1, target: 2, from_ref: -1 }] };
    expect(diffStructureDocuments(left, right)).toEqual({
      comparison: 'structure', alignment: 'yaml_position', gene_ancestry_inferred: false,
      added: ['block-0002'], removed: [], changed: [{ position: 1, left_id: 'block-0001', right_id: 'block-0001', fields: ['from'] }],
      edges_added: [['0', '1', '0'], ['1', '2', '-1']], edges_removed: [['0', '1', '-1']],
    });
  });
});
