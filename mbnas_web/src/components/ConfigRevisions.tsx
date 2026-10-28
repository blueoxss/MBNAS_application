/**
 * "구성" tab of the candidate view (contract 1.5.0, F5d/F5e/F5g): the configuration revisions of one candidate
 * (GET .../config-revisions), a line diff between any two revisions of their STORED BYTES (GET .../config-revisions/{id}/yaml,
 * both sha256 shown and checked), and the download of the exact bytes of a revision. The displayed / canonical text download
 * of the structure section is a different thing and is labelled as such there.
 *
 * A gateway without the revision routes (contract < 1.5.0) answers 404: the tab is then hidden and a note says why
 * (`useConfigRevisions` -> state 'unsupported'); RUN_NOT_FOUND / CANDIDATE_NOT_FOUND hide it as 'not_found'.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, type ApiClient } from '../api/client';
import type { ModelConfigRevision } from '../api/types';
import { bytesVerdict, decodeYaml, defaultDiffPair, lineDiff, parentLabel, REVISION_KIND_TEXT, revisionFileName, revisionLabel, type BytesVerdict, type LineDiff } from '../lib/configRevisions';
import { saveBlob } from '../lib/download';
import { DASH } from '../lib/format';
import { sha256OfBlob } from '../lib/structure';
import { DataTable, type Column } from './DataTable';
import { Badge, Banner, KV, SelectField } from './ui';

export type RevisionsState =
  | { status: 'idle' | 'loading' }
  | { status: 'ok'; revisions: ModelConfigRevision[] }
  /** the gateway does not serve the revision routes (404 that is not RUN_/CANDIDATE_NOT_FOUND): the tab is hidden */
  | { status: 'unsupported'; code: string | null }
  /** 404 RUN_NOT_FOUND / CANDIDATE_NOT_FOUND: no revision record for this candidate: the tab is hidden */
  | { status: 'not_found'; code: string }
  | { status: 'error'; message: string };

const RECORD_MISSING = new Set(['RUN_NOT_FOUND', 'CANDIDATE_NOT_FOUND']);

/**
 * Load the revision list once per (run, candidate); 404 decides whether the tab exists (see the module comment). Without a
 * connection, run or candidate, or with a client object that has no revision method (an injected double), it stays idle:
 * no tab and no note.
 */
export function useConfigRevisions(client: ApiClient | null, runId: string, candidateId: string | undefined): { state: RevisionsState; reload: () => void } {
  const [state, setState] = useState<RevisionsState>({ status: 'idle' });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!client || typeof client.configRevisions !== 'function' || !runId || !candidateId) { setState({ status: 'idle' }); return; }
    let live = true;
    const ctrl = new AbortController();
    setState({ status: 'loading' });
    client.configRevisions(runId, candidateId, ctrl.signal)
      .then((list) => { if (live) setState({ status: 'ok', revisions: Array.isArray(list.revisions) ? list.revisions : [] }); })
      .catch((e: unknown) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) setState(e.code && RECORD_MISSING.has(e.code) ? { status: 'not_found', code: e.code } : { status: 'unsupported', code: e.code });
        else setState({ status: 'error', message: e instanceof ApiError ? `${e.code ? `${e.code}: ` : ''}${e.message}` : e instanceof Error ? e.message : String(e) });
      });
    return () => { live = false; ctrl.abort(); };
  }, [client, runId, candidateId, nonce]);
  return { state, reload: useCallback(() => setNonce((n) => n + 1), []) };
}

/** Text of the note shown instead of the tab when the revisions are not available. */
export function revisionsNote(state: RevisionsState): string | null {
  if (state.status === 'unsupported') return `구성 개정 이력: 이 서버는 구성 개정 API(GET …/config-revisions, 계약 1.5.0)를 제공하지 않아 "구성" 탭을 숨겼습니다${state.code ? ` (404 ${state.code})` : ' (404)'}.`;
  if (state.status === 'not_found') return `구성 개정 이력: 서버에 이 후보의 개정 기록이 없어 "구성" 탭을 숨겼습니다 (404 ${state.code}).`;
  return null;
}

interface Fetched { blob: Blob; text: string; exact: boolean; verdict: BytesVerdict }

