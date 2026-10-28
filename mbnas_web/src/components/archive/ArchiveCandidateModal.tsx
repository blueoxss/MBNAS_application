/**
 * Candidate modal (design 4.1-8), opened by `cid=` in the hash. Reuses the console Modal (focus trap, Esc, focus
 * return). Sections: identity + roles + status, per-tier evaluations, latency breakdown, architecture (stage table,
 * yaml, module histogram), lineage (parents are links that navigate inside the modal, module diff, mutation),
 * checkpoints and provenance.
 */
import { useEffect, useMemo, useState } from 'react';
import { Modal } from '../Modal';
import { DataTable, type Column } from '../DataTable';
import { Badge, Banner, KV } from '../ui';
import { useApp } from '../../state/context';
import { archiveErrorText, useArchiveQuery } from '../../archive/api';
import type { ApiClient } from '../../api/client';
import { catalogEntryFor, originLabel, type BlockCatalogTable } from '../../lib/structure';
import type { ArchiveCandidateDetail, ArchiveCandidateRow, ArchiveEvaluation, ArchiveParentDiff, ArchiveParentRef, ArchiveStageRow } from '../../archive/types';
import { DASH, fmtBool, fmtCacheHit, fmtDelta, fmtDur, fmtFixed, fmtInt, fmtLat, fmtMap, fmtVal, recOf, shortHash, tierInfo } from '../../archive/format';
import { Histogram } from './GenerationDetail';
import { LoadBox, SourceNote, useApi } from './ArchiveBits';

export function ArchiveCandidateModal(): JSX.Element | null {
  const app = useApp();
  const a = app.archive;
  const [trail, setTrail] = useState<string[]>([]);
  useEffect(() => {
    if (!a.cid) setTrail([]);
  }, [a.cid]);
  // a deep link opens the candidate only after the server answered (the token form must stay usable before that)
  if (!a.cid || !a.runId || app.health.status !== 'ok') return null;
  return <ModalBody runId={a.runId} cid={a.cid} trail={trail} setTrail={setTrail} />;
}

function ModalBody({ runId, cid, trail, setTrail }: { runId: string; cid: string; trail: string[]; setTrail: (t: string[]) => void }): JSX.Element {
  const app = useApp();
  const a = app.archive;
  const client = useApi();
  const q = useArchiveQuery<ArchiveCandidateDetail>(client, `${runId}|${cid}`, (c, s) => c.archiveCandidate(runId, cid, s));
  const go = (next: string): void => {
    setTrail([...trail, cid]);
    a.openCandidate(next);
  };
  const back = (): void => {
    const prev = trail[trail.length - 1];
    if (!prev) return;
    setTrail(trail.slice(0, -1));
    a.openCandidate(prev);
  };
  return (
    <Modal titleId="acand-title" onClose={a.closeCandidate}>
      <div className="modal-head">
        <div>
          <h2 id="acand-title" style={{ margin: 0 }} data-testid="acand-title">
            후보 {cid}
          </h2>
          <p className="muted wrap-any mono" style={{ margin: 0 }}>
            run {runId}
          </p>
        </div>
        <div className="row">
          {trail.length > 0 && (
            <button className="btn" type="button" onClick={back} data-testid="acand-back">
              ← {trail[trail.length - 1]}
            </button>
          )}
          <button className="btn" type="button" onClick={a.closeCandidate} data-autofocus data-testid="acand-close">
            닫기 (Esc)
          </button>
        </div>
      </div>
      <div data-testid="acand-body">
        <LoadBox state={q.state} what={`후보 ${cid}`} onRetry={q.reload}>
          {(d) => <Detail d={d} onGo={go} />}
        </LoadBox>
      </div>
    </Modal>
  );
}

type CatalogState = { status: 'idle' | 'loading' } | { status: 'ok'; table: BlockCatalogTable } | { status: 'error'; message: string };
/** One request per client: the served static block catalog (the web never hardcodes the table). */
const catalogRequests = new WeakMap<ApiClient, Promise<BlockCatalogTable>>();

