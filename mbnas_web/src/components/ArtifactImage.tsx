/**
 * One diagnostic image (gt / tp / fn / fp). Delegates the auth rules to lib/artifacts:
 * signed_url -> plain <img>, bearer_fetch -> Authorization only to the API origin -> Blob URL.
 * The Blob URL is revoked on unmount and whenever the artifact changes. Every state is shown as text
 * (never an endless spinner): NOT_READY, EXPIRED, ACCESS_DENIED, MISSING, LOAD_FAILED.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ApiClient } from '../api/client';
import type { Artifact } from '../api/types';
import { loadArtifact, type ArtifactResult, type LoadState } from '../lib/artifacts';
import { Modal } from './Modal';

/**
 * The four diagnostic kinds (at most four images per evaluation) are fetched eagerly so that a broken image is
 * reported as LOAD_FAILED even when the dialog is taller than the browser's lazy-load distance (a one-column
 * mobile layout puts them thousands of px below the fold; a deferred <img> never loads and never errors, so it
 * would sit in READY / data-loaded="false" forever). Native gallery images (up to 256 per candidate) stay lazy.
 */
const EAGER_KINDS: ReadonlySet<string> = new Set(['gt', 'tp', 'fn', 'fp']);

const STATE_TEXT: Record<Exclude<LoadState, 'READY' | 'LOADING'>, { icon: string; title: string; hint: string }> = {
  NOT_READY: { icon: '⏳', title: 'NOT_READY', hint: 'The server has not produced or stored this image yet.' },
  EXPIRED: { icon: '⌛', title: 'EXPIRED', hint: 'The link expired and a refresh did not return a valid one.' },
  ACCESS_DENIED: { icon: '⛔', title: 'ACCESS_DENIED', hint: 'The server refused access (401/403). Check the token.' },
  MISSING: { icon: '∅', title: 'MISSING', hint: 'The artifact does not exist on the server (404).' },
  LOAD_FAILED: { icon: '⚠', title: 'LOAD_FAILED', hint: 'The image could not be loaded, or its stored bytes no longer match the recorded sha256.' },
};

export function useArtifact(artifact: Artifact, runId: string, client: ApiClient | null): { result: ArtifactResult | null; retry: () => void; onImgError: () => void } {
  const [result, setResult] = useState<ArtifactResult | null>(null);
  const [nonce, setNonce] = useState(0);
  const refreshedOnError = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let blobUrl: string | null = null;
    refreshedOnError.current = false;
    setResult(null);
    if (!client) {
      setResult({ state: 'LOAD_FAILED', detail: 'Not connected.' });
      return;
    }
    void loadArtifact(artifact, { client, runId }).then((r) => {
      if (cancelled) {
        if (r.isBlob && r.src) URL.revokeObjectURL(r.src);
        return;
      }
      if (r.isBlob && r.src) blobUrl = r.src;
      setResult(r);
    });
    return () => {
      cancelled = true;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [artifact, runId, client, nonce]);

  // a signed <img> that fails to load: try the refresh endpoint once, then LOAD_FAILED
  const onImgError = useCallback(() => {
    if (!client) return;
    if (!refreshedOnError.current && artifact.id && artifact.auth_mode !== 'bearer_fetch') {
      refreshedOnError.current = true;
      void client
        .refreshArtifact(runId, artifact.id)
        .then((fresh) => setResult(fresh.url && fresh.url !== artifact.url ? { state: 'READY', src: fresh.url } : { state: 'LOAD_FAILED', detail: 'Image failed to load.' }))
        .catch(() => setResult({ state: 'LOAD_FAILED', detail: 'Image failed to load.' }));
      return;
    }
    setResult({ state: 'LOAD_FAILED', detail: 'Image failed to load.' });
  }, [artifact, client, runId]);

  return { result, retry: () => setNonce((n) => n + 1), onImgError };
}

/** Zoom steps (multiples of the natural size); 'fit' scales down to the dialog width. */
export const LIGHTBOX_SCALES: readonly number[] = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4];
export type LightboxScale = 'fit' | number;

