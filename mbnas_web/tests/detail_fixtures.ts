/**
 * SYNTHETIC candidate-detail fixtures for the generation-only (evaluation OFF) workflow: a structure report in the
 * `mbnas-structure/2` shape (docs/integration_20261021/CONTRACTS_v2.md section 1) for a nine-row YAML model that
 * contains one list-from Concat row and one Detect row. Nothing here is a measured result: ids carry the
 * "synthetic-" prefix, hashes are invented, parameter counts are arbitrary. Shared by the Playwright mock
 * (e2e/mock.ts) and the vitest fixtures; imports types only, so it never reaches the production bundle.
 */
import type { Block, CandidateDetail, GeneratedCandidate } from '../src/api/types';

export const GEN_RUN_ID = 'synthetic-run-0001';
export const GEN_CID = 'synthetic-gen-c1';
export const GEN_ARCH_HASH = `sha256:${'1234abcd'.repeat(8)}`;
export const GEN_YAML_HASH = `sha256:${'5678ef01'.repeat(8)}`;

interface Row {
  module: string;
  section: 'backbone' | 'neck' | 'head';
  from: number | number[];
  args: unknown[];
  c_out: number;
  h: number;
  w: number;
  params: number;
  inputs: { src: number; c: number; h: number; w: number }[];
}

/** index = YAML row. h/w are the output size; inputs list the producing rows (observed shapes). */
const ROWS: Row[] = [
  { module: 'Conv', section: 'backbone', from: -1, args: [16, 3, 2], c_out: 16, h: 320, w: 320, params: 464, inputs: [{ src: -1, c: 3, h: 640, w: 640 }] },
  { module: 'Conv', section: 'backbone', from: -1, args: [32, 3, 2], c_out: 32, h: 160, w: 160, params: 4672, inputs: [{ src: 0, c: 16, h: 320, w: 320 }] },
  { module: 'C3k2', section: 'backbone', from: -1, args: [64, false, 0.25], c_out: 64, h: 160, w: 160, params: 7360, inputs: [{ src: 1, c: 32, h: 160, w: 160 }] },
  { module: 'Conv', section: 'backbone', from: -1, args: [64, 3, 2], c_out: 64, h: 80, w: 80, params: 36992, inputs: [{ src: 2, c: 64, h: 160, w: 160 }] },
  { module: 'C3k2', section: 'backbone', from: -1, args: [128, false, 0.25], c_out: 128, h: 80, w: 80, params: 26112, inputs: [{ src: 3, c: 64, h: 80, w: 80 }] },
  { module: 'Upsample', section: 'neck', from: -1, args: [null, 2, 'nearest'], c_out: 128, h: 160, w: 160, params: 0, inputs: [{ src: 4, c: 128, h: 80, w: 80 }] },
  { module: 'Concat', section: 'neck', from: [-1, 2], args: [1], c_out: 192, h: 160, w: 160, params: 0, inputs: [{ src: 5, c: 128, h: 160, w: 160 }, { src: 2, c: 64, h: 160, w: 160 }] },
  { module: 'C3k2', section: 'neck', from: -1, args: [64, false], c_out: 64, h: 160, w: 160, params: 29184, inputs: [{ src: 6, c: 192, h: 160, w: 160 }] },
  { module: 'Detect', section: 'head', from: [7, 4], args: ['nc'], c_out: 0, h: 0, w: 0, params: 21600, inputs: [{ src: 7, c: 64, h: 160, w: 160 }, { src: 4, c: 128, h: 80, w: 80 }] },
];

const blockId = (i: number): string => `block-${String(i).padStart(4, '0')}`;
const abs = (i: number, ref: number): number => (ref < 0 ? i + ref : ref);

