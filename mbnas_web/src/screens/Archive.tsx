/**
 * NAS 아카이브: read-only monitor of the ETRI_NAS search runs (design 4.1). Run picker -> overview, live,
 * baseline (always shown), generation progress, scatter, candidate table, generation detail (hash `gen=`),
 * candidate modal (hash `cid=`, rendered by App), plots, fidelity, confirm.
 * Everything shown is read from the run's artifacts by the server; a missing value is a dash.
 * Runs of a 12-search campaign (server `archive/campaign.py`, family `visdrone-campaign`, additive summary fields
 * `campaign_id`/`space`/`method`/`seed`/`pair`/`progress`) are not listed in the plain run table: they are grouped under
 * their campaign in a collapsible card whose rows reuse the run-list row (button `run-pick-<id>`, status badge, columns).
 */
import { useMemo, useState } from 'react';
import { DataTable, type Column } from '../components/DataTable';
import { Badge, Banner, Card } from '../components/ui';
import { ArchiveScatter } from '../components/archive/ArchiveScatter';
import { BaselineTable, buildBaselineRows, judgeNameOf } from '../components/archive/BaselineTable';
import { CandidateTable } from '../components/archive/CandidateTable';
import { ConnectGate, SourceNote } from '../components/archive/ArchiveBits';
import { ConfirmCard, FidelityCard } from '../components/archive/FidelityCard';
import { GenerationChart } from '../components/archive/GenerationChart';
import { GenerationDetail } from '../components/archive/GenerationDetail';
import { LiveCard } from '../components/archive/LiveCard';
import { OverviewCard } from '../components/archive/OverviewCard';
import { PlotsCard } from '../components/archive/PlotsCard';
import { RunPicker } from '../components/archive/RunPicker';
import { StatusBadge } from '../components/archive/ArchiveBits';
import { generationsOf } from '../archive/api';
import { DASH, fmtInt, fmtMap, fmtStamp, isNum, recOf } from '../archive/format';
import type { ArchiveRunList, ArchiveRunSummary } from '../archive/types';
import { useApp, type Loadable } from '../state/context';

/** Campaign id of a run summary (additive server field), or null for a plain run. */
export function campaignIdOf(r: ArchiveRunSummary): string | null {
  const v = (r as Record<string, unknown>).campaign_id;
  return typeof v === 'string' && v !== '' ? v : null;
}

const str = (r: ArchiveRunSummary, k: string): string => {
  const v = (r as Record<string, unknown>)[k];
  return typeof v === 'string' || typeof v === 'number' ? String(v) : DASH;
};

/** Ledger-derived progress of a campaign arm; falls back to the plain generation/candidate counts. */
function progressOf(r: ArchiveRunSummary): { gens: number | null; ok: number | null; evalFail: number | null; degenerate: number | null; total: number | null; lastEval: string | null } {
  const p = recOf((r as Record<string, unknown>).progress);
  const c = recOf(p.candidates);
  const gens = isNum(p.generations_seen) ? p.generations_seen : isNum(r.generations?.done) ? r.generations.done : null;
  return {
    gens,
    ok: isNum(c.ok) ? c.ok : isNum(r.candidates?.ok) ? r.candidates.ok : null,
    evalFail: isNum(c.eval_fail) ? c.eval_fail : null,
    degenerate: isNum(c.degenerate) ? c.degenerate : null,
    total: isNum(c.total) ? c.total : isNum(r.candidates?.total) ? r.candidates.total : null,
    lastEval: typeof p.last_eval_at === 'string' ? p.last_eval_at : null,
  };
}

const seedNum = (r: ArchiveRunSummary): number => {
  const v = (r as Record<string, unknown>).seed;
  return isNum(v) ? v : Number.MAX_SAFE_INTEGER;
};

/** Groups by campaign id (first-seen order); rows sorted space, seed, method so a GA/Random pair is adjacent. */
export function groupCampaigns(runs: readonly ArchiveRunSummary[]): { id: string; runs: ArchiveRunSummary[]; total: number | null }[] {
  const by = new Map<string, ArchiveRunSummary[]>();
  for (const r of runs) {
    const id = campaignIdOf(r);
    if (id) by.set(id, [...(by.get(id) ?? []), r]);
  }
  return [...by.entries()].map(([id, rs]) => {
    const t = (rs[0] as Record<string, unknown>).campaign_arms_total;
    return {
      id,
      total: isNum(t) ? t : null,
      runs: [...rs].sort((a, b) => str(a, 'space').localeCompare(str(b, 'space')) || seedNum(a) - seedNum(b) || str(a, 'method').localeCompare(str(b, 'method'))),
    };
  });
}

