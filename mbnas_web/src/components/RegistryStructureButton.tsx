import { useEffect, useId, useRef, useState } from 'react';
import { ApiError, type ApiClient, type RegistryStructure } from '../api/client';
import type { StructureCheckRequest, StructureReport } from '../api/types';
import { Modal } from './Modal';
import { hexDigest } from '../lib/structure';
import { ModelStructure, StructureSummary } from './ModelStructure';
import { Banner, TextField } from './ui';

type Result = { document: RegistryStructure | StructureReport; via: 'GET' | 'CHECK' };

/** Empty = the model default; otherwise a positive multiple of 32 (the server rejects anything else with INPUT_SIZE_INVALID). */
export function parseInputSize(value: string): { ok: true; value: number | undefined } | { ok: false; message: string } {
  const text = value.trim();
  if (!text) return { ok: true, value: undefined };
  const n = Number(text);
  return Number.isInteger(n) && n >= 32 && n % 32 === 0 ? { ok: true, value: n } : { ok: false, message: '입력 크기는 32 이상, 32의 배수여야 합니다.' };
}

const errorText = (e: unknown): string => e instanceof ApiError ? `${e.code ? `${e.code}: ` : ''}${e.message}` : e instanceof Error ? e.message : String(e);

/** An uploaded user model (POST /uploads/model-config): checked by `upload_id`, named by its file name; `source_sha256` of the upload answer. */
export interface UploadedSource { upload_id: string; name: string; source_sha256?: string }

/**
 * "구조 상세" for the model chosen in TrainingPanel before any run exists: a baseline registry id (GET shows the saved
 * evidence or a declaration-only document; the server builds nothing for a GET), an uploaded user YAML (`upload`, 1.5.0:
 * POST /structure-check {upload_id, model_name}) or, advanced, a user YAML path on the server (`model`). A user model has
 * no stored evidence, so it starts empty. 서버 CPU 구조 검사 실행 runs the explicit CPU observation on the server. The result
 * is rendered by the same ModelStructure view as the candidate detail.
 */
