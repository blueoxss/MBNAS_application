/** Live card: GPUs, the running run's stage / watchdog, driver status; polled every 10 s by the context. */
import { Badge, Banner, Card } from '../ui';
import type { ArchiveLive, ArchiveRun } from '../../archive/types';
import { DASH, fmtDur, fmtInt, fmtStamp, fmtVal } from '../../archive/format';
import type { ArchivePoll, Loadable } from '../../state/context';
import { ARCHIVE_POLL_MS } from '../../state/context';

export function LiveCard({ run, live, poll }: { run: ArchiveRun | null; live: Loadable<ArchiveLive>; poll: ArchivePoll }): JSX.Element | null {
  const data = live.data ?? run?.live ?? null;
  if (run?.status !== 'running' && !poll.active) return null;
  const mine = data?.running_runs?.find((x) => x.id === run?.id) ?? data?.running_runs?.[0];
  const pct = mine && typeof mine.elapsed_s === 'number' && typeof mine.budget_s === 'number' && mine.budget_s > 0 ? Math.min(100, (mine.elapsed_s / mine.budget_s) * 100) : null;
  return (
    <Card title="라이브" id="archive-live">
      <div className="stack" data-testid="live-card">
        <p className="help" data-testid="poll-status">
          {poll.active ? `${ARCHIVE_POLL_MS / 1000}초마다 갱신 중` : '갱신 중지'} · 마지막 성공 {poll.lastOkAt ? fmtStamp(poll.lastOkAt) : DASH}
        </p>
        {poll.failures > 0 && (
          <Banner tone="warn">
            <span data-testid="poll-error" className="wrap-any">
              갱신 실패 {poll.failures}회 — 마지막으로 받은 데이터를 유지하고 다음 주기에 다시 시도합니다. ({poll.lastError})
            </span>
          </Banner>
        )}
        <div className="kpis">
          <div className="kpi"><div className="v">{mine?.stage ?? DASH}</div><div className="l">현재 단계</div></div>
          <div className="kpi"><div className="v">{fmtDur(mine?.elapsed_s)}</div><div className="l">S1 경과{typeof mine?.budget_s === 'number' ? ` / 예산 ${fmtDur(mine.budget_s)}` : ''}</div></div>
          <div className="kpi"><div className="v">{mine?.nb_pid_alive == null ? DASH : mine.nb_pid_alive ? '살아 있음' : '종료'}</div><div className="l">notebook 프로세스</div></div>
          <div className="kpi"><div className="v">{mine?.kernel_pid_alive == null ? DASH : mine.kernel_pid_alive ? '살아 있음' : '종료'}</div><div className="l">kernel 프로세스</div></div>
        </div>
        {pct !== null && (
          <div className="progress" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label="예산 대비 경과">
            <span style={{ width: `${pct}%` }} />
          </div>
        )}
        {mine?.watchdog_last && <p className="help mono wrap-any">watchdog: {mine.watchdog_last}</p>}
        {data?.gpus == null ? (
          <p className="muted" data-testid="gpu-unavailable">GPU 사용량: 서버가 NVML 값을 제공하지 않음 ({live.status === 'error' ? live.error : DASH}).</p>
        ) : (
          <ul className="gpu-list" data-testid="gpu-list">
            {data.gpus.map((g) => (
              <li key={g.index}>
                <Badge tone="neutral">GPU {g.index}</Badge> {g.name ?? DASH}: {fmtInt(g.used_mib)} / {fmtInt(g.total_mib)} MiB · 사용률 {fmtVal(g.util_percent)} %
              </li>
            ))}
          </ul>
        )}
        {(data?.drivers ?? []).map((d) => (
          <details key={d.path}>
            <summary className="mono wrap-any">{d.path}</summary>
            <pre className="log" tabIndex={0}>{(d.pipeline_log_tail ?? []).join('\n') || '(로그 없음)'}</pre>
          </details>
        ))}
      </div>
    </Card>
  );
}