function VerdictBadge({ v, testId }: { v: BytesVerdict; testId: string }): JSX.Element {
  if (v.matches === true) return <Badge tone="ok"><span data-testid={testId} data-verdict="match">일치 · 받은 바이트 sha256 = 기록 source_sha256</span></Badge>;
  if (v.matches === false) return <Badge tone="err"><span data-testid={testId} data-verdict="mismatch">불일치 · 받은 바이트가 기록과 다름 (오류)</span></Badge>;
  return <span className="help" data-testid={testId} data-verdict="unknown">{v.computed === null ? '대조 못 함 (브라우저 sha256 계산 불가: HTTPS 또는 localhost 필요)' : '대조 못 함 (기록된 source_sha256 없음)'}</span>;
}

export function ConfigRevisionsPanel({ client, runId, candidateId, revisions }: { client: ApiClient | null; runId: string; candidateId: string; revisions: ModelConfigRevision[] }): JSX.Element {
  const initial = useMemo(() => defaultDiffPair(revisions), [revisions]);
  const [left, setLeft] = useState(initial?.[0] ?? '');
  const [right, setRight] = useState(initial?.[1] ?? '');
  const cache = useRef(new Map<string, Promise<Fetched>>());
  const [diff, setDiff] = useState<{ a: Fetched; b: Fetched; d: LineDiff } | null>(null);
  const [diffError, setDiffError] = useState('');
  const [diffBusy, setDiffBusy] = useState(false);
  const [downloads, setDownloads] = useState<Record<string, { verdict?: BytesVerdict; bytes?: number; error?: string; saved?: boolean }>>({});
  useEffect(() => { setLeft(initial?.[0] ?? ''); setRight(initial?.[1] ?? ''); cache.current.clear(); }, [initial]);

  const byId = useMemo(() => new Map(revisions.map((r) => [r.revision_id, r])), [revisions]);
  const fetchBytes = useCallback((revisionId: string): Promise<Fetched> => {
    const hit = cache.current.get(revisionId);
    if (hit) return hit;
    if (!client) return Promise.reject(new Error('Not connected.'));
    const p = client.configRevisionYaml(runId, candidateId, revisionId).then(async ({ blob, headerSha256 }) => {
      let computed: string | null = null;
      try { computed = await sha256OfBlob(blob); } catch { computed = null; }
      const { text, exact } = decodeYaml(await blob.arrayBuffer());
      return { blob, text, exact, verdict: bytesVerdict(computed, headerSha256, byId.get(revisionId)?.source_sha256) };
    });
    cache.current.set(revisionId, p);
    p.catch(() => cache.current.delete(revisionId));
    return p;
  }, [client, runId, candidateId, byId]);

  const showDiff = useCallback(async (a: string, b: string): Promise<void> => {
    if (!a || !b) return;
    setDiffBusy(true); setDiffError(''); setDiff(null);
    try {
      const [fa, fb] = await Promise.all([fetchBytes(a), fetchBytes(b)]);
      setDiff({ a: fa, b: fb, d: lineDiff(fa.text, fb.text) });
    } catch (e) {
      setDiffError(e instanceof ApiError ? `${e.code ? `${e.code}: ` : ''}${e.message}` : e instanceof Error ? e.message : String(e));
    } finally { setDiffBusy(false); }
  }, [fetchBytes]);

  async function download(rev: ModelConfigRevision): Promise<void> {
    setDownloads((d) => ({ ...d, [rev.revision_id]: {} }));
    try {
      const f = await fetchBytes(rev.revision_id);
      const saved = saveBlob(f.blob, revisionFileName(candidateId, rev));
      setDownloads((d) => ({ ...d, [rev.revision_id]: { verdict: f.verdict, bytes: f.blob.size, saved } }));
    } catch (e) {
      setDownloads((d) => ({ ...d, [rev.revision_id]: { error: e instanceof ApiError ? `${e.code ? `${e.code}: ` : ''}${e.message}` : e instanceof Error ? e.message : String(e) } }));
    }
  }

  const options = revisions.map((r) => ({ value: r.revision_id, label: `${revisionLabel(revisions, r)} · ${r.revision_id}` }));
  const cols: Column<ModelConfigRevision>[] = [
    { key: 'n', header: '#', cell: (r) => revisionLabel(revisions, r).split(' ')[0], num: true },
    { key: 'kind', header: 'kind', cell: (r) => <Badge tone={r.kind === 'executed' ? 'ok' : r.kind === 'selected' ? 'info' : 'neutral'} title={REVISION_KIND_TEXT[r.kind]}><span data-testid="revision-kind">{r.kind}</span></Badge>, sort: (r) => r.kind },
    { key: 'id', header: 'revision_id', cell: (r) => <span className="mono wrap-any" data-testid="revision-id">{r.revision_id}</span> },
    { key: 'src', header: 'source_sha256 (저장 바이트)', cell: (r) => <span className="mono wrap-any" data-testid="revision-source-sha256">{r.source_sha256}</span> },
    { key: 'can', header: 'canonical_hash (yaml_hash)', cell: (r) => <span className="mono wrap-any" data-testid="revision-canonical-hash">{r.canonical_hash ?? '파싱 불가 · null'}</span> },
    { key: 'arch', header: 'architecture_hash', cell: (r) => <span className="mono wrap-any" data-testid="revision-architecture-hash">{r.architecture_hash ?? '빌드 전 · null'}</span> },
    { key: 'at', header: 'created_at', cell: (r) => <span className="mono" data-testid="revision-created-at">{r.created_at}</span>, sort: (r) => r.created_at },
    {
      key: 'parent', header: 'parent', cell: (r) => {
        const label = parentLabel(revisions, r.parent_revision_id);
        const cut = label.indexOf(' (');
        return <span className="mono" data-testid="revision-parent">{cut < 0 ? label : <><span style={{ whiteSpace: 'nowrap' }}>{label.slice(0, cut)}</span><span className="help wrap-any" style={{ display: 'block' }}>{label.slice(cut)}</span></>}</span>;
      },
    },
    { key: 'art', header: 'artifact_id', cell: (r) => <span className="mono wrap-any">{r.artifact_id ?? 'core 보유 (canonical 텍스트만)'}</span> },
    {
      key: 'dl', header: '원문 바이트', cell: (r) => {
        const st = downloads[r.revision_id];
        return <div className="stack" data-testid={`revision-download-${r.revision_id}`}>
          <button className="btn btn-sm" type="button" disabled={!client} onClick={() => void download(r)} data-testid="revision-download">원문 바이트 다운로드</button>
          {st?.error && <span className="field-err" role="alert">{st.error}</span>}
          {st?.verdict && <><VerdictBadge v={st.verdict} testId="revision-download-verdict" /><span className="help mono wrap-any">{st.bytes} bytes · sha256 {st.verdict.computed ?? '계산 불가'}{st.saved === false ? ' · 이 브라우저 컨텍스트에서는 파일 저장을 시작하지 못했습니다' : ''}</span></>}
        </div>;
      },
    },
  ];
  const side = (label: 'A' | 'B', id: string, f: Fetched): JSX.Element => {
    const rev = byId.get(id);
    return <div className="stack" data-testid={`diff-side-${label}`}>
      <KV rows={[
        [`${label} 개정`, <span key="r" className="mono wrap-any">{rev ? `${revisionLabel(revisions, rev)} · ${rev.revision_id}` : id}</span>],
        ['바이트', `${f.blob.size} bytes${f.exact ? '' : ' · UTF-8 아님 (대체 문자로 표시)'}`],
        ['받은 바이트 sha256', <span key="c" className="mono wrap-any" data-testid={`diff-sha-${label}`}>{f.verdict.computed ?? '계산 불가'}</span>],
        ['기록 source_sha256', <span key="s" className="mono wrap-any">{rev?.source_sha256 ?? DASH}</span>],
        ['X-Artifact-Sha256', <span key="h" className="mono wrap-any">{f.verdict.header ?? '헤더 없음'}</span>],
        ['대조', <VerdictBadge key="v" v={f.verdict} testId={`diff-verdict-${label}`} />],
      ]} />
    </div>;
  };
  const marker = (op: string): string => (op === 'add' ? '+' : op === 'del' ? '−' : ' ');
  return (
    <div className="stack" data-testid="config-revisions">
      <p className="help">서버가 기록한 이 후보의 구성 개정입니다(오래된 것부터): original = 업로드·등록 파일, selected = 학습 선택에 묶인 구성, executed = 학습기가 실제 실행한 구성, generated = 생성기가 쓴 구성. <strong>source_sha256</strong>은 저장 바이트의 해시이고 <strong>canonical_hash</strong>는 정규화한 YAML dict 해시(구조 동일성 기준)이므로 두 값이 다른 것은 정상입니다.</p>
      <DataTable caption="구성 개정 이력 (GET …/config-revisions)" columns={cols} rows={revisions} rowKey={(r) => r.revision_id} empty="이 후보에 기록된 구성 개정이 없습니다." testId="revision-table" />
      {revisions.length > 0 && (
        <section aria-label="구성 개정 diff" className="stack" data-testid="revision-diff">
          <h4 style={{ margin: 0 }}>두 개정의 저장 바이트 비교 (행 단위 diff)</h4>
          <div className="grid-3" style={{ alignItems: 'flex-end' }}>
            <SelectField label="A (왼쪽)" value={left} options={options} onChange={setLeft} testId="diff-left" />
            <SelectField label="B (오른쪽)" value={right} options={options} onChange={setRight} testId="diff-right" />
            <button className="btn btn-sm" type="button" disabled={!client || diffBusy || !left || !right} onClick={() => void showDiff(left, right)} data-testid="diff-run">{diffBusy ? '바이트 받는 중…' : 'diff 보기'}</button>
          </div>
          <p className="help">기본 선택은 선택본(selected)↔실행본(executed)이며, 없으면 마지막 두 개정입니다. 두 쪽 모두 서버 저장 바이트를 받아 브라우저에서 sha256을 다시 계산하고 기록값과 대조합니다.</p>
          {diffError && <Banner tone="err"><span data-testid="diff-error">{diffError}</span></Banner>}
          {diff && (
            <div className="stack">
              <div className="grid-2">{side('A', left, diff.a)}{side('B', right, diff.b)}</div>
              <p data-testid="diff-summary" data-identical={String(diff.d.identical)}>
                {diff.d.identical
                  ? (diff.a.verdict.computed && diff.a.verdict.computed === diff.b.verdict.computed ? '두 개정의 저장 바이트가 같습니다 (sha256 동일).' : '두 개정의 텍스트가 행 단위로 같습니다.')
                  : `+${diff.d.added} 행 / −${diff.d.removed} 행${diff.d.finalNewlineDiffers ? ' · 파일 끝 줄바꿈 여부가 다름' : ''}${diff.d.coarse ? ' · 변경 구간이 커서 행 정렬 없이 전체 교체로 표시' : ''}`}
              </p>
              <div style={{ overflow: 'auto', maxHeight: '60vh' }} tabIndex={0} aria-label="행 단위 diff 본문">
                <table className="diff-table" data-testid="diff-lines">
                  <caption className="sr-only">A↔B 행 단위 diff: − 는 A에만, + 는 B에만 있는 행</caption>
                  <thead><tr><th scope="col">A 행</th><th scope="col">B 행</th><th scope="col">±</th><th scope="col">내용</th></tr></thead>
                  <tbody>
                    {diff.d.lines.map((l, i) => (
                      <tr key={i} className={`diff-${l.op}`} data-op={l.op}>
                        <td className="mono diff-num">{l.left ?? ''}</td>
                        <td className="mono diff-num">{l.right ?? ''}</td>
                        <td className="mono" aria-label={l.op === 'add' ? 'B에만 있음' : l.op === 'del' ? 'A에만 있음' : '같음'}>{marker(l.op)}</td>
                        <td className="mono diff-text">{l.text.replace(/\r$/, '␍')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>
      )}
      <Banner tone="info"><span data-testid="revision-export-hint">원문 바이트 다운로드는 위 표의 개정별 버튼(서버 저장본 그대로, GET …/config-revisions/&#123;id&#125;/yaml)입니다. “전체” 보기의 Architecture and checkpoint 절에 있는 “화면에 표시한 구성 YAML 다운로드”는 상세 문서의 표시본(canonical 텍스트)이라 바이트가 다를 수 있습니다. run 전체 보관은 Server 화면의 “Download export (zip)”(GET /runs/&#123;run_id&#125;/export)를 쓰세요. 계약(DD-A23 F5g)상 ZIP의 architectures/*.yaml은 원문 바이트이며, 이 화면은 ZIP 내용을 검사하지 않으므로 받은 파일의 sha256을 위 source_sha256과 대조하세요.</span></Banner>
    </div>
  );
}
