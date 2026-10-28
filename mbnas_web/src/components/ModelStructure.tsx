import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { catalogSummaryOf, EVIDENCE_ORDER, formatOriginDistribution, lowestEvidenceLevel, originDistribution, structureEdges, structureNodes, structureRecord, unobservedCodes, type BlockCatalog, type ConnectionSource, type StructureEdge, type StructureKind, type StructureNode } from '../lib/structure';
import { Badge, Banner, KV } from './ui';

const COLORS: Record<StructureKind, string> = { CNN: '#216184', Transformer: '#70529b', Merge: '#93661c', Detection: '#287659', Other: '#5f6876' };
const fmt = (value: unknown): string => value == null ? '미기록' : typeof value === 'string' ? value : JSON.stringify(value);
const DASH = '—';

const LINE_TEXT: Record<ConnectionSource, string> = {
  yaml_from: 'yaml_from · 실선 (YAML from 선언)',
  native_fixed: 'native_fixed · 점선 (코드에 고정된 흐름)',
  observed: 'observed · 2중선 (forward hook로만 확인)',
};

/** Stroke of one connection. `observed` is drawn as two strokes (wide colour line + thin background line). */
function lineStyle(connection: ConnectionSource | null): { dash?: string; double: boolean } {
  return connection === 'native_fixed' ? { dash: '6 4', double: false } : connection === 'observed' ? { double: true } : { double: false };
}

function LegendLine({ connection }: { connection: ConnectionSource }): JSX.Element {
  const style = lineStyle(connection);
  return <svg width="44" height="12" aria-hidden="true" focusable="false">
    <line x1="2" y1="6" x2="42" y2="6" stroke="#1d6a94" strokeWidth={style.double ? 5 : 2} strokeDasharray={style.dash} />
    {style.double && <line x1="2" y1="6" x2="42" y2="6" stroke="var(--surface, white)" strokeWidth="1.5" />}
  </svg>;
}

export interface ModelStructureProps {
  blocks: readonly unknown[];
  modelKey: string;
  status?: unknown;
  diagnostics?: unknown;
  /** The whole mbnas-structure document: lets the view read representation, report edges, edges_unobserved and checks. */
  report?: Record<string, unknown> | null;
  /**
   * C07-D1: a selection asked for outside the view (the candidate block table). Each new `seq` selects block `id` exactly as
   * a click on its graph node or gene button does (same detail, URL `?block=`) and then moves focus to the block detail.
   * An id that is not a node of this document is ignored.
   */
  selectRequest?: { id: string; seq: number } | null;
}

const inputSource = (value: unknown): string => value == null || value === -1 || (typeof value === 'string' && /^input$/i.test(value)) ? 'INPUT (모델 입력)' : typeof value === 'number' ? `row ${value}` : String(value);

const UNKNOWN_ORIGIN = '출처 미상';

/** "구성 블록 출처 분포: YOLO11 60% · ETRI mixed 20% · 미상 20%" from the document's `catalog_summary`. Renders nothing for a document without one. */
export function OriginDistribution({ report, testId = 'origin-distribution' }: { report: Record<string, unknown> | null | undefined; testId?: string }): JSX.Element | null {
  const summary = catalogSummaryOf(report);
  const items = originDistribution(summary);
  if (items.length === 0) return null;
  const total = items.reduce((sum, item) => sum + item.count, 0);
  return <p className="help" data-testid={testId} title={items.map((item) => `${item.family ?? UNKNOWN_ORIGIN}: ${item.count}/${total}`).join(' · ')} style={{ margin: '6px 0' }}>
    구성 블록 출처 분포: <strong>{formatOriginDistribution(summary)}</strong>
  </p>;
}

