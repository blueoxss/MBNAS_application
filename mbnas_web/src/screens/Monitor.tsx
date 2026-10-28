/**
 * 탐색 모니터링 (Monitor): Fitness-Latency and mAP50-95-Latency scatter charts side by side, cohort
 * filters + cohort guard, FPS toggle, equivalent sortable table, generation progress, candidates,
 * workers, costs and events. Only EVALUATED rows with finite valid metrics become points.
 */
import { TrainingProgress } from '../components/TrainingProgress';
import { ResolvedVariantBadge } from '../components/Capabilities';
import { useMemo, useState } from 'react';
import { DataTable, type Column } from '../components/DataTable';
import { armStyle, Scatter, type XMode } from '../components/Scatter';
import { FinalistsCard, RunKindBadges } from '../components/FinalRun';
import { Badge, Banner, Card, KV, SelectField } from '../components/ui';
import type { Candidate, GeneratedCandidate, Generation, Run, Worker } from '../api/types';
import { rec, str } from '../api/types';
import { applyFacetFilters, buildPlotPoints, FACETS, facetOptions, isFinalRow, selectCohort, UNRESOLVED_COHORT, type FacetFilters, type FacetName, type PlotPoint } from '../lib/cohort';
import { evaluationEnabled, generatedAsCandidate, inGenerationOrder } from '../lib/generation';
import { isTrainedNotEvaluated, isUntrainedUnevaluated, TRAINED_NOT_EVALUATED, trainedNotEvaluatedTitle, UNTRAINED_UNEVALUATED } from '../lib/evaluation';
import { DASH, fmtAny, fmtGpuHours, fmtMs, fmtNum, fmtPct, fmtMb, fmtSeconds, latencyToFps } from '../lib/format';
import { baselineLines, countsInvariant, generationGuides, generationKey, isGenerationComplete, representativeLines, skippedForCohort, skippedHelpText, type Segment } from '../lib/lines';
import { candidateList } from '../state/reducer';
import { useApp } from '../state/context';

const COST_ROWS: [string, string][] = [
  ['ga_billed_gpu_seconds', 'GA billed'],
  ['random_billed_gpu_seconds', 'Random billed'],
  ['actual_gpu_seconds', 'Physical (actual)'],
  ['baseline_gpu_seconds', 'Baseline'],
  ['final_gpu_seconds', 'Final retrain'],
  ['failed_gpu_seconds', 'Failed work'],
  ['retry_gpu_seconds', 'Retries'],
  ['profile_gpu_seconds', 'Device profiling'],
];

export function Legend(): JSX.Element {
  return (
    <ul className="legend" aria-label="Legend">
      {(['Baseline', 'GA', 'IndependentRandom', 'Final'] as const).map((a) => (
        <li key={a}>
          <svg width="14" height="14" aria-hidden="true">
            <g transform="translate(7 7)" fill={armStyle(a).color}>
              {armStyle(a).shape === 'square' ? <rect x="-5" y="-5" width="10" height="10" /> : armStyle(a).shape === 'circle' ? <circle r="5" /> : armStyle(a).shape === 'triangle' ? <polygon points="0,-6 6,5 -6,5" /> : <polygon points="0,-7 7,0 0,7 -7,0" />}
            </g>
          </svg>
          {armStyle(a).label}
        </li>
      ))}
      <li>
        <svg width="34" height="10" aria-hidden="true"><line x1="0" y1="5" x2="34" y2="5" stroke="var(--series-baseline)" strokeWidth="1.5" /></svg>
        baseline family (latency order)
      </li>
      <li>
        <svg width="34" height="10" aria-hidden="true"><line x1="0" y1="5" x2="34" y2="5" stroke="var(--series-ga)" strokeWidth="2" strokeDasharray="2 5" strokeLinecap="round" /></svg>
        completed generation (dotted)
      </li>
      <li>
        <svg width="34" height="10" aria-hidden="true"><line x1="0" y1="5" x2="34" y2="5" stroke="var(--series-ga)" strokeWidth="3" /></svg>
        generation representatives (solid; not a Pareto frontier)
      </li>
    </ul>
  );
}

