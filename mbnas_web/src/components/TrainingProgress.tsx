import { useEffect, useMemo, useState } from 'react';
import type { ApiClient } from '../api/client';
import type { ApiEvent, Candidate } from '../api/types';
import { num, rec } from '../api/types';
import { fmtAny, fmtNum } from '../lib/format';
import { useApp } from '../state/context';
import { Card, KV, SelectField } from './ui';

export function progressScalars(progress: unknown): Record<string, number> {
  const p = rec(progress), out: Record<string, number> = {};
  for (const key of ['learning_rate', 'loss', 'train_loss', 'images_per_second', 'samples_per_second', 'throughput', 'epoch_seconds', 'batch_seconds']) if (num(p[key]) !== null) out[key] = p[key] as number;
  for (const name of ['native_metrics', 'losses']) for (const [key, value] of Object.entries(rec(p[name]))) if (num(value) !== null) out[`${name}.${key}`] = value as number;
  return out;
}

/** A recorded checkpoint: 1.5.0 rows carry `id` (ck-…) + sha256 and no path; a 1.4.x row carried `path` (kept as a fallback). */
export interface RecordedCheckpoint { id: string | null; path: string | null; state: string; epoch: number | null; sha256: string | null }

/**
 * DD-27: the latest `training_full` row of the candidate detail's `checkpoints` list (state PRESENT or PRUNED; a MISSING row is
 * used only when nothing else exists, and is shown as MISSING). A row is usable when it names the file: by checkpoint `id`
 * (contract 1.5.0) or, from an older server, by `path`. Nothing is guessed: no row, no reference, no value.
 */
export function latestTrainingFull(checkpoints: unknown): RecordedCheckpoint | null {
  const named = (v: unknown): v is string => typeof v === 'string' && v !== '';
  const rows = (Array.isArray(checkpoints) ? checkpoints : []).map(rec).filter((row) => row.kind === 'training_full' && (named(row.id) || named(row.path)));
  const rank = (row: Record<string, unknown>): [number, string] => [num(row.epoch) ?? -1, String(row.created_at ?? '')];
  const latest = (list: Record<string, unknown>[]): Record<string, unknown> | undefined => [...list].sort((a, b) => { const [ea, ca] = rank(a), [eb, cb] = rank(b); return eb - ea || (cb < ca ? -1 : cb > ca ? 1 : 0); })[0];
  const row = latest(rows.filter((r) => r.state === 'PRESENT' || r.state === 'PRUNED')) ?? latest(rows.filter((r) => r.state === 'MISSING'));
  return row ? { id: named(row.id) ? row.id : null, path: named(row.path) ? row.path : null, state: String(row.state), epoch: num(row.epoch), sha256: typeof row.sha256 === 'string' ? row.sha256 : null } : null;
}

/** Display of a recorded checkpoint: `ck-… · sha256 <12 hex>…` (1.5.0), the path only when an older server sent no id. */
export function checkpointRef(k: RecordedCheckpoint): string {
  if (k.id) return `${k.id}${k.sha256 ? ` · sha256 ${k.sha256.replace(/^sha256:/, '').slice(0, 12)}…` : ''}`;
  return k.path ?? '미기록';
}

