/**
 * 실험 설정 (Settings): server paths, baselines, common recipe, rung table with weights/thresholds,
 * anchors and hard constraints, GA/Random budget, benchmark device, final retraining, presets.
 * Derived values (added epochs, fixed horizon, effective batch, weight sums) are read-only.
 * While a loaded run is non-terminal everything is locked (policy is immutable) with a fork action.
 * The policy comes from the server (presets / a loaded run); before connecting the editor is empty and a banner says
 * so. No protocol template is embedded in the bundle.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Badge, Banner, Card, CellNumber, CheckField, IntListField, KV, NumberField, SelectField, TextField } from '../components/ui';
import { evaluationEnabled } from '../lib/generation';
import { TrainingPanel } from '../components/TrainingPanel';
import { weightsSum } from '../lib/fitness';
import { DASH } from '../lib/format';
import { addedEpochs, addRung, baselinesOf, effectiveBatch, fixedHorizon, getIn, isEmptyPolicy, isPilotSmoke, PATH_FIELDS, removeLastRung, rungsOf, stableStringify, WEIGHT_KEYS, type BaselineEntry, type Policy } from '../lib/policy';
import { fieldKey, issuesFor, type Issue } from '../lib/validate';
import { DEFAULT_PRESET_ID, isPolicyLocked, useApp, type AppModel } from '../state/context';

function useIssues(app: AppModel): Issue[] {
  const server = (app.validation.response?.errors ?? []).map((e): Issue => ({ field: e.field, message: `[server] ${e.message}`, code: e.code ?? 'SERVER', severity: 'error' }));
  return useMemo(() => [...app.clientIssues, ...server], [app.clientIssues, server]);
}

function err(issues: Issue[], field: string): string | null {
  const hit = issuesFor(issues, field).filter((i) => i.severity === 'error');
  return hit.length ? hit.map((i) => `${i.code}: ${i.message}`).join(' ') : null;
}

/** Resolved value reported by the last Validate for a requested field (from resolved_diff, then resolved). */
export function resolvedHint(app: AppModel, field: string): string | null {
  const v = app.validation.response;
  if (!v || app.validation.status !== 'done') return null;
  const diff = (v.resolved_diff ?? []).find((d) => fieldKey(d.field) === fieldKey(field));
  if (diff) return `resolved: ${JSON.stringify(diff.resolved)}${diff.reason ? ` (${diff.reason})` : ''}`;
  const r = getIn(v.resolved, field);
  const req = getIn(app.policy, field);
  if (r !== undefined && stableStringify(r) !== stableStringify(req)) return `resolved: ${JSON.stringify(r)}`;
  if (r !== undefined) return `resolved: ${JSON.stringify(r)} (same as requested)`;
  return null;
}

function num(p: Policy, path: string): number | null {
  const v = getIn(p, path);
  return typeof v === 'number' ? v : null;
}
function txt(p: Policy, path: string): string {
  const v = getIn(p, path);
  return typeof v === 'string' ? v : '';
}

function withCurrent(options: string[], current: string): string[] {
  return current && !options.includes(current) ? [current, ...options] : options;
}

/** Which policy the editor holds, always visible (Settings preset card and the Server Validate card). */
export function PolicyLabel({ app }: { app: AppModel }): JSX.Element {
  return (
    <span className="wrap-any">
      Editor policy: <strong data-testid="policy-label">{app.presetLabel}</strong>
      {app.presetAutoApplied && (
        <>
          {' '}
          <Badge tone="info" title="applied automatically after the connection because the editor was still empty; any edit or preset choice replaces it">
            <span data-testid="policy-auto-applied">auto-applied on connect</span>
          </Badge>
        </>
      )}
    </span>
  );
}

/** Before a connection there is no policy to edit: say where it comes from. */
export function ConnectFirstBanner({ app }: { app: AppModel }): JSX.Element | null {
  if (app.health.status === 'ok') {
    if (!isEmptyPolicy(app.policy) || app.rs.run) return null;
    return (
      <Banner tone="warn">
        <span data-testid="settings-empty-banner">
          No policy is loaded{app.presets.status === 'error' ? ' (the server presets could not be loaded)' : ''}. Choose a server preset below, or load a run on the Server screen.
        </span>
      </Banner>
    );
  }
  return (
    <Banner tone="info">
      <span data-testid="settings-connect-banner">
        Not connected: the experiment policy comes from the server. Connect on the Server screen; the server preset {DEFAULT_PRESET_ID} (design example) is then applied
        automatically while this editor is still empty (anything you enter first is kept). Server presets are examples, not research values.
      </span>{' '}
      <button className="btn btn-sm" type="button" onClick={() => app.setScreen('server')}>
        Go to server connection
      </button>
    </Banner>
  );
}