function PointsTable({ points, xMode, onOpen }: { points: PlotPoint[]; xMode: XMode; onOpen: (c: Candidate) => void }): JSX.Element {
  const cols: Column<PlotPoint>[] = [
    { key: 'name', header: 'Candidate', cell: (p) => <button className="btn btn-sm" type="button" onClick={() => onOpen(p.c)} aria-label={`Open details of ${p.c.name}, evaluation ${p.id}`}>{p.c.name}</button>, sort: (p) => p.c.name },
    { key: 'arm', header: 'Arm', cell: (p) => p.c.arm, sort: (p) => p.c.arm },
    { key: 'seed', header: 'Seed', cell: (p) => fmtAny(p.c.search_seed), sort: (p) => p.c.search_seed ?? null, num: true },
    { key: 'gen', header: 'Gen', cell: (p) => p.c.generation, sort: (p) => p.c.generation, num: true },
    { key: 'rung', header: 'Rung', cell: (p) => p.c.rung, sort: (p) => p.c.rung },
    { key: 'x', header: xMode === 'ms' ? 'Latency [ms]' : 'FPS [1000/latency]', cell: (p) => (xMode === 'ms' ? fmtNum(p.latency_ms, 2) : fmtNum(latencyToFps(p.latency_ms), 1)), sort: (p) => p.latency_ms, num: true },
    { key: 'fit', header: 'Fitness [0-1]', cell: (p) => fmtNum(p.c.fitness, 4), sort: (p) => p.c.fitness ?? null, num: true },
    { key: 'ap', header: 'mAP50-95 [%]', cell: (p) => fmtPct(p.c.ap), sort: (p) => p.c.ap ?? null, num: true },
    { key: 'size', header: 'Size [MB]', cell: (p) => fmtNum(p.c.size_mb, 2), sort: (p) => p.c.size_mb ?? null, num: true },
  ];
  return <DataTable caption="Evaluated points (same rows as the charts)" columns={cols} rows={points} rowKey={(p) => p.id} maxHeight empty="No evaluated points in the selected cohort." testId="points-table" />;
}

/** Generation progress. The representative is the server's (Generation.representative_*); the client does not recompute it (R6/DD-A21). */
function GenerationTable({ gens }: { gens: Generation[] }): JSX.Element {
  const cols: Column<Generation>[] = [
    { key: 'arm', header: 'Arm', cell: (g) => g.arm, sort: (g) => g.arm },
    { key: 'seed', header: 'Seed', cell: (g) => fmtAny(g.search_seed), num: true },
    { key: 'gen', header: 'Gen', cell: (g) => g.generation, sort: (g) => g.generation, num: true },
    { key: 'rung', header: 'Rung', cell: (g) => g.rung },
    { key: 'planned', header: 'Planned', cell: (g) => fmtAny(g.planned_count), num: true },
    { key: 'ev', header: 'Evaluated', cell: (g) => fmtAny(g.evaluated_count), num: true },
    { key: 'fail', header: 'Failed', cell: (g) => fmtAny(g.failed_count), num: true },
    { key: 'na', header: 'Not admitted', cell: (g) => fmtAny(g.not_admitted_count), num: true },
    { key: 'pend', header: 'Pending', cell: (g) => fmtAny(g.pending_count), num: true },
    {
      key: 'state',
      header: 'State',
      cell: (g) =>
        isGenerationComplete(g) ? <Badge tone="ok">complete (closed, pending 0)</Badge> : g.complete && !countsInvariant(g) ? <Badge tone="err">counts mismatch</Badge> : <Badge tone="warn">open{g.planning_closed ? ' (closed)' : ''}</Badge>,
    },
    {
      key: 'rep',
      header: 'Representative (server)',
      cell: (g) => <span className="mono wrap-any">{g.representative_evaluation_id ?? g.representative_candidate_id ?? DASH}</span>,
    },
  ];
  return <DataTable caption="Generation progress (planned / evaluated / failed / not admitted / pending)" columns={cols} rows={gens} rowKey={(g) => generationKey(g)} empty="No generations reported yet." testId="generation-table" maxHeight />;
}

