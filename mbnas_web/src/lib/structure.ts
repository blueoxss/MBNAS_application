/** View data derived only from recorded YAML rows, recorded native paths and measured structure records. */
import type { BlockCatalogRecord, BlockCatalogSummary, BlockCatalogTable, BlockCatalogEntry } from '../api/types';

export type { BlockCatalogTable, BlockCatalogEntry };
export type StructureKind = 'CNN' | 'Transformer' | 'Merge' | 'Detection' | 'Other';
/** CONTRACTS_v2 1.1: yaml_from (declared from references), native_fixed (code-fixed flow), observed (seen through hooks only). */
export type ConnectionSource = 'yaml_from' | 'native_fixed' | 'observed';
export const CONNECTION_SOURCES: readonly ConnectionSource[] = ['yaml_from', 'native_fixed', 'observed'];

export interface RepeatsDetail {
  /** D: declared in the configuration. */
  declared: number | null;
  /** R: the parser depth formula applied to the declared value (null for native modules). */
  scaled: number | null;
  /** O: read from the built module. null means "not observed", never a derived substitute. */
  observed: number | null;
  /** What the counts count (WP-S2 native documents: e.g. "num_queries*group_detr", "len(embedding.layers)"); null when not recorded. */
  declaredBasis: string | null;
  observedBasis: string | null;
  /** Native repeated container this block belongs to (e.g. "embedding.layers"), with the block's index in it. */
  group: string | null;
  unitIndex: number | null;
}

/** snake_case -> camelCase of a key name (type level). */
type Camel<K extends string> = K extends `${infer H}_${infer T}` ? `${H}${Capitalize<Camel<T>>}` : K;
/** The camelCase view of a generated wire object: every key of the contract component, renamed, same value types. */
type CamelView<T> = { [K in keyof T as Camel<K & string>]: T[K] };

/**
 * `blocks[].catalog` (DD-38) in camelCase: where the block's design comes from. Every field but `role` may be null
 * ("출처 미상"); nothing is guessed. Derived from the generated `BlockCatalog` component (WP-6C: no hand-written catalogue
 * type), so a contract change of the catalogue keys is a compile error in `blockCatalogOf`. `reference` is a URL or a
 * repository path (ETRI sources); `sourcePath` is the class file inside the installed package (relative to
 * site-packages), only on explicitly checked documents.
 */
export type BlockCatalog = CamelView<BlockCatalogRecord>;
/** Document-level `catalog_summary` (generated `BlockCatalogSummary`, camelCase): blocks per known origin family and the blocks without one. */
export type CatalogSummary = CamelView<BlockCatalogSummary>;

export interface StructureNode {
  id: string;
  position: number;
  yamlPath: string;
  name: string;
  scope: string;
  kind: StructureKind;
  from: unknown;
  repeats: unknown;
  args: unknown;
  effective: Record<string, unknown>;
  internalLayers: unknown[];
  record: Record<string, unknown>;
  /** mbnas-structure/2 identity fields; null when the document does not carry them (v1). */
  stableId: string | null;
  nativePath: string | null;
  moduleClass: string | null;
  wrapper: string | null;
  wrappedModule: string | null;
  sectionBasis: string | null;
  connectionSource: ConnectionSource | null;
  repeatsDetail: RepeatsDetail;
  /** `blocks[].catalog`; null when the document predates DD-38 (the catalog key is absent, which is not "unknown"). */
  catalog: BlockCatalog | null;
  /** true for native_modules documents: the block has no gene / YAML row, so no YAML-position label may be shown. */
  native: boolean;
  /** Label for lists and graph nodes: the native path for native blocks, the YAML path otherwise. */
  label: string;
}
export interface StructureEdge {
  id: string;
  source: string | null;
  target: string;
  /** YAML `from` reference of a declared edge; null for a native edge that carries none. */
  reference: number | null;
  /** INPUT is only the recorded -1 of the first row. Unknown refs never become sequential edges. */
  state: 'connected' | 'input' | 'unresolved';
  reason?: string;
  /** Where the connection knowledge comes from; null when the record does not say (no style is guessed). */
  connection: ConnectionSource | null;
}
export interface StructureOptions {
  /** `representation` of the document (`yaml_genes` | `native_modules`). */
  representation?: unknown;
  /** `schema` of the document; `mbnas-structure/1` keeps the v1 meaning of `effective.repeats` (depth-scaled value). */
  schema?: unknown;
}
export const structureRecord = (value: unknown): Record<string, unknown> => value != null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finiteInt = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value);
const text = (value: unknown): string | null => typeof value === 'string' && value ? value : null;
const count = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
const connectionOf = (value: unknown): ConnectionSource | null => typeof value === 'string' && (CONNECTION_SOURCES as readonly string[]).includes(value) ? value as ConnectionSource : null;

