/**
 * F5a (contract 1.5.0): pick a detector model configuration YAML in the browser and upload its bytes unchanged to
 * POST /uploads/model-config (raw body). The gateway stores them content-addressed and answers {upload_id, source_sha256,
 * bytes}; the console then sends `upload_id` (+ the file name as `model_name`) to POST /structure-check and
 * POST /training-runs. No server path is involved and none is shown. The browser also hashes the file (WebCrypto) and
 * compares it with the server's source_sha256, so a transport change of the bytes is visible.
 */
import { useId, useRef, useState } from 'react';
import { ApiError, MODEL_CONFIG_MAX_BYTES, MODEL_CONFIG_NAME, type ApiClient } from '../api/client';
import type { UploadResponse } from '../api/types';
import { hexDigest, sha256OfBlob } from '../lib/structure';
import { Badge, Banner, KV } from './ui';

export interface UploadedModel extends UploadResponse {
  /** file name the user picked (sent as `model_name`) */
  name: string;
  /** sha256 the browser computed over the picked bytes; null when WebCrypto is unavailable (insecure context) */
  localSha256: string | null;
}

/** Korean explanation of the upload error codes (the server code is always shown as well). */
export const UPLOAD_ERROR_TEXT: Readonly<Record<string, string>> = {
  UPLOAD_TYPE_UNSUPPORTED: '.yaml 또는 .yml 파일만 올릴 수 있습니다.',
  PAYLOAD_TOO_LARGE: '파일이 업로드 한도(기본 1 MiB)를 넘습니다.',
  UPLOAD_EMPTY: '빈 파일은 올릴 수 없습니다.',
  UPLOAD_NOT_UTF8: 'UTF-8 텍스트가 아닙니다.',
  UPLOAD_INVALID: '업로드 본문을 읽지 못했습니다.',
  STORE_UNAVAILABLE: '서버 업로드 저장소에 쓸 수 없습니다(서버 측 문제).',
  AUTH_REQUIRED: '인증이 필요합니다. 토큰을 확인하세요.',
  ACCESS_DENIED: '권한이 없습니다.',
  RATE_LIMITED: '요청이 너무 많습니다. 잠시 후 다시 시도하세요.',
};

/** Client-side pre-check (the server is authoritative): file name, empty file, size limit. null = may be sent. */
export function precheckModelFile(file: Pick<File, 'name' | 'size'>, maxBytes = MODEL_CONFIG_MAX_BYTES): { code: string; message: string } | null {
  if (!MODEL_CONFIG_NAME.test(file.name)) return { code: 'UPLOAD_TYPE_UNSUPPORTED', message: `${file.name}: ${UPLOAD_ERROR_TEXT.UPLOAD_TYPE_UNSUPPORTED}` };
  if (file.size === 0) return { code: 'UPLOAD_EMPTY', message: `${file.name}: ${UPLOAD_ERROR_TEXT.UPLOAD_EMPTY}` };
  if (file.size > maxBytes) return { code: 'PAYLOAD_TOO_LARGE', message: `${file.name}: ${file.size} bytes > ${maxBytes} bytes. ${UPLOAD_ERROR_TEXT.PAYLOAD_TOO_LARGE}` };
  return null;
}

export function uploadErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    const hint = e.code ? UPLOAD_ERROR_TEXT[e.code] : undefined;
    return `${e.code ? `${e.code}: ` : ''}${e.message}${hint && hint !== e.message ? ` — ${hint}` : ''}${e.body?.location ? ` (location ${e.body.location})` : ''}`;
  }
  return e instanceof Error ? e.message : String(e);
}

