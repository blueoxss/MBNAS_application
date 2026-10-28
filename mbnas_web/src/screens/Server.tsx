/**
 * 서버 연결·실행 검토 (Server): API origin + in-memory bearer token, Health, presets, Validate with
 * requested/resolved diff and immutable hash, Run (idempotent), run list / load by id, pause/resume/cancel,
 * and export download.
 * API origin: empty = same origin. In production the gateway (mbnas_server, port 8710) serves this bundle itself,
 * so same origin is the gateway; in development the Vite dev proxy forwards /api to 127.0.0.1:8710.
 */
import { useState } from 'react';
import { DeviceTargetsTable, RegistryCapsTable, ResolvedVariantBadge, SearchSpaceState } from '../components/Capabilities';
import { DataTable, type Column } from '../components/DataTable';
import { FinalistsCard, RunKindBadges } from '../components/FinalRun';
import { Badge, Banner, Card, KV, NumberField, TextField } from '../components/ui';
import { canFinalRetrain, checkFinalForm, defaultTopK, NO_FINALIST_TEXT } from '../lib/finalretrain';
import type { Run } from '../api/types';
import { isNonterminal } from '../api/types';
import { DASH, fmtTime } from '../lib/format';
import { useApp, type AppModel } from '../state/context';
import { PolicyLabel } from './Settings';

function json(v: unknown): string {
  if (v === undefined) return DASH;
  const s = JSON.stringify(v);
  return s.length > 160 ? `${s.slice(0, 157)}...` : s;
}

function ConnectionCard({ app }: { app: AppModel }): JSX.Element {
  const o = app.originCheck;
  return (
    <Card title="서버 연결 · Connection">
      <div className="grid-2">
        <TextField
          label="API origin"
          mono
          value={app.originInput}
          onChange={app.setOriginInput}
          placeholder="(empty = same origin: the gateway serving this page)"
          error={o.ok ? null : o.reason}
          help="Empty: same origin. The gateway serves this console and the API together (production); during development the Vite dev proxy forwards /api to the local gateway (127.0.0.1:8710). Remote API: https://host[:port] (http:// only for localhost / 127.0.0.1); that gateway must allow this page's exact origin in CORS (MBNAS_GW_CORS_ORIGINS)."
          testId="origin-input"
        />
        <TextField
          label="Bearer token (MBNAS_GW_API_TOKEN)"
          type="password"
          mono
          value={app.token}
          onChange={app.setToken}
          autoComplete="off"
          help="Held in memory only. Never stored, put into a URL, exported or logged. Reload clears it."
          testId="token-input"
        />
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn btn-primary" type="button" disabled={!app.client || app.health.status === 'loading'} onClick={() => void app.connect()} data-testid="connect-btn">
          {app.health.status === 'loading' ? 'Connecting…' : 'Connect / Health check'}
        </button>
        {!app.token && <span className="help">No token set: requests are sent without Authorization.</span>}
      </div>
      {app.health.status === 'error' && (
        <div style={{ marginTop: 10 }}>
          <Banner tone="err">
            <span data-testid="connection-error">Connection failed: {app.health.error}</span>
          </Banner>
        </div>
      )}
    </Card>
  );
}

