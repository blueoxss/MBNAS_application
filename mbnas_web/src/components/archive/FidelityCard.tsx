/** Fidelity + confirm cards (design 4.1-10). Numbers are shown as the server read them from fidelity/ and three_seed/. */
import { Badge, Banner, Card } from '../ui';
import { DataTable, type Column } from '../DataTable';
import { useArchiveQuery } from '../../archive/api';
import type { ArchiveConfirm, ArchiveFidelity, ArchiveRun } from '../../archive/types';
import { DASH, fmtBool, fmtFixed, fmtInt, fmtMap, fmtVal, numOrNull, recOf } from '../../archive/format';
import { LoadBox, SourceNote, useApi, verdictTone } from './ArchiveBits';

export function FidelityCard({ run }: { run: ArchiveRun }): JSX.Element {
  const client = useApi();
  const absent = run.has?.fidelity === false;
  const q = useArchiveQuery<ArchiveFidelity>(client, absent ? null : run.id, (c, s) => c.archiveFidelity(run.id, s), run.status);
  return (
    <Card title="Fidelity (탐색 티어 ↔ 최종 티어 순위 일치)" id="archive-fidelity">
      {absent ? (
        <p className="muted" data-testid="fidelity-missing">이 run에는 fidelity 결과가 없습니다.</p>
      ) : (
        <LoadBox state={q.state} what="fidelity" onRetry={q.reload}>
          {(f) => <FidelityBody f={f} fallback={run.fidelity_summary} />}
        </LoadBox>
      )}
    </Card>
  );
}

function FidelityBody({ f, fallback }: { f: ArchiveFidelity; fallback: ArchiveRun['fidelity_summary'] }): JSX.Element {
  const rc = recOf(f.rank_correlation);
  const noise = recOf(f.noise);
  const rho = numOrNull(rc.spearman) ?? numOrNull(fallback?.spearman);
  const tau = numOrNull(rc.kendall) ?? numOrNull(fallback?.kendall);
  const n = numOrNull(rc.n) ?? numOrNull(fallback?.n);
  const snr = numOrNull(noise.snr) ?? numOrNull(fallback?.snr);
  const byHead = Object.entries(recOf(f.corr_by_head_mode));
  const note = f.evidence_note ?? null;
  const nonEvidence = !!note && /비증거/.test(note);
  const ci = recOf(f.bootstrap_ci);
  return (
    <div className="stack" data-testid="fidelity">
      <div className="kpis">
        <div className="kpi"><div className="v">{fmtFixed(rho, 4)}</div><div className="l">Spearman ρ</div></div>
        <div className="kpi"><div className="v">{fmtFixed(tau, 4)}</div><div className="l">Kendall τ</div></div>
        <div className="kpi"><div className="v">{fmtInt(n)}</div><div className="l">표본 수 n</div></div>
        <div className="kpi"><div className="v">{fmtFixed(snr, 2)}</div><div className="l">SNR (spread / noise)</div></div>
      </div>
      <div className="row">
        {nonEvidence ? <Badge tone="warn">비증거 — 파이프라인 점검용</Badge> : <Badge tone="neutral">증거 수준 표기: {note ?? '서버 표기 없음'}</Badge>}
        {n !== null && <Badge tone="neutral">n = {n}</Badge>}
      </div>
      {note && <p className="help wrap-any" data-testid="evidence-note">{note}</p>}
      {byHead.length > 0 && (
        <details>
          <summary>head_mode별 상관</summary>
          <dl className="kv">
            {byHead.map(([k, v]) => (
              <div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd className="wrap-any">{fmtVal(v)}</dd></div>
            ))}
          </dl>
        </details>
      )}
      {Object.keys(ci).length > 0 && (
        <details>
          <summary>bootstrap CI</summary>
          <pre className="log" tabIndex={0}>{JSON.stringify(ci, null, 2)}</pre>
        </details>
      )}
      <SourceNote source={f.source} warnings={f.warnings} />
    </div>
  );
}

interface SumRow {
  name: string;
  v: Record<string, unknown>;
}

export function ConfirmCard({ run }: { run: ArchiveRun }): JSX.Element {
  const client = useApi();
  const absent = run.has?.confirm === false;
  const q = useArchiveQuery<ArchiveConfirm>(client, absent ? null : run.id, (c, s) => c.archiveConfirm(run.id, s), run.status);
  return (
    <Card title="Confirm (3-seed 확인)" id="archive-confirm">
      {absent ? (
        <p className="muted" data-testid="confirm-missing">이 run에는 confirm(three_seed) 결과가 없습니다.</p>
      ) : (
        <LoadBox state={q.state} what="confirm" onRetry={q.reload}>
          {(c) => <ConfirmBody c={c} />}
        </LoadBox>
      )}
    </Card>
  );
}

function ConfirmBody({ c }: { c: ArchiveConfirm }): JSX.Element {
  const rows: SumRow[] = Object.entries(c.summary ?? {}).map(([name, v]) => ({ name, v: recOf(v) }));
  const cols: Column<SumRow>[] = [
    { key: 'name', header: '모델', cell: (r) => <strong>{r.name}{r.name === c.baseline_name ? ' (기준선)' : ''}</strong>, sort: (r) => r.name },
    { key: 'val', header: 'val mAP50-95 (평균)', cell: (r) => fmtMap(numOrNull(r.v.val_map50_95_mean)), num: true },
    { key: 'tiles', header: 'test tiles mAP50-95', cell: (r) => fmtMap(numOrNull(r.v.test_tile_map50_95_mean)), num: true },
    { key: 'native', header: 'test native mAP50-95', cell: (r) => fmtMap(numOrNull(r.v.test_native_map50_95_mean)), num: true },
    { key: 'lat', header: 'latency [ms] (평균)', cell: (r) => fmtFixed(numOrNull(r.v.latency_median_ms_mean), 2), num: true },
    { key: 'params', header: 'params_deploy', cell: (r) => fmtInt(numOrNull(r.v.params_deploy)), num: true },
  ];
  const verdicts = Object.entries(recOf(c.verdicts));
  return (
    <div className="stack" data-testid="confirm">
      <div className="row">
        <Badge tone={verdictTone(c.final_verdict)}>최종 판정 {c.final_verdict ?? DASH}</Badge>
        <Badge tone="neutral">seeds {(c.seeds ?? []).length ? (c.seeds ?? []).join(', ') : DASH}</Badge>
        <Badge tone="neutral">max_det 관측 {fmtInt(c.max_det_observed)}</Badge>
        <Badge tone={c.test_unlock_used ? 'warn' : 'neutral'}>test unlock 사용 {fmtBool(c.test_unlock_used)}</Badge>
      </div>
      {rows.length === 0 ? <Banner tone="warn">summary 없음.</Banner> : <DataTable caption="val / test 나란히 (seed 평균)" columns={cols} rows={rows} rowKey={(r) => r.name} testId="confirm-table" />}
      {verdicts.length > 0 && (
        <dl className="kv" data-testid="confirm-verdicts">
          {verdicts.map(([k, v]) => (
            <div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd className="wrap-any">{fmtVal(v)}</dd></div>
          ))}
        </dl>
      )}
      {c.final_report_md && (
        <details>
          <summary>final_report.md</summary>
          <pre className="log" tabIndex={0}>{c.final_report_md}</pre>
        </details>
      )}
      <SourceNote source={c.source} warnings={c.warnings} />
    </div>
  );
}