export { isTrainedNotEvaluated, TRAINED_NOT_EVALUATED };

/** DD-3: run-level reason_code of an evaluation-OFF generation run that ended with zero / fewer candidates than requested. */
export const GENERATION_REASON_TEXT: Record<string, string> = {
  GENERATION_EMPTY: '모든 seed에서 유효 후보가 0개입니다(reason_code GENERATION_EMPTY). run 상태는 부분 결과 종료(COMPLETED)이며 성공한 생성을 뜻하지 않습니다. 구조 검사 실패·중복·시도 상한을 확인하세요.',
  GENERATION_PARTIAL: '일부 seed가 요청 후보 수에 못 미쳤습니다(reason_code GENERATION_PARTIAL). 저장된 유효 후보는 유지됩니다. seed별 요약에서 미달 seed를 확인하세요.',
};

function CandidateTable({ rows, onOpen, run }: { rows: Candidate[]; onOpen: (c: Candidate) => void; run?: Run | null }): JSX.Element {
  const cols: Column<Candidate>[] = [
    { key: 'name', header: 'Candidate', cell: (c) => <button className="btn btn-sm" type="button" onClick={() => onOpen(c)} aria-label={`Open details of ${c.name}, evaluation ${c.id}`}>{c.name}</button>, sort: (c) => c.name },
    { key: 'status', header: 'Evaluation status', cell: (c) => (isTrainedNotEvaluated(c, run) ? <Badge tone="info" title={trainedNotEvaluatedTitle(c, run)}>{TRAINED_NOT_EVALUATED}</Badge> : <Badge tone={c.status === 'EVALUATED' ? 'ok' : 'warn'}>{c.status}</Badge>), sort: (c) => c.status },
    { key: 'cs', header: 'Candidate status', cell: (c) => c.candidate_status ?? DASH, sort: (c) => c.candidate_status },
    { key: 'reason', header: 'Reason code', cell: (c) => (c.reason_code ? <Badge tone="err">{c.reason_code}</Badge> : DASH) },
    { key: 'ep', header: 'Epochs / target', cell: (c) => `${c.epochs} / ${fmtAny(c.epoch_target_total)}`, sort: (c) => c.epochs, num: true },
    { key: 'rung', header: 'Rung', cell: (c) => c.rung, sort: (c) => c.rung },
    { key: 'ap', header: 'AP [%]', cell: (c) => fmtPct(c.ap), sort: (c) => c.ap ?? null, num: true },
    { key: 'rc', header: 'Recall [%]', cell: (c) => fmtPct(c.recall), sort: (c) => c.recall ?? null, num: true },
    { key: 'lat', header: 'Latency', cell: (c) => fmtMs(c.latency_ms), sort: (c) => c.latency_ms ?? null, num: true },
    { key: 'sz', header: 'Size', cell: (c) => fmtMb(c.size_mb), sort: (c) => c.size_mb ?? null, num: true },
    { key: 'fit', header: 'Fitness', cell: (c) => fmtNum(c.fitness, 4), sort: (c) => c.fitness ?? null, num: true },
    { key: 'dev', header: 'Device / precision', cell: (c) => `${c.device} / ${c.precision}` },
  ];
  return <DataTable caption="Candidates (all evaluation rows; missing metrics are shown as a dash, never 0)" columns={cols} rows={rows} rowKey={(c) => c.id} maxHeight empty="No candidates yet." testId="candidate-table" />;
}

