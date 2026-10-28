/** Server-capability views: device targets, registry export_trt / host-gpu-0 status, search-space state. */
import { DataTable, type Column } from './DataTable';
import { Badge } from './ui';
import type { Run } from '../api/types';
import { DASH } from '../lib/format';
import { getIn } from '../lib/policy';

type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Rec) : {});
const txt = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);

export interface DeviceTarget { id?: unknown; name?: unknown; kind?: unknown; available?: unknown; reason?: unknown }

export function DeviceTargetsTable({ targets }: { targets: readonly DeviceTarget[] }): JSX.Element {
  const cols: Column<DeviceTarget>[] = [
    { key: 'id', header: 'Target id', cell: (t) => <span className="mono">{txt(t.id) ?? DASH}</span> },
    { key: 'name', header: 'Name', cell: (t) => txt(t.name) ?? DASH },
    { key: 'avail', header: 'Available', cell: (t) => <Badge tone={t.available === true ? 'ok' : 'err'}>{t.available === true ? 'available' : 'unavailable'}</Badge> },
    { key: 'reason', header: 'Reason', cell: (t) => <span className="wrap-any">{txt(t.reason) ?? DASH}</span> },
  ];
  return <DataTable caption="Device targets (GET /health capabilities.device_targets)" columns={cols} rows={[...targets]} rowKey={(t) => String(t.id)} empty="No device targets reported." testId="device-targets-table" />;
}

export function SearchSpaceState({ space }: { space: unknown }): JSX.Element {
  const s = rec(space);
  const variant = txt(s.variant);
  return (
    <div className="row" data-testid="search-space-state">
      <span>search space</span>
      <Badge tone={variant === 'etri_mixed' ? 'ok' : 'warn'}>{variant ?? 'not reported'}</Badge>
      <span className="wrap-any">{txt(s.reason) ?? DASH}</span>
    </div>
  );
}

/** Resolved search-space variant of a loaded run (search_space_resolved.variant); nothing when absent. */
export function ResolvedVariantBadge({ run }: { run: Run | null | undefined }): JSX.Element | null {
  const v = txt(getIn(run?.resolved_protocol, 'console.search.search_space_resolved.variant'));
  if (!v) return null;
  return (
    <Badge tone={v === 'native_fallback' ? 'warn' : 'info'} title="resolved search space of this run">
      <span data-testid="resolved-variant">search space: {v}</span>
    </Badge>
  );
}

interface RegRow { id: string; family: string; status: string; reason?: string | null; capabilities?: unknown }

function StatusText({ status, reason }: { status: unknown; reason: unknown }): JSX.Element {
  const s = txt(status);
  if (!s) return <span>{DASH}</span>;
  const tone = s.startsWith('VERIFIED') ? 'ok' : s === 'NOT_RUN' ? 'warn' : 'err';
  const r = txt(reason);
  return (
    <span>
      <Badge tone={tone}>{s}</Badge>
      {r && <span className="help wrap-any" style={{ display: 'block' }}>{r}</span>}
    </span>
  );
}

const trtOf = (r: RegRow): Rec => rec(rec(r.capabilities).export_trt);
const gpuOf = (r: RegRow): Rec => rec(rec(rec(r.capabilities).devices)['host-gpu-0']);

export function RegistryCapsTable({ rows }: { rows: readonly RegRow[] }): JSX.Element {
  const cols: Column<RegRow>[] = [
    { key: 'id', header: 'Model', cell: (r) => <span className="mono">{r.id}</span>, sort: (r) => r.id },
    { key: 'family', header: 'Family', cell: (r) => r.family, sort: (r) => r.family },
    { key: 'status', header: 'Status', cell: (r) => <Badge tone={r.status === 'AVAILABLE' ? 'ok' : r.status === 'UNVERIFIED' ? 'warn' : 'err'}>{r.status}</Badge>, sort: (r) => r.status },
    { key: 'reason', header: 'Reason', cell: (r) => r.reason ?? DASH },
    { key: 'trt', header: 'export_trt', cell: (r) => <StatusText status={trtOf(r).status} reason={trtOf(r).reason} />, sort: (r) => String(trtOf(r).status ?? '') },
    { key: 'gpu', header: 'host-gpu-0', cell: (r) => <StatusText status={gpuOf(r).status} reason={gpuOf(r).reason} />, sort: (r) => String(gpuOf(r).status ?? '') },
    { key: 'adapter', header: 'Adapter', cell: (r) => <span className="mono wrap-any">{txt(rec(r.capabilities).adapter_class) ?? DASH}</span> },
  ];
  return <DataTable caption="Registry (status, reason, export_trt and host-gpu-0 device status per model)" columns={cols} rows={[...rows]} rowKey={(r) => r.id} maxHeight testId="registry-table" />;
}
