/**
 * Run overview (design 4.1-2): status + verdicts, stage timeline (P0..P10), budget, pre-registered gates
 * (FAIL / WARN first), stop rule / transition / orphan check, GPU guard, roles and final validation rows.
 */
import { DataTable, type Column } from '../DataTable';
import { Badge, Banner, Card, KV } from '../ui';
import type { ArchiveGate, ArchiveRun, ArchiveStage } from '../../archive/types';
import { DASH, fmtBool, fmtDur, fmtInt, fmtMap, fmtStamp, fmtVal, recOf, shortHash } from '../../archive/format';
import type { Loadable } from '../../state/context';
import { GateBadge, LoadBox, SourceNote, StatusBadge, VerdictBadge } from './ArchiveBits';

const GATE_RANK: Record<string, number> = { FAIL: 0, WARN: 1 };

/** FAIL first, then WARN, then the rest; the original (checklist) order is kept inside each group. */
export function sortGates(gates: readonly ArchiveGate[]): ArchiveGate[] {
  return gates
    .map((g, i) => ({ g, i }))
    .sort((a, b) => (GATE_RANK[a.g.status ?? ''] ?? 2) - (GATE_RANK[b.g.status ?? ''] ?? 2) || a.i - b.i)
    .map((x) => x.g);
}

export function gateCounts(gates: readonly ArchiveGate[]): Record<string, number> {
  const c: Record<string, number> = {};
  for (const g of gates) {
    const k = g.status ?? 'UNKNOWN';
    c[k] = (c[k] ?? 0) + 1;
  }
  return c;
}

/** Scalar leaves of a nested record as "a.b" -> value rows (arrays / deep objects are shown as compact JSON). */
export function flatten(v: unknown, prefix = '', depth = 0): [string, unknown][] {
  const r = recOf(v);
  const out: [string, unknown][] = [];
  for (const [k, x] of Object.entries(r)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (x !== null && typeof x === 'object' && !Array.isArray(x) && depth < 2) out.push(...flatten(x, key, depth + 1));
    else out.push([key, x]);
  }
  return out;
}

function Stages({ stages }: { stages: ArchiveStage[] }): JSX.Element {
  const max = Math.max(1, ...stages.map((s) => (typeof s.duration_s === 'number' ? s.duration_s : 0)));
  return (
    <ol className="stages" aria-label="단계 타임라인" data-testid="stage-timeline">
      {stages.map((s) => (
        <li key={s.label}>
          <span className="stage-label">{s.label}</span>
          <span className="stage-bar" aria-hidden="true">
            <span style={{ width: `${typeof s.duration_s === 'number' ? Math.max(1, (s.duration_s / max) * 100) : 0}%` }} />
          </span>
          <span className="stage-dur" title={`${fmtStamp(s.started_at)} → ${fmtStamp(s.ended_at)}`}>
            {fmtDur(s.duration_s)}
          </span>
        </li>
      ))}
    </ol>
  );
}

function JsonKV({ title, value, testId }: { title: string; value: unknown; testId: string }): JSX.Element | null {
  const rows = flatten(value);
  if (rows.length === 0) return null;
  return (
    <details data-testid={testId}>
      <summary>{title}</summary>
      <KV rows={rows.map(([k, x]) => [<span className="mono wrap-any" key={k}>{k}</span>, <span className="wrap-any" key={`${k}v`}>{fmtVal(x)}</span>])} />
    </details>
  );
}

export function OverviewCard({ state, onRetry, onOpenCandidate }: { state: Loadable<ArchiveRun>; onRetry?: () => void; onOpenCandidate: (cid: string) => void }): JSX.Element {
  return (
    <Card title="개요" id="archive-overview">
      <LoadBox state={state} what="run 개요" onRetry={onRetry}>
        {(r) => <Body r={r} onOpenCandidate={onOpenCandidate} />}
      </LoadBox>
    </Card>
  );
}

