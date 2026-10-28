/**
 * Small shared pieces of the archive screens: badges with a text label (colour is never the only cue), the
 * connection gate, load/error boxes, source + warning notes, and the mark shapes used by the charts.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { ApiClient } from '../../api/client';
import { Badge, Banner, type Tone } from '../ui';
import { useApp, type Loadable } from '../../state/context';
import type { ArchiveSource, ArchiveStatus } from '../../archive/types';
import { DASH } from '../../archive/format';

/** The configured API client (null until an origin is valid). */
export function useApi(): ApiClient | null {
  return useApp().client;
}

const STATUS_TEXT: Record<ArchiveStatus, { tone: Tone; text: string }> = {
  running: { tone: 'info', text: '▶ 실행 중' },
  done: { tone: 'ok', text: '✔ 완료' },
  failed: { tone: 'err', text: '✖ 실패' },
  unknown: { tone: 'neutral', text: '? 알 수 없음' },
};

export function StatusBadge({ status }: { status: string | null | undefined }): JSX.Element {
  const s = STATUS_TEXT[(status as ArchiveStatus) ?? 'unknown'] ?? STATUS_TEXT.unknown;
  return (
    <Badge tone={s.tone} className="status-badge">
      <span data-testid="status-text">{status && status in STATUS_TEXT ? s.text : `? ${status ?? '알 수 없음'}`}</span>
    </Badge>
  );
}

/** Verdict vocabulary differs per stage; the tone is a hint, the text is always printed. */
export function verdictTone(v: string | null | undefined): Tone {
  if (!v) return 'neutral';
  const u = v.toUpperCase();
  if (/FAIL|ABORT|STOP|NO[-_ ]?GO|REJECT|ERROR|VIOLAT/.test(u)) return 'err';
  if (/WARN|PARTIAL|HOLD|INCONCLUSIVE|PENDING|SKIP/.test(u)) return 'warn';
  if (/PASS|^GO|OK|DONE|SUCCESS|ACCEPT|CONFIRM/.test(u)) return 'ok';
  return 'neutral';
}

export function VerdictBadge({ label, value }: { label: string; value: string | null | undefined }): JSX.Element {
  return (
    <Badge tone={verdictTone(value)} title={`${label}: ${value ?? '없음'}`}>
      {label} {value ?? DASH}
    </Badge>
  );
}

const GATE_TONE: Record<string, Tone> = { PASS: 'ok', WARN: 'warn', FAIL: 'err', INFO: 'info', SKIP: 'neutral' };
const GATE_ICON: Record<string, string> = { PASS: '✔', WARN: '⚠', FAIL: '✖', INFO: 'ℹ', SKIP: '–' };

export function GateBadge({ status }: { status: string }): JSX.Element {
  return (
    <Badge tone={GATE_TONE[status] ?? 'neutral'}>
      <span aria-hidden="true">{GATE_ICON[status] ?? '?'}</span> {status}
    </Badge>
  );
}

