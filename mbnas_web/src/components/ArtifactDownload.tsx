/**
 * Download of one registered run artifact BY ID (contract 1.5.0: files are referenced by artifact / checkpoint id + sha256,
 * never by a host path). GET /runs/{run_id}/artifacts/{artifact_id} streams the store bytes; the browser hashes them and
 * compares with the recorded sha256. A failure is shown by its Error.code (DD-A39: 404 ARTIFACT_NOT_READY = not stored yet,
 * 409 LOAD_FAILED = the stored bytes no longer match their sha256), the HTTP status decides only for an unknown code.
 */
import { useState } from 'react';
import { ApiError, type ApiClient } from '../api/client';
import { stateFromError, type LoadState } from '../lib/artifacts';
import { bytesVerdict, type BytesVerdict } from '../lib/configRevisions';
import { saveBlob } from '../lib/download';
import { sha256OfBlob } from '../lib/structure';
import { Badge } from './ui';

export const DOWNLOAD_STATE_TEXT: Readonly<Record<Exclude<LoadState, 'LOADING' | 'READY'>, string>> = {
  NOT_READY: '아직 저장소에 없음 (NOT_READY)',
  LOAD_FAILED: '저장 바이트가 기록 sha256과 다르거나 읽지 못함 (LOAD_FAILED)',
  MISSING: '서버에 없음 (MISSING)',
  ACCESS_DENIED: '권한 없음 (ACCESS_DENIED)',
  EXPIRED: '만료됨 (EXPIRED)',
};

export function ArtifactDownload({ client, runId, artifactId, recordedSha256, fileName, label, testId }: {
  client: ApiClient | null; runId: string; artifactId: string; recordedSha256: string | null | undefined; fileName: string; label: string; testId: string;
}): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ verdict: BytesVerdict; bytes: number; saved: boolean } | null>(null);
  const [failure, setFailure] = useState<{ state: Exclude<LoadState, 'LOADING' | 'READY'>; code: string | null; message: string } | null>(null);
  async function run(): Promise<void> {
    if (!client || busy) return;
    setBusy(true); setResult(null); setFailure(null);
    try {
      const { blob, headerSha256 } = await client.artifactBytes(runId, artifactId);
      let computed: string | null = null;
      try { computed = await sha256OfBlob(blob); } catch { computed = null; }
      setResult({ verdict: bytesVerdict(computed, headerSha256, recordedSha256), bytes: blob.size, saved: saveBlob(blob, fileName) });
    } catch (e) {
      if (e instanceof ApiError && e.kind === 'http') setFailure({ state: stateFromError(e), code: e.code, message: e.message });
      else setFailure({ state: 'LOAD_FAILED', code: null, message: e instanceof Error ? e.message : String(e) });
    } finally { setBusy(false); }
  }
  return (
    <span className="stack" data-testid={testId}>
      <button className="btn btn-sm" type="button" disabled={!client || busy} onClick={() => void run()} data-testid={`${testId}-btn`}>{busy ? '받는 중…' : label}</button>
      {failure && <span className="field-err" role="alert" data-testid={`${testId}-state`} data-state={failure.state}>{DOWNLOAD_STATE_TEXT[failure.state]}{failure.code ? ` · ${failure.code}` : ''}: {failure.message}</span>}
      {result && (
        <span className="help" data-testid={`${testId}-state`} data-state="READY">
          {result.verdict.matches === true
            ? <Badge tone="ok">sha256 일치</Badge>
            : result.verdict.matches === false
              ? <Badge tone="err">sha256 불일치 (오류)</Badge>
              : <span>sha256 대조 못 함</span>}{' '}
          <span className="mono wrap-any">{result.bytes} bytes · {result.verdict.computed ?? '브라우저 sha256 계산 불가'}</span>
          {!result.saved && ' · 이 브라우저 컨텍스트에서는 파일 저장을 시작하지 못했습니다'}
        </span>
      )}
    </span>
  );
}