function HealthCard({ app }: { app: AppModel }): JSX.Element | null {
  const h = app.health.data;
  if (!h) return null;
  const reg = h.registry ?? [];
  const caps = h.capabilities as Record<string, unknown>;
  const devCols: Column<Record<string, unknown>>[] = [
    { key: 'name', header: 'Device', cell: (d) => String(d.name ?? d.device_name ?? d.id ?? DASH) },
    { key: 'mem', header: 'Memory', cell: (d) => json(d.memory_mb ?? d.total_memory_mb ?? d.mem) },
    { key: 'raw', header: 'Details', cell: (d) => <span className="mono wrap-any">{json(d)}</span> },
  ];
  return (
    <Card title="Health">
      <div className="row" style={{ marginBottom: 8 }}>
        <Badge tone={h.status === 'ok' ? 'ok' : 'warn'}>status: {h.status}</Badge>
        <span className="wrap-any" style={{ minWidth: 0 }}>commit <span className="mono wrap-any" data-testid="health-commit" title={h.commit_sha || undefined}>{h.commit_sha || DASH}</span></span>
        <span>workers {h.worker_count ?? DASH}</span>
        <span>server time {fmtTime(h.server_time)}</span>
      </div>
      <KV rows={[['versions', <span className="mono wrap-any" key="v">{json(h.versions)}</span>]]} />
      <SearchSpaceState space={caps.search_space} />
      {app.rs.runId && <div style={{ marginTop: 6 }}><ResolvedVariantBadge run={app.rs.run} /></div>}
      <h3 style={{ marginTop: 12 }}>Device targets</h3>
      <DeviceTargetsTable targets={Array.isArray(caps.device_targets) ? (caps.device_targets as Record<string, unknown>[]) : []} />
      <h3 style={{ marginTop: 12 }}>Devices</h3>
      <DataTable caption="Devices reported by the server" columns={devCols} rows={h.devices as Record<string, unknown>[]} rowKey={(d) => json(d)} empty="No devices reported." />
      <h3 style={{ marginTop: 12 }}>Baseline registry</h3>
      <RegistryCapsTable rows={reg} />
    </Card>
  );
}

function PresetsCard({ app }: { app: AppModel }): JSX.Element {
  const list = app.presets.data ?? [];
  const cols: Column<(typeof list)[number]>[] = [
    { key: 'id', header: 'Preset', cell: (p) => <span className="mono">{p.id}</span>, sort: (p) => p.id },
    { key: 'name', header: 'Name', cell: (p) => p.name },
    { key: 'purpose', header: 'Purpose', cell: (p) => (/smoke/i.test(p.purpose) ? <Badge tone="warn">{p.purpose} (not research values)</Badge> : p.purpose) },
    { key: 'mode', header: 'Mode', cell: (p) => p.execution_mode },
    { key: 'ns', header: 'Namespace', cell: (p) => <span className="mono">{p.namespace}</span> },
    { key: 'use', header: '', cell: (p) => <button className="btn btn-sm" type="button" disabled={app.locked} onClick={() => app.applyServerPreset(p)}>Use in editor</button> },
  ];
  return (
    <Card title="Presets (GET /api/v1/protocols/presets)">
      <div className="row">
        <button className="btn" type="button" disabled={!app.client || app.presets.status === 'loading'} onClick={() => void app.loadPresets()} data-testid="load-presets-btn">
          Load presets
        </button>
      </div>
      {app.presets.status === 'error' && <div style={{ marginTop: 10 }}><Banner tone="err">{app.presets.error}</Banner></div>}
      {app.presets.status === 'ok' && <div style={{ marginTop: 10 }}><DataTable caption="Server presets" columns={cols} rows={list} rowKey={(p) => p.id} empty="No presets." testId="presets-table" /></div>}
    </Card>
  );
}

/** A resolved_diff row where the request had no value (absent/null) and the server filled in a default. */
export function isDefaulted(d: { requested?: unknown; resolved?: unknown }): boolean {
  return (d.requested === undefined || d.requested === null) && d.resolved !== undefined && d.resolved !== null;
}

