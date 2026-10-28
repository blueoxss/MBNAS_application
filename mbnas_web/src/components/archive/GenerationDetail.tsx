/**
 * Generation detail panel (design 4.1-7), opened by `gen=` in the hash: what improved and who did it, this
 * generation's candidates (new fingerprints highlighted), lineage, and the generation's characteristics - module
 * histogram versus the previous generation, head mode, depth / params / latency distributions, front changes.
 */
import { DataTable, type Column } from '../DataTable';
import { Badge, Card } from '../ui';
import type { ArchiveCandidateRow, ArchiveGeneration, ArchiveGenerationDetail, ArchiveImprovement, ArchiveLineageRow, Dist } from '../../archive/types';
import { useArchiveQuery } from '../../archive/api';
import { DASH, fmtCacheHit, fmtDelta, fmtFixed, fmtInt, fmtLat, fmtMap, fmtVal, histogramRows } from '../../archive/format';
import { modeSummary } from './GenerationChart';
import { LoadBox, SourceNote, useApi } from './ArchiveBits';

const OBJ_LABEL: Record<string, string> = { map50_95: 'mAP50-95 (↑)', small_target_recall: 'small recall (↑)', latency_median_ms: 'latency [ms] (↓)', params_deploy: 'params_deploy (↓)', hypervolume: 'hypervolume (↑)' };

interface Props {
  runId: string;
  generation: number;
  /** changes whenever the run gets new data (poll) so the panel refetches */
  version: string | number;
  onClose: () => void;
  onOpenCandidate: (cid: string) => void;
  onSelectGeneration: (g: number) => void;
  maxGeneration: number;
}

export function GenerationDetail({ runId, generation, version, onClose, onOpenCandidate, onSelectGeneration, maxGeneration }: Props): JSX.Element {
  const client = useApi();
  const q = useArchiveQuery<ArchiveGenerationDetail>(client, `${runId}|${generation}`, (c, s) => c.archiveGeneration(runId, generation, s), version);
  return (
    <Card
      title={`세대 ${generation} 상세`}
      id="archive-gen-detail"
      actions={
        <div className="row">
          <button className="btn btn-sm" type="button" disabled={generation <= 0} onClick={() => onSelectGeneration(generation - 1)} data-testid="gen-prev">← 이전 세대</button>
          <button className="btn btn-sm" type="button" disabled={generation >= maxGeneration} onClick={() => onSelectGeneration(generation + 1)} data-testid="gen-next">다음 세대 →</button>
          <button className="btn btn-sm" type="button" onClick={onClose} data-testid="gen-close">닫기</button>
        </div>
      }
    >
      <div data-testid="gen-detail">
        <LoadBox state={q.state} what={`세대 ${generation} 상세`} onRetry={q.reload}>
          {(d) => <Body d={d} onOpenCandidate={onOpenCandidate} />}
        </LoadBox>
      </div>
    </Card>
  );
}

function Delta({ v, digits = 4 }: { v: number | null | undefined; digits?: number }): JSX.Element {
  return <span className="muted">(Δ {fmtDelta(v, digits)})</span>;
}

function DistRow({ label, d, digits = 0 }: { label: string; d: Dist | undefined; digits?: number }): JSX.Element {
  const f = (v: number | null | undefined): string => (digits === 0 ? fmtInt(v) : fmtFixed(v, digits));
  return (
    <tr>
      <th scope="row">{label}</th>
      <td className="num">{f(d?.min)}</td>
      <td className="num">{f(d?.med)}</td>
      <td className="num">{f(d?.max)}</td>
    </tr>
  );
}

/** The two histograms the table needs (a generation's characteristics, or one candidate's own module counts). */
export interface HistogramInput {
  module_histogram?: Record<string, number> | null;
  module_histogram_prev?: Record<string, number> | null;
  /** candidates of the previous generation that had a cfg (0 / absent: there is no previous generation to compare with) */
  n_prev?: number | null;
}

