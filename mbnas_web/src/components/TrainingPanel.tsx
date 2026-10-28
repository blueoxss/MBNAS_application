import { useRef, useState } from 'react';
import { ApiError, type ApiClient } from '../api/client';
import type { TrainingRequest, TrainingSource, Preset } from '../api/types';
import { getIn, setIn, stableStringify, unsetIn, type Policy } from '../lib/policy';
import { IdempotencyStore } from '../lib/runflow';
import { ModelConfigUpload, type UploadedModel } from './ModelConfigUpload';
import { RegistryStructureButton } from './RegistryStructureButton';
import { Banner, Card, CheckField, NumberField, SelectField, TextField } from './ui';

export interface TrainingFields {
  dataset: string; output: string; checkpoint: string; epochs: number | null;
  batch: number | null; size: number | null; seed: number | null;
  optimizer: string; lr: number | null;
  trainRoot?: string; valRoot?: string; testRoot?: string;
}

/** Only this new run's copy is edited. Source generation/evaluation policy is immutable. */
export function trainingProtocol(policy: Policy, fields: TrainingFields, options: { omitEpochs?: boolean } = {}): Policy {
  let result = JSON.parse(JSON.stringify(policy)) as Policy;
  // evaluate_only has no training epochs: the UI neither shows nor sends a target (the server forces epochs=0).
  const epochValues: [string, unknown][] = options.omitEpochs ? [] : [
    ['console.training.epoch_target_total', fields.epochs], ['console.training.scheduler_horizon_epochs', fields.epochs],
  ];
  if (options.omitEpochs) {
    result = unsetIn(unsetIn(result, 'console.training.epoch_target_total'), 'console.training.scheduler_horizon_epochs');
  }
  const values: [string, unknown][] = [
    ['console.paths.dataset_yaml', fields.dataset], ['console.paths.output_root', fields.output],
    ...epochValues,
    ['console.training.seed', fields.seed], ['training.batch_per_gpu', fields.batch],
    ['training.requested_input_size', [fields.size, fields.size]], ['training.optimizer', fields.optimizer],
    ['training.lr_initial', fields.lr],
  ];
  for (const [path, value] of values) result = setIn(result, path, value);
  for (const [path, value] of [['train_root', fields.trainRoot], ['search_val_root', fields.valRoot], ['final_eval_root', fields.testRoot]] as const) if (value?.trim()) result = setIn(result, `console.paths.${path}`, value.trim());
  return result;
}

const stringAt = (p: Policy, k: string): string => String(getIn(p, k) ?? '');
const numberAt = (p: Policy, k: string, fallback: number): number => typeof getIn(p, k) === 'number' ? getIn(p, k) as number : fallback;

/**
 * The source of a new training run chosen in the panel (no fixed source): a baseline registry id, an uploaded user YAML
 * (1.5.0 default: `upload_id` + `model_name`, F5a) or, behind the "advanced: server path" toggle, a legacy server
 * `model_path` (owner-local deployments). The three are exclusive (contract TrainingRequest.source).
 */
export function chosenSource(kind: 'baseline' | 'user', model: string, upload: UploadedModel | null, serverPath: boolean): TrainingSource | null {
  if (kind === 'baseline') return model.trim() ? { kind, baseline_id: model.trim() } : null;
  if (serverPath) return model.trim() ? { kind, model_path: model.trim() } : null;
  return upload ? { kind, upload_id: upload.upload_id, model_name: upload.name } : null;
}