export function GeneratedTable({ rows, onOpen }: { rows: GeneratedCandidate[]; onOpen: (c: Candidate) => void }): JSX.Element {
  const cols: Column<GeneratedCandidate>[] = [
    { key: 'order', header: '생성 순서', cell: (c) => fmtAny(c.generation_order), num: true },
    { key: 'name', header: '모델 구조', cell: (c) => <button className="btn btn-sm" type="button" onClick={() => onOpen(generatedAsCandidate(c))}>{c.name}</button> },
    { key: 'id', header: 'Candidate ID', cell: (c) => <span className="mono wrap-any">{c.candidate_id}</span> },
    { key: 'structure', header: '구조 검사', cell: (c) => <Badge tone={c.structure_status === 'PASS' ? 'ok' : c.structure_status === 'FAIL' ? 'err' : 'warn'}>{c.structure_status ?? 'UNVERIFIED'}</Badge> },
    {
      key: 'evaluation',
      header: '학습 · 성능 평가',
      // DD-A23: training_status / evaluation_status of the row (a 1.4.x row has no training_status: generation never trains)
      cell: (c) =>
        isUntrainedUnevaluated(c) ? (
          <>
            <Badge tone="warn" className="b-nowrap" title={`training_status ${String(rec(c).training_status ?? 'not reported (generated candidate)')} · evaluation_status ${String(c.evaluation_status ?? 'NOT_EVALUATED')}`}>
              {UNTRAINED_UNEVALUATED}
            </Badge>
            <div className="muted mono">{String(c.evaluation_status ?? 'NOT_EVALUATED')}</div>
          </>
        ) : (
          <span className="mono">{`${String(rec(c).training_status ?? DASH)} / ${String(c.evaluation_status ?? DASH)}`}</span>
        ),
    },
    { key: 'seed', header: 'Seed', cell: (c) => fmtAny(c.search_seed) },
    { key: 'params', header: '실제 parameters', cell: (c) => fmtNum(c.parameters, 0) },
    { key: 'metrics', header: 'mAP / Recall / Latency / Fitness', cell: () => '미평가 / 미평가 / 미평가 / 미평가' },
  ];
  return <DataTable caption="미평가 랜덤 후보 · 생성 순서 (성능 순위 아님)" columns={cols} rows={inGenerationOrder(rows)} rowKey={(c) => c.candidate_id} empty="아직 저장된 유효 후보가 없습니다. 구조 검사를 실행하지 못한 모델은 PASS로 표시하지 않습니다." testId="generated-candidates" maxHeight />;
}

/** Worker status vocabulary (contract v1.2.0): idle | busy | offline | lost. Always text + glyph, never colour only. */
export const WORKER_STATUS: Record<string, { tone: 'ok' | 'info' | 'warn' | 'err' | 'neutral'; glyph: string; help: string }> = {
  idle: { tone: 'ok', glyph: '○', help: 'idle: registered and waiting for a job' },
  busy: { tone: 'info', glyph: '●', help: 'busy: running a job (candidate / epoch shown in the row)' },
  offline: { tone: 'neutral', glyph: '–', help: 'offline: worker is not connected' },
  lost: { tone: 'err', glyph: '✖', help: 'lost: heartbeat older than the lease TTL, or the process was killed' },
};

export function WorkerStatus({ status }: { status: string }): JSX.Element {
  const k = String(status ?? '').toLowerCase();
  const v = WORKER_STATUS[k];
  if (!v) return <Badge title="status value outside the contract vocabulary">{status || DASH}</Badge>;
  return (
    <Badge tone={v.tone} title={v.help} className={`worker-${k}`}>
      <span aria-hidden="true">{v.glyph}</span> {k}
    </Badge>
  );
}