/** Header for any structure document: backend, representation, evidence source, weakest evidence, hashes, status. */
export function StructureSummary({ report, status, extra = [], badges }: { report: Record<string, unknown> | null | undefined; status?: unknown; extra?: [string, React.ReactNode][]; badges?: React.ReactNode }): JSX.Element {
  const doc = structureRecord(report);
  const schema = typeof doc.schema === 'string' ? doc.schema : null;
  const checks = Array.isArray(doc.checks) ? doc.checks : [];
  const lowest = lowestEvidenceLevel(checks);
  const notRun = checks.filter((check) => structureRecord(check).status === 'NOT_CHECKED').length;
  const shown = status ?? doc.status;
  const observation = structureRecord(doc.observation);
  const v1 = schema != null && schema !== 'mbnas-structure/2';
  return <div data-testid="structure-summary">
    <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
      <Badge tone="info"><span data-testid="summary-backend">backend: {doc.backend == null ? (v1 ? 'v1 문서(미기록)' : '미기록') : String(doc.backend)}</span></Badge>
      <Badge tone="info"><span data-testid="summary-representation">표현: {doc.representation == null ? (v1 ? 'v1 문서(미기록)' : '미기록') : String(doc.representation)}</span></Badge>
      <Badge><span data-testid="summary-evidence-source">증거: {fmt(doc.evidence_source)}</span></Badge>
      <Badge tone={lowest === 'forward' ? 'ok' : lowest ? 'warn' : 'neutral'} title={`검사별 evidence_level 중 실행된(PASS/FAIL) 검사의 최저 수준. 순서: ${EVIDENCE_ORDER.join(' < ')}`}>
        <span data-testid="summary-evidence-level">최저 증거 수준: {lowest ?? '기록 없음'}{notRun > 0 ? ` · 미검사 ${notRun}` : ''}</span>
      </Badge>
      <Badge tone={shown === 'PASS' ? 'ok' : shown === 'FAIL' ? 'err' : 'warn'}><span data-testid="summary-structure-status">구조: {String(shown ?? 'UNVERIFIED')}</span></Badge>
      {badges}
    </div>
    <div style={{ marginTop: 8 }}><KV rows={[
      ['schema', schema ?? '미기록'],
      ...extra,
      ['관측 환경', Object.keys(observation).length ? `${fmt(observation.method)} · ${fmt(observation.device)} · imgsz ${fmt(observation.imgsz)} · ${fmt(observation.checked_at)}` : '관측 기록 없음 (선언만 있거나 검사가 실행되지 않음)'],
    ]} /></div>
  </div>;
}