export function syntheticStructureReport(): Record<string, unknown> {
  const blocks = ROWS.map((r, i) => {
    const refs = Array.isArray(r.from) ? r.from : [r.from];
    const isDetect = r.module === 'Detect';
    const localIdx = ROWS.slice(0, i).filter((x) => x.section === r.section).length;
    const yamlPath = `${r.section === 'backbone' ? 'backbone' : 'head'}[${r.section === 'backbone' ? localIdx : ROWS.slice(0, i).filter((x) => x.section !== 'backbone').length}]`;
    return {
      id: blockId(i),
      block_id: blockId(i),
      stable_id: `ul:${yamlPath}@${GEN_YAML_HASH.slice(7, 19)}`,
      gene_index: i,
      position: i,
      yaml_path: yamlPath,
      yaml_section: r.section === 'backbone' ? 'backbone' : 'head',
      yaml_index: i,
      from: r.from,
      from_resolved: refs.map((x) => abs(i, x)),
      repeats: 1,
      args: r.args,
      module: r.module,
      module_class: r.module === 'C3k2' ? 'C3k2' : r.module,
      wrapper: null,
      wrapped_module: null,
      section: r.section,
      section_basis: r.section === 'backbone' ? 'yaml:backbone' : isDetect ? 'yaml:head+detection_module' : 'derived:non-detection head row -> neck',
      connection_source: 'yaml_from',
      repeats_detail: { declared: 1, scaled: 1, observed: r.module === 'C3k2' ? 1 : null },
      declared: { repeats: 1, channels: r.c_out || null },
      effective: {
        repeats: r.module === 'C3k2' ? 1 : null,
        repeats_scaled: 1,
        inputs: r.inputs.map((x) => ({ source: x.src < 0 ? 'INPUT' : blockId(x.src), c: x.c, h: x.h, w: x.w })),
        ...(isDetect
          ? { outputs: [[67, 160, 160], [67, 80, 80]], head_strides: [4, 8], nl: 2, end2end: false }
          : { c_in: r.inputs.reduce((s, x) => s + x.c, 0), c_out: r.c_out, h_out: r.h, w_out: r.w }),
        params: r.params,
      },
      internal_layers: r.params > 0
        ? [{ path: `model.${i}.conv`, module: 'Conv2d', parameters: Math.floor(r.params * 0.9), attributes: {}, shared: false }, { path: `model.${i}.act`, module: 'SiLU', parameters: 0, attributes: {}, shared: true }]
        : [],
      internal_mapping_status: r.params > 0 ? 'RECORDED' : 'UNVERIFIED',
    };
  });
  const edges: Record<string, unknown>[] = [];
  ROWS.forEach((r, i) => {
    (Array.isArray(r.from) ? r.from : [r.from]).forEach((ref) => {
      edges.push({ source: i === 0 ? null : blockId(abs(i, ref)), target: blockId(i), from_ref: ref, connection_source: 'yaml_from' });
    });
  });
  return {
    schema: 'mbnas-structure/2',
    backend: 'ultralytics',
    representation: 'yaml_genes',
    status: 'PASS',
    evidence_source: 'structure_check',
    class_count: 3,
    input_size: 640,
    scale: 'n',
    yaml_hash: GEN_YAML_HASH,
    architecture_hash: GEN_ARCH_HASH,
    signature_hash: `sha256:${'9abc0def'.repeat(8)}`,
    parameters: ROWS.reduce((s, r) => s + r.params, 0),
    flops: null,
    flops_status: 'NOT_MEASURED',
    flops_source: null,
    lineage: null,
    lineage_status: 'NOT_APPLICABLE',
    checks: [
      { name: 'yaml_rows', status: 'PASS', evidence_level: 'declared' },
      { name: 'cpu_build', status: 'PASS', evidence_level: 'built' },
      { name: 'reference_forward', status: 'PASS', evidence_level: 'forward' },
      { name: 'merge_conditions', status: 'PASS', evidence_level: 'forward' },
      { name: 'head_connection', status: 'PASS', evidence_level: 'forward' },
      { name: 'channels_spatial_stride', status: 'PASS', evidence_level: 'forward' },
    ],
    observation: { method: 'ultralytics_parse_model+forward_hook', device: 'cpu', imgsz: 640, python: 'synthetic', torch: 'synthetic', ultralytics: 'synthetic', fx_traceable: null, dinov2_weights: null, checked_at: '2026-10-21T00:00:00Z' },
    blocks,
    edges,
    edges_unobserved: [],
    diagnostics: [],
  };
}

