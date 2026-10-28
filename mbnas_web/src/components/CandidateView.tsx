/**
 * Candidate detail (used inside the modal and on the Lineage screen): identity, lineage with parent A/B
 * badges, block table with the public source kinds, architecture and checkpoint files by id + sha256 (contract 1.5.0:
 * never a host path), raw metrics, fitness weights, gate/cost/hashes, the four diagnostic images of one sample and the
 * "구성" tab (configuration revisions, diff, exact-byte download; hidden with a note when the server has no revision API).
 * Everything shown comes from server rows; nothing is invented. Missing values are an em dash.
 * Fitness and rewards are the server's values only: the client never recomputes them (DD-A8).
 */
import { useEffect, useMemo, useState } from 'react';
import type { ApiClient } from '../api/client';
import type { Artifact, Block, Candidate, CandidateCheckpoint, CandidateDetail, Run } from '../api/types';
import { num, rec } from '../api/types';
import { parseMatching, sampleRecall } from '../lib/artifacts';
import { DASH, fmtAny, fmtMb, fmtMs, fmtNum, fmtPct, fmtShape } from '../lib/format';
import { getIn } from '../lib/policy';
import { generationOnlyRow } from '../lib/generation';
import { isTrainedNotEvaluated, isUntrainedUnevaluated, TRAINED_NOT_EVALUATED, trainedNotEvaluatedTitle, UNTRAINED_UNEVALUATED } from '../lib/evaluation';
import { hashVerdict, sha256OfBlob, structureNodes, type HashVerdict } from '../lib/structure';
import { ArtifactDownload } from './ArtifactDownload';
import { ConfigRevisionsPanel, revisionsNote, useConfigRevisions } from './ConfigRevisions';
import { ModelComparison } from './ModelComparison';
import { ModelStructure, OriginDistribution, StructureSummary } from './ModelStructure';
import { TrainingPanel } from './TrainingPanel';
import { ArtifactImage } from './ArtifactImage';
import { DataTable, type Column } from './DataTable';
import { Badge, Banner, KV } from './ui';

export type LineageOp = 'crossover' | 'clone' | 'mutation' | 'random' | 'baseline' | 'final' | 'unknown';

/** crossover = 2 parents, clone = 1 parent, random / baseline = 0 parents (prefers the server's value). */
export function lineageOperation(c: Pick<Candidate, 'parents' | 'arm'> & { blocks?: readonly { source_kind?: string }[] }, serverOp?: unknown): { op: LineageOp; text: string } {
  const n = c.parents?.length ?? 0;
  const declared = typeof serverOp === 'string' ? serverOp.toLowerCase() : '';
  // final retraining: the single parent is the finalist source candidate (arm Final or server operation 'final')
  if (n === 1 && (c.arm === 'Final' || declared === 'final')) return { op: 'final', text: 'final (retrain of source candidate, 1 parent)' };
  if (n === 1 && declared === 'mutation') return { op: 'mutation', text: 'mutation (1 parent)' };
  if (n === 2) return { op: 'crossover', text: 'crossover (2 parents)' };
  if (n === 1 && (c.blocks ?? []).some((b) => b.source_kind === 'mutation')) return { op: 'mutation', text: 'mutation (1 parent)' };
  if (n === 1) return { op: 'clone', text: 'clone / elite carry-over (1 parent)' };
  if (n === 0 && (c.arm === 'IndependentRandom' || c.arm === 'GA' || declared === 'random')) return { op: 'random', text: 'random (0 parents)' };
  // a generated (evaluation-OFF) candidate without a detail: the random generator drew it (F14 Lineage rows)
  if (n === 0 && generationOnlyRow(c) && (rec(c).origin === 'random_generation' || rec(c).effective_method === 'RandomGeneration')) return { op: 'random', text: 'random (0 parents)' };
  if (n === 0 && (c.arm === 'Baseline' || declared === 'baseline')) return { op: 'baseline', text: 'baseline (0 parents)' };
  return { op: 'unknown', text: declared || 'unknown' };
}

export function parentRefs(c: Pick<Candidate, 'parents'>, all: readonly Candidate[]): { slot: 'A' | 'B' | `#${number}`; id: string; name: string | null }[] {
  return (c.parents ?? []).map((id, i) => {
    const hit = all.find((x) => x.candidate_id === id || x.id === id);
    const slot = i === 0 ? 'A' : i === 1 ? 'B' : (`#${i + 1}` as const);
    return { slot, id, name: hit?.name ?? null };
  });
}

const KIND_TEXT: Record<string, { text: string; tone: 'info' | 'warn' | 'err' | 'neutral'; cls: string }> = {
  parent_a: { text: 'A', tone: 'info', cls: 'b-parent-a' },
  parent_b: { text: 'B', tone: 'info', cls: 'b-parent-b' },
  baseline: { text: 'baseline', tone: 'neutral', cls: '' },
  random: { text: 'random', tone: 'neutral', cls: '' },
  mutation: { text: 'M (mutation)', tone: 'warn', cls: 'b-mut' },
  new: { text: 'new', tone: 'neutral', cls: '' },
};

export function SourceBadge({ kind }: { kind: string | undefined }): JSX.Element {
  const k = KIND_TEXT[kind ?? ''];
  if (!k) return <Badge>{kind ?? DASH}</Badge>;
  return (
    <Badge tone={k.tone} className={k.cls}>
      {k.text}
    </Badge>
  );
}

function compact(v: unknown): string {
  if (v === undefined || v === null) return DASH;
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s.length > 140 ? `${s.slice(0, 137)}...` : s;
}

/**
 * YAML row of a block (contract 1.5.0): a searched row carries it in `params` (from, repeats, module, args, scope), a
 * user-model row (explicit training of a user / baseline YAML, DD-A42) at the top level. The observed `effective` shapes are
 * left to the structure view.
 */
function yamlRow(b: Block): string {
  const p = b.params ?? {};
  const module = p.module ?? b.module, from = p.from ?? b.from, repeats = p.repeats ?? b.repeats, args = p.args ?? b.args;
  const parts = [module != null ? `module=${module}` : null, from != null ? `from=${JSON.stringify(from)}` : null,
    repeats != null ? `repeats=${repeats}` : null, args != null ? `args=${compact(args)}` : null, p.scope ? `scope=${p.scope}` : null];
  const text = parts.filter((x): x is string => x !== null).join(' · ');
  return text || DASH;
}