function CampaignGroups({ groups, selectedId, onSelect }: { groups: ReturnType<typeof groupCampaigns>; selectedId: string | null; onSelect: (id: string) => void }): JSX.Element | null {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  if (groups.length === 0) return null;
  const toggle = (id: string): void =>
    setCollapsed((cur) => {
      const next = new Set(cur);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const cols: Column<ArchiveRunSummary>[] = [
    {
      key: 'arm',
      header: 'Arm (space / method / seed)',
      cell: (r) => (
        <button className="btn btn-sm run-pick" type="button" aria-pressed={r.id === selectedId} onClick={() => onSelect(r.id)} aria-label={`${r.id} 선택`} data-testid={`run-pick-${r.id}`}>
          <span className="mono wrap-any">{`${str(r, 'space')} / ${str(r, 'method')} / seed ${str(r, 'seed')}`}</span>
        </button>
      ),
      sort: (r) => `${str(r, 'space')}/${str(r, 'seed').padStart(8, '0')}/${str(r, 'method')}`,
    },
    { key: 'status', header: '상태', cell: (r) => <StatusBadge status={r.status} />, sort: (r) => r.status },
    { key: 'pair', header: '쌍', cell: (r) => (isNum((r as Record<string, unknown>).pair) ? String((r as Record<string, unknown>).pair) : DASH), sort: (r) => { const v = (r as Record<string, unknown>).pair; return isNum(v) ? v : null; }, num: true },
    { key: 'gen', header: '세대', cell: (r) => fmtInt(progressOf(r).gens), sort: (r) => progressOf(r).gens, num: true },
    {
      key: 'cand',
      header: '후보 ok / eval_fail / degenerate / 전체',
      cell: (r) => {
        const p = progressOf(r);
        return `${fmtInt(p.ok)} / ${fmtInt(p.evalFail)} / ${fmtInt(p.degenerate)} / ${fmtInt(p.total)}`;
      },
      sort: (r) => progressOf(r).total,
      num: true,
    },
    { key: 'map', header: 'best mAP50-95', cell: (r) => fmtMap(r.best?.map50_95), sort: (r) => r.best?.map50_95 ?? null, num: true },
    { key: 'last', header: '마지막 평가 (UTC)', cell: (r) => fmtStamp(progressOf(r).lastEval), sort: (r) => progressOf(r).lastEval },
    { key: 'start', header: '시작 (UTC)', cell: (r) => fmtStamp(r.started_at), sort: (r) => r.started_at },
  ];
  return (
    <Card title="12-search 캠페인" id="archive-campaigns">
      <p className="muted" style={{ marginTop: 0 }}>
        캠페인 arm은 읽기 전용 어댑터가 arm 디렉터리에서 읽습니다. 진행 중 arm의 진행률은 ledger(eval_timing.jsonl) 기준이며 값이 없으면 —입니다.
      </p>
      {groups.map((g) => {
        const open = !collapsed.has(g.id);
        const count = (st: string): number => g.runs.filter((r) => r.status === st).length;
        return (
          <div key={g.id} data-testid={`campaign-${g.id}`} style={{ marginBottom: 10 }}>
            <button className="btn btn-sm" type="button" aria-expanded={open} aria-controls={`campaign-body-${g.id}`} onClick={() => toggle(g.id)} data-testid={`campaign-toggle-${g.id}`}>
              <span aria-hidden="true">{open ? '▾' : '▸'}</span> <span className="mono wrap-any">{g.id}</span>
            </button>{' '}
            <span className="tag-list" data-testid={`campaign-summary-${g.id}`}>
              <Badge tone="neutral">{`arm ${g.runs.length}${g.total !== null ? ` / ${g.total}` : ''}`}</Badge>
              <Badge tone="info">{`실행 중 ${count('running')}`}</Badge>
              <Badge tone="ok">{`완료 ${count('done')}`}</Badge>
              <Badge tone={count('failed') > 0 ? 'err' : 'neutral'}>{`실패 ${count('failed')}`}</Badge>
              {count('unknown') > 0 && <Badge tone="neutral">{`알 수 없음 ${count('unknown')}`}</Badge>}
            </span>
            {open && (
              <div id={`campaign-body-${g.id}`}>
                <DataTable caption={`캠페인 ${g.id} arm ${g.runs.length}`} columns={cols} rows={g.runs} rowKey={(r) => r.id} selectedKey={selectedId} empty="arm이 없습니다." testId={`campaign-table-${g.id}`} />
              </div>
            )}
          </div>
        );
      })}
    </Card>
  );
}

/** The run list without campaign arms (they have their own grouped card). */
function withoutCampaignRuns(runs: Loadable<ArchiveRunList>): Loadable<ArchiveRunList> {
  const d = runs.data;
  if (!d || !d.runs.some((r) => campaignIdOf(r))) return runs;
  return { ...runs, data: { ...d, runs: d.runs.filter((r) => !campaignIdOf(r)) } };
}

export function Archive(): JSX.Element {
  const app = useApp();
  const a = app.archive;
  const connected = app.health.status === 'ok';
  const gens = useMemo(() => generationsOf(a.generations.data), [a.generations.data]);
  const rows = a.candidates.data?.rows ?? [];
  const run = a.detail.data;
  const judge = judgeNameOf(a.baselines.data, run);
  const anchorId = a.baselines.data?.anchor?.candidate_id;
  const anchorRow = anchorId ? rows.find((r) => r.candidate_id === anchorId && r.tier) ?? null : null;
  const baseRows = useMemo(() => buildBaselineRows(a.baselines.data, judge, anchorRow), [a.baselines.data, judge, anchorRow]);
  const judgeRow = baseRows.find((r) => r.isJudge);
  const baselineMap = isNum(judgeRow?.map50_95) ? judgeRow.map50_95 : isNum(run?.judge_baseline?.map50_95) ? run.judge_baseline.map50_95 : null;
  const maxGen = gens.reduce((m, g) => Math.max(m, g.generation), 0);
  const version = `${gens.length}|${gens[gens.length - 1]?.hypervolume ?? ''}|${rows.length}|${a.detail.data?.status ?? ''}`;
  const notes = a.generations.data?.notes;
  const noteText = Array.isArray(notes) ? notes.join(' ') : (notes ?? null);
  const plainRuns = useMemo(() => withoutCampaignRuns(a.runs), [a.runs]);
  const campaigns = useMemo(() => groupCampaigns(a.runs.data?.runs ?? []), [a.runs.data]);

  return (
    <div>
      <div className="page-head">
        <h1>NAS 아카이브</h1>
        {a.runId && (
          <button className="btn btn-sm" type="button" onClick={a.reload} data-testid="archive-reload">
            이 run 새로고침
          </button>
        )}
      </div>
      <p className="muted" style={{ marginTop: -8 }}>
        ETRI_NAS yolo_ga_full 탐색 결과를 읽기 전용으로 보여줍니다. 수치는 아티팩트 값 그대로이며 결측은 —입니다.
      </p>
      <ConnectGate />
      {connected && (
        <>
          <RunPicker runs={plainRuns} selectedId={a.runId} onSelect={a.selectRun} onRefresh={() => void a.refreshRuns()} />
          <CampaignGroups groups={campaigns} selectedId={a.runId} onSelect={a.selectRun} />
          {a.runId && a.detail.status === 'error' && !run && (
            <Banner tone="err">
              <span data-testid="run-load-error" className="wrap-any">
                run {a.runId}을(를) 불러오지 못했습니다: {a.detail.error}
              </span>{' '}
              <button className="btn btn-sm" type="button" onClick={a.reload}>다시 시도</button>
            </Banner>
          )}
          {a.runId && <OverviewCard state={a.detail} onRetry={a.reload} onOpenCandidate={a.openCandidate} />}
          {a.runId && <LiveCard run={run} live={a.live} poll={a.poll} />}
          <BaselineTable state={a.baselines} run={run} candidates={rows} runSelected={!!a.runId} onRetry={a.reload} />
          {a.runId && (
            <>
              <div data-testid="generations-section">
                {a.generations.status === 'error' && !a.generations.data && (
                  <Banner tone="err">
                    <span data-testid="generations-error" className="wrap-any">세대 정보를 불러오지 못했습니다: {a.generations.error}</span>
                  </Banner>
                )}
                {a.generations.status === 'loading' && !a.generations.data && <p className="muted" role="status">세대 정보 불러오는 중…</p>}
                {a.generations.data && (
                  <>
                    <GenerationChart
                      generations={gens}
                      selected={a.gen}
                      onSelect={a.selectGen}
                      baselineMap={baselineMap}
                      baselineName={judge}
                      baselineTierNote={judgeRow?.tierText ?? 'final 티어'}
                      note={noteText}
                    />
                    <SourceNote source={a.generations.data.source} warnings={a.generations.data.warnings} />
                  </>
                )}
              </div>
              {a.gen !== null && <GenerationDetail runId={a.runId} generation={a.gen} version={version} maxGeneration={maxGen} onClose={() => a.selectGen(null)} onOpenCandidate={a.openCandidate} onSelectGeneration={a.selectGen} />}
              {a.candidates.status === 'error' && !a.candidates.data && (
                <Banner tone="err">
                  <span data-testid="candidates-error" className="wrap-any">후보 목록을 불러오지 못했습니다: {a.candidates.error}</span>
                </Banner>
              )}
              {a.candidates.status === 'loading' && !a.candidates.data && <p className="muted" role="status">후보 불러오는 중…</p>}
              {a.candidates.data && (
                <>
                  <ArchiveScatter rows={rows} generations={gens} baselines={a.baselines.data} judgeName={judge} onOpen={a.openCandidate} />
                  <CandidateTable rows={rows} onOpen={a.openCandidate} />
                  {a.candidates.data.warnings.length > 0 && <SourceNote warnings={a.candidates.data.warnings} />}
                </>
              )}
              {run && <PlotsCard run={run} />}
              {run && <FidelityCard run={run} />}
              {run && <ConfirmCard run={run} />}
            </>
          )}
        </>
      )}
    </div>
  );
}