/** Next zoom step in the given direction. 'fit' counts as the natural size (1); an off-grid value moves to the next larger/smaller step; the ends are clamped. Pure, so it is unit-tested. */
export function stepScale(current: LightboxScale, direction: 1 | -1): number {
  const base = current === 'fit' ? 1 : current;
  const next = direction === 1 ? LIGHTBOX_SCALES.find((value) => value > base) : [...LIGHTBOX_SCALES].reverse().find((value) => value < base);
  return next ?? (direction === 1 ? LIGHTBOX_SCALES[LIGHTBOX_SCALES.length - 1] : LIGHTBOX_SCALES[0]);
}

/**
 * In-page viewer for one already-loaded diagnostic image. It shows the same src the card shows
 * (the real artifact URL or its Blob URL): no re-inference, no second fetch, no mock. Esc / backdrop / 닫기 close it
 * (Modal returns the focus to the opener); the new-tab original link and the recorded sha256 stay available.
 */
export function ImageLightbox({ src, label, artifact, onClose }: { src: string; label: string; artifact: Artifact; onClose: () => void }): JSX.Element {
  const titleId = useId();
  const [scale, setScale] = useState<LightboxScale>(1);
  const [natural, setNatural] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const sample = artifact.sample_id ?? 'n/a';
  const shown = scale === 'fit' ? 'fit' : `${Math.round(scale * 100)} %`;
  const style = scale === 'fit' || natural === null ? { maxWidth: '100%', height: 'auto' } : { width: natural * scale, maxWidth: 'none', height: 'auto' };
  return (
    <Modal titleId={titleId} onClose={onClose}>
      <div className="modal-head">
        <h2 id={titleId} style={{ margin: 0 }}>{label} · sample {sample}</h2>
        <button className="btn btn-sm" type="button" onClick={onClose} data-autofocus data-testid="lightbox-close" style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>닫기</button>
      </div>
      <div className="row" role="group" aria-label="이미지 확대·축소" style={{ margin: '8px 0' }}>
        <button className="btn btn-sm" type="button" onClick={() => setScale(stepScale(scale, -1))} disabled={scale !== 'fit' && scale <= LIGHTBOX_SCALES[0]} aria-label="축소" data-testid="lightbox-zoom-out">−</button>
        <span className="mono" data-testid="lightbox-scale" aria-live="polite">{shown}</span>
        <button className="btn btn-sm" type="button" onClick={() => setScale(stepScale(scale, 1))} disabled={scale !== 'fit' && scale >= LIGHTBOX_SCALES[LIGHTBOX_SCALES.length - 1]} aria-label="확대" data-testid="lightbox-zoom-in">+</button>
        <button className="btn btn-sm" type="button" onClick={() => setScale(1)} data-testid="lightbox-natural">자연 크기</button>
        <button className="btn btn-sm" type="button" onClick={() => setScale('fit')} data-testid="lightbox-fit">창에 맞춤</button>
        <a href={src} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" data-testid="lightbox-original">원본 새 탭</a>
      </div>
      <div style={{ overflow: 'auto', maxHeight: '70vh', border: '1px solid var(--border)', borderRadius: 4 }} data-testid="lightbox-viewport" tabIndex={0} aria-label={`${label} 확대 영역`}>
        {failed
          ? <p className="field-err" role="alert" data-state="LOAD_FAILED">LOAD_FAILED — 이미지를 확대 창에서 불러오지 못했습니다. 원본 새 탭 링크를 사용하세요.</p>
          : <img src={src} alt={`${label} image for sample ${sample} (enlarged)`} referrerPolicy="no-referrer" style={{ display: 'block', background: '#fff', ...style }}
            onLoad={(event) => setNatural(event.currentTarget.naturalWidth > 0 ? event.currentTarget.naturalWidth : null)} onError={() => setFailed(true)} data-testid="lightbox-image" />}
      </div>
      <p className="help" data-testid="lightbox-meta">
        {artifact.sha256 ? <>artifact sha256 <span className="mono wrap-any">{artifact.sha256}</span></> : 'artifact sha256: 기록 없음'}
        {natural !== null && ` · 자연 크기 너비 ${natural} px`} · 같은 artifact 이미지를 다시 그릴 뿐 재추론하지 않습니다.
      </p>
    </Modal>
  );
}