/** Charts only recorded run-local candidate progress. Missing values remain absent, including after reconnect. */
export function TrainingProgress({ candidates, events, onOpen, client: clientProp, runId: runIdProp }: { candidates: Candidate[]; events: ApiEvent[]; onOpen: (candidate: Candidate) => void; client?: ApiClient | null; runId?: string | null }): JSX.Element | null {
  // Monitor passes neither: the connection and the loaded run come from the app context (tests may pass them directly).
  let app: ReturnType<typeof useApp> | null = null;
  try { app = useApp(); } catch { app = null; }
  const client = clientProp !== undefined ? clientProp : app?.client ?? null;
  const [wanted, setWanted] = useState('');
  const [metric, setMetric] = useState('learning_rate');
  const unique = [...new Map(candidates.map((c) => [c.candidate_id, c])).values()];
  const selected = unique.find((c) => c.candidate_id === wanted) ?? unique.find((c) => Object.keys(rec(c.progress)).length > 0) ?? unique[0];
  const records = useMemo(() => events.flatMap((event) => {
    if (event.type !== 'candidate.updated') return [];
    const row = rec(rec(event.payload).candidate ?? event.payload);
    const progress = rec(row.progress);
    if (row.candidate_id !== selected?.candidate_id || num(progress.epoch) === null) return [];
    return [{ seq: event.seq, epoch: progress.epoch as number, progress, scalars: progressScalars(progress) }];
  }), [events, selected?.candidate_id]);
  const runId = runIdProp !== undefined ? runIdProp : app?.rs.runId ?? null;
  const [recorded, setRecorded] = useState<RecordedCheckpoint | null>(null);
  const [recordedError, setRecordedError] = useState('');
  const needsRecord = !!selected && !selected.checkpoint_path;
  const progressEpoch = num(rec(selected?.progress).epoch);
  // DD-27: PAUSED / unevaluated rows carry no checkpoint_path; the checkpoint rows of the candidate detail do.
  useEffect(() => {
    let live = true;
    setRecorded(null); setRecordedError('');
    if (!needsRecord || !client || !runId || !selected) return;
    client.candidateDetail(runId, selected.candidate_id)
      .then((detail) => { if (live) setRecorded(latestTrainingFull(detail.checkpoints)); })
      .catch((e: unknown) => { if (live) setRecordedError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [needsRecord, client, runId, selected?.candidate_id, selected?.status, selected?.candidate_status, progressEpoch]);
  if (!selected) return null;
  const fromRecord = needsRecord ? recorded : null;
  const latest = rec(selected.progress), last = records[records.length - 1]?.progress;
  const progress = { ...last, ...latest };
  const scalar = progressScalars(progress);
  const keys = [...new Set([...records.flatMap((row) => Object.keys(row.scalars)), ...Object.keys(scalar)])];
  const active = keys.includes(metric) ? metric : keys[0] ?? '';
  const points = records.filter((row) => num(row.scalars[active]) !== null);
  const epoch = num(progress.epoch), target = num(progress.epoch_target_total ?? selected.epoch_target_total);
  const values = points.map((row) => row.scalars[active]);
  const lo = values.length ? Math.min(...values) : 0, hi = values.length ? Math.max(...values) : 1;
  const firstEpoch = points[0]?.epoch ?? 0, lastEpoch = points[points.length - 1]?.epoch ?? 1;
  const xy = points.map((row) => `${50 + (row.epoch - firstEpoch) / Math.max(1, lastEpoch - firstEpoch) * 680},${170 - (row.scalars[active] - lo) / Math.max(1e-15, hi - lo) * 140}`).join(' ');
  return <Card title="학습 진행 · 기록된 지표·checkpoint">
    <SelectField label="모니터링 모델" value={selected.candidate_id} options={unique.map((c) => ({ value: c.candidate_id, label: `${c.name} · ${c.candidate_id}` }))} onChange={setWanted} />
    <button className="btn btn-sm" type="button" onClick={() => onOpen(selected)}>선택 모델 구조·checkpoint·이미지</button>
    <KV rows={[
      ['상태 / 단계', `${selected.candidate_status ?? selected.status} / ${selected.rung}`],
      ['epoch / 목표', `${fmtAny(epoch)} / ${fmtAny(target)}`], ['step', fmtAny(progress.global_step)],
      ['진행률', epoch !== null && target !== null && target > 0 ? `${fmtNum(Math.min(100, Math.max(0, epoch / target * 100)), 1)} %` : '미기록'],
      ['learning rate', fmtAny(scalar.learning_rate)], ['loss', fmtAny(scalar.loss ?? scalar.train_loss)],
      ['처리 속도 [images/s]', fmtAny(scalar.images_per_second ?? scalar.samples_per_second ?? scalar.throughput)],
      ['checkpoint 상태', selected.checkpoint_state ?? fromRecord?.state ?? '미기록'],
      ['checkpoint', <span key="ck" className="mono wrap-any" data-testid="progress-checkpoint" title={fromRecord?.sha256 ?? selected.checkpoint_sha256 ?? undefined}>{selected.checkpoint_path ?? (fromRecord ? checkpointRef(fromRecord) : '미기록')}</span>],
      ['checkpoint 출처', <span key="cs" data-testid="progress-checkpoint-source">{selected.checkpoint_path ? '평가 행의 checkpoint_path' : fromRecord ? `후보 checkpoint 기록 (training_full${fromRecord.epoch !== null ? `, epoch ${fromRecord.epoch}` : ''})` : recordedError ? `후보 checkpoint 기록 조회 실패: ${recordedError}` : '기록 없음'}</span>],
      ['오류·중단 이유', selected.reason_code ?? '미기록'],
    ]} />
    {keys.length > 0 ? <>
      <SelectField label="기록 지표 그래프" value={active} options={keys} onChange={setMetric} />
      {points.length > 0 ? <svg viewBox="0 0 760 210" role="img" aria-label={`${active} by recorded epoch`} style={{ width: '100%', maxHeight: 240 }}>
        <path d="M 50 20 V 175 H 735" fill="none" stroke="currentColor" />
        <text x="5" y="26" fontSize="11" fill="currentColor">{fmtNum(hi, 5)}</text><text x="5" y="173" fontSize="11" fill="currentColor">{fmtNum(lo, 5)}</text>
        <polyline points={xy} fill="none" stroke="#256a9c" strokeWidth="2" />
        <text x="50" y="196" fontSize="12" fill="currentColor">epoch {firstEpoch}</text><text x="640" y="196" fontSize="12" fill="currentColor">epoch {lastEpoch}</text>
      </svg> : <p className="muted">현재 snapshot 값만 있습니다. 그래프용 epoch 이력은 미기록입니다.</p>}
    </> : <p className="muted">기록된 학습 scalar가 아직 없습니다. 미측정 loss·처리 속도를 추정하지 않습니다.</p>}
    <p className="help">현재 run의 보유 이벤트 구간만 표시합니다. 서로 다른 후보의 loss·평가 점수를 합치지 않습니다. ETA는 계산 근거가 없어 표시하지 않습니다.</p>
  </Card>;
}