export function PresetBar({ app }: { app: AppModel }): JSX.Element {
  const serverPresets = app.presets.data ?? [];
  const options = serverPresets.map((p) => ({ value: p.id, label: `${p.name} (server preset${p.purpose ? `, ${p.purpose}` : ''})` }));
  const pilot = isPilotSmoke(app.policy);
  const onPick = (id: string): void => {
    const p = serverPresets.find((x) => x.id === id);
    if (p) app.applyServerPreset(p);
  };
  function exportPolicy(): void {
    const blob = new Blob([JSON.stringify(app.policy, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'mbnas-policy.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }
  return (
    <Card title="프리셋 · Preset">
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div style={{ flex: '1 1 320px' }}>
          <SelectField label="Preset" value="" allowEmpty={`current: ${app.presetLabel}`} options={options} onChange={(v) => v && onPick(v)} />
        </div>
        <button className="btn" type="button" disabled={!app.client || app.presets.status === 'loading'} onClick={() => void app.loadPresets()}>
          서버 프리셋 불러오기
        </button>
        <button className="btn" type="button" onClick={exportPolicy}>
          정책 JSON 내보내기 (토큰 미포함)
        </button>
      </div>
      {pilot && (
        <p style={{ marginTop: 10 }}>
          <Badge className="b-pilot" tone="warn">
            PILOT · SMOKE · not research values
          </Badge>{' '}
          <span className="muted">Tiny subset and 2/4/6 epochs only for pipeline smoke tests. Results are not model accuracy.</span>
        </p>
      )}
      <p style={{ marginTop: 10 }}>
        <PolicyLabel app={app} />
      </p>
      {app.presets.status === 'error' && <Banner tone="err">{app.presets.error}</Banner>}
      {app.presets.status === 'ok' && <p className="help">{serverPresets.length} server preset(s) loaded: {serverPresets.map((p) => p.id).join(', ') || DASH}</p>}
      <p className="help">
        Presets come from the server (GET /api/v1/protocols/presets): {DEFAULT_PRESET_ID} is a design example and PILOT_SMOKE a smoke preset; neither holds research values
        (status of the current policy: {txt(app.policy, 'status') || DASH}). The server validator is authoritative.
      </p>
    </Card>
  );
}

function PathsCard({ app, issues }: { app: AppModel; issues: Issue[] }): JSX.Element {
  const allow = app.health.data?.allowlists;
  const evaluated = evaluationEnabled(app.policy);
  return (
    <Card title="서버 데이터 경로 · server Linux paths">
      <Banner tone="info">
        These are absolute paths on the <strong>server (Linux)</strong>, not paths on this computer. The server checks existence, allowlists, symlinks and split leakage.
      </Banner>
      <div className="stack">
        {PATH_FIELDS.map((f) => {
          // with evaluation OFF the evaluation-only paths stay visible, disabled, with their preserved values.
          const preserved = !evaluated && f !== 'output_root';
          return (
            <fieldset key={f} className="lock" disabled={preserved} data-testid={`path-${f}-group`} data-preserved={String(preserved)}>
              <TextField
                label={f}
                mono
                value={txt(app.policy, `console.paths.${f}`)}
                onChange={(v) => app.editField(`console.paths.${f}`, v)}
                error={preserved ? null : err(issues, `console.paths.${f}`)}
                help={preserved ? '성능 평가 OFF: 이 값은 보존되며 이번 생성 작업에서 사용·검증하지 않습니다. 평가를 다시 켜면 그대로 사용됩니다.' : f === 'output_root' ? 'Run output (DB, checkpoints, artifacts). Must be writable and inside the server output allowlist.' : f === 'dataset_yaml' ? 'YOLO data.yaml on the server.' : 'Distinct from the other split roots.'}
                testId={`path-${f}`}
              />
            </fieldset>
          );
        })}
      </div>
      {allow && (
        <p className="help wrap-any">
          Server allowlists - dataset roots: {(allow.dataset_roots ?? []).join(', ') || DASH}; output roots: {(allow.output_roots ?? []).join(', ') || DASH}
        </p>
      )}
    </Card>
  );
}

/**
 * Fixed baseline order: the `console.baselines` of the server preset DEFAULT_BASELINE14 (the 14 default baselines) once
 * the presets are loaded; without them, the order of the current policy itself. Nothing is hard-coded in the bundle.
 */
function referenceBaselines(app: AppModel): { list: BaselineEntry[]; fromPreset: boolean } {
  const fixed = baselinesOf(app.presets.data?.find((p) => p.id === DEFAULT_PRESET_ID)?.protocol);
  return fixed.length ? { list: fixed, fromPreset: true } : { list: baselinesOf(app.policy), fromPreset: false };
}

function BaselinesCard({ app }: { app: AppModel }): JSX.Element {
  const list = (getIn(app.policy, 'console.baselines') as { id: string; enabled: boolean; name?: string; family?: string }[] | undefined) ?? [];
  const reg = new Map((app.health.data?.registry ?? []).map((r) => [r.id, r]));
  const ref = referenceBaselines(app);
  const extra = ref.fromPreset ? list.filter((b) => !ref.list.some((x) => x.id === b.id)) : [];
  const setEnabled = (id: string, enabled: boolean): void => {
    const exists = list.some((b) => b.id === id);
    const meta = ref.list.find((x) => x.id === id);
    const next = exists ? list.map((b) => (b.id === id ? { ...b, enabled } : b)) : [...list, { id, name: meta?.name, family: meta?.family, enabled }];
    app.editField('console.baselines', next);
  };
  const row = (id: string, label: string, family: string): JSX.Element => {
    const entry = list.find((b) => b.id === id);
    const enabled = !!entry?.enabled;
    const r = reg.get(id);
    const unavailable = !!r && r.status !== 'AVAILABLE';
    return (
      <li key={id} style={{ listStyle: 'none' }}>
        <CheckField
          label={
            <>
              <span className="mono">{id}</span> {label !== id && <span className="muted">({label})</span>} <Badge>{family}</Badge>{' '}
              {r ? <Badge tone={r.status === 'AVAILABLE' ? 'ok' : r.status === 'UNVERIFIED' ? 'warn' : 'err'}>{r.status}</Badge> : <Badge title="connect to the server to see registry status">registry: unknown</Badge>}
            </>
          }
          help={unavailable ? `Unavailable: ${r?.reason ?? 'no reason reported'}${enabled ? ' (uncheck to exclude)' : ''}` : undefined}
          checked={enabled}
          disabled={unavailable && !enabled}
          onChange={(v) => setEnabled(id, v)}
          testId={`baseline-${id}`}
        />
      </li>
    );
  };
  return (
    <Card title={ref.fromPreset ? `Baseline14 (fixed order of the server preset ${DEFAULT_PRESET_ID})` : 'Baselines (order of the current policy)'}>
      {ref.list.length === 0 && <p className="muted" data-testid="baselines-empty">No baselines yet: connect to the server and load a preset (or a run).</p>}
      <ol className="stack" style={{ padding: 0, margin: 0 }}>
        {ref.list.map((b) => row(b.id, b.name ?? b.id, b.family ?? 'baseline'))}
      </ol>
      {extra.length > 0 && (
        <>
          <h3 style={{ marginTop: 14 }}>Preset-specific baselines (not part of baseline14)</h3>
          <ul className="stack" style={{ padding: 0, margin: 0 }}>
            {extra.map((b) => row(b.id, b.name ?? b.id, b.family ?? 'preset'))}
          </ul>
        </>
      )}
    </Card>
  );
}

/** Initialization help derived from the registry capabilities.init_modes (no per-model claims hard-coded). */
export function initializationHelp(initModes: readonly (readonly [string, readonly string[] | undefined])[]): string {
  const base =
    'scratch = true random init (comparison_group common_scratch); pretrained_native = the backend official weights, a separate native_pretrained comparison group, never mixed with scratch.';
  const rows = initModes.filter(([, m]) => Array.isArray(m)) as (readonly [string, readonly string[]])[];
  if (!rows.length) return `${base} Connect to the server to see which baselines support each mode.`;
  const ids = (f: (m: readonly string[]) => boolean): string[] => rows.filter(([, m]) => f(m)).map(([id]) => id);
  const both = ids((m) => m.includes('scratch') && m.includes('pretrained_native'));
  const scratchOnly = ids((m) => m.includes('scratch') && !m.includes('pretrained_native'));
  const nativeOnly = ids((m) => !m.includes('scratch') && m.includes('pretrained_native'));
  const rf = rows.filter(([id, m]) => /rf-?detr/i.test(id) && m.includes('scratch')).map(([id]) => id);
  const parts = [base];
  if (rf.length) parts.push(`${rf.join(', ')}: scratch is a true random init (no pretrained weights loaded); pretrained_native forms the separate native_pretrained comparison group.`);
  if (both.length) parts.push(`Both modes: ${both.join(', ')}.`);
  if (scratchOnly.length) parts.push(`scratch only: ${scratchOnly.join(', ')}.`);
  if (nativeOnly.length) parts.push(`pretrained_native only: ${nativeOnly.join(', ')}.`);
  return parts.join(' ');
}

function RecipeCard({ app, issues }: { app: AppModel; issues: Issue[] }): JSX.Element {
  const p = app.policy;
  const eff = effectiveBatch(p);
  const horizon = fixedHorizon(rungsOf(p));
  const reg = app.health.data?.registry ?? [];
  const initModes = reg.map((r) => [r.id, (r.capabilities as { init_modes?: string[] }).init_modes] as const).filter(([, m]) => Array.isArray(m));
  const size = (getIn(p, 'training.requested_input_size') as number[] | undefined) ?? [];
  const betas = (getIn(p, 'training.betas') as number[] | undefined) ?? [];
  const set = (path: string) => (v: unknown) => app.editField(path, v);
  const hint = (f: string): string | undefined => resolvedHint(app, f) ?? undefined;
  return (
    <Card title="공통 학습 recipe · common recipe (all candidates)">
      <div className="grid-3">
        <SelectField label="Initialization" value={txt(p, 'training.initialization')} options={withCurrent(['scratch', 'pretrained_native'], txt(p, 'training.initialization'))} onChange={set('training.initialization')} help={hint('training.initialization') ?? initializationHelp(initModes)} />
        <SelectField label="Optimizer" value={txt(p, 'training.optimizer')} options={withCurrent(['AdamW', 'SGD', 'Adam', 'auto'], txt(p, 'training.optimizer'))} onChange={set('training.optimizer')} help={hint('training.optimizer') ?? 'auto: the applied LR may differ from the input LR (see resolved).'} />
        <SelectField label="Precision (training)" value={txt(p, 'training.precision')} options={withCurrent(['amp_fp16_fp32', 'fp32', 'bf16'], txt(p, 'training.precision'))} onChange={set('training.precision')} help={hint('training.precision')} />
        <NumberField label="Initial learning rate lr_initial" value={num(p, 'training.lr_initial')} onChange={set('training.lr_initial')} error={err(issues, 'training.lr_initial')} help={hint('training.lr_initial')} />
        <NumberField label="Final LR ratio lr_final_ratio" unit="x lr_initial" value={num(p, 'training.lr_final_ratio')} onChange={set('training.lr_final_ratio')} error={err(issues, 'training.lr_final_ratio')} />
        <SelectField label="LR scheduler" value={txt(p, 'training.scheduler')} options={withCurrent(['cosine_global_search_horizon', 'linear_global_search_horizon'], txt(p, 'training.scheduler'))} onChange={set('training.scheduler')} />
        <NumberField label="Scheduler horizon (fixed, derived)" unit="epochs" value={horizon} readOnly help="= cumulative epochs of the last rung; the schedule is fixed at the start and rungs are stop points." />
        <NumberField label="Warmup" unit="epochs" value={num(p, 'training.warmup_epochs')} onChange={set('training.warmup_epochs')} error={err(issues, 'training.warmup_epochs')} help="Once at the beginning; must be < L0 cumulative epochs." />
        <NumberField label="Weight decay" value={num(p, 'training.weight_decay')} onChange={set('training.weight_decay')} error={err(issues, 'training.weight_decay')} />
        <NumberField label="Beta 1 (betas[0])" value={betas[0] ?? null} onChange={(v) => app.editField('training.betas', [v, betas[1] ?? 0.999])} />
        <NumberField label="Beta 2 (betas[1])" value={betas[1] ?? null} onChange={(v) => app.editField('training.betas', [betas[0] ?? 0.9, v])} />
        <NumberField label="Batch per GPU" unit="images" value={num(p, 'training.batch_per_gpu')} onChange={set('training.batch_per_gpu')} error={err(issues, 'training.batch_per_gpu')} help={hint('training.batch_per_gpu')} />
        <NumberField label="Gradient accumulation" unit="steps" value={num(p, 'training.gradient_accumulation')} onChange={set('training.gradient_accumulation')} error={err(issues, 'training.gradient_accumulation')} />
        <NumberField label="GPUs per candidate (world size)" value={num(p, 'training.gpus_per_candidate')} onChange={set('training.gpus_per_candidate')} error={err(issues, 'training.gpus_per_candidate')} />
        <NumberField label="Effective batch (derived)" unit="images" value={eff} readOnly help="batch per GPU x accumulation x GPUs" />
        <NumberField label="Requested input height" unit="px" value={size[0] ?? null} onChange={(v) => app.editField('training.requested_input_size', [v, size[1] ?? v])} error={err(issues, 'training.requested_input_size')} help={hint('training.requested_input_size') ?? 'The resolved (actual) shape is reported by the server after Validate.'} />
        <NumberField label="Requested input width" unit="px" value={size[1] ?? null} onChange={(v) => app.editField('training.requested_input_size', [size[0] ?? v, v])} />
        <TextField label="Augmentation profile" value={txt(p, 'training.augmentation_profile')} onChange={set('training.augmentation_profile')} mono help="PINNED_NATIVE_PROFILE_REQUIRED = use the backend's pinned native profile." />
        <NumberField label="close_mosaic" unit="epochs" value={num(p, 'training.close_mosaic_epochs')} onChange={set('training.close_mosaic_epochs')} help="Fixed against the whole horizon." />
        <TextField label="Early-stop policy" value={txt(p, 'training.candidate_early_stop')} onChange={set('training.candidate_early_stop')} mono help="disabled_for_search_rung_comparability keeps rungs comparable." />
        <IntListField label="Search seeds (proposal RNG)" value={getIn(p, 'console.search.seeds') as number[] | undefined} onChange={set('console.search.seeds')} error={err(issues, 'console.search.seeds')} help="Search seeds drive architecture proposals." />
        <TextField label="Training seed policy" value={txt(p, 'comparison.training_seed_policy')} onChange={set('comparison.training_seed_policy')} mono help="Training seeds are separate from search seeds and logged per trial." />
      </div>
      <h3 style={{ marginTop: 14 }}>Loss profile (separate from fitness weights; read-only mapping)</h3>
      <KV
        rows={[
          ['loss_profile', <span className="mono" key="l">{txt(p, 'training.loss_profile') || DASH}</span>],
          ['change loss weights at promotion', String(getIn(p, 'training.change_loss_weights_at_promotion') ?? DASH)],
          ['native loss per baseline', initModes.length || reg.length ? reg.map((r) => `${r.id}: ${String((r.capabilities as { native_loss?: unknown }).native_loss ?? DASH)}`).join('; ') : 'connect to the server to see the backend mapping'],
          ['init modes per baseline', initModes.length ? initModes.map(([id, m]) => `${id}: ${(m as string[]).join('/')}`).join('; ') : DASH],
        ]}
      />
      <p className="help">Loss weights shape gradients; fitness weights score candidates. They are never mixed.</p>
    </Card>
  );
}

function RungsCard({ app, issues, locked }: { app: AppModel; issues: Issue[]; locked: boolean }): JSX.Element {
  const rungs = rungsOf(app.policy);
  const added = addedEpochs(rungs);
  const setRung = (i: number, path: string, v: unknown): void => app.editField(`rungs.${i}.${path}`, v);
  return (
    <Card
      title="Rung table · 누적 epochs, 이름 있는 fitness weights, 승급 기준"
      actions={
        <div className="row">
          <button className="btn btn-sm" type="button" disabled={locked} onClick={() => app.setPolicy(addRung(app.policy))}>
            Add rung
          </button>
          <button className="btn btn-sm" type="button" disabled={locked || rungs.length <= 1} onClick={() => app.setPolicy(removeLastRung(app.policy))}>
            Remove last rung
          </button>
        </div>
      }
    >
      <p className="help">Epochs are cumulative targets. Added epochs are computed and cannot be typed. Weights must sum to 1 (|sum-1| ≤ 1e-6); there is no auto-normalisation.</p>
      <div className="table-wrap" role="region" aria-label="Rung table" tabIndex={0}>
        <fieldset className="lock" disabled={locked}>
        <table className="data" data-testid="rung-table">
          <caption className="sr-only">Rungs</caption>
          <thead>
            <tr>
              <th scope="col">Rung</th>
              <th scope="col">Cumulative epochs [epochs]</th>
              <th scope="col">Added (computed) [epochs]</th>
              {WEIGHT_KEYS.map((k) => (
                <th key={k} scope="col">
                  w_{k}
                </th>
              ))}
              <th scope="col">Weight sum</th>
              <th scope="col">Promote: score ≥ (0-1)</th>
              <th scope="col">Promote: AP ≥ (fraction 0-1)</th>
            </tr>
          </thead>
          <tbody>
            {rungs.map((r, i) => {
              const sum = weightsSum(r.fitness_weights ?? {});
              const bad = Math.abs(sum - 1) > 1e-6;
              return (
                <tr key={`${r.id}-${i}`}>
                  <th scope="row">{r.id}</th>
                  <td>
                    <CellNumber label={`${r.id} cumulative epochs`} value={r.epoch_target_total} onChange={(v) => setRung(i, 'epoch_target_total', v)} step={1} testId={`rung-${i}-epochs`} invalid={!!err(issues, `rungs.${i}.epoch_target_total`)} />
                    {err(issues, `rungs.${i}.epoch_target_total`) && <div className="field-err">{err(issues, `rungs.${i}.epoch_target_total`)}</div>}
                  </td>
                  <td>
                    <span data-testid={`rung-${i}-added`}>+{added[i] ?? DASH}</span>
                  </td>
                  {WEIGHT_KEYS.map((k) => (
                    <td key={k}>
                      <CellNumber label={`${r.id} weight ${k}`} value={r.fitness_weights?.[k]} onChange={(v) => setRung(i, `fitness_weights.${k}`, v)} testId={`rung-${i}-w-${k}`} />
                    </td>
                  ))}
                  <td>
                    <span data-testid={`rung-${i}-sum`} className={bad ? 'field-err' : ''}>
                      {sum.toFixed(6)} {bad ? '✖ must be 1' : '✔'}
                    </span>
                    {err(issues, `rungs.${i}.fitness_weights`) && <div className="field-err" role="alert">{err(issues, `rungs.${i}.fitness_weights`)}</div>}
                  </td>
                  <td>
                    {r.promotion ? <CellNumber label={`${r.id} promotion score threshold`} value={r.promotion.score_ge} onChange={(v) => setRung(i, 'promotion.score_ge', v)} testId={`rung-${i}-score`} /> : <span className="muted">final search rung</span>}
                  </td>
                  <td>{r.promotion ? <CellNumber label={`${r.id} promotion AP guard`} value={r.promotion.ap_ge} onChange={(v) => setRung(i, 'promotion.ap_ge', v)} testId={`rung-${i}-ap`} /> : DASH}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </fieldset>
      </div>
      <p className="help" data-testid="horizon-note">
        Fixed scheduler horizon (last rung cumulative target): {fixedHorizon(rungs) ?? DASH} epochs. Promotion is candidate-local: a pass never changes another candidate's epochs or weights.
      </p>
    </Card>
  );
}

function ConstraintsCard({ app, issues }: { app: AppModel; issues: Issue[] }): JSX.Element {
  const p = app.policy;
  const lat = (getIn(p, 'fitness.latency_bounds_ms') as number[] | undefined) ?? [];
  const siz = (getIn(p, 'fitness.size_bounds_MB') as number[] | undefined) ?? [];
  return (
    <Card title="Anchors · 고정 정규화 범위와 hard constraints">
      <p className="help">Reward anchors are fixed and shared by all generations (never the observed min/max). Out-of-range values are clipped for the reward but the raw value is kept.</p>
      <div className="grid-3">
        <NumberField label="Latency anchor min" unit="ms" value={lat[0] ?? null} onChange={(v) => app.editField('fitness.latency_bounds_ms', [v, lat[1] ?? null])} error={err(issues, 'fitness.latency_bounds_ms')} />
        <NumberField label="Latency anchor max" unit="ms" value={lat[1] ?? null} onChange={(v) => app.editField('fitness.latency_bounds_ms', [lat[0] ?? null, v])} />
        <NumberField label="Hard constraint: latency ≤" unit="ms" value={num(p, 'hard_constraints.latency_le_ms')} onChange={(v) => app.editField('hard_constraints.latency_le_ms', v)} error={err(issues, 'hard_constraints.latency_le_ms')} />
        <NumberField label="Size anchor min" unit="MB (decimal)" value={siz[0] ?? null} onChange={(v) => app.editField('fitness.size_bounds_MB', [v, siz[1] ?? null])} error={err(issues, 'fitness.size_bounds_MB')} />
        <NumberField label="Size anchor max" unit="MB (decimal)" value={siz[1] ?? null} onChange={(v) => app.editField('fitness.size_bounds_MB', [siz[0] ?? null, v])} />
        <NumberField label="Hard constraint: weights size ≤" unit="MB" value={num(p, 'hard_constraints.weights_size_le_MB')} onChange={(v) => app.editField('hard_constraints.weights_size_le_MB', v)} error={err(issues, 'hard_constraints.weights_size_le_MB')} />
      </div>
    </Card>
  );
}

export const POSTPROCESS_HELP = 'latency operating point; AP evaluation floor stays 0.001';

export function SearchCard({ app, issues }: { app: AppModel; issues: Issue[] }): JSX.Element {
  const p = app.policy;
  const evaluated = evaluationEnabled(p);
  const familyKey = stableStringify(getIn(p, 'console.search.families'));
  const [familyText, setFamilyText] = useState(((getIn(p, 'console.search.families') as string[] | undefined) ?? ['yolo11n', 'yolo26n']).join(', '));
  useEffect(() => { setFamilyText(((getIn(p, 'console.search.families') as string[] | undefined) ?? ['yolo11n', 'yolo26n']).join(', ')); }, [familyKey]);
  const arms = (getIn(p, 'console.search.arms') as string[] | undefined) ?? [];
  const toggle = (arm: string, on: boolean): void => app.editField('console.search.arms', on ? [...new Set([...arms, arm])] : arms.filter((a) => a !== arm));
  const set = (path: string) => (v: unknown) => app.editField(path, v);
  // optional contract fields: empty input removes the key (the server then records its default in resolved)
  const setOpt = (path: string) => (v: number | null) => app.editField(path, v === null ? undefined : v);
  const hint = (f: string): string => resolvedHint(app, f) ?? '';
  // postprocess is an object of optional keys: clearing the last one removes the object as well
  const setPP = (k: 'conf' | 'iou' | 'max_det') => (v: number | null) => {
    const cur = { ...((getIn(p, 'console.benchmark.postprocess') as Record<string, unknown> | undefined) ?? {}) };
    if (v === null) delete cur[k];
    else cur[k] = v;
    app.editField('console.benchmark.postprocess', Object.keys(cur).length ? cur : undefined);
  };
  return (
    <Card title="GA / Random 탐색 · budget · benchmark">
      <CheckField label="성능 평가 수행" checked={evaluated} onChange={set('console.search.evaluation_enabled')} testId="evaluation-enabled" help={evaluated ? '기존 탐색 방식과 단계별 학습·성능 평가·승급 정책을 적용합니다.' : '학습·성능 평가 없이, 구조 검사를 통과한 랜덤 모델 후보를 생성합니다.'} />
      {!evaluated && <>
        <Banner tone="info">실제 적용 방식: RandomGeneration. 요청한 Arms 설정은 보존됩니다. IndependentRandom 평가 비교 실험과 별개이며, 후보는 생성 순서로 표시됩니다.</Banner>
        <div className="grid-3">
          <NumberField label="요청 후보 수" value={num(p, 'console.search.candidate_count') ?? num(p, 'console.search.population') ?? 10} step={1} min={1} onChange={set('console.search.candidate_count')} error={err(issues, 'console.search.candidate_count')} testId="generation-count" />
          <NumberField label="최대 생성 시도 수" value={num(p, 'console.search.max_attempts')} step={1} min={1} placeholder="max(100, 후보 수 × 20)" onChange={setOpt('console.search.max_attempts')} error={err(issues, 'console.search.max_attempts')} />
          <NumberField label="클래스 수 nc (필수)" value={num(p, 'console.search.nc')} step={1} min={1} onChange={set('console.search.nc')} error={err(issues, 'console.search.nc')} testId="generation-nc" />
          <NumberField label="구조 확인 입력 크기" unit="px · square" value={num(p, 'console.search.input_size')} step={32} min={32} placeholder="640" onChange={setOpt('console.search.input_size')} error={err(issues, 'console.search.input_size')} />
          <TextField label="모델 계열 (쉼표 구분)" value={familyText} onChange={setFamilyText} onBlur={() => app.editField('console.search.families', familyText.split(',').map((x) => x.trim()).filter(Boolean))} help="서버 검색 공간이 지원하는 계열만 사용할 수 있습니다." />
          <IntListField label="생성 seed" value={(getIn(p, 'console.search.seeds') as number[] | undefined) ?? [0]} onChange={set('console.search.seeds')} error={err(issues, 'console.search.seeds')} />
          <CheckField label="동일 구조 중복 허용" checked={getIn(p, 'console.search.allow_duplicates') === true} onChange={set('console.search.allow_duplicates')} />
        </div>
      </>}
      <fieldset disabled={!evaluated} style={{ marginBottom: 12 }}>
        <legend>요청 탐색 방식 · Arms (평가 ON에서 적용)</legend>
        <CheckField label="GA" checked={arms.includes('GA')} onChange={(v) => toggle('GA', v)} />
        <CheckField label="IndependentRandom" checked={arms.includes('IndependentRandom')} onChange={(v) => toggle('IndependentRandom', v)} />
        {err(issues, 'console.search.arms') && <span className="field-err">{err(issues, 'console.search.arms')}</span>}
      </fieldset>
      <fieldset style={{ marginBottom: 12 }}>
        <legend>Search space</legend>
        <CheckField
          label="Allow native fallback"
          checked={getIn(p, 'console.search.allow_native_fallback') === true}
          onChange={(v) => app.editField('console.search.allow_native_fallback', v ? true : undefined)}
          help={<>Field <span className="mono wrap-any">console.search.allow_native_fallback</span>. Only used when the ETRI mixed pool cannot load. Searches the CNN + native attention fallback space only (not the ETRI mixed space). Default off: validation then fails with SEARCH_SPACE_UNAVAILABLE.</>}
          testId="allow-native-fallback"
        />
      </fieldset>
      <fieldset className="lock" disabled={!evaluated}>
      <div className="grid-3">
        <NumberField label="Population" unit="candidates / generation" value={num(p, 'console.search.population')} onChange={set('console.search.population')} step={1} error={err(issues, 'console.search.population')} />
        <NumberField label="Generations" value={num(p, 'console.search.generations')} onChange={set('console.search.generations')} step={1} error={err(issues, 'console.search.generations')} />
        <NumberField label="Mutation rate" unit="0-1" value={num(p, 'console.search.mutation_rate')} onChange={set('console.search.mutation_rate')} error={err(issues, 'console.search.mutation_rate')} />
        <NumberField
          label="GA crossover rate"
          unit="0-1"
          value={num(p, 'console.search.crossover_rate')}
          onChange={setOpt('console.search.crossover_rate')}
          min={0}
          max={1}
          step="any"
          placeholder="0.5"
          error={err(issues, 'console.search.crossover_rate')}
          help={`GA crossover probability per child (GA arm only). Empty = server default 0.5, recorded in resolved. ${hint('console.search.crossover_rate')}`.trim()}
          testId="crossover-rate"
        />
        <NumberField label="Budget (required)" unit="GPU-hours per arm and seed" value={num(p, 'console.search.budget_gpu_hours')} onChange={set('console.search.budget_gpu_hours')} error={err(issues, 'console.search.budget_gpu_hours')} help="An empty budget is never unlimited." testId="budget" />
        <TextField label="Benchmark device" value={txt(p, 'console.benchmark.device')} onChange={set('console.benchmark.device')} help="Target device name for latency measurement." />
        <SelectField label="Benchmark precision" value={txt(p, 'console.benchmark.precision')} options={withCurrent(['FP32', 'FP16', 'INT8'], txt(p, 'console.benchmark.precision'))} onChange={set('console.benchmark.precision')} />
        <NumberField label="Benchmark batch" value={num(p, 'console.benchmark.batch')} onChange={set('console.benchmark.batch')} step={1} error={err(issues, 'console.benchmark.batch')} />
        <NumberField label="Warmup iterations" value={num(p, 'console.benchmark.warmup')} onChange={set('console.benchmark.warmup')} step={1} error={err(issues, 'console.benchmark.warmup')} />
        <NumberField
          label="Postprocess conf"
          unit="0-1"
          value={num(p, 'console.benchmark.postprocess.conf')}
          onChange={setPP('conf')}
          min={0}
          max={1}
          placeholder="0.25"
          error={err(issues, 'console.benchmark.postprocess.conf')}
          help={`${POSTPROCESS_HELP} Default 0.25. ${hint('console.benchmark.postprocess.conf')}`.trim()}
          testId="pp-conf"
        />
        <NumberField
          label="Postprocess iou"
          unit="0-1"
          value={num(p, 'console.benchmark.postprocess.iou')}
          onChange={setPP('iou')}
          min={0}
          max={1}
          placeholder="0.7"
          error={err(issues, 'console.benchmark.postprocess.iou')}
          help={`${POSTPROCESS_HELP} Default 0.7. ${hint('console.benchmark.postprocess.iou')}`.trim()}
          testId="pp-iou"
        />
        <NumberField
          label="Postprocess max_det"
          unit="detections"
          value={num(p, 'console.benchmark.postprocess.max_det')}
          onChange={setPP('max_det')}
          step={1}
          min={1}
          placeholder="300"
          error={err(issues, 'console.benchmark.postprocess.max_det')}
          help={`${POSTPROCESS_HELP} Default 300. ${hint('console.benchmark.postprocess.max_det')}`.trim()}
          testId="pp-maxdet"
        />
        <NumberField label="Repeats" value={num(p, 'console.benchmark.repeats')} onChange={set('console.benchmark.repeats')} step={1} error={err(issues, 'console.benchmark.repeats')} />
        <NumberField label="Baseline epoch target" unit="epochs" value={num(p, 'console.baseline_epoch_target')} onChange={set('console.baseline_epoch_target')} step={1} />
        <NumberField label="Baseline budget" unit="GPU-hours" value={num(p, 'console.baseline_budget_gpu_hours')} onChange={set('console.baseline_budget_gpu_hours')} />
      </div>
      </fieldset>
    </Card>
  );
}

function FinalCard({ app }: { app: AppModel }): JSX.Element {
  const p = app.policy;
  const set = (path: string) => (v: unknown) => app.editField(path, v);
  return (
    <Card title="최종 재학습 · final retraining (separate from search)">
      <Banner tone="info">Final retraining has its own horizon, budget and results. The final evaluation split is never used for search, promotion or parent selection.</Banner>
      <div className="grid-3">
        <NumberField label="Final cumulative epochs" unit="epochs" value={num(p, 'final_retraining.epoch_target_total')} onChange={(v) => { app.editField('final_retraining.epoch_target_total', v); app.editField('final_retraining.scheduler_horizon_epochs', v); }} step={1} />
        <NumberField label="Final horizon (= epochs)" unit="epochs" value={num(p, 'final_retraining.scheduler_horizon_epochs')} readOnly />
        <NumberField label="Top-k per arm and seed" value={num(p, 'final_retraining.top_k_per_arm_per_seed')} onChange={set('final_retraining.top_k_per_arm_per_seed')} step={1} />
        <NumberField label="Final budget (separate)" unit="GPU-hours" value={num(p, 'final_retraining.budget_GPU_hours_separate')} onChange={set('final_retraining.budget_GPU_hours_separate')} help="Empty means final retraining cannot start." />
        <TextField label="Initialization" value={txt(p, 'final_retraining.initialization')} onChange={set('final_retraining.initialization')} mono />
        <TextField label="Finalist selection" value={txt(p, 'final_retraining.finalist_selection')} onChange={set('final_retraining.finalist_selection')} mono />
      </div>
    </Card>
  );
}

export function IssuesCard({ issues, empty = false }: { issues: Issue[]; empty?: boolean }): JSX.Element {
  const errors = issues.filter((i) => i.severity === 'error');
  const warns = issues.filter((i) => i.severity === 'warning');
  const infos = issues.filter((i) => i.severity === 'info');
  return (
    <Card title="입력 점검 · pre-checks (server remains authoritative)">
      {empty && errors.length === 0 && warns.length === 0 && <Banner tone="info"><span data-testid="issues-empty-policy">Nothing to pre-check: no policy is loaded yet (connect to the server, choose a preset or load a run).</span></Banner>}
      {!empty && errors.length === 0 && warns.length === 0 && <Banner tone="ok">No client-side problems found. Run Validate on the Server screen for the authoritative check.</Banner>}
      {errors.length > 0 && (
        <ul data-testid="issue-list" className="stack" style={{ margin: 0, paddingLeft: 18 }}>
          {errors.map((i, k) => (
            <li key={k}>
              <Badge tone="err">{i.code}</Badge> <span className="mono">{i.field}</span>: {i.message}
            </li>
          ))}
        </ul>
      )}
      {warns.length > 0 && (
        <ul className="stack" style={{ margin: '8px 0 0', paddingLeft: 18 }}>
          {warns.map((i, k) => (
            <li key={k}>
              <Badge tone="warn">{i.code}</Badge> <span className="mono">{i.field}</span>: {i.message}
            </li>
          ))}
        </ul>
      )}
      {infos.length > 0 && (
        <ul className="stack" data-testid="issue-info-list" style={{ margin: '8px 0 0', paddingLeft: 18 }}>
          {infos.map((i, k) => (
            <li key={k}>
              <Badge tone="info">{i.code}</Badge> {i.message}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Lock({ locked, children }: { locked: boolean; children: ReactNode }): JSX.Element {
  return (
    <fieldset className="lock" disabled={locked}>
      {children}
    </fieldset>
  );
}

export function Settings(): JSX.Element {
  const app = useApp();
  const issues = useIssues(app);
  const locked = isPolicyLocked(app.rs.run);
  const evaluated = evaluationEnabled(app.policy);
  return (
    <div>
      <div className="page-head">
        <h1>실험 설정</h1>
        {isPilotSmoke(app.policy) && (
          <Badge className="b-pilot" tone="warn">
            PILOT · SMOKE · not research values
          </Badge>
        )}
      </div>
      {locked && (
        <Banner tone="warn" role="alert">
          <strong data-testid="lock-banner">Policy is locked</strong> while run <span className="mono">{app.rs.runId}</span> is {app.rs.run?.status}. Epochs, weights, thresholds and recipe are immutable during a run. To change them, fork a new version.{' '}
          <button className="btn btn-sm" type="button" onClick={app.forkFromRun}>
            Fork as new version
          </button>
        </Banner>
      )}
      {!locked && app.rs.run && (
        <Banner tone="info">
          Showing the resolved protocol of run <span className="mono">{app.rs.runId}</span> ({app.rs.run.status}). Edits start a new, unvalidated version.
        </Banner>
      )}
      <ConnectFirstBanner app={app} />
      {/* One disabled fieldset per card (not one around everything): the scrollable rung table must stay keyboard
          focusable (axe scrollable-region-focusable), so its fieldset lives inside its scroll container. */}
      <div data-testid="settings-fieldset" data-locked={String(locked)}>
        <Lock locked={locked}><PresetBar app={app} /></Lock>
        <Lock locked={locked}><PathsCard app={app} issues={issues} /></Lock>
        <Lock locked={locked}><SearchCard app={app} issues={issues} /></Lock>
        {!evaluated && <Banner tone="info">아래 학습·평가·승급 설정은 이번 생성 작업에 사용하지 않으며 보존됩니다. 성능 평가를 다시 켜면 그대로 복원됩니다.</Banner>}
        <Lock locked={locked || !evaluated}><BaselinesCard app={app} /></Lock>
        <Lock locked={locked || !evaluated}><RecipeCard app={app} issues={issues} /></Lock>
        <RungsCard app={app} issues={issues} locked={locked || !evaluated} />
        <Lock locked={locked || !evaluated}><ConstraintsCard app={app} issues={issues} /></Lock>
        <Lock locked={locked || !evaluated}><FinalCard app={app} /></Lock>
      </div>
      <TrainingPanel client={app.client} protocol={app.policy} onCreated={(id) => { void app.loadRun(id); app.setScreen('monitor'); }} />
      <IssuesCard issues={issues} empty={isEmptyPolicy(app.policy)} />
    </div>
  );
}