/** Recorded genes / native modules and actual connections. This view never infers runtime measurements. */
export function ModelStructure({ blocks, modelKey, status, diagnostics, report, selectRequest }: ModelStructureProps): JSX.Element {
  const doc = structureRecord(report);
  const representation = doc.representation;
  const native = representation === 'native_modules';
  const shownStatus = status ?? doc.status;
  const shownDiagnostics = diagnostics ?? doc.diagnostics;
  const nodes = useMemo(() => structureNodes(blocks, modelKey, { representation, schema: doc.schema }), [blocks, modelKey, representation, doc.schema]);
  const reportEdges = Array.isArray(doc.edges) ? doc.edges : undefined;
  const edges = useMemo(() => structureEdges(nodes, reportEdges), [nodes, reportEdges]);
  const [selected, setSelected] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null;
    return new URL(window.location.href).searchParams.get('block');
  });
  const [zoom, setZoom] = useState(1);
  const [focusDetail, setFocusDetail] = useState(false);
  const sectionRef = useRef<HTMLElement | null>(null);
  const uid = useId().replace(/:/g, '');
  const marker = `structure-arrow-${uid}`;
  const current = nodes.find((node) => node.id === selected) ?? nodes[0];
  const indexById = new Map(nodes.map((node, index) => [node.id, index]));
  const scopes = [...new Set(nodes.map((node) => node.scope))];
  const warnings = Array.isArray(shownDiagnostics) ? shownDiagnostics : [];
  const unobservedNotice = unobservedCodes(shownStatus, shownDiagnostics);
  const declaredOnly = doc.evidence_source === 'declared_configuration';
  const unobserved = Array.isArray(doc.edges_unobserved) ? doc.edges_unobserved.map(structureRecord) : null;
  const checks = Array.isArray(doc.checks) ? doc.checks.map(structureRecord) : [];
  const connections = new Set(edges.map((edge) => edge.connection));

  useEffect(() => {
    setSelected(typeof window === 'undefined' ? null : new URL(window.location.href).searchParams.get('block'));
  }, [modelKey]);
  const select = (id: string): void => {
    setSelected(id);
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      url.searchParams.set('block', id);
      window.history.replaceState(window.history.state, '', url);
    }
  };
  // C07-D1: only a new request (seq) selects; the same select() as a node / gene click, then focus moves to the detail
  // heading (the table button that asked is hidden by the tab switch, so focus must not stay on it).
  const requestSeq = selectRequest?.seq;
  useEffect(() => {
    if (!selectRequest || !nodes.some((node) => node.id === selectRequest.id)) return;
    select(selectRequest.id);
    setFocusDetail(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs per request; nodes and select are read at that moment
  }, [requestSeq]);
  useEffect(() => {
    if (!focusDetail) return;
    setFocusDetail(false);
    sectionRef.current?.querySelector<HTMLElement>('[data-testid="selected-block"] h4')?.focus();
  }, [focusDetail]);

  const notices = <>
    {unobservedNotice.length > 0 && <Banner tone="info" role="status"><span data-testid="structure-unobserved-notice"><strong>구조 관측 없음 / 지원되지 않음</strong> (status UNVERIFIED · {unobservedNotice.join(', ')}). 이 모델 또는 이 실행 환경에서는 요청한 구조를 관측할 수 없습니다. 검사가 실패한 것(FAIL)이 아니며, 관측하지 못한 값은 비어 있습니다.</span></Banner>}
    {shownStatus === 'FAIL' && <Banner tone="err"><span data-testid="structure-fail-notice"><strong>구조 검사 실패 (FAIL)</strong>: 구성이 실제 CPU 빌드·forward 또는 규칙 검사에서 거부되었습니다. 아래 진단을 확인하세요.</span></Banner>}
    {declaredOnly && <Banner tone="info"><span data-testid="structure-declared-only-notice">선언된 구성만 표시합니다. 저장된 구조 검사 기록이 없어 관측값(repeats 관측, 입력 shape, 내부 layer)은 비어 있습니다.</span></Banner>}
  </>;
  if (nodes.length === 0) {
    return <div data-testid="model-structure-wrap">{notices}<p className="muted" data-testid="model-structure-empty">이 모델의 구조 기록이 없습니다. 구성 파일 또는 서버 구조 검사 결과를 확인하세요.</p></div>;
  }

  const relevant = (source: string | null, target: string): boolean => source === current?.id || target === current?.id;
  const unit = native ? 'native 모듈' : 'gene';
  const drawn = edges.filter((edge) => edge.state !== 'unresolved');
  return (
    <section data-testid="model-structure" aria-label="모델 구조 상세" ref={sectionRef}>
      {notices}
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>{native ? '모델 구조 · native 모듈 / 연결' : '모델 구조 · gene / 실제 연결'}</h3>
        <span>구조 검사: <strong data-testid="structure-status">{fmt(shownStatus)}</strong></span>
      </div>
      <OriginDistribution report={report} testId="model-origin-distribution" />
      <p className="help">{native
        ? 'native 모델에는 YAML gene이 없습니다. 블록은 모델 객체의 module 경로이며, 연결선은 기록된 연결(코드 고정 또는 hook 관측)만 그립니다. 관측하지 못한 구간은 선을 만들지 않고 아래 "관측되지 않은 구간"에 나열합니다.'
        : 'Gene은 YAML 구성 행입니다. 반복 블록의 내부 layer는 기록된 경우 아래에서 펼쳐 볼 수 있습니다. 연결선은 실제 from 참조이며 gene 순서만으로 연결하지 않습니다.'}</p>
      <div className="row" aria-label="블록 유형 범례" style={{ gap: 8 }}>
        {(Object.keys(COLORS) as StructureKind[]).map((kind) => <span key={kind} style={{ color: COLORS[kind], border: `1px solid ${COLORS[kind]}`, borderRadius: 4, padding: '2px 6px' }}>{kind}</span>)}
      </div>
      <ul className="row" aria-label="연결 출처 범례" data-testid="connection-legend" style={{ gap: 14, listStyle: 'none', padding: 0, margin: '8px 0 0', flexWrap: 'wrap' }}>
        {(Object.keys(LINE_TEXT) as ConnectionSource[]).map((connection) => <li key={connection} data-testid={`legend-${connection}`} style={{ display: 'flex', alignItems: 'center', gap: 6 }}><LegendLine connection={connection} /><small>{LINE_TEXT[connection]}</small></li>)}
        {connections.has(null) && <li data-testid="legend-unrecorded"><small>가는 실선 · 연결 출처 미기록</small></li>}
      </ul>

      {scopes.map((scope) => {
        const inScope = nodes.filter((node) => node.scope === scope);
        const bases = [...new Set(inScope.map((node) => node.sectionBasis).filter((basis): basis is string => !!basis))];
        return <details key={scope} open style={{ marginTop: 12 }}>
          <summary><strong>{scope === 'unrecorded' ? '소속 미기록' : scope}</strong> · {inScope.length} {unit}{bases.length > 0 && <small data-testid={`basis-${scope}`}> · section_basis: {bases.join(' / ')}</small>}</summary>
          <div role="group" aria-label={`${scope} ${unit}`} style={{ display: 'flex', gap: 6, overflowX: 'auto', padding: '10px 2px' }}>
            {inScope.map((node) => <button
              key={node.id} type="button" onClick={() => select(node.id)} aria-pressed={current?.id === node.id}
              data-testid={`gene-${node.position}`} title={`${node.id} / ${node.label}`}
              style={{ flex: '0 0 148px', textAlign: 'left', color: 'var(--text)', background: current?.id === node.id ? 'var(--surface-2, #e8eef3)' : 'var(--surface, white)', border: `${current?.id === node.id ? 3 : 1}px solid ${COLORS[node.kind]}`, borderRadius: 6, padding: 8 }}
            ><small>#{node.position} · {node.kind}</small><br /><strong>{node.name}</strong><br /><small className="mono wrap-any">{node.label}</small></button>)}
          </div>
        </details>;
      })}

      <div className="row" style={{ justifyContent: 'space-between', marginTop: 14 }}>
        <h4>{native ? '기록된 연결' : '실제 from 연결'}</h4>
        <label>확대 <select aria-label="구조 그래프 확대" value={zoom} onChange={(event) => setZoom(Number(event.target.value))}>
          <option value={0.75}>75%</option><option value={1}>100%</option><option value={1.25}>125%</option><option value={1.5}>150%</option>
        </select></label>
      </div>
      <div role="region" aria-label="모델 연결 그래프" tabIndex={0} style={{ overflow: 'auto', maxHeight: 500, border: '1px solid var(--border, #d8dee5)', borderRadius: 6 }}>
        {/* role="group", not "img": an img's children are presentational, but the block nodes below are focusable buttons
            (axe nested-interactive, V4-B). The group keeps the name; Tab / Enter / Space still reach and select each node. */}
        <svg role="group" aria-label={native ? '기록된 native 연결 그래프; 블록 버튼을 선택하면 상세 표시' : '실제 YAML from 연결 그래프; 블록 버튼을 선택하면 상세 표시'} viewBox={`0 0 780 ${nodes.length * 70 + 60}`} width={780 * zoom} height={(nodes.length * 70 + 60) * zoom} data-testid="structure-graph">
          <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" /></marker></defs>
          <text x="285" y="18" fill="currentColor" fontSize="12">모델 입력</text>
          {drawn.map((edge: StructureEdge) => {
            const target = indexById.get(edge.target);
            if (target == null) return null;
            const source = edge.source == null ? -1 : indexById.get(edge.source);
            if (source == null) return null;
            const sy = source < 0 ? 22 : 50 + source * 70 + 20;
            const ty = 50 + target * 70;
            const highlighted = relevant(edge.source, edge.target);
            const lane = 560 + (Math.abs(target - source) % 6) * 29;
            const d = source < 0 ? `M 330 ${sy} L 330 ${ty - 22}` : `M 505 ${sy} C ${lane} ${sy}, ${lane} ${ty}, 505 ${ty}`;
            const style = lineStyle(edge.connection);
            const color = highlighted ? '#1d6a94' : '#a4adb9';
            const width = highlighted ? 2.5 : 1.1;
            return <g key={edge.id} data-source={edge.source ?? 'INPUT'} data-target={edge.target} data-from={edge.reference ?? ''} data-connection={edge.connection ?? ''}>
              <path d={d} fill="none" stroke={color} strokeWidth={style.double ? width + 3 : width} strokeDasharray={style.dash} markerEnd={`url(#${marker})`} />
              {style.double && <path d={d} fill="none" stroke="var(--surface, white)" strokeWidth={1.2} />}
              {highlighted && <text x={source < 0 ? 344 : lane - 32} y={ty - 4} fontSize="11" fill="currentColor">{edge.reference != null ? `from ${edge.reference}` : edge.connection ?? ''}</text>}
            </g>;
          })}
          {nodes.map((node, index) => <g key={node.id} transform={`translate(160 ${28 + index * 70})`} role="button" tabIndex={0} aria-label={`선택 ${node.position} ${node.name}`} aria-pressed={current?.id === node.id} onClick={() => select(node.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(node.id); } }} style={{ cursor: 'pointer' }}>
            <rect width="345" height="44" rx="5" fill="var(--surface, white)" stroke={COLORS[node.kind]} strokeWidth={current?.id === node.id ? 3 : 1} />
            <rect width="5" height="44" rx="2" fill={COLORS[node.kind]} />
            <text x="12" y="18" fill="currentColor" fontSize="13">#{node.position} {node.name.length > 34 ? `${node.name.slice(0, 31)}…` : node.name}</text>
            <text x="12" y="34" fill="currentColor" fontSize="11">{node.native
              ? `${node.scope} · ${(node.nativePath ?? '경로 미기록').slice(0, 40)}`
              : `${node.scope} · from ${fmt(node.from)} · repeats ${fmt(node.repeats)}`}</text>
          </g>)}
        </svg>
      </div>
      {edges.some((edge) => edge.state === 'unresolved') && <p role="status" className="field-err">해결할 수 없는 연결: {edges.filter((edge) => edge.state === 'unresolved').map((edge) => `${edge.target}${edge.reference != null ? ` from ${edge.reference}` : ''}: ${edge.reason}`).join('; ')}</p>}
      {nodes.some((node) => node.from == null && !node.native) && <p className="help">from이 기록되지 않은 블록은 연결선을 생성하지 않았습니다.</p>}
      {native && edges.length === 0 && <p className="help" data-testid="no-recorded-edges">이 문서에는 기록된 연결이 없습니다. 선을 추정해 그리지 않았습니다.</p>}

      {(native || (unobserved && unobserved.length > 0)) && <section aria-label="관측되지 않은 구간" data-testid="edges-unobserved">
        <h4>관측되지 않은 구간</h4>
        {unobserved && unobserved.length > 0
          ? <><p className="help">아래 module은 hook로 입력·출력을 보지 못해 연결선이 없습니다. 선이 없다는 것이 연결이 없다는 뜻은 아닙니다.</p>
            <ul>{unobserved.map((item, index) => <li key={index} data-testid="edge-unobserved-item"><span className="mono wrap-any">{fmt(item.module_path)}</span> · {fmt(item.reason)}</li>)}</ul></>
          : <p className="help">보고서에 기록된 관측되지 않은 구간이 없습니다.</p>}
      </section>}

      {current && <BlockDetail node={current} />}
      {checks.length > 0 && <details open data-testid="structure-checks"><summary>검사별 증거 수준 ({checks.length})</summary>
        <div className="table-wrap" role="region" aria-label="구조 검사 항목, 결과, evidence_level" tabIndex={0} style={{ marginTop: 10 }}><table className="data"><caption className="sr-only">구조 검사 항목, 결과, evidence_level</caption>
          <thead><tr><th scope="col">검사</th><th scope="col">결과</th><th scope="col">evidence_level</th><th scope="col">검토 행/쌍</th></tr></thead>
          <tbody>{checks.map((check, index) => <tr key={index}><td>{fmt(check.name)}</td><td>{fmt(check.status)}</td><td data-testid={`check-level-${String(check.name)}`}>{check.evidence_level == null ? '기록 없음' : String(check.evidence_level)}</td><td>{check.rows_checked == null ? DASH : String(check.rows_checked)}</td></tr>)}</tbody>
        </table></div>
        <p className="help">derived_from_forward: 개별 규칙 없이 forward 성공만으로 통과한 항목입니다.</p>
      </details>}
      {warnings.length > 0 && <details open><summary>구조 검사 진단 ({warnings.length})</summary><ul>{warnings.map((value, index) => {
        const diag = structureRecord(value);
        return <li key={index}><strong>{fmt(diag.code)}</strong> · {fmt(diag.path ?? diag.position)}: {fmt(diag.cause ?? diag.message)}{diag.suggestion != null && <> · 수정 방향: {fmt(diag.suggestion)}</>}{diag.evidence_level != null && <> · 증거 {fmt(diag.evidence_level)}</>}</li>;
      })}</ul></details>}
    </section>
  );
}

/** DD-38 rows of the selected block. A null field reads "출처 미상": the catalog never fills in a guess. */
function BlockOrigin({ catalog }: { catalog: BlockCatalog }): JSX.Element {
  const known = (value: string | null): string => value ?? UNKNOWN_ORIGIN;
  const url = catalog.reference != null && /^https?:\/\//.test(catalog.reference);
  return <dl data-testid="block-origin" aria-label="블록 설계 출처" style={{ display: 'grid', gridTemplateColumns: 'minmax(110px, 180px) minmax(0, 1fr)', gap: '6px 12px', marginTop: 10 }}>
    <div style={{ display: 'contents' }}><dt>출처 계열</dt><dd data-testid="origin-family" style={{ margin: 0 }}>{known(catalog.originFamily)}</dd></div>
    <div style={{ display: 'contents' }}><dt>도입 버전</dt><dd data-testid="origin-introduced" style={{ margin: 0 }}>{known(catalog.introducedIn)}</dd></div>
    <div style={{ display: 'contents' }}><dt>역할</dt><dd data-testid="origin-role" style={{ margin: 0 }}>{known(catalog.role)}</dd></div>
    <div style={{ display: 'contents' }}><dt>참고</dt><dd data-testid="origin-reference" className="wrap-any" style={{ margin: 0 }}>
      {catalog.reference == null ? UNKNOWN_ORIGIN : url ? <a href={catalog.reference} target="_blank" rel="noreferrer noopener">{catalog.reference}</a> : <span className="mono">{catalog.reference}</span>}
    </dd></div>
    <div style={{ display: 'contents' }}><dt>설치본 소스</dt><dd data-testid="origin-source" className="mono wrap-any" style={{ margin: 0 }}>{catalog.sourcePath ?? '미확인 (구조 검사를 실행하면 설치본에서 확인해 기록합니다)'}</dd></div>
    {catalog.notes != null && <div style={{ display: 'contents' }}><dt>카탈로그 메모</dt><dd data-testid="origin-notes" className="wrap-any" style={{ margin: 0 }}>{catalog.notes}</dd></div>}
  </dl>;
}

function BlockDetail({ node: current }: { node: StructureNode }): JSX.Element {
  const effective = current.effective;
  const inputs = Array.isArray(effective.inputs) ? effective.inputs.map(structureRecord) : [];
  const local = Array.isArray(current.record.checks_local) ? current.record.checks_local.map(structureRecord) : [];
  const isHead = effective.head_strides != null || effective.nl != null || effective.end2end != null;
  const layers = current.internalLayers.map(structureRecord);
  const detail = current.repeatsDetail;
  const shared = layers.filter((layer) => layer.shared === true).length;
  const rows: [string, unknown][] = [
    ['block ID', current.id], ['stable_id', current.stableId],
    ...(current.native ? [['native path', current.nativePath] as [string, unknown]] : [['YAML 위치', current.yamlPath] as [string, unknown], ['native path', current.nativePath] as [string, unknown]]),
    ['소속 / 유형', `${current.scope} / ${current.kind}`], ['section_basis', current.sectionBasis],
    [current.native ? 'module (경로 이름)' : 'module (선언)', current.name], ['module_class (관측)', current.moduleClass],
    ['wrapper', current.wrapper], ['wrapped_module', current.wrappedModule],
    ['connection_source', current.connectionSource],
    ...(current.native ? [] : [['from', current.from] as [string, unknown], ['args', current.args] as [string, unknown]]),
    ['선언 channels', structureRecord(current.record.declared).channels],
    ['입력 channels (합)', effective.c_in], ['출력 channels', effective.c_out],
    ['입력 shape', effective.input_shape ?? (effective.h_in != null ? [effective.h_in, effective.w_in] : null)],
    ['출력 shape', effective.output_shape ?? (effective.h_out != null ? [effective.h_out, effective.w_out] : null)],
    ['stride', effective.stride], ['실제 parameters', effective.params], ['계산량 (기록값)', effective.flops],
    // contract 1.5.0 / DD-A19: the repair trace (reason, old name, before/after) is owner-only and never in a public document
    ['부모 출처', current.record.source], ['부모 block ID', current.record.parent_block_id], ['operation', current.record.operation],
  ];
  return <section aria-label="선택 블록 상세" data-testid="selected-block" style={{ marginTop: 14 }}>
    <h4 tabIndex={-1}>선택 블록 #{current.position} · {current.name}</h4>
    <dl style={{ display: 'grid', gridTemplateColumns: 'minmax(110px, 180px) minmax(0, 1fr)', gap: '6px 12px' }}>
      {rows.map(([label, value]) => <div key={label} style={{ display: 'contents' }}><dt>{label}</dt><dd className="mono wrap-any" style={{ margin: 0 }}>{fmt(value)}</dd></div>)}
    </dl>

    {current.catalog && <BlockOrigin catalog={current.catalog} />}

    <div className="table-wrap" role="region" aria-label="반복 수: 선언 · scale 공식 · 관측" tabIndex={0} style={{ marginTop: 10 }}><table className="data" data-testid="repeats-columns"><caption>반복 수: 선언 · scale 공식 · 관측 (서로 대체하지 않음)</caption>
      <thead><tr><th scope="col">선언 (D)</th><th scope="col">scale (R, depth 공식 파생)</th><th scope="col">관측 (O)</th></tr></thead>
      <tbody><tr>
        <td data-testid="repeats-declared">{detail.declared == null ? '미기록' : detail.declared}</td>
        <td data-testid="repeats-scaled">{detail.scaled == null ? (current.native ? '해당 없음' : '미기록') : detail.scaled}</td>
        <td data-testid="repeats-observed">{detail.observed == null ? '미관측' : detail.observed}</td>
      </tr></tbody>
    </table></div>
    {detail.observed == null && <p className="help">관측 반복 수가 없으면 공식 값(scale)으로 대신 채우지 않습니다.</p>}
    {(detail.declaredBasis || detail.observedBasis || detail.group) && <p className="help" data-testid="repeats-basis">
      {detail.declaredBasis && <>선언 기준: <span className="mono">{detail.declaredBasis}</span>. </>}
      {detail.observedBasis && <>관측 기준: <span className="mono">{detail.observedBasis}</span>. </>}
      {detail.group && <>반복 컨테이너: <span className="mono">{detail.group}</span>{detail.unitIndex != null ? ` (${detail.unitIndex}번째)` : ''}.</>}</p>}

    {inputs.length > 0 && <div className="table-wrap" role="region" aria-label="입력 (소스별 실제 전달된 tensor)" tabIndex={0} style={{ marginTop: 10 }}><table className="data" data-testid="block-inputs"><caption>입력 (소스별 실제 전달된 tensor)</caption>
      <thead><tr><th scope="col">소스</th><th scope="col">c</th><th scope="col">h</th><th scope="col">w</th></tr></thead>
      <tbody>{inputs.map((input, index) => <tr key={index}><td className="mono">{inputSource(input.source)}{typeof input.source_path === 'string' && input.source_path ? ` · ${input.source_path}` : ''}</td><td>{fmt(input.c)}</td><td>{fmt(input.h)}</td><td>{fmt(input.w)}</td></tr>)}</tbody>
    </table></div>}
    {isHead && <KV rows={[
      ['head_strides', <span key="s" data-testid="head-strides" className="mono">{fmt(effective.head_strides)}</span>], ['nl', <span key="n" data-testid="head-nl" className="mono">{fmt(effective.nl)}</span>],
      ['end2end', <span key="e" data-testid="head-end2end" className="mono">{fmt(effective.end2end)}</span>], ['nc', fmt(effective.nc)], ['outputs (shape, batch 제외)', <span key="o" className="mono wrap-any">{fmt(effective.outputs)}</span>],
    ]} />}

    {layers.length > 0 ? <details data-testid="internal-layers"><summary>기록된 내부 layer ({layers.length}{shared > 0 ? `, 공유 ${shared}` : ''})</summary>
      <div className="table-wrap" role="region" aria-label="내부 layer 경로, module, parameter, 공유 여부" tabIndex={0} style={{ marginTop: 10 }}><table className="data"><caption className="sr-only">내부 layer 경로, module, parameter, 공유 여부</caption>
        <thead><tr><th scope="col">path</th><th scope="col">module</th><th scope="col">parameters</th><th scope="col">공유</th></tr></thead>
        <tbody>{layers.map((layer, index) => <tr key={index} data-shared={layer.shared === true ? 'true' : layer.shared === false ? 'false' : 'unknown'}>
          <td className="mono wrap-any">{fmt(layer.path)}</td><td>{fmt(layer.module)}</td>
          <td className="mono" title={fmt(layer.parameters)}>{Array.isArray(layer.parameters) ? `${layer.parameters.length} tensor` : '미기록'}</td>
          <td>{layer.shared === true ? <Badge tone="warn" title="이 module 인스턴스는 여러 블록이 함께 가진 객체입니다. 모든 블록에 나타나며 parameter 합산 시 한 번만 세어야 합니다.">공유</Badge> : layer.shared === false ? '아니오' : '미기록'}</td>
        </tr>)}</tbody>
      </table></div>
      <p className="help">공유 표시된 layer(예: Conv의 기본 활성화)는 같은 인스턴스가 여러 블록에 나타납니다.</p>
    </details> : <p className="help">내부 layer 대응은 기록되지 않았습니다. YAML 반복 수로 내부 layer를 추정하지 않습니다.</p>}

    {local.length > 0 && <ul data-testid="checks-local" aria-label="블록 단위 진단">{local.map((item, index) => <li key={index}><strong>{fmt(item.code)}</strong> · {fmt(item.status)} · {fmt(item.cause)}{item.evidence_level != null && <> · 증거 {fmt(item.evidence_level)}</>}</li>)}</ul>}
    {(Object.keys(structureRecord(current.record.before)).length > 0 || Object.keys(structureRecord(current.record.after)).length > 0) && <details><summary>기록된 변경 전 / 후</summary><pre className="log" tabIndex={0}>{JSON.stringify({ before: current.record.before, after: current.record.after }, null, 2)}</pre></details>}
  </section>;
}