export function syntheticGeneratedRows(): GeneratedCandidate[] {
  const mk = (order: number, seed: number, cid: string, parameters: number | null): GeneratedCandidate => ({
    id: cid,
    candidate_id: cid,
    run_id: GEN_RUN_ID,
    name: `${cid}-model`,
    family: 'yolo11n',
    arm: 'RandomGeneration',
    origin: 'random_generation',
    candidate_status: 'BUILD_VALID',
    requested_method: ['GA'],
    effective_method: 'RandomGeneration',
    evaluation_status: 'NOT_EVALUATED',
    structure_status: 'PASS',
    training_status: 'UNTRAINED', // contract 1.5.0: generation never trains
    generation_order: order,
    parameters,
    ap: null,
    recall: null,
    latency_ms: null,
    fitness: null,
    search_seed: seed,
    generation: 0,
    architecture_hash: `sha256:${String(order).repeat(64).slice(0, 64)}`,
  });
  // returned out of generation order on purpose: the table must sort by (seed first-seen, generation_order)
  return [mk(2, 7, 'synthetic-gen-c3', null), mk(0, 7, GEN_CID, ROWS.reduce((s, r) => s + r.params, 0)), mk(1, 7, 'synthetic-gen-c2', 51234)];
}

export function syntheticGeneratedDetail(cid: string = GEN_CID, runId: string = GEN_RUN_ID): CandidateDetail {
  return {
    candidate_id: cid,
    run_id: runId,
    name: `${cid}-model`,
    arm: 'RandomGeneration',
    family: 'yolo11n',
    architecture_hash: GEN_ARCH_HASH,
    architecture_yaml: null,
    architecture_path: null,
    blocks: [],
    parents: [],
    lineage: { operation: 'random', template: 'yolo11n' },
    evaluations: [],
    checkpoints: [],
    cost: {},
    gate_history: [],
    candidate_status: 'BUILD_VALID',
    // 1.5.0 closed audit: generation {execution, effective_method, ...}, structure = a StructureReport
    audit: { generation: { execution: 'CPU', effective_method: 'RandomGeneration' }, structure: { schema: 'mbnas-structure/2', status: 'PASS' } },
    structure: syntheticStructureReport() as CandidateDetail['structure'],
    structure_status: 'PASS',
    evaluation_status: 'NOT_EVALUATED',
    training_status: 'UNTRAINED',
    origin: 'random_generation',
    artifacts: [],
  };
}

/**
 * Public (contract 1.5.0) block-table rows of the same nine-row model: one row per YAML position, as a searched candidate
 * reports them (YAML row in `params`). C07-D1 tests pair them with `syntheticStructureReport()` (block ids `block-000N`).
 */
export function syntheticContractBlocks(): Block[] {
  return ROWS.map((r, i) => ({
    name: r.module,
    source: 'random',
    source_kind: 'random',
    mutated: false,
    position: i,
    operation: 'sample',
    parent_block_id: null,
    params: { module: r.module, from: r.from, repeats: 1, args: r.args, scope: r.section === 'backbone' ? 'backbone' : 'head' },
  }));
}

export const SYNTHETIC_EDGE_COUNT = ROWS.reduce((n, r) => n + (Array.isArray(r.from) ? r.from.length : 1), 0);
export const SYNTHETIC_CONCAT_INDEX = ROWS.findIndex((r) => r.module === 'Concat');
export const SYNTHETIC_DETECT_INDEX = ROWS.findIndex((r) => r.module === 'Detect');