export function WorkerTable({ workers }: { workers: Worker[] }): JSX.Element {
  const cols: Column<Worker>[] = [
    { key: 'id', header: 'Worker', cell: (w) => <span className="mono">{w.id}</span> },
    // contract 1.5.0 core-issued runner / device ids (DD-A19); an older server does not send them: dash
    { key: 'runner', header: 'Runner ID', cell: (w) => <span className="mono wrap-any">{str(rec(w).runner_id) ?? DASH}</span>, sort: (w) => str(rec(w).runner_id) },
    { key: 'devid', header: 'Device ID', cell: (w) => <span className="mono wrap-any">{str(rec(w).device_id) ?? DASH}</span>, sort: (w) => str(rec(w).device_id) },
    { key: 'dev', header: 'Device', cell: (w) => w.device_name ?? w.device },
    { key: 'st', header: 'Status', cell: (w) => <WorkerStatus status={w.status} />, sort: (w) => w.status },
    { key: 'util', header: 'Utilization [%]', cell: (w) => (typeof w.utilization_percent === 'number' ? w.utilization_percent.toFixed(0) : DASH), num: true },
    // I4-#9: contract Worker telemetry. null / missing is a dash, never 0.
    { key: 'vram', header: 'VRAM [MB]', cell: (w) => fmtNum(w.vram_mb, 0), sort: (w) => w.vram_mb ?? null, num: true },
    { key: 'pw', header: 'Power [W]', cell: (w) => fmtNum(w.power_w, 1), sort: (w) => w.power_w ?? null, num: true },
    { key: 'clk', header: 'SM clock [MHz]', cell: (w) => fmtNum(w.sm_clock_mhz, 0), sort: (w) => w.sm_clock_mhz ?? null, num: true },
    { key: 'cand', header: 'Candidate', cell: (w) => <span className="mono wrap-any">{w.candidate_id ?? DASH}</span> },
    { key: 'ep', header: 'Epoch', cell: (w) => `${fmtAny(w.epoch)} / ${fmtAny(w.epoch_target_total)}`, num: true },
    { key: 'lr', header: 'LR', cell: (w) => (typeof w.learning_rate === 'number' ? w.learning_rate.toExponential(2) : DASH), num: true },
  ];
  return (
    <>
      <DataTable caption="Workers" columns={cols} rows={workers} rowKey={(w) => w.id} empty="No workers reported." testId="worker-table" />
      <p className="help" data-testid="worker-status-legend">
        Status: {Object.values(WORKER_STATUS).map((v) => v.help).join(' · ')}.
      </p>
    </>
  );
}