/** Shared for baseline, an uploaded (or, advanced, server-side) YAML model, evaluated search and generation-only candidates. */
export function TrainingPanel({ client, protocol, source: fixedSource, onCreated }: {
  client: ApiClient | null; protocol: Policy; source?: TrainingSource; onCreated?: (runId: string) => void;
}): JSX.Element {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetLoading, setPresetLoading] = useState(false);
  const [presetError, setPresetError] = useState('');
  const [presetLabel, setPresetLabel] = useState('원본에 보존된 설정');
  const [recipeOverride, setRecipeOverride] = useState<Policy | null>(null);
  const [jsonDraft, setJsonDraft] = useState('');
  const [jsonError, setJsonError] = useState('');
  const recipe = recipeOverride ?? protocol;
  const hasRecipe = !!getIn(recipe, 'training') && Array.isArray(getIn(recipe, 'rungs')) && (getIn(recipe, 'rungs') as unknown[]).length > 0 && !!getIn(recipe, 'fitness');
  const [kind, setKind] = useState<'baseline' | 'user'>('baseline');
  const [model, setModel] = useState('');
  const [upload, setUpload] = useState<UploadedModel | null>(null);
  const [serverPath, setServerPath] = useState(false);
  const [mode, setMode] = useState<TrainingRequest['mode']>('scratch');
  const [validation, setValidation] = useState(true);
  const [fields, setFields] = useState<TrainingFields>(() => ({
    dataset: stringAt(protocol, 'console.paths.dataset_yaml'), output: stringAt(protocol, 'console.paths.output_root'),
    checkpoint: '', trainRoot: stringAt(protocol, 'console.paths.train_root'), valRoot: stringAt(protocol, 'console.paths.search_val_root'), testRoot: stringAt(protocol, 'console.paths.final_eval_root'), epochs: numberAt(protocol, 'console.training.epoch_target_total', numberAt(protocol, 'console.extra.training.epoch_target_total', numberAt(protocol, 'console.baseline_epoch_target', 1))),
    batch: numberAt(protocol, 'training.batch_per_gpu', 1), size: (getIn(protocol, 'training.requested_input_size') as number[] | undefined)?.[0] ?? 640,
    seed: numberAt(protocol, 'console.training.seed', 0), optimizer: stringAt(protocol, 'training.optimizer') || 'AdamW',
    lr: numberAt(protocol, 'training.lr_initial', 0.001),
  }));
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<{ error: boolean; text: string; runId?: string; sha?: string | null; modelName?: string | null } | null>(null);
  const keys = useRef(new IdempotencyStore());
  const edit = <K extends keyof TrainingFields>(key: K, value: TrainingFields[K]): void => setFields((v) => ({ ...v, [key]: value }));
  async function submit(): Promise<void> {
    if (!client || pending) return;
    const source = fixedSource ?? chosenSource(kind, model, upload, serverPath);
    if (!source) return;
    const request = {
      source, protocol: mode === 'resume' ? protocol : trainingProtocol(recipe, fields, { omitEpochs: mode === 'evaluate_only' }), mode, validation_enabled: validation,
      ...(fields.checkpoint ? { checkpoint: fields.checkpoint } : {}),
    };
    const fingerprint = `training|${stableStringify(request)}`;
    const key = keys.current.keyFor(fingerprint);
    setPending(true); setMessage(null);
    try {
      const created = await client.trainingRun({ ...request, idempotency_key: key });
      // Keep the successful payload key: a double click or uncertain retry opens the same run.
      setMessage({ error: false, text: `별도 ${mode === 'evaluate_only' ? '평가' : '학습'} run 생성: ${created.run_id}`, runId: created.run_id, sha: created.source?.source_sha256 ?? null, modelName: created.source?.model_name ?? null });
    } catch (e) {
      const retry = e instanceof ApiError && (e.retryable || e.kind === 'parse' || e.status === 401 || e.status === 403);
      if (!retry) keys.current.settle(fingerprint);
      setMessage({ error: true, text: `${e instanceof Error ? e.message : String(e)}${retry ? ' · 재시도 시 같은 요청 키를 사용합니다.' : ''}` });
    } finally { setPending(false); }
  }
  async function loadTrainingPresets(): Promise<void> {
    if (!client || presetLoading) return;
    setPresetLoading(true); setPresetError('');
    try { setPresets(await client.presets()); } catch (e) { setPresetError(e instanceof Error ? e.message : String(e)); }
    finally { setPresetLoading(false); }
  }
  function chooseRecipe(id: string): void {
    const preset = presets.find((p) => p.id === id); if (!preset) return;
    const next = preset.protocol as Policy;
    setRecipeOverride(next); setJsonDraft(JSON.stringify(next, null, 2)); setJsonError('');
    setPresetLabel(`${preset.name} · ${preset.purpose ?? '서버 제공'} · ${String(next.status ?? '검증 상태는 실행 전 확인')}`);
    setFields((previous) => ({ ...previous,
      epochs: numberAt(next, 'console.training.epoch_target_total', numberAt(next, 'console.baseline_epoch_target', previous.epochs ?? 1)),
      batch: numberAt(next, 'training.batch_per_gpu', previous.batch ?? 1),
      size: (getIn(next, 'training.requested_input_size') as number[] | undefined)?.[0] ?? previous.size,
      optimizer: stringAt(next, 'training.optimizer') || previous.optimizer,
      lr: numberAt(next, 'training.lr_initial', previous.lr ?? 0.001),
      dataset: previous.dataset || stringAt(next, 'console.paths.dataset_yaml'), output: previous.output || stringAt(next, 'console.paths.output_root'),
    }));
  }
  // The server accepts evaluate_only only for an official baseline (selection.py EVAL_ONLY_UNSUPPORTED: search/generated candidates and
  // user models are refused with 422), so the option exists only where it can succeed; 'resume' is the mirror rule (fixed source only).
  const evaluateOnlyAllowed = (fixedSource?.kind ?? kind) === 'baseline';
  const needsCheckpoint = mode === 'pretrained' && (fixedSource?.kind ?? kind) !== 'baseline';
  return <Card title={fixedSource ? '이 모델 학습 · 별도 run' : '개별 모델 학습 · baseline / 사용자 모델'}>
    {!hasRecipe && <Banner tone="warn">원본 생성 작업에는 학습 recipe·단계별 지표 가중치·정규화 설정이 없습니다. 아래 “서버 학습 recipe 불러오기”로 서버 preset을 명시적으로 선택하거나 새 학습용 전체 설정을 입력한 뒤 실제 데이터·학습 조건을 확인하세요.</Banner>}
    <div className="row" style={{ alignItems: 'flex-end' }}>
      <button className="btn btn-sm" type="button" disabled={!client || presetLoading} onClick={() => void loadTrainingPresets()}>{presetLoading ? '불러오는 중…' : '서버 학습 recipe 불러오기'}</button>
      <SelectField label="새 학습 recipe 선택" value="" allowEmpty={presetLabel} options={presets.map((preset) => ({ value: preset.id, label: `${preset.name} · ${preset.purpose ?? '서버 preset'} · ${String((preset.protocol as Policy).status ?? 'status 미기록')}` }))} onChange={chooseRecipe} testId="training-recipe" />
    </div>
    {presetError && <p className="field-err" role="alert">{presetError}</p>}
    <p className="help">SMOKE/PILOT preset은 짧은 동작 검증용이며 연구 기본값·정확도 결과가 아닙니다. 선택한 recipe와 실제 경로는 새 학습 run 생성 시 서버에서 검증합니다. 모델 출처는 그대로 유지됩니다.</p>
    <details>
      <summary>새 학습 run의 전체 recipe·평가 설정</summary>
      <p className="help">현재 recipe의 loss·optimizer 설정과 평가 지표 가중치는 별개입니다. 성능 평가 OFF 원본은 변경하지 않습니다. 예시 숫자는 실운영 기본값이나 실험 결과가 아닙니다.</p>
      <p className="help">가중치: {JSON.stringify((getIn(recipe, 'rungs') as { fitness_weights?: unknown }[] | undefined)?.[0]?.fitness_weights ?? null)} · 정규화: {JSON.stringify(getIn(recipe, 'fitness') ?? null)}</p>
      <label className="field">전체 protocol JSON (새 학습 run 전용)
        <textarea rows={12} className="mono" aria-label="새 학습 protocol JSON" value={jsonDraft || JSON.stringify(recipe, null, 2)} onChange={(e) => setJsonDraft(e.target.value)} />
      </label>
      <button className="btn btn-sm" type="button" onClick={() => { try { const parsed: unknown = JSON.parse(jsonDraft || JSON.stringify(recipe)); if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('JSON object가 필요합니다.'); setRecipeOverride(parsed as Policy); setJsonError(''); } catch (e) { setJsonError(e instanceof Error ? e.message : String(e)); } }}>전체 설정 적용</button>
      {jsonError && <p role="alert" className="field-err">{jsonError}</p>}
      <div className="grid-3">
        <TextField label="Train root · 서버 절대경로 (선택)" value={fields.trainRoot ?? ''} onChange={(v) => edit('trainRoot', v)} mono help="비우면 dataset YAML의 split을 서버가 확인합니다." />
        <TextField label="Search validation root · 서버 절대경로 (선택)" value={fields.valRoot ?? ''} onChange={(v) => edit('valRoot', v)} mono />
        <TextField label="Final evaluation root · 서버 절대경로 (선택)" value={fields.testRoot ?? ''} onChange={(v) => edit('testRoot', v)} mono />
      </div>
    </details>
    <p className="help">서버 경로를 입력합니다. 원본 후보 run은 변경하지 않습니다. 검증은 원본 생성 작업의 평가 ON/OFF와 별개입니다. epoch·batch·seed 값은 이 별도 실행의 설정입니다.</p>
    {fixedSource ? <p className="mono wrap-any">출처: {fixedSource.kind} · {fixedSource.run_id} / {fixedSource.candidate_id}</p> : <div className="grid-2">
      <SelectField label="모델 출처" value={kind} options={[{ value: 'baseline', label: 'baseline · 서버 registry' }, { value: 'user', label: '사용자 지정 모델 · YAML 업로드' }]} onChange={(v) => { setKind(v as typeof kind); setModel(''); setUpload(null); setServerPath(false); if (v !== 'baseline' && mode === 'evaluate_only') setMode('scratch'); }} />
      {kind === 'baseline'
        ? <TextField label="Baseline registry ID" value={model} onChange={setModel} mono help="서버 registry의 baseline id입니다. 서버가 지원 여부를 확인합니다." />
        : <div className="stack" data-testid="user-model-source">
          {!serverPath && <ModelConfigUpload client={client} value={upload} onChange={setUpload} />}
          <CheckField label="고급: 서버 경로 사용 (model_path)" checked={serverPath} onChange={(v) => { setServerPath(v); setModel(''); setUpload(null); }} testId="model-path-advanced"
            help="업로드 대신 서버 호스트의 YAML 절대경로를 보냅니다(owner 단일 호스트 배포용). 브라우저 로컬 파일 경로가 아니며, 서버가 허용 경로(model_yaml_roots)와 지원 여부를 확인합니다. 업로드와 함께 보낼 수 없습니다." />
          {serverPath && <TextField label="모델 구성 YAML 서버 절대경로 (고급)" value={model} onChange={setModel} mono testId="model-path-input" help="브라우저 로컬 파일 경로가 아닙니다. 서버가 허용 경로와 지원 여부를 확인합니다." />}
        </div>}
      <RegistryStructureButton client={client} kind={kind} model={model} upload={kind === 'user' && !serverPath ? upload : null} />
    </div>}
    <div className="grid-3">
      <SelectField label="실행 방식" value={mode} options={[{ value: 'scratch', label: '처음부터 학습' }, { value: 'pretrained', label: '사전학습 가중치로 추가 학습' }, ...(fixedSource ? [{ value: 'resume', label: '기존 학습 run의 full-state 재개' }] : []), ...(evaluateOnlyAllowed ? [{ value: 'evaluate_only', label: '공식 가중치 평가 전용' }] : [])]} onChange={(v) => setMode(v as typeof mode)} testId="training-mode" />
      <TextField label="학습 장치" value="worker assigned · cuda:0" onChange={() => undefined} readOnly help="선택한 worker GPU가 작업 안에서 cuda:0으로 매핑됩니다. 실제 GPU 정보는 모니터링에서 확인합니다." />
      <TextField label="데이터셋 YAML · 서버 경로" value={fields.dataset} onChange={(v) => edit('dataset', v)} mono />
      <TextField label="결과 저장 · 서버 경로" value={fields.output} onChange={(v) => edit('output', v)} mono />
      <TextField label={mode === 'resume' ? 'Full-state checkpoint · 서버 경로' : '초기 가중치 / checkpoint · 서버 경로'} value={fields.checkpoint} onChange={(v) => edit('checkpoint', v)} mono help={mode === 'resume' ? '해당 학습 run의 최신 full-state를 복원합니다. 경로를 쓰면 저장된 최신 checkpoint와 일치해야 합니다. weights-only는 완전한 재개가 아닙니다.' : 'baseline 공식 가중치는 지원 registry에서 자동 선택됩니다. 사용자·탐색 모델의 추가 학습은 가중치 경로가 필요합니다.'} />
      {mode === 'evaluate_only'
        ? <p className="help" data-testid="evaluate-only-epoch-help">평가 전용: 학습 epoch 없음</p>
        : <NumberField label="목표 epoch" value={fields.epochs} min={1} step={1} onChange={(v) => edit('epochs', v)} />}
      <NumberField label="Batch per GPU" value={fields.batch} min={1} step={1} onChange={(v) => edit('batch', v)} />
      <NumberField label="입력 크기" unit="px · square" value={fields.size} min={32} step={32} onChange={(v) => edit('size', v)} />
      <NumberField label="학습 seed" value={fields.seed} min={0} step={1} onChange={(v) => edit('seed', v)} />
      <SelectField label="Optimizer" value={fields.optimizer} options={['AdamW', 'SGD', 'Adam', 'auto']} onChange={(v) => edit('optimizer', v)} />
      <NumberField label="Learning rate" value={fields.lr} min={0} onChange={(v) => edit('lr', v)} />
      <CheckField label="이 학습 run의 validation 수행" checked={validation} onChange={setValidation} testId="training-validation" help="해제해도 실제 저장된 last/full-state checkpoint를 사용합니다." />
    </div>
    {mode === 'resume' && <Banner tone="info">기존 학습 run의 원래 protocol과 최신 full-state를 사용합니다. 위 recipe 수정값은 재개에 적용하지 않습니다. 외부 checkpoint를 새 run으로 가져오는 기능은 지원하지 않습니다(기존 paused run의 최신 full-state만 재개). 생성·탐색 run을 새 학습으로 시작하려면 처음부터 학습 또는 사전학습 추가 학습을 선택하세요.</Banner>}
    {needsCheckpoint && !fields.checkpoint && <Banner tone="warn">이 모델의 추가 학습에 사용할 가중치 서버 경로를 입력하세요.</Banner>}
    {!fixedSource && kind === 'user' && !serverPath && !upload && <p className="help" data-testid="upload-required">학습할 사용자 모델 YAML 파일을 먼저 업로드하세요(또는 고급: 서버 경로).</p>}
    <button className="btn btn-primary" type="button" disabled={!client || pending || (!hasRecipe && mode !== 'resume') || (!fixedSource && !chosenSource(kind, model, upload, serverPath)) || !fields.dataset || !fields.output || (needsCheckpoint && !fields.checkpoint)} onClick={() => void submit()} data-testid="training-submit">{pending ? '요청 중…' : mode === 'evaluate_only' ? '평가 전용 실행' : '이 모델 학습'}</button>
    {message && <Banner tone={message.error ? 'err' : 'ok'}>{message.text}{(message.sha || message.modelName) && <span className="mono wrap-any" data-testid="training-source-sha"> · {message.modelName ?? '모델'} source_sha256 {message.sha ?? '미기록'}</span>}{message.runId && onCreated && <> <button className="btn btn-sm" type="button" onClick={() => onCreated(message.runId!)}>학습 모니터링 열기</button></>}</Banner>}
  </Card>;
}