/** `blocks[].catalog` of the server; null when the key is absent or not an object (an older document). */
export function blockCatalogOf(value: unknown): BlockCatalog | null {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) return null;
  const c = value as Record<string, unknown>;
  return { catalogKey: text(c.catalog_key), originFamily: text(c.origin_family), introducedIn: text(c.introduced_in), role: text(c.role),
    reference: text(c.reference), notes: text(c.notes), sourcePath: text(c.source_path) };
}

/** `catalog_summary` of a structure document; null when absent (older documents). Counts must be non-negative integers. */
export function catalogSummaryOf(report: unknown): CatalogSummary | null {
  const raw = structureRecord(structureRecord(report).catalog_summary);
  if (Object.keys(raw).length === 0) return null;
  const by: Record<string, number> = {};
  for (const [family, value] of Object.entries(structureRecord(raw.by_origin_family))) {
    if (typeof value === 'number' && Number.isInteger(value) && value > 0) by[family] = value;
  }
  const unknown = typeof raw.unknown === 'number' && Number.isInteger(raw.unknown) && raw.unknown > 0 ? raw.unknown : 0;
  return { byOriginFamily: by, unknown };
}

/** Short display names for the long family labels of the ETRI / PyTorch groups; every other family is shown as the server names it. */
export const ORIGIN_LABEL: Record<string, string> = {
  'ETRI mixed operator pool': 'ETRI mixed',
  'ETRI controlled hybrid': 'ETRI hybrid',
  'PyTorch torch.nn': 'PyTorch',
};
export const originLabel = (family: string | null): string => family == null ? '미상' : (ORIGIN_LABEL[family] ?? family);

export interface OriginShare { family: string | null; label: string; count: number; percent: number }
/** Origin distribution, largest first, '미상' last; percent = count / (known + unknown blocks). Empty when there are no blocks. */
export function originDistribution(summary: CatalogSummary | null): OriginShare[] {
  if (!summary) return [];
  const known = Object.entries(summary.byOriginFamily).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const total = known.reduce((sum, [, count]) => sum + count, 0) + summary.unknown;
  if (total <= 0) return [];
  const share = (family: string | null, count: number): OriginShare => ({ family, label: originLabel(family), count, percent: Math.round((count / total) * 100) });
  return [...known.map(([family, count]) => share(family, count)), ...(summary.unknown > 0 ? [share(null, summary.unknown)] : [])];
}
/** "YOLO11 60% · ETRI mixed 20% · 미상 20%" (a nonzero share that rounds to 0 is shown as "<1%"). */
export function formatOriginDistribution(summary: CatalogSummary | null): string {
  return originDistribution(summary).map((item) => `${item.label} ${item.percent === 0 ? '<1' : item.percent}%`).join(' · ');
}

/** Client-side lookup in the served static table (the web never hardcodes it). `nn.Upsample` -> `Upsample`; null = not catalogued. */
export function catalogEntryFor(table: BlockCatalogTable | null | undefined, module: string): BlockCatalogEntry | null {
  if (!table || !module) return null;
  const name = module.replace(/^(torch\.nn\.|nn\.)/, '');
  const key = name.includes('.') ? name.split('.').pop() ?? name : name;
  return table.entries[key] ?? null;
}

export function structureKind(module: string): StructureKind {
  if (/detect|detrhead|detectionhead|segment|pose|obb/i.test(module)) return 'Detection';
  if (/concat|patchmerge|(^|\.)(add|sum|multiply)$|shortcut/i.test(module)) return 'Merge';
  if (/transformer|attention|attn|c2psa|c2fpsa|a2c2f|aifi|vit|swin/i.test(module)) return 'Transformer';
  if (/conv|csp|bottleneck|c[1-3fks]|spp|rep|resnet|dw|focus|ghost|elan/i.test(module)) return 'CNN';
  return 'Other';
}

/** Three repeat counts kept apart (declared / scaled / observed). v1 documents have no `repeats_detail`: there
 *  `effective.repeats` was the depth-scaled value, so it is shown as "scale" and nothing is observed. */