export function Histogram({ ch }: { ch: HistogramInput | null | undefined }): JSX.Element {
  // the contract sends {} (not null) for the first generation: an empty previous histogram with n_prev 0 means "no previous generation"
  const prevH = ch?.module_histogram_prev ?? null;
  const prev = prevH && (Object.keys(prevH).length > 0 || (ch?.n_prev ?? 0) > 0) ? prevH : null;
  const rows = histogramRows(ch?.module_histogram, prev).slice(0, 14);
  const max = Math.max(1, ...rows.flatMap((r) => [r.cur, r.prev ?? 0]));
  const hasPrev = prev != null;
  if (rows.length === 0) return <p className="muted" data-testid="hist-empty">모듈 히스토그램 없음 (cfg.json 미확보).</p>;
  return (
    <div className="table-wrap" role="region" aria-label="모듈 히스토그램" tabIndex={0}>
      <table className="data hist" data-testid="module-hist">
        <caption>모듈 히스토그램: 현재 세대 vs {hasPrev ? '직전 세대' : '(직전 세대 없음)'} · |Δ| 큰 순</caption>
        <thead>
          <tr>
            <th scope="col">module</th>
            <th scope="col" className="num">직전</th>
            <th scope="col" className="num">현재</th>
            <th scope="col" className="num">Δ</th>
            <th scope="col">막대 (위 현재, 아래 직전)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.module}>
              <th scope="row" className="mono">{r.module}</th>
              <td className="num">{r.prev === null ? DASH : r.prev}</td>
              <td className="num">{r.cur}</td>
              <td className="num" data-testid={`hist-delta-${r.module}`}>{r.delta === null ? DASH : fmtDelta(r.delta, 0)}</td>
              <td>
                <span className="hbar cur" aria-hidden="true" style={{ width: `${(r.cur / max) * 100}%` }} />
                {hasPrev && <span className="hbar prev" aria-hidden="true" style={{ width: `${((r.prev ?? 0) / max) * 100}%` }} />}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Body({ d, onOpenCandidate }: { d: ArchiveGenerationDetail; onOpenCandidate: (cid: string) => void }): JSX.Element {
  const g: ArchiveGeneration = d.generation;
  const ch = d.characteristics ?? g.characteristics;
  const newIds = new Set(g.new_fingerprint_ids ?? []);
  const impCols: Column<ArchiveImprovement>[] = [
    { key: 'obj', header: '목표', cell: (i) => <span>{OBJ_LABEL[i.objective] ?? i.objective}{i.kind === 'initial' ? <Badge tone="neutral" className="newfp">초기값</Badge> : null}</span> },
    { key: 'prev', header: '이전 best', cell: (i) => (i.objective.includes('params') ? fmtInt(i.prev_best) : fmtFixed(i.prev_best, i.objective.includes('latency') ? 2 : 4)), num: true },
    { key: 'new', header: '새 best', cell: (i) => (i.objective.includes('params') ? fmtInt(i.new_best) : fmtFixed(i.new_best, i.objective.includes('latency') ? 2 : 4)), num: true },
    { key: 'delta', header: 'Δ', cell: (i) => fmtDelta(i.delta, i.objective.includes('latency') ? 2 : i.objective.includes('params') ? 0 : 4), num: true },
    { key: 'cid', header: '달성 후보', cell: (i) => (i.candidate_id ? <button className="btn btn-sm" type="button" onClick={() => onOpenCandidate(i.candidate_id as string)} aria-label={`${i.candidate_id} 상세 열기`}>{i.candidate_id}</button> : DASH) },
  ];
  const candCols: Column<ArchiveCandidateRow>[] = [
    { key: 'cid', header: '후보', cell: (r) => <span><button className="btn btn-sm" type="button" onClick={() => onOpenCandidate(r.candidate_id)} aria-label={`${r.candidate_id} 상세 열기`}>{r.candidate_id}</button>{(r.new_fingerprint === true || (r.new_fingerprint == null && newIds.has(r.candidate_id))) && <Badge tone="ok" className="newfp">새 fp</Badge>}</span>, sort: (r) => r.candidate_id },
    { key: 'tier', header: '티어', cell: (r) => r.tier ?? DASH, sort: (r) => r.tier },
    { key: 'status', header: '상태', cell: (r) => <Badge tone={r.status === 'ok' ? 'ok' : r.status === 'degenerate' || r.status === 'prune' ? 'warn' : 'err'} title={r.error ?? undefined}>{r.status}</Badge> },
    { key: 'mode', header: 'mode', cell: (r) => r.lineage?.mode ?? DASH },
    { key: 'map', header: 'mAP50-95', cell: (r) => fmtMap(r.metrics?.map50_95), num: true, sort: (r) => r.metrics?.map50_95 ?? null },
    { key: 'sr', header: 'small recall', cell: (r) => fmtMap(r.metrics?.small_target_recall), num: true, sort: (r) => r.metrics?.small_target_recall ?? null },
    { key: 'lat', header: 'latency [ms]', cell: (r) => fmtLat(r.metrics?.latency_median_ms), num: true, sort: (r) => r.metrics?.latency_median_ms ?? null },
    { key: 'params', header: 'params_deploy', cell: (r) => fmtInt(r.metrics?.params_deploy), num: true, sort: (r) => r.metrics?.params_deploy ?? null },
    { key: 'front', header: '정면', cell: (r) => (r.on_front2d ? <Badge tone="err">2D</Badge> : DASH) },
  ];
  const linCols: Column<ArchiveLineageRow>[] = [
    { key: 'cid', header: '후보', cell: (r) => <button className="btn btn-sm" type="button" onClick={() => onOpenCandidate(r.candidate_id)} aria-label={`${r.candidate_id} 상세 열기`}>{r.candidate_id}</button>, sort: (r) => r.candidate_id },
    { key: 'mode', header: '모드', cell: (r) => <Badge tone={r.mode === 'ga_crossover' ? 'info' : r.mode === 'seeded_anchor' ? 'neutral' : 'warn'}>{r.mode ?? DASH}</Badge>, sort: (r) => r.mode },
    {
      key: 'parents',
      header: '부모',
      cell: (r) =>
        (r.parents ?? []).length ? (
          <span className="tag-list">
            {(r.parents ?? []).map((p) =>
              /^[\w.:@-]+$/.test(p) && !p.includes('/') ? (
                <button key={p} className="btn btn-sm" type="button" onClick={() => onOpenCandidate(p)} aria-label={`부모 ${p} 열기`}>{p}</button>
              ) : (
                <span key={p} className="mono wrap-any" title="후보 id가 아닌 경로/시드 (seeded)">{p.split('/').pop()}</span>
              ),
            )}
          </span>
        ) : (
          DASH
        ),
    },
    { key: 'mut', header: 'mutation', cell: (r) => <span className="mono wrap-any">{fmtVal(r.mutation)}</span> },
    { key: 'cache', header: 'cache_hit', cell: (r) => <span className="mono wrap-any">{fmtCacheHit(r.cache_hit)}</span> },
  ];
  const addedRemoved = { added: g.front_added ?? [], removed: g.front_removed ?? [] };
  return (
    <div className="stack">
      <div className="kpis">
        <div className="kpi"><div className="v">{fmtFixed(g.hypervolume, 4)} <Delta v={g.delta_vs_prev?.hypervolume} /></div><div className="l">hypervolume (런타임){g.hv_improved ? ' · 개선' : ''}</div></div>
        <div className="kpi"><div className="v">{fmtMap(g.best_map50_95)} <Delta v={g.delta_vs_prev?.best_map50_95} /></div><div className="l">best mAP50-95 · Δ vs baseline* {fmtDelta(g.delta_vs_baseline?.best_map50_95)}</div></div>
        <div className="kpi"><div className="v">{fmtMap(g.best_small_recall)} <Delta v={g.delta_vs_prev?.best_small_recall} /></div><div className="l">best small recall</div></div>
        <div className="kpi"><div className="v">{fmtLat(g.best_latency)} <Delta v={g.delta_vs_prev?.best_latency} digits={2} /></div><div className="l">best latency [ms] · Δ vs baseline* {fmtDelta(g.delta_vs_baseline?.best_latency, 2)}</div></div>
        <div className="kpi"><div className="v">{fmtInt(g.valid)} / {fmtInt(g.population)}</div><div className="l">valid / population (train_fail {fmtInt(g.train_fail)}, degenerate {fmtInt(g.degenerate)})</div></div>
        <div className="kpi"><div className="v">{fmtInt(g.unique_fingerprints)} · 새 {fmtInt(g.new_fingerprints)}</div><div className="l">unique fp · 이전 세대에 없던 fp</div></div>
        <div className="kpi"><div className="v">{fmtInt(g.stagnation)}</div><div className="l">stagnation (런타임) · phase {g.phase ?? DASH}</div></div>
        <div className="kpi"><div className="v">{fmtInt(g.archive_size)}</div><div className="l">archive 크기</div></div>
      </div>
      {g.mutation && (
        <p className="help wrap-any" data-testid="mutation-cfg">
          변이 설정: rate {fmtVal(g.mutation.mutation_rate)} · prefer_modular {fmtVal(g.mutation.prefer_modular)} · pure_random_injection {fmtVal(g.mutation.pure_random_injection)} · 이유 {fmtVal(g.mutation.reasons)}
        </p>
      )}

      <h3>무엇이 개선됐나</h3>
      {(d.improvements ?? []).length === 0 ? (
        <p className="muted" data-testid="no-improvement">이 세대에서 이전 best를 갱신한 목표가 없습니다.</p>
      ) : (
        <DataTable caption="개선 목록 (이전 세대까지의 best 대비)" columns={impCols} rows={d.improvements ?? []} rowKey={(i) => i.objective} testId="improvements" />
      )}

      <h3>이 세대 후보 ({d.candidates.length}행)</h3>
      <DataTable caption="이 세대 평가 행 (새 fp 강조)" columns={candCols} rows={d.candidates} rowKey={(r) => `${r.candidate_id}|${r.tier ?? ''}`} testId="gen-candidates" maxHeight empty="이 세대 평가 행이 없습니다." />

      <h3>Lineage</h3>
      <p className="help" data-testid="lineage-summary">모드별 개수: {modeSummary(g.lineage_modes)}</p>
      <DataTable caption="후보별 계보 (부모를 누르면 그 후보로 이동)" columns={linCols} rows={d.lineage ?? []} rowKey={(r) => r.candidate_id} testId="gen-lineage" maxHeight empty="계보 정보 없음." />

      <h3>세대 특징</h3>
      <p className="help">
        모듈 히스토그램 산식: 해당 세대 status=ok 후보의 cfg.json backbone+head 행에서 module 이름별 개수의 합계(평균 아님). 직전 세대도 같은 산식이고 Δ = 현재 − 직전입니다.
      </p>
      {ch && typeof ch.n_cfg_missing === 'number' && (
        <p className="help" data-testid="cfg-coverage">
          cfg 확보 {fmtInt(ch.n_cfg)} / {fmtInt(ch.n)} 후보 (누락 {fmtInt(ch.n_cfg_missing)}) · 직전 세대 {fmtInt(ch.n_prev_cfg)} / {fmtInt(ch.n_prev)}. 히스토그램은 cfg가 있는 후보만 셉니다{ch.n_cfg_missing > 0 ? ' — 누락분은 반영되지 않았습니다' : ''}.
        </p>
      )}
      <Histogram ch={ch} />
      {(ch?.top_modules_delta ?? []).length > 0 && (
        <p className="help" data-testid="top-delta">서버가 보고한 Δ 상위: {(ch?.top_modules_delta ?? []).map((t) => `${t.module} ${fmtDelta(t.delta, 0)}`).join(', ')}</p>
      )}
      <div className="grid-2">
        <div className="table-wrap" role="region" aria-label="분포" tabIndex={0}>
          <table className="data" data-testid="distributions">
            <caption>분포 (ok 후보 {fmtInt(ch?.n)}개)</caption>
            <thead><tr><th scope="col">항목</th><th scope="col" className="num">min</th><th scope="col" className="num">median</th><th scope="col" className="num">max</th></tr></thead>
            <tbody>
              <DistRow label="n_backbone" d={ch?.depth?.n_backbone} />
              <DistRow label="n_head" d={ch?.depth?.n_head} />
              <DistRow label="stage_count" d={ch?.depth?.stage_count} />
              <DistRow label="params_deploy" d={ch?.params_deploy} />
              <DistRow label="latency_median_ms" d={ch?.latency_median_ms} digits={2} />
            </tbody>
          </table>
        </div>
        <div>
          <p data-testid="head-mode"><strong>head_mode</strong>: {Object.entries(ch?.head_mode ?? {}).map(([k, n]) => `${k} ${n}`).join(' · ') || DASH}</p>
          <p data-testid="front-change">
            <strong>2D 정면 변화</strong>: 추가 {addedRemoved.added.length} / 제거 {addedRemoved.removed.length} (직전 세대 대비)
          </p>
          <div className="tag-list">
            {addedRemoved.added.map((c) => (
              <button key={`a${c}`} className="btn btn-sm" type="button" onClick={() => onOpenCandidate(c)} aria-label={`정면에 추가된 ${c} 열기`}>＋ {c}</button>
            ))}
            {addedRemoved.removed.map((c) => (
              <button key={`r${c}`} className="btn btn-sm" type="button" onClick={() => onOpenCandidate(c)} aria-label={`정면에서 제거된 ${c} 열기`}>－ {c}</button>
            ))}
          </div>
          {((g.front4d_added ?? []).length > 0 || (g.front4d_removed ?? []).length > 0) && (
            <p className="help" data-testid="front4d-change">4D 정면: 추가 {(g.front4d_added ?? []).length} / 제거 {(g.front4d_removed ?? []).length}</p>
          )}
          <p className="help">정면 = (mAP50-95↑, latency↓) 2D 비지배, 그 세대까지의 ok 후보. 이 세대 front {(g.front2d ?? []).length}개, 4목적 front {(g.front4d ?? []).length}개.</p>
        </div>
      </div>
      {d.prev_front2d_points && d.front2d_points && (
        <p className="help">정면 점 수: 직전 {d.prev_front2d_points.length} → 현재 {d.front2d_points.length}</p>
      )}
      <SourceNote source={d.source} warnings={d.warnings} />
    </div>
  );
}