/**
 * C07-D1: the structure block id of each YAML position, derived with the same `structureNodes` call (same blocks, model key
 * and document options) as the ModelStructure view, so a block-table row opens exactly the block detail that its graph node
 * and gene button open. A native-module document has no YAML row <-> module correspondence, so it maps nothing.
 */
export function structureBlockIdsByPosition(structureBlocks: readonly unknown[], modelKey: string, structure: Record<string, unknown>): Map<number, string> {
  const ids = new Map<number, string>();
  for (const node of structureNodes(structureBlocks, modelKey, { representation: structure.representation, schema: structure.schema })) {
    if (!node.native && !ids.has(node.position)) ids.set(node.position, node.id);
  }
  return ids;
}

/**
 * Public block lineage (contract 1.5.0 / DD-A19): position, block, source kind (a repaired row is reported with the source it
 * was repaired from; the repair trace, old names and before/after are owner-only and never reach the console), operation,
 * parent block and the YAML row. C07-D1: the Pos cell of a row whose position is a node of the structure view is a button
 * that opens the same block detail as the graph node / gene button (`?block=`, `model_tab=structure`); a row without such a
 * node keeps a plain cell.
 */
function BlockTable({ blocks, structureIds, onOpenBlock }: { blocks: Block[]; structureIds?: ReadonlyMap<number, string>; onOpenBlock?: (id: string) => void }): JSX.Element {
  const posCell = (b: Block): React.ReactNode => {
    const id = typeof b.position === 'number' ? structureIds?.get(b.position) : undefined;
    if (id == null || !onOpenBlock) return fmtAny(b.position);
    return <button type="button" className="btn btn-sm" data-testid={`block-row-${b.position}`} data-block-id={id}
      aria-label={`${b.position} · ${b.name}: 구조·gene 탭에서 이 블록 상세 보기`} title={`${id}: 구조·gene 탭의 같은 블록 상세(그래프·gene 목록과 동일)`}
      onClick={() => onOpenBlock(id)}>{b.position}</button>;
  };
  const cols: Column<Block>[] = [
    { key: 'pos', header: 'Pos', cell: posCell, sort: (b) => b.position ?? null, num: true },
    { key: 'name', header: 'Block', cell: (b) => <span className="mono wrap-any">{b.name}</span>, sort: (b) => b.name },
    { key: 'kind', header: 'Source', cell: (b) => <SourceBadge kind={b.source_kind} />, sort: (b) => b.source_kind },
    { key: 'op', header: 'Operation', cell: (b) => b.operation ?? DASH, sort: (b) => b.operation },
    { key: 'mut', header: 'Mutated', cell: (b) => (b.mutated ? 'yes' : 'no') },
    { key: 'parent', header: 'parent_block_id', cell: (b) => <span className="mono wrap-any">{b.parent_block_id ?? DASH}</span> },
    { key: 'row', header: 'YAML row', cell: (b) => <span className="mono wrap-any">{yamlRow(b)}</span> },
  ];
  return <DataTable caption="Blocks (position, source kind, operation, YAML row; Pos opens the block detail)" columns={cols} rows={blocks} rowKey={(b) => `${b.position ?? ''}:${b.name}`} empty="No block records for this candidate." testId="block-table" />;
}

/** Checkpoint states in which the gateway may hold the bytes: PRESENT (contract 1.5.0) and READY (an artifact-style state). */
const DOWNLOADABLE_CHECKPOINT_STATES: ReadonlySet<string> = new Set(['PRESENT', 'READY']);

/**
 * The id a checkpoint row is downloaded by (DD-A51 c, DD-A55): the store `artifact_id` when the server filled it in, else the
 * checkpoint id itself for a PRESENT / READY row, because the gateway serves checkpoints by id on the same artifact route
 * (GET /runs/{run_id}/artifacts/{ck-id}). null for a row without either (PRUNED / MISSING and no store id): nothing to fetch.
 * Bytes that are not in the store yet answer 404 ARTIFACT_NOT_READY, shown as "아직 저장소에 없음" (ArtifactDownload).
 */
export function checkpointDownloadId(k: Pick<CandidateCheckpoint, 'id' | 'state' | 'artifact_id'>): { id: string; via: 'artifact_id' | 'checkpoint_id' } | null {
  if (typeof k.artifact_id === 'string' && k.artifact_id !== '') return { id: k.artifact_id, via: 'artifact_id' };
  if (typeof k.id === 'string' && k.id !== '' && DOWNLOADABLE_CHECKPOINT_STATES.has(String(k.state))) return { id: k.id, via: 'checkpoint_id' };
  return null;
}

/** Checkpoint rows of the candidate detail (1.5.0: id ck-… + sha256 + store artifact_id; no path). */
function CheckpointTable({ rows, client, runId, candidateId }: { rows: CandidateCheckpoint[]; client: ApiClient | null; runId: string; candidateId: string }): JSX.Element {
  const cols: Column<CandidateCheckpoint>[] = [
    { key: 'id', header: 'checkpoint id', cell: (k) => <span className="mono wrap-any" data-testid="checkpoint-id">{k.id}</span> },
    { key: 'kind', header: 'kind', cell: (k) => k.kind, sort: (k) => k.kind },
    { key: 'rung', header: 'rung / epoch', cell: (k) => `${k.rung ?? DASH} / ${fmtAny(k.epoch)}`, sort: (k) => k.epoch, num: true },
    { key: 'state', header: 'state', cell: (k) => <Badge tone={k.state === 'PRESENT' ? 'ok' : k.state === 'PRUNED' ? 'warn' : 'err'}>{k.state}</Badge> },
    { key: 'sha', header: 'sha256', cell: (k) => <span className="mono wrap-any" data-testid="checkpoint-sha256">{k.sha256}</span> },
    { key: 'bytes', header: 'bytes', cell: (k) => fmtAny(k.bytes), num: true },
    {
      key: 'art', header: 'artifact_id / 다운로드', cell: (k) => {
        const download = checkpointDownloadId(k);
        if (!download) return <span className="help" data-testid={`checkpoint-store-${k.id}`} data-via="none">저장소 미등록 (artifact_id 없음)</span>;
        return <>
          {download.via === 'artifact_id'
            ? <span className="mono wrap-any" data-testid={`checkpoint-store-${k.id}`} data-via="artifact_id">{download.id}</span>
            : <span className="help" data-testid={`checkpoint-store-${k.id}`} data-via="checkpoint_id" title="서버가 저장소 id(artifact_id)를 아직 기재하지 않았습니다. 게이트웨이는 같은 artifact 경로에서 checkpoint id로 제공합니다(DD-A51).">artifact_id 미기재 · checkpoint id로 요청</span>}
          <ArtifactDownload client={client} runId={runId} artifactId={download.id} recordedSha256={k.sha256} fileName={`${candidateId}-${k.id}.pt`} label="id로 다운로드" testId={`checkpoint-download-${k.id}`} />
        </>;
      },
    },
  ];
  return <DataTable caption="Checkpoints (id + sha256; files are fetched by artifact id, else by checkpoint id, never by path)" columns={cols} rows={rows} rowKey={(k) => k.id} empty="No checkpoint records." testId="checkpoint-table" />;
}