export function repeatsDetailOf(record: Record<string, unknown>, effective: Record<string, unknown>, declaredFallback: unknown, schema: unknown): RepeatsDetail {
  const detail = structureRecord(record.repeats_detail);
  const none = { declaredBasis: null, observedBasis: null, group: null, unitIndex: null };
  if (Object.keys(detail).length > 0) {
    return { declared: count(detail.declared), scaled: count(detail.scaled), observed: count(detail.observed),
      declaredBasis: text(detail.declared_basis), observedBasis: text(detail.observed_basis), group: text(detail.group), unitIndex: count(detail.unit_index) };
  }
  const declared = count(structureRecord(record.declared).repeats) ?? count(declaredFallback);
  if (schema === 'mbnas-structure/2') return { declared, scaled: count(effective.repeats_scaled), observed: count(effective.repeats), ...none };
  return { declared, scaled: count(effective.repeats_scaled) ?? count(effective.repeats), observed: null, ...none };
}

/** One gene remains one YAML row. Internal runtime layers are a separate recorded list. */
export function structureNodes(blocks: readonly unknown[], modelKey: string, options: StructureOptions = {}): StructureNode[] {
  const scopeCounts = new Map<string, number>();
  return blocks.map((value, index) => {
    const record = structureRecord(value);
    const p = structureRecord(record.params);
    const position = finiteInt(record.position) ? record.position : finiteInt(record.gene_index) ? record.gene_index : index;
    const scope = text(record.section) ?? text(p.scope) ?? text(record.scope) ?? 'unrecorded';
    const local = scopeCounts.get(scope) ?? 0;
    scopeCounts.set(scope, local + 1);
    const name = text(p.module) ?? text(record.module) ?? text(record.name) ?? text(record.module_class) ?? text(record.native_path) ?? 'unrecorded';
    const effective = structureRecord(p.effective ?? record.effective);
    const layers = record.internal_layers ?? p.internal_layers ?? effective.internal_layers;
    const nativePath = text(record.native_path);
    // A block without gene is a native module: explicit representation wins, otherwise the gene_index:null + no YAML row shape.
    const native = options.representation === 'native_modules' ||
      (options.representation == null && 'gene_index' in record && record.gene_index == null && text(record.yaml_path) == null && p.from == null && record.from == null);
    const yamlPath = text(record.yaml_path) ?? text(p.yaml_path) ?? (scope === 'unrecorded' ? `row[${position}]` : `${scope}[${local}]`);
    const from = p.from ?? record.from;
    const repeats = p.repeats ?? record.repeats;
    return {
      id: text(record.block_id) ?? text(record.id) ?? `${modelKey}#${position}`,
      position,
      yamlPath,
      name, scope, kind: structureKind(name),
      from,
      repeats,
      args: p.args ?? record.args,
      effective,
      internalLayers: Array.isArray(layers) ? layers : [],
      record,
      stableId: text(record.stable_id),
      nativePath,
      moduleClass: text(record.module_class),
      wrapper: text(record.wrapper),
      wrappedModule: text(record.wrapped_module),
      sectionBasis: text(record.section_basis),
      connectionSource: connectionOf(record.connection_source),
      repeatsDetail: repeatsDetailOf(record, effective, repeats, options.schema),
      catalog: blockCatalogOf(record.catalog),
      native,
      label: native ? (nativePath ?? name) : yamlPath,
    };
  });
}

const isInputEndpoint = (value: unknown): boolean => value == null || value === -1 || (typeof value === 'string' && /^input$/i.test(value));

/** Resolve a report edge endpoint to a block id: block id, stable_id, native_path or the block's position number. */
function endpoint(value: unknown, nodes: readonly StructureNode[]): StructureNode | undefined {
  if (typeof value === 'string') return nodes.find((node) => node.id === value || node.stableId === value || node.nativePath === value);
  if (finiteInt(value)) return nodes.find((node) => node.position === value);
  return undefined;
}

/**
 * Edges. Declared edges come from the Ultralytics `from` semantics (a negative ref is relative to the current YAML row).
 * Blocks without `from` (native modules) get edges only from the report's own `edges`; nothing is ever chained or
 * invented for a block the report does not connect.
 */
