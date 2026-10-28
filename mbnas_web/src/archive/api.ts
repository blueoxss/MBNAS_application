/**
 * Composition helpers over the ApiClient archive methods (the endpoints themselves live in api/client.ts):
 * error text, "load everything page by page", a small query hook that keeps stale data while it reloads.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, type ApiClient } from '../api/client';
import type { Loadable } from '../state/context';
import type { ArchiveCandidateRow, ArchiveGeneration, ArchiveGenerationList } from './types';

export const ARCHIVE_PAGE_SIZE = 500;
const MAX_PAGES = 40;

/** Human readable text for a failed archive request, with the server error code when there is one. */
export function archiveErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.kind === 'network') return `서버에 연결할 수 없습니다 (${e.message}).`;
    if (e.kind === 'timeout') return `응답 시간 초과: ${e.message}`;
    if (e.kind === 'config') return e.message;
    if (e.status === 401 || e.status === 403) return `HTTP ${e.status}: 토큰이 없거나 올바르지 않습니다.`;
    const code = e.code ? ` ${e.code}` : '';
    if (e.status === 404) return `HTTP 404${code}: 대상(run / 후보)을 찾을 수 없습니다 — ${e.message}`;
    if (e.status === 422) return `HTTP 422${code}: 잘못된 식별자입니다 — ${e.message}`;
    if (e.status === 503) return `HTTP 503${code}: 아카이브 루트를 읽을 수 없습니다 — ${e.message}`;
    return `HTTP ${e.status ?? ''}${code}: ${e.message}`;
  }
  return e instanceof Error ? e.message : String(e);
}

export const idle = <T,>(): Loadable<T> => ({ status: 'idle', data: null, error: null });

/** Run a request and turn the outcome into a Loadable (never throws; an abort is rethrown to the caller). */
export async function settle<T>(p: Promise<T>): Promise<Loadable<T>> {
  try {
    return { status: 'ok', data: await p, error: null };
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    return { status: 'error', data: null, error: archiveErrorText(e) };
  }
}

export interface ArchiveCandidates {
  rows: ArchiveCandidateRow[];
  total: number;
  warnings: string[];
}

/** Fetch every page of GET /archive/runs/{id}/candidates (pages of ARCHIVE_PAGE_SIZE) and join them. */
export async function fetchAllCandidates(client: ApiClient, runId: string, signal?: AbortSignal): Promise<ArchiveCandidates> {
  const rows: ArchiveCandidateRow[] = [];
  const warnings: string[] = [];
  let total = Infinity;
  for (let page = 0; page < MAX_PAGES && rows.length < total; page++) {
    const r = await client.archiveCandidates(runId, { limit: ARCHIVE_PAGE_SIZE, offset: page * ARCHIVE_PAGE_SIZE }, signal);
    total = typeof r.total === 'number' ? r.total : rows.length + r.candidates.length;
    warnings.push(...(r.warnings ?? []));
    if (r.candidates.length === 0) break;
    rows.push(...r.candidates);
  }
  if (rows.length < total) warnings.push(`후보 ${rows.length} / ${Number.isFinite(total) ? total : '?'}행만 불러왔습니다.`);
  return { rows, total: Number.isFinite(total) ? total : rows.length, warnings };
}

/** Rows keyed by candidate id (first row wins; a candidate has several tier rows). */
export function indexByCandidate(rows: readonly ArchiveCandidateRow[]): Map<string, ArchiveCandidateRow[]> {
  const m = new Map<string, ArchiveCandidateRow[]>();
  for (const r of rows) {
    const l = m.get(r.candidate_id);
    if (l) l.push(r);
    else m.set(r.candidate_id, [r]);
  }
  return m;
}

export function generationsOf(list: ArchiveGenerationList | null | undefined): ArchiveGeneration[] {
  return [...(list?.generations ?? [])].sort((a, b) => a.generation - b.generation);
}

/**
 * Query hook. `key` is the identity of the data (run id, generation ...): a different key drops the old data.
 * `version` only triggers a refresh; the previous data stays visible while the next request is in flight (status
 * 'loading' with data) so a poll refresh does not blank the card. An error keeps the old data of the same key.
 */
export function useArchiveQuery<T>(client: ApiClient | null, key: string | null, fetcher: (c: ApiClient, signal: AbortSignal) => Promise<T>, version: string | number = 0): { state: Loadable<T>; reload: () => void } {
  const [state, setState] = useState<Loadable<T>>(idle());
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const lastKey = useRef<string | null>(null);
  useEffect(() => {
    if (!client || key === null) {
      lastKey.current = null;
      setState(idle());
      return;
    }
    const same = lastKey.current === key;
    lastKey.current = key;
    const ctrl = new AbortController();
    setState((s) => ({ status: 'loading', data: same ? s.data : null, error: null }));
    fetcherRef
      .current(client, ctrl.signal)
      .then((data) => {
        if (!ctrl.signal.aborted) setState({ status: 'ok', data, error: null });
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        setState((s) => ({ status: 'error', data: s.data, error: archiveErrorText(e) }));
      });
    return () => ctrl.abort();
  }, [client, key, version, nonce]);
  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { state, reload };
}