export function ModelConfigUpload({ client, value, onChange, disabled }: {
  client: ApiClient | null; value: UploadedModel | null; onChange: (u: UploadedModel | null) => void; disabled?: boolean;
}): JSX.Element {
  const id = useId();
  const input = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ code: string | null; text: string; local: boolean } | null>(null);
  const token = useRef(0);

  async function pick(file: File | undefined): Promise<void> {
    if (!file || !client) return;
    const mine = ++token.current;
    setError(null);
    onChange(null);
    const pre = precheckModelFile(file);
    if (pre) {
      setError({ code: pre.code, text: pre.message, local: true });
      if (input.current) input.current.value = '';
      return;
    }
    setBusy(true);
    try {
      let localSha256: string | null = null;
      try { localSha256 = await sha256OfBlob(file); } catch { localSha256 = null; }
      const answer = await client.uploadModelConfig(file, file.name);
      if (mine === token.current) onChange({ ...answer, name: file.name, localSha256 });
    } catch (e) {
      if (mine === token.current) setError({ code: e instanceof ApiError ? e.code : null, text: uploadErrorText(e), local: false });
    } finally {
      if (mine === token.current) setBusy(false);
    }
  }

  const server = value ? hexDigest(value.source_sha256) : null;
  const verdict = value ? (value.localSha256 && server ? value.localSha256 === server : null) : null;
  return (
    <div className="stack" data-testid="model-upload">
      <div className="field">
        <label htmlFor={id}>모델 구성 YAML 파일 업로드 <span className="unit">[.yaml / .yml, 최대 {MODEL_CONFIG_MAX_BYTES} bytes]</span></label>
        <input id={id} ref={input} type="file" accept=".yaml,.yml,application/x-yaml,application/yaml,text/yaml" disabled={disabled || !client || busy}
          aria-describedby={`${id}-h`} data-testid="model-upload-input" onChange={(e) => void pick(e.target.files?.[0])} />
        <span className="help" id={`${id}-h`}>브라우저의 파일 바이트를 그대로 서버(게이트웨이) 업로드 저장소에 올립니다(POST /uploads/model-config). 서버는 sha256으로 저장하고 upload_id를 돌려주며, 이후 구조 검사와 학습 요청은 이 upload_id로 보냅니다. 서버 경로는 쓰지도 보이지도 않습니다.</span>
      </div>
      {busy && <p className="muted" role="status" data-testid="model-upload-busy">업로드 중… (sha256 계산 후 전송)</p>}
      {error && <Banner tone="err"><span data-testid="model-upload-error" data-code={error.code ?? ''}>{error.local ? `전송하지 않음 (브라우저 사전 확인) · ${error.code}: ${error.text}` : error.text}</span></Banner>}
      {value && (
        <div data-testid="model-upload-result">
          <KV rows={[
            ['파일', <span key="n" className="mono wrap-any" data-testid="upload-name">{value.name}</span>],
            ['크기', <span key="b" data-testid="upload-bytes">{value.bytes} bytes</span>],
            ['upload_id', <span key="u" className="mono wrap-any" data-testid="upload-id">{value.upload_id}</span>],
            ['source_sha256 (서버 저장 바이트)', <span key="s" className="mono wrap-any" data-testid="upload-sha256">{value.source_sha256}</span>],
            ['브라우저 계산 sha256', <span key="l" className="mono wrap-any" data-testid="upload-local-sha256">{value.localSha256 ?? '계산 불가 (WebCrypto 없음: HTTPS 또는 localhost에서만 제공)'}</span>],
            ['대조', verdict === true
              ? <Badge key="v" tone="ok"><span data-testid="upload-verdict">일치 · 올린 바이트 그대로 저장됨</span></Badge>
              : verdict === false
                ? <Badge key="v" tone="err"><span data-testid="upload-verdict">불일치 · 전송 중 바이트가 바뀜</span></Badge>
                : <span key="v" className="help" data-testid="upload-verdict">대조 못 함 (브라우저 해시 없음)</span>],
          ]} />
          <button className="btn btn-sm" type="button" onClick={() => { token.current += 1; onChange(null); setError(null); if (input.current) input.current.value = ''; }} data-testid="model-upload-clear">업로드 선택 해제</button>
        </div>
      )}
    </div>
  );
}