export function CheckpointState({ state }: { state: Candidate['checkpoint_state'] | undefined }): JSX.Element | null {
  if (state === 'PRUNED') return <Badge tone="warn" title="removed by the run retention policy (last_rung_end_only); the path no longer exists" className=""><span data-testid="checkpoint-state">pruned by retention</span></Badge>;
  if (state === 'MISSING') return <Badge tone="err" title="absent on disk without a retention record" className=""><span data-testid="checkpoint-state">missing on disk</span></Badge>;
  return null;
}

function fmtThreshold(v: unknown): string {
  return Array.isArray(v) ? `[${v.map((x) => fmtAny(x)).join(', ')}]` : fmtAny(v);
}

/** Gate decision: eligibility, reason code and every check; op ">=", "<=" or "in" (threshold is then a list). */
export function GateChecks({ gate }: { gate: Record<string, unknown> }): JSX.Element {
  const checks = Array.isArray(gate.checks) ? (gate.checks as unknown[]).map((x) => rec(x)) : [];
  const rest = Object.entries(gate).filter(([k]) => k !== 'checks');
  return (
    <div data-testid="gate-view">
      <div>{rest.map(([k, v]) => `${k}=${compact(v)}`).join('; ')}</div>
      {checks.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '4px 0 0' }}>
          {checks.map((ch, i) => (
            <li key={i} data-testid="gate-check">
              <Badge tone={ch.passed === true ? 'ok' : 'err'} className="">{ch.passed === true ? 'PASS' : 'FAIL'}</Badge> <strong>{fmtAny(ch.name)}</strong>:{' '}
              <span className="mono wrap-any">{`${fmtAny(ch.value)} ${fmtAny(ch.op)} ${fmtThreshold(ch.threshold)}`}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const KINDS = ['gt', 'tp', 'fn', 'fp'] as const;
const KIND_LABEL: Record<(typeof KINDS)[number], string> = { gt: 'GT (ground truth)', tp: 'TP (true positive)', fn: 'FN (missed)', fp: 'FP (false positive)' };

export function ArtifactPanel({ c, runId, client }: { c: Candidate; runId: string; client: ApiClient | null }): JSX.Element {
  const diag = (c.artifacts ?? []).filter((a) => (KINDS as readonly string[]).includes(a.kind) && (!a.evaluation_id || a.evaluation_id === c.id));
  const samples = useMemo(() => [...new Set(diag.map((a) => a.sample_id ?? ''))], [diag]);
  const [sample, setSample] = useState(samples[0] ?? '');
  useEffect(() => {
    if (!samples.includes(sample)) setSample(samples[0] ?? '');
  }, [samples, sample]);
  if (diag.length === 0) return <p className="muted">No GT/TP/FN/FP images are attached to this evaluation yet.</p>;
  const mine = diag.filter((a) => (a.sample_id ?? '') === sample);
  const byKind = new Map<string, Artifact>(mine.map((a) => [a.kind, a]));
  // all four images of one sample carry the same matching record; take the first one that has counts
  const m = mine.map((a) => parseMatching(a.matching)).find((x) => Object.keys(x.counts).length > 0) ?? parseMatching(undefined);
  const matchRec = rec(mine.find((a) => str(rec(a.matching).checkpoint_sha256))?.matching ?? mine[0]?.matching);
  const splitName = str(matchRec.split);
  const splitHash = mine.find((a) => a.split_hash)?.split_hash ?? c.split_hash;
  const evalCkpt = str(matchRec.checkpoint_sha256);
  const sr = sampleRecall(m.counts, m.sampleRecall);
  const c4 = m.counts;
  return (
    <div data-testid="artifact-panel">
      {samples.length > 1 && (
        <div className="field" style={{ maxWidth: 360, marginBottom: 8 }}>
          <label htmlFor="sample-select">Sample</label>
          <select id="sample-select" value={sample} onChange={(e) => setSample(e.target.value)}>
            {samples.map((s) => (
              <option key={s} value={s}>
                {s || '(unnamed)'}
              </option>
            ))}
          </select>
        </div>
      )}
      <KV
        rows={[
          ['sample_id', <span className="mono wrap-any" key="s">{sample || DASH}</span>],
          ['evaluation_id', <span className="mono wrap-any" key="e">{c.id}</span>],
          ['split', <span className="mono wrap-any" key="sp">{splitName ?? DASH}{splitHash ? ` (split_hash ${splitHash})` : ''}</span>],
          ['eval checkpoint sha256', <span className="mono wrap-any" key="ck" title="checkpoint that produced these images (artifact.matching)">{evalCkpt ?? DASH}</span>],
          ['training state sha256', <span className="mono wrap-any" key="ts" title="full training-state file of this evaluation (candidate.checkpoint_sha256)">{c.checkpoint_sha256 ?? DASH}</span>],
          ['TP / FP / FN / GT', `${fmtAny(c4.tp)} / ${fmtAny(c4.fp)} / ${fmtAny(c4.fn)} / ${fmtAny(c4.gt)}`],
          ['matching rule', m.rule ? `${m.rule} (IoU ≥ ${fmtAny(m.iou)}, conf ≥ ${fmtAny(m.conf)}, one-to-one)` : DASH],
          ['sample recall', sr === null ? 'N/A (no ground truth in this sample, or counts missing)' : `${(sr * 100).toFixed(1)} % (this sample only; not dataset recall ${fmtPct(c.recall)})`],
        ]}
      />
      <div className="artifact-grid" style={{ marginTop: 10 }}>
        {KINDS.map((k) => {
          const a = byKind.get(k);
          return (
            <div className="artifact-cell" key={k}>
              {a ? (
                <ArtifactImage artifact={a} runId={runId} client={client} label={KIND_LABEL[k]} />
              ) : (
                <div className="artifact-state" role="status" data-state="MISSING" data-testid={`artifact-${k}`}>
                  <strong>{KIND_LABEL[k]}</strong>
                  <span>MISSING: not generated for this sample.</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v : null;
}

/** Server audit shape (candidates.audit_json): label, config_estimate.*, built.*, search_space_variant, fallback_reason. */
export function AuditPanel({ audit }: { audit: Record<string, unknown> | undefined | null }): JSX.Element {
  if (!audit || Object.keys(audit).length === 0) {
    return (
      <p className="muted" data-testid="candidate-audit-missing">
        No architecture audit returned by the server for this candidate.
      </p>
    );
  }
  const est = rec(audit.config_estimate);
  const built = rec(audit.built);
  const head = rec(built.detect_head);
  const label = str(audit.label) ?? str(built.label) ?? str(est.label);
  const estB = str(est.backbone_family);
  const builtB = str(built.backbone_family);
  const match = estB && builtB ? (estB === builtB ? 'MATCH' : 'MISMATCH') : null;
  const variant = str(audit.search_space_variant);
  const fallback = str(audit.fallback_reason);
  return (
    <div data-testid="candidate-audit">
      <KV
        rows={[
          ['audit label', <strong key="l">{label ?? DASH}</strong>],
          [
            'backbone family',
            <span key="b" data-testid="audit-backbone">
              estimate {estB ?? DASH} / built {builtB ?? DASH}{' '}
              {match && (
                <Badge tone={match === 'MATCH' ? 'ok' : 'warn'} className="" title="config estimate vs built model backbone family">
                  <span data-testid="audit-backbone-match">{match}</span>
                </Badge>
              )}
            </span>,
          ],
          ['neck family (built)', <span key="n" data-testid="audit-neck">{str(built.neck_family) ?? DASH}</span>],
          ['detect head conv modules', <span key="d" data-testid="audit-detect-convs">{fmtAny(head.conv_modules)}</span>],
          ['search_space_variant', <span key="v" data-testid="audit-variant" className="mono">{variant ?? DASH}</span>],
          ['fallback_reason', <span key="f" data-testid="audit-fallback">{fallback ? <Badge tone="warn">{fallback}</Badge> : 'none'}</span>],
          ...(built.full_transformer === true ? ([['model class', <Badge key="t" tone="info">full Transformer</Badge>]] as [string, JSX.Element][]) : []),
        ]}
      />
    </div>
  );
}

/** Hashes of the file the user just downloaded: file bytes (WebCrypto) vs the server-registered file sha256 vs the canonical-dict yaml_hash. */
export function DownloadHashes({ info }: { info: { verdict: HashVerdict; bytes: number; error?: string } }): JSX.Element {
  const { verdict } = info;
  return (
    <div data-testid="download-hash" className="stack" aria-label="다운로드 파일 해시">
      <KV rows={[
        ['파일 sha256 (다운로드한 바이트)', <span className="mono wrap-any" key="f" data-testid="hash-file">{verdict.file ?? DASH}</span>],
        ['등록 sha256 (서버 artifact, 파일 바이트 해시)', <span className="mono wrap-any" key="r" data-testid="hash-registered">{verdict.registered ?? '등록값 없음'}</span>],
        ['canonical yaml_hash (구조 dict 해시)', <span className="mono wrap-any" key="c" data-testid="hash-canonical">{verdict.canonical ?? '기록 없음'}</span>],
        ['크기', `${info.bytes} bytes`],
      ]} />
      {verdict.file === null && <Banner tone="warn"><span data-testid="hash-unavailable">{info.error ? `해시 계산 실패: ${info.error}` : '이 브라우저 컨텍스트에서는 WebCrypto(crypto.subtle)를 쓸 수 없어 파일 sha256을 계산하지 못했습니다(HTTPS 또는 localhost에서만 제공). 값을 추정하지 않습니다.'}</span></Banner>}
      {verdict.fileMatchesRegistered === true && <Banner tone="ok"><span data-testid="hash-registered-verdict">파일 sha256이 서버에 등록된 artifact sha256과 일치합니다.</span></Banner>}
      {verdict.fileMatchesRegistered === false && <Banner tone="err"><span data-testid="hash-registered-verdict">오류: 파일 sha256이 서버에 등록된 artifact sha256과 다릅니다. 같은 파일 바이트라면 해시가 같아야 하므로 다운로드 또는 서버 등록값에 문제가 있습니다.</span></Banner>}
      {verdict.file !== null && verdict.registered === null && <p className="help" data-testid="hash-registered-verdict">서버에 등록된 sha256이 없어 파일 해시를 대조하지 못했습니다.</p>}
      {verdict.fileEqualsCanonical !== null && <p className="help" data-testid="hash-canonical-verdict">{verdict.fileEqualsCanonical ? 'canonical yaml_hash와도 같은 값입니다.' : 'canonical yaml_hash와 다른 것은 정상입니다.'}</p>}
      <p className="help">두 해시는 정의가 다릅니다. 파일 sha256은 저장된 YAML 파일 바이트의 해시이고, canonical yaml_hash는 정규화한 구성 dict의 해시(구조 동일성 기준)입니다. 따라서 파일 sha256과 canonical yaml_hash는 달라도 오류가 아니며, 파일 sha256과 등록 sha256이 다른 경우만 오류입니다.</p>
    </div>
  );
}

export interface CandidateViewProps {
  c: Candidate;
  run: Run | null;
  runId: string;
  all: readonly Candidate[];
  client: ApiClient | null;
  onTrainingCreated?: (id: string) => void;
}

export function CandidateView({ c, run, runId, all, client, onTrainingCreated }: CandidateViewProps): JSX.Element {
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [wantedTab, setTab] = useState(() => typeof window === 'undefined' ? 'all' : new URL(window.location.href).searchParams.get('model_tab') ?? 'all');
  const chooseTab = (value: string): void => { setTab(value); const url = new URL(window.location.href); url.searchParams.set('model_tab', value); window.history.replaceState(null, '', url); };
  // C07-D1: a block-table row asks the structure view to select its block (each click a new seq)
  const [blockRequest, setBlockRequest] = useState<{ id: string; seq: number } | null>(null);
  useEffect(() => setBlockRequest(null), [c.candidate_id]);
  const openBlock = (id: string): void => {
    chooseTab('structure');
    setBlockRequest((previous) => ({ id, seq: (previous?.seq ?? 0) + 1 }));
  };
  const [checking, setChecking] = useState(false);
  const [download, setDownload] = useState<{ verdict: HashVerdict; bytes: number; error?: string } | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setDetail(null);
    setDetailErr(null);
    if (!client || !c.candidate_id) return;
    client
      .candidateDetail(runId, c.candidate_id)
      .then((d) => live && setDetail(d))
      .catch((e: unknown) => live && setDetailErr(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [client, runId, c.candidate_id]);

  const generated = generationOnlyRow(c);
  // a generated candidate shown through this view carries GeneratedCandidate fields the evaluation row type does not declare
  const row = rec(c);
  const revisions = useConfigRevisions(client, runId, c.candidate_id);
  const revisionNote = revisionsNote(revisions.state);
  const configTab = revisions.state.status === 'ok' || revisions.state.status === 'loading' || revisions.state.status === 'error';
  // a remembered "구성" tab (model_tab=config) falls back to the full view while the server offers no revisions
  const tab = wantedTab === 'config' && !configTab ? 'all' : wantedTab;
  const structure = rec(detail?.structure);
  const provenance = rec(rec(detail?.audit).training_source);
  const provenanceKind = typeof provenance.kind === 'string' ? provenance.kind : null;
  const modelOrigin = provenanceKind ?? detail?.origin ?? str(row.origin) ?? c.arm;
  const originLabel = generated ? 'RandomGeneration' : ({ baseline: 'Baseline', user: '사용자 지정 모델', search: '탐색 모델', generated: '평가 OFF 생성 모델의 학습' } as Record<string, string>)[modelOrigin] ?? modelOrigin;
  const trainingSourceKind = generated ? 'generated' : provenanceKind && provenanceKind !== 'baseline' ? 'search' : c.arm === 'Baseline' ? 'baseline' : 'search';
  const baselineId = typeof provenance.baseline_id === 'string' ? provenance.baseline_id : c.architecture_id ?? c.name;
  const structureStatus = detail?.structure_status ?? row.structure_status ?? structure.status;
  const blocks = c.blocks?.length ? c.blocks : (detail?.blocks ?? []);
  const structureBlocks = Array.isArray(structure.blocks) ? structure.blocks : blocks;
  const structureIds = useMemo(() => structureBlockIdsByPosition(structureBlocks, c.candidate_id, structure),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the document options are the only fields of `structure` read
    [structureBlocks, c.candidate_id, structure.representation, structure.schema]);
  const generation = rec(rec(detail?.audit).generation);
  // DD-5: the row's performance_status may only be known from the audit of the opened detail
  const trainedNotEvaluated = isTrainedNotEvaluated({ ...c, performance_status: (c as unknown as Record<string, unknown>).performance_status ?? rec(rec(detail?.audit).performance).status }, run);
  // DD-A23: training_status (contract 1.5.0) of the row or the detail; a 1.4.x generated candidate without one is UNTRAINED
  const trainingRow = { ...row, training_status: row.training_status ?? detail?.training_status, evaluation_status: row.evaluation_status ?? detail?.evaluation_status };
  const untrained = !trainedNotEvaluated && isUntrainedUnevaluated(trainingRow);
  // the executed architecture file by id + sha256 (1.5.0): the 'architecture' artifact, else audit.execution_configuration {artifact_id, sha256}
  const archArtifact = [...(c.artifacts ?? []), ...(detail?.artifacts ?? [])]
    .find((artifact) => artifact.kind === 'architecture' && (!rec(artifact.metadata).candidate_id || rec(artifact.metadata).candidate_id === c.candidate_id));
  const execution = rec(rec(detail?.audit).execution_configuration);
  const archId = archArtifact?.id ?? str(execution.artifact_id);
  const registeredSha = archArtifact?.sha256 ?? str(execution.sha256);
  const checkpoints = detail?.checkpoints ?? [];
  async function downloadYaml(): Promise<void> {
    if (!detail?.architecture_yaml) return;
    const blob = new Blob([detail.architecture_yaml], { type: 'application/yaml' });
    let sha: string | null = null, error: string | undefined;
    try { sha = await sha256OfBlob(blob); } catch (e) { error = e instanceof Error ? e.message : String(e); }
    setDownload({ verdict: hashVerdict(sha, registeredSha, structure.yaml_hash ?? rec(structure.configuration).yaml_hash), bytes: blob.size, error });
    if (typeof URL.createObjectURL !== 'function') return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `${c.candidate_id}.yaml`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }
  async function checkStructure(): Promise<void> {
    if (!client || checking) return;
    setChecking(true); setDetailErr(null);
    try {
      await client.json(`/api/v1/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(c.candidate_id)}/structure-check`, { method: 'POST', timeoutMs: 120000 });
      setDetail(await client.candidateDetail(runId, c.candidate_id));
    } catch (e) { setDetailErr(e instanceof Error ? e.message : String(e)); }
    finally { setChecking(false); }
  }
  const lineage = rec(detail?.lineage);
  const op = lineageOperation(c, lineage.operation);
  const parents = parentRefs(c, all);
  const weights = rec(c.fitness_weights);
  const gate = rec(c.gate);
  const cost = { ...rec(detail?.cost), ...rec(c.cost) };
  const raw = rec(c.raw_metrics);
  const otherImages = [...new Map([...(c.artifacts ?? []), ...(detail?.artifacts ?? [])].filter((artifact) =>
    !['gt', 'tp', 'fn', 'fp'].includes(artifact.kind) &&
    ((artifact.mime_type ?? '').startsWith('image/') || ['pr_curve', 'confusion_matrix'].includes(artifact.kind)) &&
    (!rec(artifact.metadata).candidate_id || rec(artifact.metadata).candidate_id === c.candidate_id) &&
    (!artifact.evaluation_id || artifact.evaluation_id === c.id)
  ).map((artifact) => [artifact.id ?? artifact.url, artifact])).values()];
  const sameCandidate = (detail?.evaluations ?? all.filter((x) => x.candidate_id === c.candidate_id)) as Candidate[];
  const evalCols: Column<Candidate>[] = [
    { key: 'id', header: 'evaluation_id', cell: (x) => <span className="mono wrap-any">{x.id}</span> },
    { key: 'rung', header: 'Rung', cell: (x) => x.rung, sort: (x) => x.rung },
    { key: 'ep', header: 'Epochs', cell: (x) => fmtAny(x.epoch_target_total ?? x.epochs), num: true },
    { key: 'dev', header: 'Device', cell: (x) => `${x.device} ${x.precision}` },
    { key: 'ap', header: 'AP', cell: (x) => fmtPct(x.ap), num: true },
    { key: 'fit', header: 'Fitness', cell: (x) => fmtNum(x.fitness), num: true },
    { key: 'st', header: 'Status', cell: (x) => x.status },
  ];

  return (
    <div className="stack" data-testid="candidate-view">
      <div className="row">
        <Badge tone="info">{originLabel}</Badge>
        <Badge>{c.family}</Badge>
        {trainedNotEvaluated
          ? <Badge tone="info" title={trainedNotEvaluatedTitle(c, run)}><span data-testid="trained-not-evaluated">{TRAINED_NOT_EVALUATED}</span></Badge>
          : untrained
            ? <Badge tone="warn" className="b-nowrap" title={`training_status ${String(trainingRow.training_status ?? 'not reported (generated candidate: generation never trains)')} · evaluation_status ${String(trainingRow.evaluation_status ?? 'NOT_EVALUATED')}: no weights were trained and no metric was measured`}><span data-testid="untrained-unevaluated">{UNTRAINED_UNEVALUATED}</span></Badge>
            : <Badge tone={c.status === 'EVALUATED' ? 'ok' : 'warn'}>{generated ? '미평가 · NOT_EVALUATED' : c.status}</Badge>}
        {!generated && !trainedNotEvaluated && !untrained && detail?.evaluation_status === 'NOT_EVALUATED' && <Badge tone="warn">성능 미평가 · NOT_EVALUATED</Badge>}
        {c.candidate_status && c.candidate_status !== c.status && <Badge title="mutable candidate_status">{c.candidate_status}</Badge>}
        {c.reason_code && <Badge tone="err">{c.reason_code}</Badge>}
      </div>

      <section aria-label="모델 구조 요약" data-testid="candidate-structure-summary">
        <StructureSummary report={detail ? structure : null} status={structureStatus} badges={detail?.evaluation_status ? <Badge tone={detail.evaluation_status === 'EVALUATED' ? 'ok' : 'warn'}><span data-testid="summary-evaluation-status">평가: {detail.evaluation_status}</span></Badge> : undefined}
          extra={generated || Object.keys(generation).length > 0 ? [
            ['생성 seed / 순서', <span key="go" data-testid="summary-generation">seed {fmtAny(generation.seed ?? c.search_seed)} / generation_order {fmtAny(row.generation_order ?? generation.index)}{generation.attempt != null ? ` (시도 ${fmtAny(generation.attempt)})` : ''}</span>],
            ['생성 방식 / 실행', `${[generation.requested_method ?? row.requested_method].flat().map((m) => fmtAny(m)).join(', ')} → ${fmtAny(generation.effective_method)} / ${fmtAny(generation.execution)} · duplicate_scope ${fmtAny(generation.duplicate_scope)}`],
          ] : []} />
        <OriginDistribution report={detail ? structure : null} testId="candidate-origin-distribution" />
      </section>
      <div className="row" aria-label="모델 상세 보기">
        {[['all', '전체'], ['structure', '구조·gene'], ...(configTab ? [['config', '구성']] : []), ['training', '학습'], ['images', '실제 이미지']].map(([key, title]) => <button key={key} className="btn btn-sm" type="button" aria-pressed={tab === key} onClick={() => chooseTab(key)} data-testid={`model-tab-${key}`}>{title}</button>)}
      </div>
      {revisionNote && <p className="help" data-testid="config-tab-note" data-state={revisions.state.status}>{revisionNote}</p>}
      <section hidden={tab !== 'all'} aria-labelledby="cv-id">
        <h3 id="cv-id">Identity</h3>
        <KV
          rows={[
            ['name', c.name],
            ['candidate_id', <span className="mono wrap-any" key="1">{c.candidate_id}</span>],
            [generated ? 'candidate record (evaluation 없음)' : c.status === 'EVALUATED' && detail?.evaluation_status !== 'NOT_EVALUATED' ? 'evaluation_id' : 'record_id (성능 평가 기록 아님)', <span className="mono wrap-any" key="2">{c.id}</span>],
            ['architecture_hash', <span className="mono wrap-any" key="3">{c.architecture_hash ?? detail?.architecture_hash ?? DASH}</span>],
            ['architecture_id', <span className="mono wrap-any" key="3b">{c.architecture_id ?? DASH}</span>],
            ['저장 arm / seed', `${c.arm} / search ${fmtAny(c.search_seed)} / training ${fmtAny(c.training_seed)}`],
            ...(generated ? [['요청 / 실제 생성 방식', `${fmtAny(row.requested_method)} / RandomGeneration`] as [string, string]] : []),
            ['generation / rung', `gen ${c.generation} / ${c.rung}`],
            ['epochs', `${c.epochs} of target ${fmtAny(c.epoch_target_total)} [epochs]`],
            ['attempt / job', `${fmtAny(c.attempt)} / ${fmtAny(c.job_id)}`],
            ['revision', `${fmtAny(c.revision)}${c.supersedes_evaluation_id ? ` (supersedes ${c.supersedes_evaluation_id})` : ''}`],
          ]}
        />
      </section>

      <section hidden={tab !== 'all' && tab !== 'structure'} aria-labelledby="cv-structure">
        <h3 id="cv-structure">모델 구조 · Gene / 실제 연결</h3>
        <KV rows={[
          ['출처', modelOrigin], ['클래스 수 / 입력 크기 / scale', `${fmtAny(structure.class_count)} / ${fmtAny(structure.input_size)} / ${fmtAny(structure.scale)}`],
          ['yaml_hash', <span className="mono wrap-any" key="yh">{fmtAny(structure.yaml_hash)}</span>],
          ['architecture_hash', <span className="mono wrap-any" key="ah">{fmtAny(structure.architecture_hash ?? c.architecture_hash)}</span>],
          ['signature_hash', <span className="mono wrap-any" key="sh">{fmtAny(structure.signature_hash)}</span>],
          ['실제 parameters / 계산량', `${fmtAny(structure.parameters)} / ${fmtAny(structure.flops)}`],
          ['구성 종류 / 증거', `${fmtAny(rec(structure.configuration).kind)} / ${fmtAny(structure.evidence_source)}`],
        ]} />
        <button className="btn btn-sm" type="button" disabled={!client || checking} onClick={() => void checkStructure()} data-testid="structure-check">{checking ? 'CPU 구조 검사 중…' : 'CPU 구조 검사 실행'}</button>
        {detailErr && <p className="field-err" role="alert">{detailErr}</p>}
        <ModelStructure blocks={structureBlocks} modelKey={c.candidate_id} status={typeof structureStatus === 'string' ? structureStatus : undefined} diagnostics={Array.isArray(structure.diagnostics) ? structure.diagnostics : undefined} report={detail ? structure : null} selectRequest={blockRequest} />
        <ModelComparison c={c} blocks={structureBlocks} all={all} client={client} runId={runId} structure={detail ? structure : null} datasetYaml={str(getIn(run?.resolved_protocol, 'console.paths.dataset_yaml'))} />
      </section>
      <div hidden={tab !== 'all' && tab !== 'training'}><TrainingPanel client={client} protocol={(run?.resolved_protocol ?? {}) as Record<string, unknown>} source={{ kind: trainingSourceKind, run_id: runId, candidate_id: c.candidate_id, ...(trainingSourceKind === 'baseline' ? { baseline_id: baselineId } : {}) }} onCreated={onTrainingCreated} /></div>
      <section hidden={tab !== 'all'} aria-labelledby="cv-lin">
        <h3 id="cv-lin">Lineage</h3>
        <p data-testid="lineage-op">
          Operation: <strong>{op.text}</strong>
        </p>
        {parents.length === 0 ? (
          <p className="muted">{c.arm === 'Baseline' ? `유전 계보: 해당 없음 · ${modelOrigin} 원래 구조` : `No parents (${op.op === 'unknown' ? 'unknown operation' : op.op}).`}</p>
        ) : (
          <ul className="tag-list" style={{ listStyle: 'none', padding: 0, margin: '0 0 8px' }}>
            {parents.map((p) => (
              <li key={p.id} data-testid="lineage-parent">
                <Badge tone="info" className={p.slot === 'A' ? 'b-parent-a' : p.slot === 'B' ? 'b-parent-b' : ''}>
                  {op.op === 'final' ? 'Source candidate (finalist)' : `Parent ${p.slot}`}
                </Badge>{' '}
                <span className="mono wrap-any">{p.name ?? p.id}</span>
                {p.name && <span className="muted mono wrap-any"> ({p.id})</span>}
              </li>
            ))}
          </ul>
        )}
        {detailErr && <p className="help">Candidate detail endpoint: {detailErr}</p>}
        <BlockTable blocks={blocks} structureIds={structureIds} onOpenBlock={openBlock} />
      </section>

      {configTab && (
        <section hidden={tab !== 'all' && tab !== 'config'} aria-labelledby="cv-config" data-testid="config-tab">
          <h3 id="cv-config">구성 · 개정 이력 / diff / 원문 바이트</h3>
          {revisions.state.status === 'loading' && <p className="muted" role="status">구성 개정 이력을 불러오는 중…</p>}
          {revisions.state.status === 'error' && <Banner tone="err"><span data-testid="config-revisions-error">구성 개정 이력 조회 실패: {revisions.state.message}</span> <button className="btn btn-sm" type="button" onClick={revisions.reload}>다시 시도</button></Banner>}
          {revisions.state.status === 'ok' && <ConfigRevisionsPanel client={client} runId={runId} candidateId={c.candidate_id} revisions={revisions.state.revisions} />}
        </section>
      )}

      <section hidden={tab !== 'all'} aria-labelledby="cv-path">
        <h3 id="cv-path">Architecture and checkpoint (id + sha256)</h3>
        <p className="help">파일은 서버 경로가 아니라 artifact / checkpoint id와 sha256으로 식별하고, 내려받을 때도 id로 요청합니다(계약 1.5.0).</p>
        <KV
          rows={[
            ['architecture artifact_id', <span key="a"><span className="mono wrap-any" data-testid="architecture-artifact-id">{archId ?? '미등록'}</span>{archId && <ArtifactDownload client={client} runId={runId} artifactId={archId} recordedSha256={registeredSha} fileName={`${c.candidate_id}-architecture.yaml`} label="실행 구성 파일 id로 다운로드" testId="architecture-download" />}</span>],
            ['architecture sha256', <span className="mono wrap-any" key="as" data-testid="architecture-sha256">{registeredSha ?? DASH}</span>],
            ['checkpoint_sha256 (평가 행)', <span key="b"><span className="mono wrap-any" data-testid="row-checkpoint-sha256">{c.checkpoint_sha256 ?? DASH}</span> <CheckpointState state={c.checkpoint_state} /></span>],
          ]}
        />
        {checkpoints.length > 0 && <CheckpointTable rows={checkpoints} client={client} runId={runId} candidateId={c.candidate_id} />}
        {detail?.architecture_yaml && (
          <details>
            <summary>실제 보유 구성 YAML · architecture (상세 문서의 표시본·canonical 텍스트)</summary>
            <button className="btn btn-sm" type="button" onClick={() => void downloadYaml()} data-testid="download-model-yaml">화면에 표시한 구성 YAML 다운로드 (표시본·canonical 텍스트)</button>
            <p className="help">표시본은 상세 문서가 담은 텍스트입니다. 서버에 저장된 원문 바이트는 “구성” 탭의 개정별 “원문 바이트 다운로드”로 받습니다.</p>
            {download && <DownloadHashes info={download} />}
            <pre className="log" tabIndex={0}>{detail.architecture_yaml}</pre>
          </details>
        )}
      </section>

      <section hidden={tab !== 'all'} aria-labelledby="cv-audit">
        <h3 id="cv-audit">Architecture audit (config estimate vs built model)</h3>
        {c.arm === 'Baseline' ? <p className="muted" data-testid="candidate-audit-baseline">baseline (no search audit)</p> : detail ? <AuditPanel audit={detail.audit} /> : <p className="muted">{detailErr ? 'Audit unavailable (candidate detail failed).' : 'Loading audit...'}</p>}
      </section>

      {generated && <Banner tone="info">성능 미평가: mAP·recall·latency·fitness는 null입니다. 구조에서 실제 계산된 값만 별도로 표시합니다. 파라미터 수: {fmtNum(num(structure.parameters) ?? num(row.parameters), 0)}</Banner>}
      {!generated && <section hidden={tab !== 'all'} aria-labelledby="cv-met">
        <h3 id="cv-met">Metrics (raw, as measured)</h3>
        <KV
          rows={[
            ['AP@[.50:.95]', fmtPct(c.ap)],
            ['Recall', fmtPct(c.recall)],
            ['Latency p50 / p95 / mean', `${fmtMs(c.latency_statistics?.p50_ms ?? c.latency_ms)} / ${fmtMs(c.latency_statistics?.p95_ms)} / ${fmtMs(c.latency_statistics?.mean_ms)}`],
            ['Weights-only size', fmtMb(c.size_mb)],
            ['Latency reward r_T / size reward r_S', `${fmtNum(c.rewards?.latency_reward)} / ${fmtNum(c.rewards?.size_reward)}`],
            ['Fitness (server)', <strong key="f">{fmtNum(c.fitness, 4)}</strong>],
            ['Fitness weights applied', Object.keys(weights).length ? Object.entries(weights).map(([k, v]) => `${k}=${fmtAny(v)}`).join(', ') : DASH],
            ['Device / precision / batch', `${c.device} / ${c.precision} / ${fmtAny(c.batch)}`],
            ['Input shape requested / resolved', `${fmtShape(c.requested_input_shape)} / ${fmtShape(c.resolved_input_shape)} [px]`],
            ['Latency source / scope', `${c.latency_source} / ${c.latency_scope}`],
            ['Postprocess runtime', <span key="pp" className="mono" data-testid="postprocess-runtime" title="NMS implementation timed in forward_and_postprocess; rows with different values are not comparable latencies">{c.postprocess_runtime ?? DASH}</span>],
            ...Object.entries(raw).map(([k, v]): [string, string] => [`raw.${k}`, fmtAny(v)]),
          ]}
        />
      </section>

      }
      <section hidden={tab !== 'all'} aria-labelledby="cv-gate">
        <h3 id="cv-gate">Gate, cost, hashes</h3>
        <KV
          rows={[
            ['Gate decision', Object.keys(gate).length ? <GateChecks key="g" gate={gate} /> : DASH],
            ['reason_code', c.reason_code ?? DASH],
            ['Cost', Object.keys(cost).length ? Object.entries(cost).map(([k, v]) => `${k}=${compact(v)}`).join('; ') : DASH],
            ['protocol_hash', <span className="mono wrap-any" key="p">{c.protocol_hash ?? DASH}</span>],
            ['objective_hash', <span className="mono wrap-any" key="o">{c.objective_hash}</span>],
            ['evaluation_hash', <span className="mono wrap-any" key="e">{c.evaluation_hash}</span>],
            ['split_hash', <span className="mono wrap-any" key="s">{c.split_hash}</span>],
            ['comparison_group', <span className="mono wrap-any" key="g">{c.comparison_group}</span>],
            ['namespace / mode', `${c.namespace ?? DASH} / ${c.execution_mode ?? DASH}`],
            ['metric definitions', Object.keys(rec(c.metric_definitions)).length ? Object.entries(rec(c.metric_definitions)).map(([k, v]) => `${k}=${compact(v)}`).join('; ') : DASH],
          ]}
        />
      </section>

      {sameCandidate.length > 1 && (
        <section hidden={tab !== 'all'} aria-labelledby="cv-ev">
          <h3 id="cv-ev">All evaluations of this candidate</h3>
          <DataTable caption="Immutable evaluation rows (rung / device history)" columns={evalCols} rows={sameCandidate} rowKey={(x) => x.id} selectedKey={c.id} />
        </section>
      )}

      <section hidden={tab !== 'all' && tab !== 'images'} aria-labelledby="cv-art">
        <h3 id="cv-art">Diagnostic images: GT / TP / FN / FP (same sample, same evaluation)</h3>
        <ArtifactPanel c={c} runId={runId} client={client} />
        <section aria-label="실제 학습·검증·예측 이미지" data-testid="native-artifact-gallery">
          <h4>실제 학습·검증·예측 산출물</h4>
          {getIn(run?.resolved_protocol, 'console.extra.training.validation_enabled') === false && <Banner tone="warn">validation 미실행: native plot의 val 항목·0은 측정값이 아니며 성능 수치로 해석하지 않습니다. 원본 산출물은 그대로 표시합니다.</Banner>}
          <p className="help mono wrap-any">run {runId} · candidate {c.candidate_id}</p>
          {otherImages.length ? <div className="artifact-grid">{otherImages.map((artifact) => {
            const metadata = rec(artifact.metadata);
            return <div className="artifact-cell" key={artifact.id ?? artifact.url}>
              <ArtifactImage artifact={artifact} runId={runId} client={client} label={`${String(metadata.filename ?? artifact.kind)} · ${String(metadata.category ?? 'image')}`} />
              <p className="help wrap-any">stage {fmtAny(metadata.stage)} · batch {fmtAny(metadata.batch_index)} · epoch {metadata.epoch == null ? '미기록' : fmtAny(metadata.epoch)} · checkpoint {metadata.checkpoint_id == null ? '미기록' : fmtAny(metadata.checkpoint_id)}</p>
              {typeof metadata.identifier_note === 'string' && <p className="help">{metadata.identifier_note}</p>}
              <details><summary>실제 산출물 식별 정보</summary><KV rows={[
                ['artifact ID', artifact.id ?? DASH], ['sha256', artifact.sha256 ?? DASH], ['MIME', artifact.mime_type ?? DASH],
                ['run / candidate', `${runId} / ${String(metadata.candidate_id ?? c.candidate_id)}`],
                ['stage epoch 범위', `${fmtAny(metadata.stage_epoch_start)} → ${fmtAny(metadata.stage_epoch_target)}`],
              ]} /></details>
            </div>;
          })}</div> : <p className="muted">이 후보·평가에 등록된 학습 이미지·예측 이미지·confusion matrix가 없습니다.</p>}
        </section>
      </section>
    </div>
  );
}