function Body({ r, onOpenCandidate }: { r: ArchiveRun; onOpenCandidate: (cid: string) => void }): JSX.Element {
  const gates = sortGates(r.gates ?? []);
  const counts = gateCounts(gates);
  const bad = (counts.FAIL ?? 0) + (counts.WARN ?? 0);
  const gateCols: Column<ArchiveGate>[] = [
    { key: 'status', header: '결과', cell: (g) => <GateBadge status={g.status ?? 'UNKNOWN'} /> },
    { key: 'name', header: '게이트', cell: (g) => <span className="wrap-any">{g.name}</span> },
    { key: 'hard', header: '필수', cell: (g) => (g.hard ? <Badge tone="neutral">hard</Badge> : DASH) },
    { key: 'measured', header: '측정값', cell: (g) => <span className="wrap-any">{g.measured || DASH}</span> },
    { key: 'criterion', header: '기준', cell: (g) => <span className="wrap-any">{g.criterion || DASH}</span> },
  ];
  const roles = r.roles ?? [];
  const fv = r.final_val ?? [];
  const gg = r.gpu_guard;
  return (
    <div className="stack" data-testid="overview">
      <div className="row">
        <span className="mono wrap-any" data-testid="overview-id">{r.id}</span>
        <StatusBadge status={r.status} />
        <Badge tone="neutral">{r.family}</Badge>
        {r.scale && <Badge tone="neutral">scale {r.scale}</Badge>}
        {r.dataset_id && <Badge tone="neutral">{r.dataset_id}</Badge>}
      </div>
      {r.status_detail && <p className="help wrap-any" data-testid="status-detail">상태 근거: {r.status_detail}</p>}
      <div className="row" aria-label="판정">
        <VerdictBadge label="notebook" value={r.verdicts?.notebook} />
        <VerdictBadge label="transition" value={r.verdicts?.transition} />
        <VerdictBadge label="confirm" value={r.verdicts?.confirm} />
        <VerdictBadge label="smoke" value={r.verdicts?.smoke_check} />
      </div>
      <div className="kpis">
        <div className="kpi"><div className="v">{`${r.generations?.done ?? DASH} / ${r.generations?.planned ?? DASH}`}</div><div className="l">세대 done / planned</div></div>
        <div className="kpi"><div className="v">{`${fmtInt(r.candidates?.ok)} / ${fmtInt(r.candidates?.total)}`}</div><div className="l">후보 ok / 전체 (실패 {fmtInt(r.candidates?.failed)})</div></div>
        <div className="kpi"><div className="v">{fmtMap(r.best?.map50_95)}</div><div className="l">best mAP50-95</div></div>
        <div className="kpi"><div className="v">{fmtMap(r.best?.small_target_recall)}</div><div className="l">best small recall</div></div>
        <div className="kpi"><div className="v">{r.best?.latency_min_ms == null ? DASH : r.best.latency_min_ms.toFixed(2)}</div><div className="l">최소 latency [ms]</div></div>
        <div className="kpi"><div className="v">{fmtDur(r.duration_s)}</div><div className="l">소요 · {fmtStamp(r.started_at)} → {fmtStamp(r.ended_at)}</div></div>
      </div>
      <KV
        rows={[
          ['arm', r.arm ? `${r.arm.space ?? DASH} / ${r.arm.method ?? DASH} / seed ${r.arm.seed ?? DASH}` : DASH],
          ['protocol_v2_train', <span className="mono" key="h1" title={r.hashes?.protocol_v2_train ?? undefined}>{shortHash(r.hashes?.protocol_v2_train)}</span>],
          ['metric_protocol', <span className="mono" key="h2" title={r.hashes?.metric_protocol ?? undefined}>{shortHash(r.hashes?.metric_protocol)}</span>],
          ['dataset_manifest', <span className="mono" key="h3" title={r.hashes?.dataset_manifest ?? undefined}>{shortHash(r.hashes?.dataset_manifest)}</span>],
          ['경로', <span className="mono wrap-any" key="p">{r.path ?? DASH}</span>],
        ]}
      />

      {(r.stages ?? []).length > 0 ? <Stages stages={r.stages ?? []} /> : <p className="muted" data-testid="stage-missing">단계 시각 정보 없음 (executed.ipynb 미기록).</p>}

      <JsonKV title="예산 (budget.json)" value={r.budget} testId="budget" />
      <JsonKV title="preset" value={r.preset} testId="preset" />
      <JsonKV title="데이터셋" value={r.dataset} testId="dataset" />
      <JsonKV title="accuracy floor" value={r.accuracy_floor} testId="accuracy-floor" />

      <details open={bad > 0} data-testid="gates">
        <summary>
          사전등록 게이트 {gates.length}건 — {Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(' · ') || '없음'}
        </summary>
        {gates.length === 0 ? <p className="muted">checklist.json 없음.</p> : <DataTable caption="게이트 (FAIL / WARN 상단)" columns={gateCols} rows={gates} rowKey={(g) => g.name} maxHeight testId="gate-table" />}
      </details>

      {(r.stop_rule || r.transition || r.orphan_check) && (
        <div className="stack" data-testid="driver-records">
          {r.stop_rule && <Banner tone="err"><span className="wrap-any">중단 규칙 발동: {fmtVal(recOf(r.stop_rule).rule ?? recOf(r.stop_rule).reason ?? r.stop_rule)}</span></Banner>}
          <JsonKV title="stop_rule" value={r.stop_rule} testId="stop-rule" />
          <JsonKV title="transition" value={r.transition} testId="transition" />
          <JsonKV title="orphan_check" value={r.orphan_check} testId="orphan-check" />
        </div>
      )}
      {gg && (
        <p className="help" data-testid="gpu-guard">
          GPU 가드: 기록 {fmtInt(gg.records)}건 · 대기 에피소드 {gg.episodes?.length ?? 0}건 · 포기 {fmtInt(gg.giveups)}건
          {(gg.episodes ?? []).slice(0, 3).map((e) => ` · GPU${e.gpu ?? '?'} ${fmtDur(e.waited_s)} 대기 (외부 최대 ${fmtInt(e.external_mib_max)} MiB)`).join('')}
        </p>
      )}
      {roles.length > 0 && (
        <div className="tag-list" data-testid="roles">
          {roles.map((x) =>
            x.candidate_id ? (
              <button key={x.role} className="btn btn-sm" type="button" onClick={() => onOpenCandidate(x.candidate_id as string)} aria-label={`${x.role} 후보 ${x.candidate_id} 열기`}>
                {x.role}: <span className="mono">{x.candidate_id}</span>
              </button>
            ) : (
              <Badge key={x.role} tone="neutral">{x.role}: {DASH}</Badge>
            ),
          )}
        </div>
      )}
      {fv.length > 0 && (
        <DataTable
          caption="final 검증 (final_summary.final_val)"
          testId="final-val-table"
          columns={[
            { key: 'cid', header: '후보', cell: (x) => (x.cid ?? x.candidate_id ? <button className="btn btn-sm" type="button" onClick={() => onOpenCandidate((x.cid ?? x.candidate_id) as string)}>{x.cid ?? x.candidate_id}</button> : DASH), sort: (x) => x.cid ?? x.candidate_id },
            { key: 'status', header: '상태', cell: (x) => x.status ?? DASH },
            { key: 'm50', header: 'val mAP50', cell: (x) => fmtMap(x.val_map50), num: true, sort: (x) => x.val_map50 ?? null },
            { key: 'm5095', header: 'val mAP50-95', cell: (x) => fmtMap(x.val_map50_95), num: true, sort: (x) => x.val_map50_95 ?? null },
            { key: 'sr', header: 'small recall', cell: (x) => fmtMap(x.small_target_recall), num: true },
            { key: 'lat', header: 'latency [ms]', cell: (x) => (x.latency_median_ms == null ? DASH : x.latency_median_ms.toFixed(2)), num: true },
            { key: 'params', header: 'params_deploy', cell: (x) => fmtInt(x.params_deploy), num: true },
            { key: 'head', header: 'head_mode', cell: (x) => x.head_mode ?? DASH },
          ]}
          rows={fv}
          rowKey={(x) => String(x.cid ?? x.candidate_id ?? "")}
        />
      )}
      <p className="help">plots: {(r.plots ?? []).map((p) => `${p.kind}${p.exists ? '' : '(없음)'}`).join(', ') || DASH} · confirm 사용 {fmtBool(r.has?.confirm)}</p>
      <SourceNote source={r.source} warnings={r.warnings} />
    </div>
  );
}