export function structureEdges(nodes: readonly StructureNode[], reportEdges: readonly unknown[] = []): StructureEdge[] {
  const positions = new Map(nodes.map((node) => [node.position, node]));
  const edges: StructureEdge[] = [];
  for (const node of nodes) {
    const refs = Array.isArray(node.from) ? node.from : [node.from];
    refs.forEach((reference, slot) => {
      if (!finiteInt(reference)) return;
      const id = `${node.id}:in:${slot}`;
      const connection = node.connectionSource ?? 'yaml_from';
      if (node.position === 0 && reference === -1) {
        edges.push({ id, source: null, target: node.id, reference, state: 'input', connection });
        return;
      }
      const absolute = reference < 0 ? node.position + reference : reference;
      const source = positions.get(absolute);
      if (!source || absolute >= node.position) {
        edges.push({ id, source: null, target: node.id, reference, state: 'unresolved', reason: !source ? `Missing source row ${absolute}` : `Source row ${absolute} is not an earlier row`, connection });
        return;
      }
      edges.push({ id, source: source.id, target: node.id, reference, state: 'connected', connection });
    });
  }
  const declared = new Set(edges.map((edge) => edge.target));
  reportEdges.forEach((value, index) => {
    const item = structureRecord(value);
    const target = endpoint(item.target, nodes);
    // a block that already has declared from-edges is never connected twice
    if (target && declared.has(target.id)) return;
    const connection = connectionOf(item.connection_source) ?? target?.connectionSource ?? null;
    const reference = finiteInt(item.from_ref) ? item.from_ref : null;
    const id = `${target?.id ?? `edge-${index}`}:rep:${index}`;
    if (!target) { edges.push({ id, source: null, target: String(item.target ?? ''), reference, state: 'unresolved', reason: `Unknown target ${JSON.stringify(item.target ?? null)}`, connection }); return; }
    if (isInputEndpoint(item.source)) { edges.push({ id, source: null, target: target.id, reference, state: 'input', connection }); return; }
    const source = endpoint(item.source, nodes);
    if (!source) { edges.push({ id, source: null, target: target.id, reference, state: 'unresolved', reason: `Unknown source ${JSON.stringify(item.source)}`, connection }); return; }
    edges.push({ id, source: source.id, target: target.id, reference, state: 'connected', connection });
  });
  return edges;
}

/** Structural diff matches YAML positions, not a claimed genetic ancestry. */
export function compareStructure(left: readonly StructureNode[], right: readonly StructureNode[]): { position: number; change: 'added' | 'removed' | 'changed' | 'same' }[] {
  const a = new Map(left.map((node) => [node.position, node]));
  const b = new Map(right.map((node) => [node.position, node]));
  return [...new Set([...a.keys(), ...b.keys()])].sort((x, y) => x - y).map((position) => {
    const l = a.get(position), r = b.get(position);
    const config = (node: StructureNode): string => JSON.stringify([node.name, node.from, node.repeats, node.args, node.scope]);
    return { position, change: !l ? 'added' : !r ? 'removed' : config(l) !== config(r) ? 'changed' : 'same' };
  });
}

/** Same shape as the server `search.structure.compare_structures` result (parity-tested against it). */
export interface StructureDiff {
  comparison: 'structure';
  alignment: 'yaml_position';
  gene_ancestry_inferred: false;
  added: string[];
  removed: string[];
  changed: { position: number; left_id: string; right_id: string; fields: string[] }[];
  edges_added: [string, string, string][];
  edges_removed: [string, string, string][];
}
const DIFF_KEYS = ['module', 'from', 'repeats', 'args'] as const;
const same = (a: unknown, b: unknown): boolean => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
/** Python `str()` of the scalars an edge tuple holds (None, ints, strings). */
const pyStr = (value: unknown): string => value === null || value === undefined ? 'None' : String(value);
const tupleLess = (a: readonly string[], b: readonly string[]): number => {
  for (let i = 0; i < Math.min(a.length, b.length); i += 1) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  return a.length - b.length;
};
const edgeTuples = (doc: Record<string, unknown>): Map<string, [string, string, string]> => {
  const tuples = new Map<string, [string, string, string]>();
  for (const value of Array.isArray(doc.edges) ? doc.edges : []) {
    const e = structureRecord(value);
    const tuple: [string, string, string] = [pyStr(e.source), pyStr(e.target), pyStr(e.from_ref)];
    tuples.set(JSON.stringify(tuple), tuple);
  }
  return tuples;
};
const blockId = (block: unknown): string => String(structureRecord(block).id ?? '');

/**
 * Exact positional diff of two structure documents, mirroring `compare_structures` of the server: rows are matched
 * by index, a row is `changed` when module/from/repeats/args differ, and the edge sets are compared as
 * (source, target, from_ref) tuples of the report's own `edges`. It makes no gene-ancestry claim.
 */