export function Monitor(): JSX.Element {
  const app = useApp();
  const { rs } = app;
  const [filters, setFilters] = useState<FacetFilters>({});
  const [wantedCohort, setWantedCohort] = useState<string | null>(null);
  const [xMode, setXMode] = useState<XMode>('ms');
  const cands = useMemo(() => candidateList(rs), [rs]);

  const model = useMemo(() => {
    const fit = buildPlotPoints(cands, 'fitness');
    const ap = buildPlotPoints(cands, 'ap');
    const fitF = applyFacetFilters(fit.points, filters);
    const apF = applyFacetFilters(ap.points, filters);
    const selA = selectCohort(apF, wantedCohort);
    const selF = selectCohort(fitF, selA.selected);
    const gens = rs.generations;
    const lines = (sel: typeof selA, all: PlotPoint[]): { segs: Segment[]; skipped: ReturnType<typeof generationGuides>['skipped'] } => {
      const guides = generationGuides(sel.inCohort, gens, all);
      return { segs: [...baselineLines(sel.inCohort), ...guides.segments, ...representativeLines(sel.inCohort, gens)], skipped: guides.skipped };
    };
    return { fit, ap, selA, selF, linesF: lines(selF, fit.points), linesA: lines(selA, ap.points) };
  }, [cands, filters, wantedCohort, rs.generations]);

  const hidden = model.selA.hiddenOtherCohort;
  const unresolved = model.ap.excluded.UNRESOLVED_COHORT;
  const setFilter = (f: FacetName, v: string): void => setFilters((o) => ({ ...o, [f]: v }));

  if (!rs.runId) {
    return (
      <div>
        <div className="page-head"><h1>탐색 모니터링</h1></div>
        <Banner tone="info">No run loaded. Only values returned by a real server are drawn here. Open the Server screen to connect, then Run or load an existing run.</Banner>
        <button className="btn btn-primary" type="button" onClick={() => app.setScreen('server')}>Go to server connection</button>
      </div>
    );
  }
  const run = rs.run;
  const costs = (run?.costs ?? {}) as Record<string, number | null | undefined>;
  const lastEvents = rs.events.slice(-60).reverse();

  return (
    <div>
      <div className="page-head">
        <h1>탐색 모니터링</h1>
        {run && <Badge tone="info" title="run status">{run.status}</Badge>}
        <RunKindBadges run={run} onLoad={(id) => void app.loadRun(id)} />
        {run?.namespace && <Badge>{run.namespace}</Badge>}
        {run?.execution_mode && <Badge>{run.execution_mode}{run.purpose ? ` / ${run.purpose}` : ''}</Badge>}
        <ResolvedVariantBadge run={run} />
        <Badge title="event stream">stream: {rs.conn}</Badge>
        <span className="muted mono wrap-any">{rs.runId}</span>
      </div>
      {rs.conn === 'reconnecting' || rs.conn === 'error' || rs.conn === 'auth_error' ? <Banner tone={rs.conn === 'reconnecting' ? 'warn' : 'err'}>Event stream {rs.conn}: {rs.connDetail || 'trying again'}. Charts show the last authoritative state.</Banner> : null}

      {!evaluationEnabled(run?.resolved_protocol) && <Card title="평가 없는 랜덤 후보 생성">
        <Banner tone="info">학습·성능 평가·benchmark·fitness·승급을 수행하지 않는 생성 작업입니다. 아래 순서는 생성 순서이며 성능 순위가 아닙니다. 모델을 열어 별도 학습 run을 요청할 수 있습니다.</Banner>
        {run?.reason_code && GENERATION_REASON_TEXT[run.reason_code] && <Banner tone="warn"><span data-testid="generation-reason-banner">부분 결과: {GENERATION_REASON_TEXT[run.reason_code]}</span></Banner>}
        <GeneratedTable rows={rs.generatedCandidates} onOpen={app.openCandidate} />
        {Object.entries(rec(rec(run?.budget).generation_results)).map(([seed, record]) => {
          const summary = rec(record);
          return <section key={seed} aria-label={`생성 결과 seed ${seed}`}>
            <h3>Seed {seed} · {fmtAny(summary.status)}</h3>
            <KV rows={[
              ['요청 / 실제 방식', `${fmtAny(summary.requested_method)} / ${fmtAny(summary.effective_method)}`],
              ['생성 / 요청 후보 수', `${fmtAny(summary.generated_count)} / ${fmtAny(summary.requested_count)}`],
              ['시도 / 최대 시도', `${fmtAny(summary.attempts)} / ${fmtAny(summary.max_attempts)}`],
              ['중복 수 / 허용 정책', `${fmtAny(summary.duplicates)} / ${fmtAny(summary.allow_duplicates)}`],
              ['구조 검사 실패 집계', JSON.stringify(summary.failure_counts ?? {})],
            ]} />
            {summary.partial === true && <Banner tone="warn">부분 생성 결과입니다. 저장된 유효 후보는 유지됩니다. 구조 검사 실패·중복·시도 상한을 확인하세요.</Banner>}
            {Array.isArray(summary.diagnostics) && summary.diagnostics.length > 0 && <details><summary>실패 위치·진단 코드·원인</summary><pre className="log wrap-any">{JSON.stringify(summary.diagnostics, null, 2)}</pre></details>}
          </section>;
        })}
      </Card>}
      <TrainingProgress candidates={cands} events={rs.events} onOpen={app.openCandidate} />
      <FinalistsCard run={run} />

      {evaluationEnabled(run?.resolved_protocol) && <>
      <Card title="Cohort 필터">
        <div className="filters">
          <SelectField
            label="Cohort (one drawn at a time)"
            value={model.selA.selected ?? ''}
            allowEmpty={model.selA.groups.length ? undefined : '(no evaluated points)'}
            options={model.selA.groups.map((g) => ({ value: g.key, label: `${g.label} [${g.count}]` }))}
            onChange={(v) => setWantedCohort(v)}
            testId="cohort-select"
          />
          {FACETS.map((f) => (
            <SelectField key={f.name} label={f.label} value={filters[f.name] ?? ''} allowEmpty="All" options={facetOptions(cands, f.name)} onChange={(v) => setFilter(f.name, v)} testId={`facet-${f.name}`} />
          ))}
        </div>
        <fieldset style={{ marginTop: 12, marginBottom: 0 }}>
          <legend>X axis</legend>
          <div className="row">
            <label className="check"><input type="radio" name="xmode" checked={xMode === 'ms'} onChange={() => setXMode('ms')} /> Latency [ms]</label>
            <label className="check"><input type="radio" name="xmode" checked={xMode === 'fps'} onChange={() => setXMode('fps')} data-testid="x-fps" /> FPS [1000 / latency_ms]</label>
          </div>
        </fieldset>
        {model.selA.inCohort.length > 0 && model.selA.inCohort.every((p) => isFinalRow(p.c)) && (
          <Banner tone="info">
            <span data-testid="final-cohort-note">Selected cohort: FINAL retraining (comparison group final, rung FINAL, arm Final, final_eval split). It is drawn alone and is never mixed with search cohorts.</span>
          </Banner>
        )}
        {(hidden > 0 || unresolved > 0) && (
          <Banner tone="warn">
            <span data-testid="cohort-guard">
              Cohort guard:{' '}
              {hidden > 0 ? `${hidden} point(s) from ${Math.max(0, model.selA.groups.length - 1)} other cohort(s) are not drawn together with the selected cohort` : ''}
              {hidden > 0 && unresolved > 0 ? '; ' : ''}
              {unresolved > 0 ? `${unresolved} evaluated row(s) are ${UNRESOLVED_COHORT} (missing batch/shape/seed/hash fields) and are not drawn` : ''}.
            </span>
          </Banner>
        )}
      </Card>

      <Card title="Fitness / mAP50-95 vs latency">
        <div className="chart-grid">
          <div>
            <h3>Fitness – Latency</h3>
            <Scatter chartId="fitness" title="Fitness versus latency" yLabel="Fitness" yKind="fitness" xMode={xMode} points={model.selF.inCohort} segments={model.linesF.segs} onOpen={(p) => app.openCandidate(p.c)} />
          </div>
          <div>
            <h3>mAP50-95 – Latency</h3>
            <Scatter chartId="ap" title="mAP50-95 versus latency" yLabel="mAP50-95 (AP x 100)" yKind="ap" xMode={xMode} points={model.selA.inCohort} segments={model.linesA.segs} onOpen={(p) => app.openCandidate(p.c)} />
          </div>
        </div>
        <Legend />
        <p className="help">
          Points are EVALUATED rows with finite valid metrics only; failed, pending and unmeasured rows are not points. Lines never imply a Pareto frontier.
          {skippedHelpText(skippedForCohort(model.linesF.skipped, model.selF.inCohort)) ? ` ${skippedHelpText(skippedForCohort(model.linesF.skipped, model.selF.inCohort))}` : ''} Excluded: {model.ap.excluded.NOT_EVALUATED} not evaluated, {model.ap.excluded.INVALID_METRICS} invalid metrics, {model.ap.excluded.SUPERSEDED} superseded.
        </p>
        <PointsTable points={model.selA.inCohort} xMode={xMode} onOpen={app.openCandidate} />
      </Card>

      <Card title="Generation 진행"><GenerationTable gens={rs.generations} /></Card>
      <Card title="후보 표"><CandidateTable rows={cands} onOpen={app.openCandidate} run={run} /></Card>
      </>}
      <div className="grid-2">
        <Card title="Workers"><WorkerTable workers={Object.values(rs.workers)} /></Card>
        <Card title="비용 · Costs (GPU-hours)">
          <div className="kpis" data-testid="costs">
            {COST_ROWS.map(([k, label]) => {
              const v = costs[k];
              return (
                <div className="kpi" key={k}>
                  <div className="v" title={typeof v === 'number' ? `${v} s raw (${fmtSeconds(v)})` : 'not measured'}>{fmtGpuHours(v)}</div>
                  <div className="l">{label}</div>
                </div>
              );
            })}
          </div>
          <p className="help">A dash means not measured (never 0). Hover a value for raw GPU-seconds.{run?.budget ? ` Budget: ${JSON.stringify(run.budget)}` : ''}</p>
        </Card>
      </div>
      <Card title="Events (latest 60)">
        <div className="log" role="log" aria-label="Event log" tabIndex={0} data-testid="event-log">
          {lastEvents.length === 0 && <span className="muted">No events yet.</span>}
          {lastEvents.map((e) => (
            <div key={e.seq} className="wrap-any">#{e.seq} {e.type} {JSON.stringify(e.payload).slice(0, 200)}</div>
          ))}
        </div>
      </Card>
    </div>
  );
}