function useBlockCatalog(client: ApiClient | null): CatalogState {
  const [state, setState] = useState<CatalogState>({ status: client ? 'loading' : 'idle' });
  useEffect(() => {
    if (!client) { setState({ status: 'idle' }); return undefined; }
    let alive = true;
    let request = catalogRequests.get(client);
    if (!request) {
      request = client.blockCatalog();
      catalogRequests.set(client, request);
      request.catch(() => { if (catalogRequests.get(client) === request) catalogRequests.delete(client); });  // a failure is retried by the next modal
    }
    setState({ status: 'loading' });
    request.then((table) => { if (alive) setState({ status: 'ok', table }); }, (e: unknown) => { if (alive) setState({ status: 'error', message: archiveErrorText(e) }); });
    return () => { alive = false; };
  }, [client]);
  return state;
}

/** Module histogram of the candidate with the design-origin family beside each module. */
function ModuleOrigins({ histogram, catalog }: { histogram: Record<string, number>; catalog: CatalogState }): JSX.Element | null {
  const rows = useMemo(() => Object.entries(histogram).filter(([, n]) => typeof n === 'number').sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])), [histogram]);
  if (rows.length === 0) return null;
  return (
    <div className="table-wrap" role="region" aria-label="모듈별 출처 계열" tabIndex={0}>
      <table className="data" data-testid="module-origins">
        <caption>모듈별 설계 출처 (서버 블록 카탈로그 기준, 없으면 '출처 미상')</caption>
        <thead><tr><th scope="col">module</th><th scope="col" className="num">개수</th><th scope="col">출처 계열</th></tr></thead>
        <tbody>
          {rows.map(([module, count]) => {
            const entry = catalog.status === 'ok' ? catalogEntryFor(catalog.table, module) : null;
            const family = entry?.origin_family ?? null;
            return (
              <tr key={module}>
                <th scope="row" className="mono">{module}</th>
                <td className="num">{count}</td>
                <td data-testid={`origin-${module}`} title={entry ? [entry.introduced_in ? `도입 ${entry.introduced_in}` : null, entry.role].filter(Boolean).join(' · ') : undefined}>
                  {catalog.status === 'ok' ? (family ? originLabel(family) : '출처 미상') : catalog.status === 'error' ? DASH : '조회 중…'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {catalog.status === 'error' && <p className="help" data-testid="origin-catalog-error">블록 카탈로그를 불러오지 못해 출처를 표시하지 못했습니다: {catalog.message}</p>}
    </div>
  );
}

const STAGE_LABEL: Record<string, string> = { search: '탐색 티어', final: '최종 검증(final)', exclusive: '배타 latency', confirm: 'confirm' };

const mval = (e: ArchiveEvaluation, k: string): unknown => recOf(e.metrics)[k];

/** The row's own search-tier metrics as an evaluation, used when the server sent no evaluation list. */
function rowAsEvaluation(r: ArchiveCandidateRow): ArchiveEvaluation {
  return { stage: 'search', tier: r.tier, epochs: r.epochs, seed: r.seed, status: r.status, metrics: r.metrics ?? {}, source: 'row' };
}

function evalCols(): Column<ArchiveEvaluation>[] {
  return [
    { key: 'stage', header: '단계', cell: (e) => STAGE_LABEL[e.stage] ?? e.stage, sort: (e) => e.stage },
    { key: 'tier', header: '티어', cell: (e) => <span title={tierInfo(e.tier).label}>{e.tier ?? DASH}</span>, sort: (e) => e.tier },
    { key: 'status', header: '상태', cell: (e) => (e.status ? <Badge tone={e.status === 'ok' ? 'ok' : 'err'}>{e.status}</Badge> : DASH) },
    { key: 'ep', header: 'epochs / seed', cell: (e) => `${fmtInt(e.epochs)} / ${fmtVal(e.seed)}`, num: true },
    { key: 'map', header: 'mAP50-95', cell: (e) => fmtMap(mval(e, 'map50_95') ?? mval(e, 'val_map50_95')), num: true },
    { key: 'm50', header: 'mAP50', cell: (e) => fmtMap(mval(e, 'map50') ?? mval(e, 'val_map50')), num: true },
    { key: 'sr', header: 'small recall', cell: (e) => fmtMap(mval(e, 'small_target_recall')), num: true },
    { key: 'lat', header: 'latency [ms]', cell: (e) => fmtLat(mval(e, 'latency_median_ms')), num: true },
    { key: 'params', header: 'params_deploy', cell: (e) => fmtInt(mval(e, 'params_deploy')), num: true },
    { key: 'time', header: 'train_time', cell: (e) => fmtDur(mval(e, 'train_time_s')), num: true },
    { key: 'src', header: '출처', cell: (e) => <span className="mono wrap-any">{e.source ?? DASH}</span> },
  ];
}

function ParentCard({ p, label, onGo }: { p: ArchiveParentRef; label: string; onGo: (cid: string) => void }): JSX.Element {
  return (
    <div className="artifact-cell" data-testid={`parent-${label}`}>
      <strong>부모 {label}</strong>{' '}
      <button className="btn btn-sm" type="button" onClick={() => onGo(p.candidate_id)} aria-label={`부모 ${label} ${p.candidate_id} 로 이동`} data-testid={`parent-link-${label}`}>
        {p.candidate_id}
      </button>
      <div className="help">
        세대 {p.generation ?? DASH} · fp <span className="mono">{shortHash(p.fingerprint)}</span> · cfg {fmtBool(p.has_cfg, '있음', '없음')}
      </div>
      <div className="help">mAP50-95 {fmtMap(p.metrics_summary?.map50_95)} · latency {fmtLat(p.metrics_summary?.latency_median_ms)} ms · params {fmtInt(p.metrics_summary?.params_deploy)}</div>
    </div>
  );
}

function Diff({ d }: { d: ArchiveParentDiff }): JSX.Element {
  return (
    <div className="artifact-cell" data-testid="parent-diff">
      <strong>vs 부모 {d.parent}</strong> <span className="help">(cfg 행 부모 {fmtInt(d.n_rows_parent)} → 자식 {fmtInt(d.n_rows_child)})</span>
      <p style={{ margin: '6px 0' }}>
        <span className="muted">추가된 모듈:</span> {(d.modules_added ?? []).length ? (d.modules_added ?? []).map((m) => <Badge key={m} tone="ok">＋ {m}</Badge>) : DASH}
      </p>
      <p style={{ margin: '6px 0' }}>
        <span className="muted">제거된 모듈:</span> {(d.modules_removed ?? []).length ? (d.modules_removed ?? []).map((m) => <Badge key={m} tone="err">－ {m}</Badge>) : DASH}
      </p>
      {(d.stage_diff ?? []).length > 0 && (
        <div className="table-wrap" role="region" aria-label="stage 차이" tabIndex={0}>
          <table className="data">
            <caption>stage별 차이</caption>
            <thead><tr><th scope="col">stage</th><th scope="col">부모</th><th scope="col">자식</th></tr></thead>
            <tbody>
              {(d.stage_diff ?? []).map((s) => (
                <tr key={s.stage_index}>
                  <td className="num">{s.stage_index}</td>
                  <td className="mono wrap-any">{(s.parent_modules ?? []).join(', ') || DASH}</td>
                  <td className="mono wrap-any">{(s.child_modules ?? []).join(', ') || DASH}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Detail({ d, onGo }: { d: ArchiveCandidateDetail; onGo: (cid: string) => void }): JSX.Element {
  const client = useApi();
  const catalog = useBlockCatalog(client);
  const row = d.row;
  const arch = d.architecture;
  const lat = d.latency;
  const lin = d.lineage;
  const stageCols: Column<ArchiveStageRow>[] = [
    { key: 'i', header: 'stage', cell: (s) => s.stage_index, num: true, sort: (s) => s.stage_index },
    { key: 'gs', header: 'global_start', cell: (s) => fmtInt(s.global_start), num: true },
    { key: 'mods', header: 'modules', cell: (s) => <span className="mono wrap-any">{(s.modules ?? []).join(', ') || DASH}</span> },
    { key: 'ds', header: 'downsamples', cell: (s) => fmtInt(s.downsamples), num: true },
  ];
  const evals: ArchiveEvaluation[] = (d.evaluations ?? []).length ? [...(d.evaluations ?? [])] : [rowAsEvaluation(row)];
  const exclusive = recOf(lat?.exclusive);
  const confirm = d.confirm ? recOf(d.confirm) : null;
  const histo = arch?.module_histogram ? { module_histogram: arch.module_histogram, module_histogram_prev: null } : null;
  return (
    <div className="stack" style={{ marginTop: 12 }}>
      <section aria-labelledby="ac-id">
        <h3 id="ac-id">신원 · 상태</h3>
        <div className="row">
          <Badge tone={row.status === 'ok' ? 'ok' : 'err'} title={row.error ?? undefined}>상태 {row.status}</Badge>
          <Badge tone="neutral">세대 {row.generation}</Badge>
          <Badge tone="neutral">{row.tier ?? '티어 없음'}</Badge>
          {row.is_final && <Badge tone="info">final ◆</Badge>}
          {(d.roles ?? row.roles ?? []).map((r) => <Badge key={r} tone="info">★ {r}</Badge>)}
          {row.on_front2d && <Badge tone="err">2D 정면</Badge>}
          {row.on_front4d && <Badge tone="err">4D 정면</Badge>}
        </div>
        {row.error && <Banner tone="err"><span className="wrap-any" data-testid="acand-error">오류: {row.error}</span></Banner>}
        <KV
          rows={[
            ['fingerprint', <span className="mono wrap-any" key="f">{row.fingerprint ?? DASH}</span>],
            ['seed / epochs', `${fmtVal(row.seed)} / ${fmtVal(row.epochs)}`],
            ['config / genotype hash', <span className="mono" key="h">{shortHash(row.hashes?.config)} / {shortHash(row.hashes?.genotype)}</span>],
            ['metric / training protocol', <span className="mono" key="p">{shortHash(row.hashes?.metric_protocol)} / {shortHash(row.hashes?.training_protocol)}</span>],
            ['Δ vs baseline*', `mAP50-95 ${fmtDelta(row.delta_vs_baseline?.map50_95)} · latency ${fmtDelta(row.delta_vs_baseline?.latency_median_ms, 2)} ms · params ${fmtDelta(row.delta_vs_baseline?.params_deploy, 0)}`],
          ]}
        />
        <p className="help">* Δ = 이 행 − 판정 기준선(final 티어). 행의 티어가 탐색 티어이면 학습 길이가 달라 참고용입니다.</p>
      </section>

      <section aria-labelledby="ac-eval">
        <h3 id="ac-eval">티어별 평가</h3>
        <DataTable caption="평가 행 (탐색 티어 / final / 배타 latency / confirm)" columns={evalCols()} rows={evals} rowKey={(e) => `${e.stage}|${e.tier ?? ''}|${e.seed ?? ''}`} testId="acand-evals" empty="평가 행 없음" />
        {Object.keys(exclusive).length > 0 && (
          <p className="help wrap-any" data-testid="exclusive-latency">배타 latency (GPU 단독 측정): {Object.entries(exclusive).map(([k, v]) => `${k}=${fmtVal(v)}`).join(' · ')}</p>
        )}
        {confirm && (
          <p className="help wrap-any" data-testid="acand-confirm">confirm: {Object.entries(confirm).map(([k, v]) => `${k}=${fmtVal(v)}`).join(' · ')}</p>
        )}
      </section>

      <section aria-labelledby="ac-lat">
        <h3 id="ac-lat">latency 분해</h3>
        <KV
          rows={[
            ['median / p90', `${fmtLat(lat?.median)} / ${fmtLat(lat?.p90)} ms`],
            ['mean ± std (cv)', `${fmtLat(lat?.mean)} ± ${fmtLat(lat?.std)} ms (cv ${fmtFixed(lat?.cv, 4)})`],
            ['forward_only', `${fmtLat(lat?.forward_only)} ms`],
            ['t_post (후처리)', `${fmtLat(lat?.t_post)} ms`],
            ['head_mode', lat?.head_mode ?? DASH],
            ['latency protocol hash', <span className="mono" key="l">{lat?.protocol_hash ?? DASH}</span>],
          ]}
        />
      </section>

      <section aria-labelledby="ac-arch">
        <h3 id="ac-arch">아키텍처</h3>
        {!arch ? (
          <p className="muted" data-testid="arch-missing">아키텍처(cfg.json/yaml)를 확보하지 못했습니다{row.has_cfg === false ? ' — has_cfg=false' : ''}.</p>
        ) : (
          <div className="stack">
            <p className="help">n_backbone {fmtInt(arch.n_backbone)} · n_head {fmtInt(arch.n_head)} · stage_count {fmtInt(arch.stage_count)} · nc {fmtVal(arch.cfg?.nc)}</p>
            {(arch.stages ?? []).length > 0 && <DataTable caption="stage 표" columns={stageCols} rows={arch.stages ?? []} rowKey={(s) => String(s.stage_index)} testId="stage-table" />}
            {histo && <Histogram ch={histo} />}
            {arch.module_histogram && <ModuleOrigins histogram={arch.module_histogram} catalog={catalog} />}
            {arch.yaml_text ? (
              <details>
                <summary>yaml 보기</summary>
                <pre className="log yaml" tabIndex={0} data-testid="yaml-block">{arch.yaml_text}</pre>
              </details>
            ) : (
              <p className="muted">yaml 본문 없음.</p>
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="ac-lin">
        <h3 id="ac-lin">계보</h3>
        {!lin ? (
          <p className="muted">계보 정보 없음.</p>
        ) : (
          <div className="stack">
            <div className="row">
              <Badge tone="info">mode {lin.mode ?? DASH}</Badge>
              <Badge tone="neutral">cache_hit {fmtCacheHit(lin.cache_hit)}</Badge>
              <span className="mono wrap-any" data-testid="mutation">mutation {fmtVal(lin.mutation)}</span>
            </div>
            {(lin.parents ?? []).length === 0 ? (
              <p className="muted" data-testid="no-parents">부모 없음 (seeded 또는 기록 없음).</p>
            ) : (
              <div className="grid-2">{(lin.parents ?? []).map((p, i) => <ParentCard key={p.candidate_id} p={p} label={i === 0 ? 'A' : 'B'} onGo={onGo} />)}</div>
            )}
            <div className="grid-2">{(lin.diff_vs_parents ?? []).map((x) => <Diff key={x.parent} d={x} />)}</div>
          </div>
        )}
      </section>

      <section aria-labelledby="ac-ck">
        <h3 id="ac-ck">체크포인트 · 출처</h3>
        {(d.checkpoints ?? []).length === 0 ? (
          <p className="muted" data-testid="no-checkpoint">체크포인트 기록 없음.</p>
        ) : (
          <div className="table-wrap" role="region" aria-label="체크포인트" tabIndex={0}>
            <table className="data" data-testid="checkpoints">
              <caption>체크포인트</caption>
              <thead><tr><th scope="col">티어</th><th scope="col">경로</th><th scope="col">존재</th><th scope="col">sha256</th></tr></thead>
              <tbody>
                {(d.checkpoints ?? []).map((c, i) => (
                  <tr key={i}>
                    <td>{c.tier ?? DASH}</td>
                    <td className="mono wrap-any">{c.path ?? DASH}</td>
                    <td>{c.exists == null ? DASH : <Badge tone={c.exists ? 'ok' : 'warn'}>{c.exists ? '있음' : '없음'}</Badge>}</td>
                    <td className="mono wrap-any">{c.sha256 ?? DASH}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <KV
          rows={[
            ['cfg.json', <span className="mono wrap-any" key="c">{d.provenance?.cfg_path ?? DASH}</span>],
            ['yaml', <span className="mono wrap-any" key="y">{d.provenance?.yaml_path ?? DASH}</span>],
            ['jsonl', <span className="mono wrap-any" key="j">{d.provenance?.jsonl_path ?? DASH}</span>],
          ]}
        />
        <SourceNote source={d.source} warnings={d.warnings} />
      </section>
    </div>
  );
}