/** Source files of a response (design 3: every response names its files) and its warnings. */
export function SourceNote({ source, warnings }: { source?: ArchiveSource | null; warnings?: string[] }): JSX.Element | null {
  const w = warnings ?? [];
  if (!source && w.length === 0) return null;
  return (
    <div className="stack" style={{ marginTop: 8 }}>
      {w.length > 0 && (
        <Banner tone="warn">
          <span data-testid="archive-warnings">
            {w.map((x, i) => (
              <span key={i} style={{ display: 'block' }} className="wrap-any">
                {x}
              </span>
            ))}
          </span>
        </Banner>
      )}
      {source && (
        <details className="source-note">
          <summary>출처 파일 {source.files?.length ?? 0}개</summary>
          <p className="help mono wrap-any" data-testid="source-run-dir">
            {source.run_dir}
          </p>
          <ul className="help mono wrap-any" style={{ margin: 0, paddingLeft: 18 }}>
            {(source.files ?? []).map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** Loading / error shell for a section. Stale data (loading or error with data) is still rendered by `children`. */
export function LoadBox<T>({ state, what, children, onRetry }: { state: Loadable<T>; what: string; children: (data: T) => ReactNode; onRetry?: () => void }): JSX.Element {
  return (
    <>
      {state.status === 'error' && (
        <Banner tone="err">
          <span data-testid="archive-error" className="wrap-any">
            {what}을(를) 불러오지 못했습니다: {state.error}
          </span>{' '}
          {onRetry && (
            <button className="btn btn-sm" type="button" onClick={onRetry}>
              다시 시도
            </button>
          )}
        </Banner>
      )}
      {state.status === 'loading' && !state.data && (
        <p className="muted" role="status" data-testid="archive-loading">
          {what} 불러오는 중…
        </p>
      )}
      {state.status === 'idle' && !state.data && <p className="muted">{what}: 대기 중</p>}
      {state.data !== null && state.data !== undefined && children(state.data)}
    </>
  );
}

/** Inline connection form shown by the archive screens until the server answered /health. */
export function ConnectGate(): JSX.Element | null {
  const app = useApp();
  if (app.health.status === 'ok') return null;
  return (
    <section className="card" aria-labelledby="gate-t" data-testid="connect-gate">
      <h2 id="gate-t">서버 연결</h2>
      <p className="help">아카이브는 읽기 전용 API를 사용합니다. 토큰은 메모리에만 보관되며 저장·URL 포함되지 않습니다. 새로고침하면 다시 입력해야 합니다.</p>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          void app.connect();
        }}
      >
        <div className="field" style={{ minWidth: 260, flex: '1 1 260px' }}>
          <label htmlFor="gate-token">API 토큰</label>
          <input id="gate-token" type="password" autoComplete="off" spellCheck={false} value={app.token} onChange={(e) => app.setToken(e.target.value)} data-testid="gate-token" />
        </div>
        <button className="btn btn-primary" type="submit" disabled={!app.client || app.health.status === 'loading'} data-testid="gate-connect">
          {app.health.status === 'loading' ? '연결 중…' : '연결'}
        </button>
      </form>
      {app.health.status === 'error' && (
        <Banner tone="err">
          <span data-testid="gate-error" className="wrap-any">
            연결 실패: {app.health.error}
          </span>
        </Banner>
      )}
    </section>
  );
}

// ---- chart helpers -----------------------------------------------------------------------------------------------

export type MarkShape = 'circle' | 'diamond' | 'square' | 'star' | 'triangle';

const STAR = '0,-8 2.4,-2.6 8,-2.4 3.6,1.4 5,7 0,3.8 -5,7 -3.6,1.4 -8,-2.4 -2.4,-2.6';

export function Mark({ shape, r = 6 }: { shape: MarkShape; r?: number }): JSX.Element {
  switch (shape) {
    case 'square':
      return <rect x={-r} y={-r} width={2 * r} height={2 * r} />;
    case 'diamond':
      return <polygon points={`0,${-r - 1.5} ${r + 1.5},0 0,${r + 1.5} ${-r - 1.5},0`} />;
    case 'triangle':
      return <polygon points={`0,${-r - 1} ${r + 1},${r} ${-r - 1},${r}`} />;
    case 'star':
      return <polygon points={STAR} transform={`scale(${(r + 2) / 8})`} />;
    default:
      return <circle r={r} />;
  }
}

/** Width of an element tracked with a ResizeObserver (jsdom has none: the default is used). */
export function useBoxWidth(def = 560): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(def);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = (): void => setW(Math.max(200, Math.floor(el.clientWidth || def)));
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [def]);
  return [ref, w];
}

/** "Chart | Table" switch used by every chart card (the table is the keyboard / screen-reader equivalent). */
export function ViewToggle({ view, onChange, idBase }: { view: 'chart' | 'table'; onChange: (v: 'chart' | 'table') => void; idBase: string }): JSX.Element {
  return (
    <div className="row" role="group" aria-label="표시 방식">
      <button type="button" className="btn btn-sm" aria-pressed={view === 'chart'} onClick={() => onChange('chart')} data-testid={`${idBase}-view-chart`}>
        차트
      </button>
      <button type="button" className="btn btn-sm" aria-pressed={view === 'table'} onClick={() => onChange('table')} data-testid={`${idBase}-view-table`}>
        표로 보기
      </button>
    </div>
  );
}