export function RegistryStructureButton({ client, kind, model, upload }: { client: ApiClient | null; kind: 'baseline' | 'user'; model: string; upload?: UploadedSource | null }): JSX.Element {
  const viaUpload = kind === 'user' && !!upload;
  const id = viaUpload ? upload.name : model.trim();
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [size, setSize] = useState('');
  const [classCount, setClassCount] = useState('');
  const token = useRef(0);
  const titleId = useId();
  useEffect(() => () => { token.current += 1; }, []);

  async function load(): Promise<void> {
    if (!client) return;
    const mine = ++token.current;
    setOpen(true); setResult(null); setError('');
    if (kind !== 'baseline') return; // a user YAML has no saved evidence to read
    setLoading(true);
    try {
      const document = await client.registryStructure(id);
      if (mine === token.current) setResult({ document, via: 'GET' });
    } catch (e) { if (mine === token.current) setError(errorText(e)); }
    finally { if (mine === token.current) setLoading(false); }
  }
  async function check(): Promise<void> {
    if (!client || running) return;
    const parsed = parseInputSize(size);
    if (!parsed.ok) { setError(parsed.message); return; }
    let classes: number | undefined;
    if (kind === 'user' && classCount.trim()) {
      const n = Number(classCount);
      if (!Number.isInteger(n) || n < 1) { setError('클래스 수는 1 이상의 정수여야 합니다.'); return; }
      classes = n;
    }
    const mine = ++token.current;
    setRunning(true); setError('');
    try {
      const extra: Pick<StructureCheckRequest, 'input_size' | 'class_count'> = { ...(parsed.value !== undefined ? { input_size: parsed.value } : {}), ...(classes !== undefined ? { class_count: classes } : {}) };
      const document = kind === 'baseline'
        ? await client.registryStructureCheck(id, parsed.value)
        : viaUpload
          ? await client.structureCheck({ upload_id: upload.upload_id, model_name: upload.name, ...extra })
          : await client.structureCheck({ model_path: id, ...extra });
      if (mine === token.current) setResult({ document, via: 'CHECK' });
    } catch (e) { if (mine === token.current) setError(errorText(e)); }
    finally { if (mine === token.current) setRunning(false); }
  }
  const close = (): void => { token.current += 1; setOpen(false); setLoading(false); setRunning(false); };
  const doc = result?.document as (Record<string, unknown> & { evidence_source?: string | null }) | undefined;
  const blocks = Array.isArray(doc?.blocks) ? doc.blocks : [];
  return <>
    <button className="btn btn-sm" type="button" disabled={!client || !id} onClick={() => void load()} data-testid="registry-structure-open" title={kind === 'baseline' ? 'GET /registry/{id}/structure' : '사용자 YAML은 저장된 구조 증거가 없어 서버 CPU 검사를 실행합니다'}>구조 상세</button>
    {open && <Modal titleId={titleId} onClose={close}>
      <div className="modal-head">
        <div>
          <h2 id={titleId} style={{ margin: 0 }}>구조 상세 · {kind === 'baseline' ? 'baseline registry' : '사용자 모델 YAML'}</h2>
          <p className="muted wrap-any mono" style={{ margin: 0 }} data-testid="registry-structure-subject">{viaUpload ? `${upload.name} · ${upload.upload_id}` : id}</p>
        </div>
        <button className="btn" type="button" onClick={close} data-autofocus data-testid="registry-structure-close">Close (Esc)</button>
      </div>
      <div className="stack" data-testid="registry-structure-modal">
        <p className="help">{kind === 'baseline'
          ? '아래는 서버에 저장된 구조 검사 기록이거나, 기록이 없으면 선언만 보여주는 문서입니다(조회는 모델을 만들지 않음). 실제 관측이 필요하면 CPU 구조 검사를 실행하세요.'
          : viaUpload
            ? '업로드한 사용자 모델 YAML은 저장된 구조 기록이 없습니다. 서버가 upload_id의 저장 바이트를 CPU로 검사하며 결과는 저장되지 않습니다.'
            : '사용자 모델 YAML은 저장된 구조 기록이 없습니다. 서버 허용 경로의 YAML만 검사하며 결과는 저장되지 않습니다.'}</p>
        <div className="grid-3" style={{ alignItems: 'flex-end' }}>
          <TextField label="입력 크기 (선택)" value={size} onChange={setSize} placeholder="비우면 모델 기본값" help="32의 배수" testId="registry-structure-size" />
          {kind === 'user' && <TextField label="클래스 수 (선택)" value={classCount} onChange={setClassCount} placeholder="비우면 YAML의 nc" testId="registry-structure-nc" />}
          <button className="btn btn-primary" type="button" disabled={!client || running} onClick={() => void check()} data-testid="registry-structure-check">{running ? 'CPU 구조 검사 중…' : '서버 CPU 구조 검사 실행'}</button>
        </div>
        {loading && <p className="muted" role="status">구조 기록을 불러오는 중…</p>}
        {error && <Banner tone="err">{error}</Banner>}
        {!result && !loading && !error && kind === 'user' && <p className="muted" data-testid="registry-structure-none">아직 구조 정보가 없습니다. "서버 CPU 구조 검사 실행"을 누르세요.</p>}
        {result && doc && <div data-testid="registry-structure-result">
          <StructureSummary report={doc} extra={[
            ['출처', result.via === 'GET' ? '저장된 기록 조회 (GET /registry/{id}/structure)' : '방금 실행한 서버 CPU 검사'],
            // a registry model is named by its id; a user model by the uploaded file name (model_name) or, advanced, the server path
            kind === 'baseline' ? ['model_id', typeof doc.model_id === 'string' ? doc.model_id : id] : viaUpload ? ['model_name (업로드 파일)', upload.name] : ['model_path (서버 경로)', id],
            ['yaml_hash', <span key="yh" className="mono wrap-any">{String(doc.yaml_hash ?? '미기록')}</span>],
            ['architecture_hash', <span key="ah" className="mono wrap-any">{String(doc.architecture_hash ?? '미기록')}</span>],
            // 1.5.0: a user structure check reports the sha256 of the YAML bytes it checked; for an upload it must be the upload's
            ...(kind === 'user' ? [['검사한 YAML 바이트 sha256', <span key="ss" className="mono wrap-any" data-testid="structure-source-sha256">{String(doc.source_sha256 ?? '미기록')}</span>] as [string, JSX.Element]] : []),
            ...(viaUpload && hexDigest(doc.source_sha256) && hexDigest(upload.source_sha256)
              ? [['업로드 sha256 대조', <span key="sv" data-testid="structure-source-verdict">{hexDigest(doc.source_sha256) === hexDigest(upload.source_sha256) ? '일치 · 검사한 바이트 = 업로드한 바이트' : '불일치 · 검사한 바이트가 업로드와 다름 (오류)'}</span>] as [string, JSX.Element]]
              : []),
          ]} />
          <ModelStructure blocks={blocks} modelKey={`registry:${id}`} status={typeof doc.status === 'string' ? doc.status : undefined} report={doc} />
        </div>}
      </div>
    </Modal>}
  </>;
}
