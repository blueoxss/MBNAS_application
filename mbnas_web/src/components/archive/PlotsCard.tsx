/**
 * Plots card (design 4.1-9): the run's plot PNGs fetched with the Authorization header and shown through a Blob
 * URL. Loading rules and states reuse lib/artifacts (`loadOnce`: bearer_fetch, API-origin check, 401/403/404/409/410
 * mapped to states). The Blob URL is revoked when the image goes away.
 */
import { useEffect, useState } from 'react';
import type { ApiClient } from '../../api/client';
import type { Artifact } from '../../api/types';
import { loadOnce, type ArtifactResult } from '../../lib/artifacts';
import { Card } from '../ui';
import { ARCHIVE_PLOT_KINDS, type ArchivePlotKind, type ArchiveRun } from '../../archive/types';
import { useApi } from './ArchiveBits';

const STATE_TEXT: Record<string, string> = {
  NOT_READY: '아직 생성되지 않았습니다 (NOT_READY).',
  EXPIRED: '만료되었습니다 (EXPIRED).',
  ACCESS_DENIED: '접근이 거부되었습니다 — 토큰을 확인하세요 (ACCESS_DENIED).',
  MISSING: '이 run에는 해당 그림 파일이 없습니다 (MISSING).',
  LOAD_FAILED: '그림을 불러오지 못했습니다 (LOAD_FAILED).',
};

function artifactFor(runId: string, kind: ArchivePlotKind): Artifact {
  // the archive file endpoint is an authenticated GET; the shape below is only the lib/artifacts input
  return { id: kind, kind: 'gt', url: `/api/v1/archive/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(kind)}`, auth_mode: 'bearer_fetch', state: 'READY' } as Artifact;
}

function PlotImage({ runId, kind, label, client }: { runId: string; kind: ArchivePlotKind; label: string; client: ApiClient | null }): JSX.Element {
  const [res, setRes] = useState<ArtifactResult | null>(null);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;
    setRes(null);
    if (!client) {
      setRes({ state: 'LOAD_FAILED', detail: '서버에 연결되지 않았습니다.' });
      return;
    }
    void loadOnce(artifactFor(runId, kind), { client, runId }).then((r) => {
      if (cancelled) {
        if (r.isBlob && r.src) URL.revokeObjectURL(r.src);
        return;
      }
      if (r.isBlob && r.src) url = r.src;
      setRes(r);
    });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [client, runId, kind, nonce]);

  if (!res) return <div className="artifact-state" role="status" data-testid={`plot-${kind}`} data-state="LOADING"><strong>{label}</strong><span className="muted">불러오는 중…</span></div>;
  if (res.state === 'READY' && res.src) {
    return (
      <figure style={{ margin: 0 }} data-testid={`plot-${kind}`} data-state="READY">
        <img src={res.src} alt={`${label} 그림 (run ${runId})`} loading="lazy" referrerPolicy="no-referrer" />
        <figcaption><strong>{label}</strong></figcaption>
      </figure>
    );
  }
  return (
    <div className="artifact-state" role="alert" data-testid={`plot-${kind}`} data-state={res.state}>
      <strong>{label}</strong>
      <span>{res.detail ?? STATE_TEXT[res.state] ?? res.state}</span>
      {res.state !== 'MISSING' && res.state !== 'ACCESS_DENIED' && <button className="btn btn-sm" type="button" onClick={() => setNonce((n) => n + 1)}>다시 시도</button>}
    </div>
  );
}

export function PlotsCard({ run }: { run: ArchiveRun }): JSX.Element {
  const client = useApi();
  const known = run.plots;
  const present = (k: ArchivePlotKind): boolean | null => {
    if (!known) return null;
    const hit = known.find((p) => p.kind === k || `plot_${p.kind}` === k);
    return hit ? hit.exists : false;
  };
  return (
    <Card title="그림" id="archive-plots">
      <div className="artifact-grid" data-testid="plots-grid">
        {ARCHIVE_PLOT_KINDS.map((p) =>
          present(p.kind) === false ? (
            <div key={p.kind} className="artifact-state" data-testid={`plot-${p.kind}`} data-state="MISSING">
              <strong>{p.label}</strong>
              <span className="muted">이 run에는 파일이 없습니다 (MISSING).</span>
            </div>
          ) : (
            <PlotImage key={`${run.id}-${p.kind}`} runId={run.id} kind={p.kind} label={p.label} client={client} />
          ),
        )}
      </div>
      <p className="help">그림은 런타임이 만든 PNG 그대로이며(토큰은 Authorization 헤더로만 전송) 새로 그리거나 보정하지 않습니다.</p>
    </Card>
  );
}