function ValidateCard({ app }: { app: AppModel }): JSX.Element {
  const v = app.validation;
  const r = v.response;
  const diffCols: Column<NonNullable<typeof r>['resolved_diff'] extends infer D ? (D extends (infer E)[] ? E : never) : never>[] = [
    { key: 'field', header: 'Field', cell: (d) => <span className="mono">{d.field}</span> },
    { key: 'req', header: 'Requested', cell: (d) => (isDefaulted(d) ? <span className="muted" title="not set in the request">not set</span> : <span className="mono wrap-any">{json(d.requested)}</span>) },
    {
      key: 'res',
      header: 'Resolved',
      cell: (d) => (
        <span className="wrap-any">
          <span className="mono">{json(d.resolved)}</span>
          {isDefaulted(d) && (
            <>
              {' '}
              <Badge tone="info" title="the server applied a default for a field that was not requested">default applied</Badge>
            </>
          )}
        </span>
      ),
    },
    { key: 'why', header: 'Reason', cell: (d) => d.reason ?? DASH },
  ];
  const issueCols: Column<{ field: string; message: string; code?: string }>[] = [
    { key: 'field', header: 'Field', cell: (e) => <span className="mono">{e.field}</span> },
    { key: 'code', header: 'Code', cell: (e) => <Badge tone="err">{e.code ?? DASH}</Badge> },
    { key: 'msg', header: 'Message', cell: (e) => e.message },
  ];
  const clientErrors = app.clientIssues.filter((i) => i.severity === 'error').length;
  return (
    <Card title="Validate · 실행 전 검토">
      <div className="row">
        <button className="btn btn-primary" type="button" disabled={!app.client || v.status === 'pending' || app.health.status === 'loading'} onClick={() => void app.validate()} data-testid="validate-btn">
          {v.status === 'pending' ? 'Validating…' : 'Validate'}
        </button>
        {clientErrors > 0 && <Badge tone="warn">{clientErrors} client pre-check problem(s); the server decides</Badge>}
        {app.dirty && !app.rs.runId && <Badge tone="warn" title="editor or origin changed after validation">Changed since Validate - validate again</Badge>}
        {app.rs.runId && <Badge tone="info" title="loading a run copies its immutable resolved protocol into the editor">editor shows the loaded run's resolved protocol</Badge>}
      </div>
      <p className="help" style={{ marginTop: 8 }}>
        <PolicyLabel app={app} />
        {app.health.status === 'loading' ? ' · connecting (server presets load first)' : ''}
      </p>
      {v.status === 'error' && !r && (
        <div style={{ marginTop: 10 }}>
          <Banner tone="err">{v.error}</Banner>
        </div>
      )}
      {r && (
        <div className="stack" style={{ marginTop: 12 }} data-testid="validation-result">
          <div className="row">
            <Badge tone={r.valid ? 'ok' : 'err'}>{r.valid ? 'VALID' : 'INVALID'}</Badge>
            {r.protocol_hash && <span>protocol_hash <span className="mono wrap-any" data-testid="protocol-hash">{r.protocol_hash}</span></span>}
            {r.validation_id && <span>validation_id <span className="mono">{r.validation_id}</span></span>}
          </div>
          {r.errors.length > 0 && <DataTable caption={`Errors (${r.errors.length})`} columns={issueCols} rows={r.errors} rowKey={(e) => `${e.field}:${e.code}:${e.message}`} testId="validate-errors" />}
          {(r.warnings?.length ?? 0) > 0 && <DataTable caption={`Warnings (${r.warnings?.length})`} columns={issueCols.map((c) => (c.key === 'code' ? { ...c, cell: (e: { code?: string }) => <Badge tone="warn">{e.code ?? DASH}</Badge> } : c))} rows={r.warnings ?? []} rowKey={(e) => `${e.field}:${e.code}:${e.message}`} />}
          {/* an INVALID result makes no resolution claim, so the diff is shown only for a valid result */}
          {r.valid && (r.resolved_diff?.length ?? 0) > 0 && <DataTable caption="Requested vs resolved" columns={diffCols} rows={r.resolved_diff ?? []} rowKey={(d) => d.field} testId="resolved-diff" />}
          <KV rows={[['capabilities', <span className="mono wrap-any" key="c">{json(r.capabilities)}</span>]]} />
        </div>
      )}
    </Card>
  );
}

function RunCard({ app }: { app: AppModel }): JSX.Element {
  const s = app.submitState;
  return (
    <Card title="Run · 실행">
      <p className="help">Run sends the resolved protocol returned by Validate with its hash and an Idempotency-Key. It is enabled only after a valid Validate of the current, unchanged editor state.</p>
      <div className="row">
        <button className="btn btn-primary" type="button" disabled={!app.runnable || s.status === 'pending'} onClick={() => void app.submit()} data-testid="run-btn">
          {s.status === 'retryable' ? 'Retry Run (same Idempotency-Key)' : s.status === 'pending' ? 'Submitting…' : 'Run'}
        </button>
        {!app.runnable && (
          <span className="help" data-testid="run-disabled-reason">
            {app.rs.runId ? 'A run is loaded; unload it (or fork it in Settings) and Validate to start another run.' : app.validation.status === 'idle' ? 'Validate first.' : app.dirty ? 'Editor changed - Validate again.' : 'Validation is not valid.'}
          </span>
        )}
        {app.locked && <Badge tone="warn">a non-terminal run is loaded</Badge>}
      </div>
      {s.status !== 'idle' && s.status !== 'pending' && (
        <div style={{ marginTop: 10 }}>
          <Banner tone={s.status === 'created' ? 'ok' : s.status === 'retryable' ? 'warn' : 'err'}>
            <span data-testid="submit-message">{s.message}</span>
          </Banner>
          {s.errors.length > 0 && (
            <ul>
              {s.errors.map((e, i) => (
                <li key={i} className="mono">{json(e)}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

function RunsCard({ app }: { app: AppModel }): JSX.Element {
  const [id, setId] = useState('');
  const cols: Column<Run>[] = [
    { key: 'id', header: 'Run', cell: (r) => <span className="mono wrap-any">{r.id}</span>, sort: (r) => r.id },
    { key: 'st', header: 'Status', cell: (r) => <Badge tone={isNonterminal(r.status) ? 'info' : r.status === 'COMPLETED' ? 'ok' : 'err'}>{r.status}</Badge>, sort: (r) => r.status },
    { key: 'ns', header: 'Namespace', cell: (r) => r.namespace ?? DASH },
    { key: 'ts', header: 'Created', cell: (r) => fmtTime(r.created_at), sort: (r) => r.created_at },
    { key: 'act', header: '', cell: (r) => <button className="btn btn-sm" type="button" onClick={() => void app.loadRun(r.id)}>Load</button> },
  ];
  return (
    <Card title="기존 Run 불러오기 · Load run">
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div style={{ flex: '1 1 260px' }}>
          <TextField label="Run id" mono value={id} onChange={setId} testId="run-id-input" />
        </div>
        <button className="btn" type="button" disabled={!app.client || !id.trim()} onClick={() => void app.loadRun(id.trim())} data-testid="load-run-btn">
          Load by id
        </button>
        <button className="btn" type="button" disabled={!app.client} onClick={() => void app.refreshRuns()} data-testid="list-runs-btn">
          List runs
        </button>
      </div>
      {app.loadError && <div style={{ marginTop: 10 }}><Banner tone="err"><span data-testid="load-run-error" role="alert" className="wrap-any">Cannot load run {app.loadError.runId}: {app.loadError.message}</span></Banner></div>}
      {app.runs.status === 'error' && <Banner tone="err">{app.runs.error}</Banner>}
      {app.runs.status === 'ok' && <div style={{ marginTop: 10 }}><DataTable caption="Runs (newest first)" columns={cols} rows={app.runs.data ?? []} rowKey={(r) => r.id} empty="The server has no runs." /></div>}
    </Card>
  );
}

function ControlCard({ app }: { app: AppModel }): JSX.Element | null {
  const [reason, setReason] = useState('');
  const [confirmCancel, setConfirmCancel] = useState(false);
  const run = app.rs.run;
  if (!run) return null;
  const st = run.status;
  const busy = app.controlState.status === 'pending';
  return (
    <Card title="Run 제어 · Control">
      <div className="row" style={{ marginBottom: 8 }}>
        <span className="mono wrap-any">{run.id}</span>
        <Badge tone={isNonterminal(st) ? 'info' : st === 'COMPLETED' ? 'ok' : 'err'}>{st}</Badge>
        <Badge title="event stream state">stream: {app.rs.conn}{app.rs.connDetail ? ` (${app.rs.connDetail})` : ''}</Badge>
        <span className="muted">last seq {app.rs.lastSeq}</span>
      </div>
      <div className="row" style={{ marginBottom: 8 }} data-testid="run-kind-row">
        <RunKindBadges run={run} onLoad={(id) => void app.loadRun(id)} />
      </div>
      <TextField label="Reason (optional, recorded with the action)" value={reason} onChange={setReason} testId="control-reason" />
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn" type="button" disabled={busy || st !== 'RUNNING'} onClick={() => void app.control('pause', reason)} data-testid="pause-btn">Pause</button>
        <button className="btn" type="button" disabled={busy || st !== 'PAUSED'} onClick={() => void app.control('resume', reason)} data-testid="resume-btn">Resume</button>
        {!confirmCancel ? (
          <button className="btn btn-danger" type="button" disabled={busy || !isNonterminal(st) || st === 'CANCEL_REQUESTED'} onClick={() => setConfirmCancel(true)} data-testid="cancel-btn">Cancel run…</button>
        ) : (
          <>
            <button className="btn btn-danger" type="button" onClick={() => { setConfirmCancel(false); void app.control('cancel', reason); }} data-testid="cancel-confirm-btn">Confirm cancel</button>
            <button className="btn" type="button" onClick={() => setConfirmCancel(false)}>Keep running</button>
          </>
        )}
        <button className="btn" type="button" onClick={() => void app.exportRun()} disabled={app.exportState.status === 'pending'} data-testid="export-btn">Download export (zip)</button>
        <button className="btn" type="button" onClick={app.detachRun}>Unload run</button>
      </div>
      {app.controlState.message && <div style={{ marginTop: 10 }}><Banner tone={app.controlState.status === 'error' ? 'err' : 'ok'}>{app.controlState.message}</Banner></div>}
      {app.exportState.message && <div style={{ marginTop: 10 }}><Banner tone={app.exportState.status === 'error' ? 'err' : 'ok'}>Export: {app.exportState.message}</Banner></div>}
    </Card>
  );
}

function FinalRetrainForm({ app }: { app: AppModel }): JSX.Element {
  const run = app.rs.run as Run;
  const [topK, setTopK] = useState<number | null>(() => defaultTopK(run));
  const [budget, setBudget] = useState<number | null>(null);
  const [reason, setReason] = useState('');
  const s = app.finalState.parentRunId === run.id ? app.finalState : null;
  const check = checkFinalForm({ topK, budget, reason });
  const pending = s?.status === 'pending';
  const retry = s?.status === 'retryable';
  const budgetShown = budget !== null ? check.budgetError : null;
  return (
    <Card title="Final Retrain · 최종 재학습" id="final-retrain">
      <div data-testid="final-retrain-panel">
        <p className="help">
          Starts a separate FINAL_RETRAIN child run for this COMPLETED search run. The server picks the finalists with the frozen rule from L2 rows only (never a lower rung); the final evaluation split is used only there. The recipe (scratch, epochs, horizon) comes from this run's resolved protocol.
        </p>
        <div className="grid-3">
          <NumberField
            label="Top-k per arm and seed"
            value={topK}
            onChange={setTopK}
            step={1}
            min={1}
            error={check.topKError}
            help={`Default from the resolved protocol: ${defaultTopK(run) ?? 'not set (server default)'}.`}
            testId="final-topk"
          />
          <NumberField
            label="Final budget (required)"
            unit="GPU-hours"
            value={budget}
            onChange={setBudget}
            min={0}
            error={budgetShown}
            help="Required and greater than 0. Separate from the search budget; an empty budget is never unlimited."
            testId="final-budget"
          />
          <TextField label="Reason (optional, recorded)" value={reason} onChange={setReason} testId="final-reason" />
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button
            className="btn btn-primary"
            type="button"
            disabled={!app.client || !check.ok || pending}
            onClick={() => check.request && void app.startFinalRetrain(check.request)}
            data-testid="final-run-btn"
          >
            {retry ? 'Retry Final Retrain (same Idempotency-Key)' : pending ? 'Submitting…' : 'Start final retraining'}
          </button>
          {!check.ok && (
            <span className="help" data-testid="final-disabled-reason">
              {check.budgetError && budget === null ? 'Enter a budget (GPU-hours, > 0).' : 'Fix the highlighted field.'}
            </span>
          )}
        </div>
        {s && s.status === 'created' && (
          <div style={{ marginTop: 10 }} data-testid="final-result">
            <Banner tone={s.finalistCount === 0 ? 'warn' : 'ok'}>
              <span data-testid="final-message">{s.message}</span>
            </Banner>
            <KV
              rows={[
                ['child run id', <span className="mono wrap-any" key="c" data-testid="final-child-id">{s.childRunId}</span>],
                ['finalist_count', <span key="n" data-testid="final-finalist-count">{s.finalistCount ?? DASH}</span>],
                ['parent run id', <span className="mono wrap-any" key="p">{s.parentRunId}</span>],
                ['Idempotency-Key', <span className="mono wrap-any" key="k">{s.key}</span>],
              ]}
            />
            {s.finalistCount === 0 && <p className="help">0 finalists: the child run reports NO_FINALIST ({NO_FINALIST_TEXT}).</p>}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn btn-primary" type="button" onClick={() => s.childRunId && void app.loadRun(s.childRunId)} data-testid="final-load-child">
                Load child run
              </button>
            </div>
          </div>
        )}
        {s && (s.status === 'conflict' || s.status === 'rejected' || s.status === 'retryable') && (
          <div style={{ marginTop: 10 }} data-testid="final-result">
            <Banner tone={s.status === 'retryable' ? 'warn' : 'err'}>
              <span data-testid="final-error" data-error-kind={s.errorKind ?? ''}>
                {s.errorKind && s.errorKind !== 'NO_FINALIST' && s.errorKind !== 'RETRYABLE' && s.errorKind !== 'OTHER' ? <strong>{s.errorKind}: </strong> : null}
                {s.errorKind === 'NO_FINALIST' ? <strong>NO_FINALIST: </strong> : null}
                {s.message}
              </span>
            </Banner>
            {s.errors.length > 0 && (
              <ul>
                {s.errors.map((e, i) => (
                  <li key={i} className="mono">{json(e)}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

/** Panel for a loaded, COMPLETED search run; FINAL_RETRAIN runs show their finalists instead. */
function FinalRetrainCard({ app }: { app: AppModel }): JSX.Element | null {
  const run = app.rs.run;
  if (!run) return null;
  if (run.kind === 'FINAL_RETRAIN') return <FinalistsCard run={run} />;
  if (!canFinalRetrain(run)) return null;
  return <FinalRetrainForm key={run.id} app={app} />;
}

export function Server(): JSX.Element {
  const app = useApp();
  return (
    <div>
      <div className="page-head">
        <h1>서버 연결 · 실행 검토</h1>
      </div>
      <ConnectionCard app={app} />
      <HealthCard app={app} />
      <PresetsCard app={app} />
      <ValidateCard app={app} />
      <RunCard app={app} />
      <ControlCard app={app} />
      <FinalRetrainCard app={app} />
      <RunsCard app={app} />
    </div>
  );
}