export function diffStructureDocuments(left: unknown, right: unknown): StructureDiff {
  const l = structureRecord(left), r = structureRecord(right);
  const a = Array.isArray(l.blocks) ? l.blocks : [], b = Array.isArray(r.blocks) ? r.blocks : [];
  const added: string[] = [], removed: string[] = [], changed: StructureDiff['changed'] = [];
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    if (index >= a.length) added.push(blockId(b[index]));
    else if (index >= b.length) removed.push(blockId(a[index]));
    else {
      const x = structureRecord(a[index]), y = structureRecord(b[index]);
      const fields = DIFF_KEYS.filter((key) => !same(x[key], y[key]));
      if (fields.length > 0) changed.push({ position: index, left_id: blockId(x), right_id: blockId(y), fields });
    }
  }
  const le = edgeTuples(l), re = edgeTuples(r);
  const only = (from: Map<string, [string, string, string]>, other: Map<string, [string, string, string]>): [string, string, string][] =>
    [...from.entries()].filter(([key]) => !other.has(key)).map(([, tuple]) => tuple).sort(tupleLess);
  return { comparison: 'structure', alignment: 'yaml_position', gene_ancestry_inferred: false, added, removed, changed, edges_added: only(re, le), edges_removed: only(le, re) };
}

// ---- evidence levels, notices and hashes (CONTRACTS_v2 section 3) ---------------------------------------------------
/** Weakest first. `derived_from_forward` is a pass that rests on the forward alone, without a rule of its own. */
export const EVIDENCE_ORDER = ['declared', 'built', 'derived_from_forward', 'forward'] as const;
export type EvidenceLevel = typeof EVIDENCE_ORDER[number];

/** Lowest evidence level among the checks that actually ran (status PASS or FAIL); null when none carries a level. */
export function lowestEvidenceLevel(checks: unknown): EvidenceLevel | null {
  const levels = (Array.isArray(checks) ? checks : []).map(structureRecord)
    .filter((check) => check.status !== 'NOT_CHECKED')
    .map((check) => EVIDENCE_ORDER.indexOf(check.evidence_level as EvidenceLevel))
    .filter((rank) => rank >= 0);
  return levels.length ? EVIDENCE_ORDER[Math.min(...levels)] : null;
}

export const UNOBSERVED_CODES = ['GENE_REPRESENTATION_NOT_APPLICABLE', 'STRUCTURE_RUNTIME_UNAVAILABLE'] as const;
/** "구조 관측 없음 / 지원되지 않음": status UNVERIFIED with one of the two codes. This is not a FAIL. */
export function unobservedCodes(status: unknown, diagnostics: unknown): string[] {
  if (status !== 'UNVERIFIED') return [];
  const codes = (Array.isArray(diagnostics) ? diagnostics : []).map((item) => structureRecord(item).code);
  return UNOBSERVED_CODES.filter((code) => codes.includes(code));
}

/** Lower-case hex of a registered or canonical hash, without a `sha256:` prefix; null when it is not a SHA-256 hex digest. */
export function hexDigest(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const hex = value.trim().toLowerCase().replace(/^sha256:/, '');
  return /^[0-9a-f]{64}$/.test(hex) ? hex : null;
}

const readBytes = (blob: Blob): Promise<ArrayBuffer> => typeof blob.arrayBuffer === 'function'
  ? blob.arrayBuffer()
  : new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result as ArrayBuffer); reader.onerror = () => reject(reader.error); reader.readAsArrayBuffer(blob); });

/** SHA-256 of exactly the bytes of a Blob (the file the user receives). null when WebCrypto is unavailable (insecure context). */
export async function sha256OfBlob(blob: Blob): Promise<string | null> {
  const subtle = typeof globalThis.crypto !== 'undefined' ? globalThis.crypto.subtle : undefined;
  if (!subtle) return null;
  const bytes = await readBytes(blob);
  const digest = new Uint8Array(await subtle.digest('SHA-256', bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

export interface HashVerdict {
  /** SHA-256 of the downloaded bytes; null when it could not be computed. */
  file: string | null;
  /** sha256 registered with the artifact by the server (null when no artifact / audit record exists). */
  registered: string | null;
  /** canonical-dict hash `structure.yaml_hash` (a different hash definition than the file bytes). */
  canonical: string | null;
  /** file == registered. null when either is missing. A `false` is an ERROR (the same bytes must hash alike). */
  fileMatchesRegistered: boolean | null;
  /** file == canonical. A difference is EXPECTED: the two hashes are defined over different inputs. */
  fileEqualsCanonical: boolean | null;
}
export function hashVerdict(file: string | null, registered: unknown, canonical: unknown): HashVerdict {
  const reg = hexDigest(registered), can = hexDigest(canonical);
  return {
    file, registered: reg, canonical: can,
    fileMatchesRegistered: file && reg ? file === reg : null,
    fileEqualsCanonical: file && can ? file === can : null,
  };
}
