/** Candidate table (design 4.1-6): filters (generation, tier, status, role, front only), sorting, and a button per row that opens the modal. */
import { useMemo, useState } from 'react';
import { DataTable, type Column } from '../DataTable';
import { Badge, Card, CheckField, SelectField } from '../ui';
import type { ArchiveCandidateRow } from '../../archive/types';
import { DASH, fmtDelta, fmtInt, fmtLat, fmtMap } from '../../archive/format';

const STATUS_TONE: Record<string, 'ok' | 'err' | 'warn' | 'neutral'> = { ok: 'ok', train_fail: 'err', oom: 'err', eval_fail: 'err', invalid: 'err', degenerate: 'warn', prune: 'warn', gpu_busy: 'warn' };

export function rowKey(r: ArchiveCandidateRow): string {
  return `${r.candidate_id}|${r.tier ?? ''}`;
}

export function CandidateTable({ rows, onOpen, caption = '후보 평가 행', testId = 'candidate-table', defaultGen }: { rows: readonly ArchiveCandidateRow[]; onOpen: (cid: string) => void; caption?: string; testId?: string; defaultGen?: string }): JSX.Element {
  const [gen, setGen] = useState(defaultGen ?? '');
  const [tier, setTier] = useState('');
  const [status, setStatus] = useState('');
  const [role, setRole] = useState('');
  const [front, setFront] = useState(false);
  const gens = useMemo(() => [...new Set(rows.map((r) => r.generation))].sort((a, b) => a - b).map(String), [rows]);
  const tiers = useMemo(() => [...new Set(rows.map((r) => r.tier).filter((t): t is string => !!t))].sort(), [rows]);
  const statuses = useMemo(() => [...new Set(rows.map((r) => r.status))].sort(), [rows]);
  const roles = useMemo(() => [...new Set(rows.flatMap((r) => r.roles ?? []))].sort(), [rows]);
  const shown = rows.filter((r) => (!gen || String(r.generation) === gen) && (!tier || r.tier === tier) && (!status || r.status === status) && (!role || (r.roles ?? []).includes(role)) && (!front || r.on_front2d || r.on_front4d));

  const cols: Column<ArchiveCandidateRow>[] = [
    { key: 'cid', header: '후보', cell: (r) => <button className="btn btn-sm" type="button" onClick={() => onOpen(r.candidate_id)} aria-label={`${r.candidate_id} (${r.tier ?? '티어 없음'}) 상세 열기`} data-testid={`cand-open-${r.candidate_id}`}>{r.candidate_id}</button>, sort: (r) => r.candidate_id },
    { key: 'gen', header: '세대', cell: (r) => r.generation, sort: (r) => r.generation, num: true },
    { key: 'tier', header: '티어', cell: (r) => (r.tier ?? DASH) + (r.is_final ? ' ◆' : ''), sort: (r) => r.tier },
    { key: 'status', header: '상태', cell: (r) => <Badge tone={STATUS_TONE[r.status] ?? 'neutral'} title={r.error ?? undefined}>{r.status}</Badge>, sort: (r) => r.status },
    { key: 'mode', header: 'mode', cell: (r) => r.lineage?.mode ?? DASH, sort: (r) => r.lineage?.mode },
    { key: 'map', header: 'mAP50-95', cell: (r) => fmtMap(r.metrics?.map50_95), sort: (r) => r.metrics?.map50_95 ?? null, num: true },
    { key: 'sr', header: 'small recall', cell: (r) => fmtMap(r.metrics?.small_target_recall), sort: (r) => r.metrics?.small_target_recall ?? null, num: true },
    { key: 'lat', header: 'latency [ms]', cell: (r) => fmtLat(r.metrics?.latency_median_ms), sort: (r) => r.metrics?.latency_median_ms ?? null, num: true },
    { key: 'params', header: 'params_deploy', cell: (r) => fmtInt(r.metrics?.params_deploy), sort: (r) => r.metrics?.params_deploy ?? null, num: true },
    { key: 'dmap', header: 'Δ mAP vs baseline*', cell: (r) => fmtDelta(r.delta_vs_baseline?.map50_95), sort: (r) => r.delta_vs_baseline?.map50_95 ?? null, num: true },
    { key: 'dlat', header: 'Δ latency [ms]*', cell: (r) => fmtDelta(r.delta_vs_baseline?.latency_median_ms, 2), sort: (r) => r.delta_vs_baseline?.latency_median_ms ?? null, num: true },
    { key: 'roles', header: '역할', cell: (r) => <span className="tag-list">{(r.roles ?? []).map((x) => <Badge key={x} tone="info">{x}</Badge>)}{(r.roles ?? []).length === 0 ? DASH : null}</span> },
    { key: 'front', header: '정면', cell: (r) => <span className="tag-list">{r.on_front2d && <Badge tone="err">2D</Badge>}{r.on_front4d && <Badge tone="err">4D</Badge>}{!r.on_front2d && !r.on_front4d ? DASH : null}</span>, sort: (r) => (r.on_front4d ? 0 : r.on_front2d ? 1 : 2) },
  ];

  return (
    <Card title="후보표" id="archive-candidates">
      <div className="filters" style={{ marginBottom: 10 }}>
        <SelectField label="세대" value={gen} onChange={setGen} allowEmpty="전체" options={gens} testId="cand-filter-gen" />
        <SelectField label="티어" value={tier} onChange={setTier} allowEmpty="전체" options={tiers} testId="cand-filter-tier" />
        <SelectField label="상태" value={status} onChange={setStatus} allowEmpty="전체" options={statuses} testId="cand-filter-status" />
        <SelectField label="역할" value={role} onChange={setRole} allowEmpty="전체" options={roles} testId="cand-filter-role" />
        <CheckField label="정면만" checked={front} onChange={setFront} testId="cand-filter-front" />
      </div>
      <DataTable caption={`${caption} ${shown.length} / ${rows.length}`} columns={cols} rows={shown} rowKey={rowKey} testId={testId} maxHeight empty="조건에 맞는 후보가 없습니다." />
      <p className="help" style={{ marginTop: 6 }}>
        * Δ = 후보 − 판정 기준선 (기준선은 final 티어, 후보 행은 해당 행의 티어 값 — 티어가 다르면 참고용). ◆ = final 티어 행. 값이 없으면 —로 표시하며 0으로 채우지 않습니다.
      </p>
    </Card>
  );
}