export function ArtifactImage({ artifact, runId, client, label }: { artifact: Artifact; runId: string; client: ApiClient | null; label: string }): JSX.Element {
  const { result, retry, onImgError } = useArtifact(artifact, runId, client);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgTimeout, setImgTimeout] = useState(false);
  const [zoomed, setZoomed] = useState(false);

  useEffect(() => {
    setImgLoaded(false);
    setImgTimeout(false);
    setZoomed(false);
    if (!result || result.state !== 'READY') return;
    const t = setTimeout(() => setImgTimeout(true), 20000);
    return () => clearTimeout(t);
  }, [result]);

  if (!result) {
    return (
      <div className="artifact-state" role="status" data-state="LOADING" data-testid={`artifact-${artifact.kind}`}>
        <strong>{label}</strong>
        <span className="muted">Loading…</span>
      </div>
    );
  }
  if (result.state === 'READY' && result.src) {
    if (imgTimeout && !imgLoaded) {
      return (
        <div className="artifact-state" role="alert" data-state="LOAD_FAILED" data-testid={`artifact-${artifact.kind}`}>
          <strong>{label}</strong>
          <span>{STATE_TEXT.LOAD_FAILED.icon} LOAD_FAILED — timed out after 20 s.</span>
          <button className="btn btn-sm" type="button" onClick={retry}>
            Retry
          </button>
        </div>
      );
    }
    return (
      <figure style={{ margin: 0 }} data-state="READY" data-loaded={imgLoaded ? 'true' : 'false'} data-testid={`artifact-${artifact.kind}`}>
        <img
          src={result.src}
          alt={`${label} image for sample ${artifact.sample_id ?? 'n/a'}`}
          referrerPolicy="no-referrer"
          loading={EAGER_KINDS.has(artifact.kind) ? 'eager' : 'lazy'} /* gallery images defer until near the viewport; data-loaded reports the real load */
          onLoad={() => setImgLoaded(true)}
          onError={onImgError}
          onClick={() => setZoomed(true)}
          style={{ cursor: 'zoom-in' }}
        />
        <figcaption>
          <strong>{label}</strong> · <button className="btn btn-sm" type="button" onClick={() => setZoomed(true)} aria-label={`${label} 확대 보기`} data-testid={`artifact-zoom-${artifact.kind}`}>확대</button> <a href={result.src} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" style={{ whiteSpace: 'nowrap' }}>원본 새 탭</a>
          {artifact.sample_id && <> · <span className="help" data-testid={`artifact-sample-${artifact.kind}`}>sample {artifact.sample_id}</span></>}
        </figcaption>
        {zoomed && <ImageLightbox src={result.src} label={label} artifact={artifact} onClose={() => setZoomed(false)} />}
      </figure>
    );
  }
  const s = STATE_TEXT[result.state === 'READY' ? 'LOAD_FAILED' : result.state];
  return (
    <div className="artifact-state" role="alert" data-state={result.state} data-testid={`artifact-${artifact.kind}`}>
      <strong>{label}</strong>
      <span>
        <span aria-hidden="true">{s.icon} </span>
        {s.title}
      </span>
      <span className="help">{result.detail ?? s.hint}</span>
      {result.code && <span className="help mono" data-testid={`artifact-code-${artifact.kind}`}>code {result.code}</span>}
      {(result.state === 'NOT_READY' || result.state === 'LOAD_FAILED' || result.state === 'EXPIRED') && (
        <button className="btn btn-sm" type="button" onClick={retry}>
          Retry
        </button>
      )}
    </div>
  );
}
