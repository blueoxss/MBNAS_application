/**
 * Application model: connection (origin + in-memory token), server data (health/presets/runs),
 * the experiment policy editor with Validate -> Run flow, and the loaded run (snapshot + SSE).
 *
 * Policy source: the editor starts empty; no template is embedded in the bundle. `connect()` checks
 * health, loads the server presets and, while the editor is still untouched (nothing typed, no preset chosen, no run
 * loaded), applies the server preset DEFAULT_BASELINE14 with the visible label "server preset DEFAULT_BASELINE14
 * (design example)". The connection reports ready only after that step, so Validate never runs on a half-initialised editor.
 *
 * Persistence: NOTHING is written to localStorage/sessionStorage/cookies. The bearer token lives only in
 * React state; the run id may appear in the URL hash (it is not a secret), the token never does.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react';
import { ApiClient, ApiError, normalizeOrigin, type OriginCheck } from '../api/client';
import { fetchAllCandidates, idle as idleLoad, settle, type ArchiveCandidates } from '../archive/api';
import { buildArchiveHash, buildCompareHash, parseHashString } from '../archive/hash';
import type { ArchiveBaselineList, ArchiveGenerationList, ArchiveLive, ArchiveRun, ArchiveRunList } from '../archive/types';
import { responseChunks, streamingSupported } from '../api/sse';
import { abortableSleep, RunSync, type SyncDeps } from '../api/sync';
import type { Candidate, FinalRetrainRequest, GeneratedCandidate, Health, Preset, Run, ValidateResponse } from '../api/types';
import { isNonterminal } from '../api/types';
import { IdempotencyStore, submitFinalRetrain, submitRun, type SubmitOutcome } from '../lib/runflow';
import { canFinalRetrain, classifyFinalError, type FinalErrorKind } from '../lib/finalretrain';
import { generatedAsCandidate } from '../lib/generation';
import { isEmptyPolicy, normalizePolicy, setIn, stableStringify, unsetIn, type Policy } from '../lib/policy';
import { validatePolicy, type Issue } from '../lib/validate';
import { initialRunState, runReducer, candidateList, type RunState } from './reducer';

export type Screen = 'archive' | 'compare' | 'monitor' | 'settings' | 'lineage' | 'history' | 'server';
export const SCREENS: readonly Screen[] = ['archive', 'compare', 'monitor', 'settings', 'lineage', 'history', 'server'];
/** Screens that read the read-only NAS archive API (their `run` hash parameter is an archive run id, not a console run). */
export const ARCHIVE_SCREENS: readonly Screen[] = ['archive', 'compare'];

/** Poll period of a running archive run (no SSE). */
export const ARCHIVE_POLL_MS = 10_000;

export interface ArchivePoll {
  active: boolean;
  lastOkAt: string | null;
  failures: number;
  lastError: string | null;
}

/** State of the NAS archive screens (design 4.1/4.2). All data is read-only server data; nothing is persisted. */
export interface ArchiveModel {
  runs: Loadable<ArchiveRunList>;
  refreshRuns: () => Promise<void>;
  runId: string | null;
  selectRun: (id: string | null) => void;
  detail: Loadable<ArchiveRun>;
  baselines: Loadable<ArchiveBaselineList>;
  generations: Loadable<ArchiveGenerationList>;
  candidates: Loadable<ArchiveCandidates>;
  live: Loadable<ArchiveLive>;
  poll: ArchivePoll;
  /** reload every section of the selected run now */
  reload: () => void;
  gen: number | null;
  selectGen: (g: number | null) => void;
  cid: string | null;
  openCandidate: (cid: string) => void;
  closeCandidate: () => void;
  compareIds: string[];
  setCompareIds: (ids: string[]) => void;
}

export interface Loadable<T> {
  status: 'idle' | 'loading' | 'ok' | 'error';
  data: T | null;
  error: string | null;
}

export interface ValidationState {
  status: 'idle' | 'pending' | 'done' | 'error';
  response: ValidateResponse | null;
  /** origin + canonical editor JSON that was validated; compared with the current state for dirtiness */
  requestKey: string | null;
  error: string | null;
  httpStatus: number | null;
}

export interface SubmitState {
  status: 'idle' | 'pending' | 'created' | 'retryable' | 'conflict' | 'rejected';
  message: string | null;
  key: string | null;
  runId: string | null;
  errors: { field?: string; message?: string; code?: string }[];
}

/** State of the last POST /runs/{id}/final-retrain attempt, tied to the parent run it was sent for. */
export interface FinalRetrainState {
  status: 'idle' | 'pending' | 'created' | 'retryable' | 'conflict' | 'rejected';
  parentRunId: string | null;
  errorKind: FinalErrorKind | null;
  message: string | null;
  key: string | null;
  childRunId: string | null;
  finalistCount: number | null;
  errors: { field?: string; message?: string; code?: string }[];
}

export const IDLE_FINAL: FinalRetrainState = { status: 'idle', parentRunId: null, errorKind: null, message: null, key: null, childRunId: null, finalistCount: null, errors: [] };

export interface ControlState {
  status: 'idle' | 'pending' | 'ok' | 'error';
  message: string | null;
}

/** Server preset applied automatically after the first successful connection while the editor is untouched. */
export const DEFAULT_PRESET_ID = 'DEFAULT_BASELINE14';
/** Visible label of the automatically applied preset. */
export const AUTO_PRESET_LABEL = `server preset ${DEFAULT_PRESET_ID} (design example)`;
/** Label of the empty editor before any policy was loaded. */
export const NO_POLICY_LABEL = 'no policy loaded';
/** Label after the user typed into the empty editor (no preset or run was loaded first). */
export const EDITED_POLICY_LABEL = 'manual edits (no server preset loaded)';

/** True when the loaded run's state makes the policy immutable. */
export function isPolicyLocked(run: Pick<Run, 'status'> | null | undefined): boolean {
  return !!run && isNonterminal(run.status);
}

export function validationKey(origin: string, policy: unknown): string {
  return `${origin}|${stableStringify(policy)}`;
}

/** Run may be sent only after a valid Validate of the current, unchanged editor state. */
export function canRun(v: ValidationState, currentKey: string): boolean {
  return v.status === 'done' && !!v.response?.valid && !!v.response.protocol_hash && v.requestKey === currentKey;
}

function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.kind === 'network') return `Cannot reach the server (${e.message}). Check the API origin, that the server is running, and CORS (exact page origin must be allowed).`;
    if (e.kind === 'timeout') return e.message;
    if (e.status === 401) return 'HTTP 401: token missing or invalid.';
    if (e.status === 403) return 'HTTP 403: not authorized.';
    return `HTTP ${e.status ?? ''}${e.code ? ` ${e.code}` : ''}: ${e.message}`;
  }
  return e instanceof Error ? e.message : String(e);
}

function parseHash(): { screen: Screen; run: string | null; cid: string | null; archive: { run: string | null; gen: number | null; cid: string | null; runs: string[] } } {
  const raw = typeof window !== 'undefined' ? window.location.hash : '';
  // an empty hash opens the NAS archive (default entry point); an unknown name does too
  const p = parseHashString(raw, SCREENS, 'archive');
  const screen = p.screen as Screen;
  const isArchive = (ARCHIVE_SCREENS as readonly string[]).includes(screen);
  return { screen, run: isArchive ? null : p.run, cid: isArchive ? null : p.cid, archive: { run: isArchive ? p.run : null, gen: isArchive ? p.gen : null, cid: isArchive ? p.cid : null, runs: p.runs } };
}

export interface AppModel {
  screen: Screen;
  setScreen: (s: Screen) => void;
  // connection
  originInput: string;
  setOriginInput: (v: string) => void;
  originCheck: OriginCheck;
  token: string;
  setToken: (v: string) => void;
  client: ApiClient | null;
  health: Loadable<Health>;
  connect: () => Promise<void>;
  presets: Loadable<Preset[]>;
  loadPresets: () => Promise<void>;
  runs: Loadable<Run[]>;
  refreshRuns: () => Promise<void>;
  // policy
  policy: Policy;
  setPolicy: (p: Policy) => void;
  /** value undefined removes the key (optional contract fields must be absent, not null) */
  editField: (path: string, value: unknown) => void;
  applyServerPreset: (p: Preset) => void;
  presetLabel: string;
  /** true while the editor holds the DEFAULT_BASELINE14 preset that connect() applied automatically (no edit since) */
  presetAutoApplied: boolean;
  locked: boolean;
  clientIssues: Issue[];
  validation: ValidationState;
  validate: () => Promise<void>;
  runnable: boolean;
  dirty: boolean;
  submit: () => Promise<void>;
  submitState: SubmitState;
  forkFromRun: () => void;
  // loaded run
  rs: RunState;
  loadRun: (id: string) => Promise<void>;
  /** initial snapshot of the requested run failed (e.g. 404): inline error, run not loaded */
  loadError: { runId: string; message: string } | null;
  detachRun: () => void;
  control: (action: 'pause' | 'resume' | 'cancel', reason: string) => Promise<void>;
  controlState: ControlState;
  exportRun: () => Promise<void>;
  exportState: ControlState;
  finalState: FinalRetrainState;
  startFinalRetrain: (request: Omit<FinalRetrainRequest, 'idempotency_key'>) => Promise<void>;
  // NAS archive
  archive: ArchiveModel;
  // candidate modal
  modalCandidate: Candidate | null;
  openCandidate: (c: Candidate, opts?: { restore?: boolean }) => void;
  closeCandidate: () => void;
}

const Ctx = createContext<AppModel | null>(null);

export function useApp(): AppModel {
  const v = useContext(Ctx);
  if (!v) throw new Error('useApp outside AppProvider');
  return v;
}

const idle = <T,>(): Loadable<T> => ({ status: 'idle', data: null, error: null });

export function AppProvider({ children, initialToken = '' }: { children: ReactNode; initialToken?: string }): JSX.Element {
  const boot = useMemo(parseHash, []);
  const [screen, setScreenState] = useState<Screen>(boot.screen);
  const [originInput, setOriginInput] = useState('');
  const [token, setToken] = useState(initialToken);
  const originCheck = useMemo(() => normalizeOrigin(originInput), [originInput]);
  const client = useMemo(() => (originCheck.ok ? new ApiClient({ origin: originCheck.origin, token }) : null), [originCheck, token]);
  const clientRef = useRef<ApiClient | null>(client);
  clientRef.current = client;

  const [health, setHealth] = useState<Loadable<Health>>(idle());
  const [presets, setPresets] = useState<Loadable<Preset[]>>(idle());
  const [runs, setRuns] = useState<Loadable<Run[]>>(idle());

  const [policy, setPolicyState] = useState<Policy>({});
  const [presetLabel, setPresetLabel] = useState<string>(NO_POLICY_LABEL);
  const [presetAutoApplied, setPresetAutoApplied] = useState(false);
  /** true until the editor receives anything other than the automatic preset (an edit, a chosen preset, a run, a fork) */
  const pristineRef = useRef(true);
  const touchPolicy = useCallback(() => {
    pristineRef.current = false;
    setPresetAutoApplied(false);
    // an edit of the empty editor is labelled as such; a preset / run / fork sets its own label right after this call
    setPresetLabel((l) => (l === NO_POLICY_LABEL ? EDITED_POLICY_LABEL : l));
  }, []);
  const [validation, setValidation] = useState<ValidationState>({ status: 'idle', response: null, requestKey: null, error: null, httpStatus: null });
  const [submitState, setSubmitState] = useState<SubmitState>({ status: 'idle', message: null, key: null, runId: null, errors: [] });
  const [controlState, setControlState] = useState<ControlState>({ status: 'idle', message: null });
  const [loadError, setLoadError] = useState<{ runId: string; message: string } | null>(null);
  const [exportState, setExportState] = useState<ControlState>({ status: 'idle', message: null });
  const [finalState, setFinalState] = useState<FinalRetrainState>(IDLE_FINAL);
  const [rs, dispatch] = useReducer(runReducer, initialRunState);
  const [modalCandidate, setModalCandidate] = useState<Candidate | null>(null);

  const idemRef = useRef(new IdempotencyStore());
  const syncRef = useRef<RunSync | null>(null);
  const rsRef = useRef<RunState>(rs);
  rsRef.current = rs;

  const locked = isPolicyLocked(rs.run);
  const lockedRef = useRef(locked);
  lockedRef.current = locked;

  const openCandidate = useCallback((candidate: Candidate, opts?: { restore?: boolean }) => {
    setModalCandidate(candidate);
    const url = new URL(window.location.href);
    const qi = url.hash.indexOf('?');
    const qs = new URLSearchParams(qi < 0 ? '' : url.hash.slice(qi + 1));
    // block / model_tab belong to one candidate. Switching to a different candidate must not inherit them
    // (restoring the same cid after a reload keeps them).
    // the boot restore (opts.restore) re-opens the same candidate after setScreen() dropped cid from the hash, so it must keep them.
    if (!opts?.restore && qs.get('cid') !== candidate.candidate_id) { url.searchParams.delete('block'); url.searchParams.delete('model_tab'); }
    if (rsRef.current.runId) qs.set('run', rsRef.current.runId);
    qs.set('cid', candidate.candidate_id);
    url.hash = `/${screenRef.current}?${qs}`;
    window.history.replaceState(null, '', url);
  }, []);
  const closeCandidate = useCallback(() => {
    setModalCandidate(null);
    const url = new URL(window.location.href);
    const qi = url.hash.indexOf('?');
    const qs = new URLSearchParams(qi < 0 ? '' : url.hash.slice(qi + 1));
    qs.delete('cid');
    // the tab and selected block are per-candidate view state (real query string and, defensively, the hash query).
    url.searchParams.delete('block'); url.searchParams.delete('model_tab');
    qs.delete('block'); qs.delete('model_tab');
    url.hash = `${qi < 0 ? url.hash : url.hash.slice(0, qi)}${qs.size ? `?${qs}` : ''}`;
    window.history.replaceState(null, '', url);
  }, []);
  const restoredCandidate = useRef(false);
  useEffect(() => {
    if (restoredCandidate.current || !boot.cid || !rs.loaded || rs.runId !== boot.run) return;
    const hit = candidateList(rs).find((candidate) => candidate.candidate_id === boot.cid);
    const generated = rs.generatedCandidates.find((candidate) => candidate.candidate_id === boot.cid);
    if (hit || generated) { restoredCandidate.current = true; openCandidate(hit ?? generatedAsCandidate(generated as GeneratedCandidate), { restore: true }); }
  }, [boot.cid, boot.run, rs, openCandidate]);

  // ---- NAS archive state -------------------------------------------------------------------
  const [archRuns, setArchRuns] = useState<Loadable<ArchiveRunList>>(idleLoad());
  const [archRunId, setArchRunId] = useState<string | null>(boot.archive.run);
  const [archGen, setArchGen] = useState<number | null>(boot.archive.gen);
  const [archCid, setArchCid] = useState<string | null>(boot.archive.cid);
  const [archDetail, setArchDetail] = useState<Loadable<ArchiveRun>>(idleLoad());
  const [archBaselines, setArchBaselines] = useState<Loadable<ArchiveBaselineList>>(idleLoad());
  const [archGenerations, setArchGenerations] = useState<Loadable<ArchiveGenerationList>>(idleLoad());
  const [archCandidates, setArchCandidates] = useState<Loadable<ArchiveCandidates>>(idleLoad());
  const [archLive, setArchLive] = useState<Loadable<ArchiveLive>>(idleLoad());
  const [archPoll, setArchPoll] = useState<ArchivePoll>({ active: false, lastOkAt: null, failures: 0, lastError: null });
  const [compareIds, setCompareIdsState] = useState<string[]>(boot.archive.runs);
  const archSel = useRef({ run: boot.archive.run, gen: boot.archive.gen, cid: boot.archive.cid });
  const compareRef = useRef<string[]>(boot.archive.runs);
  const screenRef = useRef<Screen>(boot.screen);
  screenRef.current = screen;

  const writeArchiveHash = useCallback((sel: { run: string | null; gen: number | null; cid: string | null }) => {
    archSel.current = sel;
    if (screenRef.current === 'archive') window.history.replaceState(null, '', buildArchiveHash(sel));
  }, []);

  const setScreen = useCallback((s: Screen) => {
    setScreenState(s);
    screenRef.current = s;
    if (s === 'archive') {
      window.history.replaceState(null, '', buildArchiveHash(archSel.current));
      return;
    }
    if (s === 'compare') {
      window.history.replaceState(null, '', buildCompareHash(compareRef.current));
      return;
    }
    const run = rsRef.current.runId;
    window.history.replaceState(null, '', `#/${s}${run ? `?run=${encodeURIComponent(run)}` : ''}`);
  }, []);

  // ---- server data -------------------------------------------------------------------------
  /** GET /protocols/presets into `presets`; returns the list (null on failure, which is shown and never thrown). */
  const fetchPresets = useCallback(async (c: ApiClient): Promise<Preset[] | null> => {
    setPresets({ status: 'loading', data: null, error: null });
    try {
      const raw = await c.presets();
      const list = Array.isArray(raw) ? raw : [];
      setPresets({ status: 'ok', data: list, error: null });
      return list;
    } catch (e) {
      setPresets({ status: 'error', data: null, error: describeError(e) });
      return null;
    }
  }, []);

  const connect = useCallback(async () => {
    const c = clientRef.current;
    if (!c) return;
    setHealth({ status: 'loading', data: null, error: null });
    let h: Health;
    try {
      h = await c.health();
    } catch (e) {
      setHealth({ status: 'error', data: null, error: describeError(e) });
      return;
    }
    // presets are loaded automatically on a successful connection; DEFAULT_BASELINE14 fills an untouched editor.
    const list = await fetchPresets(c);
    const preset = list?.find((p) => p.id === DEFAULT_PRESET_ID);
    if (preset && pristineRef.current && !lockedRef.current && !rsRef.current.runId) {
      setPolicyState(normalizePolicy(JSON.parse(JSON.stringify(preset.protocol)) as Policy));
      setPresetLabel(AUTO_PRESET_LABEL);
      setPresetAutoApplied(true);
    }
    setHealth({ status: 'ok', data: h, error: null });
  }, [fetchPresets]);

  const loadPresets = useCallback(async () => {
    const c = clientRef.current;
    if (!c) return;
    await fetchPresets(c);
  }, [fetchPresets]);

  const refreshRuns = useCallback(async () => {
    const c = clientRef.current;
    if (!c) return;
    setRuns((r) => ({ ...r, status: 'loading', error: null }));
    try {
      setRuns({ status: 'ok', data: await c.listRuns(), error: null });
    } catch (e) {
      setRuns({ status: 'error', data: null, error: describeError(e) });
    }
  }, []);

  // ---- NAS archive loaders -----------------------------------------------------------------
  const refreshArchiveRuns = useCallback(async () => {
    const c = clientRef.current;
    if (!c) return;
    setArchRuns((r) => ({ status: 'loading', data: r.data, error: null }));
    const r = await settle(c.archiveRuns());
    setArchRuns((prev) => (r.status === 'ok' ? r : { status: 'error', data: prev.data, error: r.error }));
  }, []);

  /** 'full' blanks every section and loads it again; 'poll' replaces a section only when its request succeeded. */
  const loadArchiveRun = useCallback(async (id: string, mode: 'full' | 'poll', signal: AbortSignal) => {
    const c = clientRef.current;
    if (!c) return;
    if (mode === 'full') {
      const loading = { status: 'loading' as const, data: null, error: null };
      setArchDetail(loading);
      setArchBaselines(loading);
      setArchGenerations(loading);
      setArchCandidates(loading);
    }
    let failure: string | null = null;
    const apply = <T,>(r: Loadable<T>, set: (v: Loadable<T>) => void): void => {
      if (signal.aborted) return;
      if (mode === 'poll' && r.status !== 'ok') {
        failure = failure ?? r.error ?? 'error';
        return;
      }
      set(r);
    };
    try {
      await Promise.all([
        settle(c.archiveRun(id, signal)).then((r) => apply(r, setArchDetail)),
        settle(c.archiveBaselines(id, signal)).then((r) => apply(r, setArchBaselines)),
        settle(c.archiveGenerations(id, signal)).then((r) => apply(r, setArchGenerations)),
        settle(fetchAllCandidates(c, id, signal)).then((r) => apply(r, setArchCandidates)),
      ]);
    } catch {
      return; // aborted
    }
    if (mode === 'poll' && !signal.aborted) {
      const err = failure;
      setArchPoll((p) => (err ? { ...p, failures: p.failures + 1, lastError: err } : { ...p, failures: 0, lastError: null, lastOkAt: new Date().toISOString() }));
    }
  }, []);

  const refreshArchiveLive = useCallback(async (signal: AbortSignal): Promise<string | null> => {
    const c = clientRef.current;
    if (!c) return null;
    const r = await settle(c.archiveLive(signal)).catch(() => null);
    if (!r || signal.aborted) return null;
    if (r.status === 'ok') setArchLive(r);
    return r.status === 'ok' ? null : r.error;
  }, []);

  const archiveOn = screen === 'archive';
  const connected = health.status === 'ok';

  // first visit of an archive screen after the server answered: list the runs
  useEffect(() => {
    if (connected && (screen === 'archive' || screen === 'compare') && archRuns.status === 'idle') void refreshArchiveRuns();
  }, [connected, screen, archRuns.status, refreshArchiveRuns]);

  // the selected run: (re)load all sections whenever it is shown
  const [archNonce, setArchNonce] = useState(0);
  useEffect(() => {
    if (!client || !connected || !archRunId || !archiveOn) return;
    const ctrl = new AbortController();
    setArchLive(idleLoad());
    setArchPoll({ active: false, lastOkAt: null, failures: 0, lastError: null });
    void loadArchiveRun(archRunId, 'full', ctrl.signal);
    return () => ctrl.abort();
  }, [client, connected, archRunId, archiveOn, archNonce, loadArchiveRun]);

  // a running run is polled every ARCHIVE_POLL_MS; a failed poll keeps the data on screen and is retried
  const archStatus = archDetail.data?.status ?? archRuns.data?.runs.find((r) => r.id === archRunId)?.status;
  useEffect(() => {
    if (!client || !connected || !archRunId || !archiveOn || archStatus !== 'running') {
      setArchPoll((p) => (p.active ? { ...p, active: false } : p));
      return;
    }
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setArchPoll((p) => ({ ...p, active: true }));
    void refreshArchiveLive(ctrl.signal);
    const tick = async (): Promise<void> => {
      if (ctrl.signal.aborted) return;
      if (typeof document === 'undefined' || !document.hidden) {
        await loadArchiveRun(archRunId, 'poll', ctrl.signal);
        const liveErr = await refreshArchiveLive(ctrl.signal);
        if (liveErr && !ctrl.signal.aborted) setArchPoll((p) => ({ ...p, failures: p.failures + 1, lastError: liveErr }));
      }
      if (!ctrl.signal.aborted) timer = setTimeout(() => void tick(), ARCHIVE_POLL_MS);
    };
    timer = setTimeout(() => void tick(), ARCHIVE_POLL_MS);
    return () => {
      ctrl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [client, connected, archRunId, archiveOn, archStatus, loadArchiveRun, refreshArchiveLive]);

  // the run stopped running: the list row (status badge, best values) is stale now
  const prevArchStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (prevArchStatus.current === 'running' && archStatus && archStatus !== 'running') void refreshArchiveRuns();
    prevArchStatus.current = archStatus;
  }, [archStatus, refreshArchiveRuns]);

  const selectArchiveRun = useCallback(
    (id: string | null) => {
      setArchRunId(id);
      setArchGen(null);
      setArchCid(null);
      if (id === null) {
        setArchDetail(idleLoad());
        setArchBaselines(idleLoad());
        setArchGenerations(idleLoad());
        setArchCandidates(idleLoad());
      }
      writeArchiveHash({ run: id, gen: null, cid: null });
    },
    [writeArchiveHash],
  );
  const selectArchiveGen = useCallback(
    (g: number | null) => {
      setArchGen(g);
      writeArchiveHash({ run: archSel.current.run, gen: g, cid: null });
      setArchCid(null);
    },
    [writeArchiveHash],
  );
  const openArchiveCandidate = useCallback(
    (cid: string) => {
      setArchCid(cid);
      writeArchiveHash({ ...archSel.current, cid });
    },
    [writeArchiveHash],
  );
  const closeArchiveCandidate = useCallback(() => {
    setArchCid(null);
    writeArchiveHash({ ...archSel.current, cid: null });
  }, [writeArchiveHash]);
  const setCompareIds = useCallback((ids: string[]) => {
    const next = [...new Set(ids)].slice(0, 6);
    compareRef.current = next;
    setCompareIdsState(next);
    if (screenRef.current === 'compare') window.history.replaceState(null, '', buildCompareHash(next));
  }, []);

  const archive: ArchiveModel = {
    runs: archRuns,
    refreshRuns: refreshArchiveRuns,
    runId: archRunId,
    selectRun: selectArchiveRun,
    detail: archDetail,
    baselines: archBaselines,
    generations: archGenerations,
    candidates: archCandidates,
    live: archLive,
    poll: archPoll,
    reload: () => setArchNonce((n) => n + 1),
    gen: archGen,
    selectGen: selectArchiveGen,
    cid: archCid,
    openCandidate: openArchiveCandidate,
    closeCandidate: closeArchiveCandidate,
    compareIds,
    setCompareIds,
  };

  // ---- policy ------------------------------------------------------------------------------
  const setPolicy = useCallback((p: Policy) => {
    if (lockedRef.current) return;
    touchPolicy();
    setPolicyState(p);
  }, [touchPolicy]);
  const editField = useCallback((path: string, value: unknown) => {
    if (lockedRef.current) return;
    touchPolicy();
    setPolicyState((p) => normalizePolicy(value === undefined ? unsetIn(p, path) : setIn(p, path, value)));
  }, [touchPolicy]);
  const applyServerPreset = useCallback((p: Preset) => {
    if (lockedRef.current) return;
    touchPolicy();
    setPolicyState(normalizePolicy(JSON.parse(JSON.stringify(p.protocol)) as Policy));
    setPresetLabel(`${p.name} (server preset ${p.id})`);
  }, [touchPolicy]);

  // an empty editor (nothing loaded or typed yet) has nothing to pre-check; Settings says so instead of listing missing rungs
  const clientIssues = useMemo(() => (isEmptyPolicy(policy) ? [] : validatePolicy(policy)), [policy]);
  const originKey = originCheck.ok ? originCheck.origin : '!';
  const currentKey = validationKey(originKey, policy);
  const runnable = canRun(validation, currentKey);
  const dirty = validation.status !== 'idle' && validation.requestKey !== currentKey;

  const validate = useCallback(async () => {
    const c = clientRef.current;
    if (!c) return;
    const key = validationKey(c.origin, policy);
    setValidation({ status: 'pending', response: null, requestKey: key, error: null, httpStatus: null });
    setSubmitState({ status: 'idle', message: null, key: null, runId: null, errors: [] });
    try {
      const response = await c.validate(policy);
      setValidation({ status: 'done', response, requestKey: key, error: null, httpStatus: 200 });
    } catch (e) {
      const status = e instanceof ApiError ? e.status : null;
      const errors = e instanceof ApiError && e.body?.errors ? e.body.errors : [];
      setValidation({
        status: 'error',
        response: errors.length ? ({ valid: false, errors: errors as never, protocol_hash: null, resolved: null, capabilities: {} } as ValidateResponse) : null,
        requestKey: key,
        error: describeError(e),
        httpStatus: status,
      });
    }
  }, [policy]);

  // ---- loaded run --------------------------------------------------------------------------
  const detachRun = useCallback(() => {
    syncRef.current?.stop();
    syncRef.current = null;
    dispatch({ type: 'reset' });
    window.history.replaceState(null, '', `#/${screen}`);
  }, [screen]);

  const loadRun = useCallback(async (id: string) => {
    const c = clientRef.current;
    if (!c) return;
    syncRef.current?.stop();
    setModalCandidate(null);
    setLoadError(null);
    dispatch({ type: 'begin', runId: id });
    const deps: SyncDeps = {
      runId: id,
      fetchSnapshot: (signal) => c.snapshot(id, signal),
      openStream: async (after, signal) => {
        if (!streamingSupported()) return null;
        const res = await c.openEvents(id, after, signal);
        return res.body ? responseChunks(res) : null;
      },
      sleep: abortableSleep,
    };
    const sync = new RunSync(deps, {
      onSnapshot: (snap, reason) => {
        dispatch({ type: 'snapshot', snap, reason, at: new Date().toISOString() });
        const resolved = snap.run?.resolved_protocol;
        if (reason === 'initial' && resolved) {
          touchPolicy();
          setPolicyState(normalizePolicy(JSON.parse(JSON.stringify(resolved)) as Policy));
          setPresetLabel(`run ${snap.run.id} resolved protocol`);
        }
      },
      onEvent: (ev) => dispatch({ type: 'event', ev }),
      onStatus: (status, detail) => {
        if ((status === 'error' || status === 'auth_error') && !rsRef.current.loaded && rsRef.current.runId === id) {
          // initial snapshot failed: do not enter the loaded state, report inline in the Load run card
          setLoadError({ runId: id, message: detail || status });
          syncRef.current?.stop();
          syncRef.current = null;
          dispatch({ type: 'reset' });
          window.history.replaceState(null, '', `#/${screen}`);
          return;
        }
        dispatch({ type: 'status', status, detail, at: new Date().toISOString() });
      },
      isTerminal: () => !isNonterminal(rsRef.current.run?.status) && rsRef.current.run !== null,
    });
    syncRef.current = sync;
    window.history.replaceState(null, '', `#/${screen}?run=${encodeURIComponent(id)}`);
    void sync.start();
  }, [screen, touchPolicy]);

  useEffect(() => () => syncRef.current?.stop(), []);

  const forkFromRun = useCallback(() => {
    const resolved = rsRef.current.run?.resolved_protocol;
    syncRef.current?.stop();
    syncRef.current = null;
    dispatch({ type: 'reset' });
    lockedRef.current = false;
    touchPolicy();
    if (resolved) setPolicyState(normalizePolicy(JSON.parse(JSON.stringify(resolved)) as Policy));
    setPresetLabel('forked from run (new version, not yet validated)');
    setValidation({ status: 'idle', response: null, requestKey: null, error: null, httpStatus: null });
    window.history.replaceState(null, '', `#/${screen}`);
  }, [screen, touchPolicy]);

  const submit = useCallback(async () => {
    const c = clientRef.current;
    const v = validation.response;
    if (!c || !v || !canRun(validation, validationKey(c.origin, policy))) return;
    setSubmitState((s) => ({ ...s, status: 'pending', message: null }));
    let outcome: SubmitOutcome;
    try {
      outcome = await submitRun(c, idemRef.current, v);
    } catch (e) {
      setSubmitState({ status: 'rejected', message: describeError(e), key: null, runId: null, errors: [] });
      return;
    }
    if (outcome.kind === 'created') {
      setSubmitState({ status: 'created', message: `Run created: ${outcome.created.run_id}`, key: outcome.key, runId: outcome.created.run_id, errors: [] });
      void loadRun(outcome.created.run_id);
      return;
    }
    const errors = outcome.error.body?.errors ?? [];
    setSubmitState({
      status: outcome.kind,
      message:
        outcome.kind === 'retryable'
          ? `${describeError(outcome.error)} — retry will reuse Idempotency-Key ${outcome.key}`
          : outcome.kind === 'conflict'
            ? `409 conflict: ${outcome.error.message}`
            : `Rejected: ${outcome.error.message}`,
      key: outcome.key,
      runId: null,
      errors: errors as SubmitState['errors'],
    });
  }, [validation, policy, loadRun]);

  const control = useCallback(async (action: 'pause' | 'resume' | 'cancel', reason: string) => {
    const c = clientRef.current;
    const runId = rsRef.current.runId;
    if (!c || !runId) return;
    const fp = `${runId}|${action}|${reason}`;
    const key = idemRef.current.keyFor(fp);
    setControlState({ status: 'pending', message: null });
    try {
      const run = await c.control(runId, action, { reason: reason || undefined, idempotency_key: key } as { reason?: string; idempotency_key: string });
      idemRef.current.settle(fp);
      dispatch({ type: 'run', run });
      setControlState({ status: 'ok', message: `${action}: server answered ${run.status}` });
    } catch (e) {
      if (e instanceof ApiError && e.kind === 'http' && (e.status === 409 || e.status === 422)) idemRef.current.settle(fp);
      setControlState({ status: 'error', message: describeError(e) });
    }
  }, []);

  const exportRun = useCallback(async () => {
    const c = clientRef.current;
    const runId = rsRef.current.runId;
    if (!c || !runId) return;
    setExportState({ status: 'pending', message: null });
    try {
      const blob = await c.exportRun(runId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `mbnas-export-${runId}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      setExportState({ status: 'ok', message: `Downloaded ${blob.size} bytes` });
    } catch (e) {
      setExportState({ status: 'error', message: describeError(e) });
    }
  }, []);

  const startFinalRetrain = useCallback(async (request: Omit<FinalRetrainRequest, 'idempotency_key'>) => {
    const c = clientRef.current;
    const parent = rsRef.current.run;
    if (!c || !parent || !canFinalRetrain(parent)) return;
    const parentRunId = parent.id;
    setFinalState({ ...IDLE_FINAL, status: 'pending', parentRunId });
    let outcome: SubmitOutcome;
    try {
      outcome = await submitFinalRetrain(c, idemRef.current, { parentRunId, request });
    } catch (e) {
      setFinalState({ ...IDLE_FINAL, status: 'rejected', parentRunId, errorKind: 'OTHER', message: describeError(e) });
      return;
    }
    if (outcome.kind === 'created') {
      const n = outcome.created.finalist_count;
      setFinalState({
        ...IDLE_FINAL,
        status: 'created',
        parentRunId,
        key: outcome.key,
        childRunId: outcome.created.run_id,
        finalistCount: typeof n === 'number' ? n : null,
        message: `FINAL_RETRAIN run created: ${outcome.created.run_id}`,
      });
      return;
    }
    const info = classifyFinalError(outcome.error);
    setFinalState({
      ...IDLE_FINAL,
      status: outcome.kind,
      parentRunId,
      key: outcome.key,
      errorKind: info.kind,
      message: outcome.kind === 'retryable' ? `${info.message} — retry will reuse Idempotency-Key ${outcome.key}` : info.message,
      errors: (outcome.error.body?.errors ?? []) as FinalRetrainState['errors'],
    });
  }, []);

  // reload convenience: once connected, load the run named in the URL hash (never the token)
  const autoLoaded = useRef(false);
  useEffect(() => {
    if (health.status === 'ok' && boot.run && !autoLoaded.current) {
      autoLoaded.current = true;
      void loadRun(boot.run);
    }
  }, [health.status, boot.run, loadRun]);

  const model: AppModel = {
    screen,
    setScreen,
    originInput,
    setOriginInput,
    originCheck,
    token,
    setToken,
    client,
    health,
    connect,
    presets,
    loadPresets,
    runs,
    refreshRuns,
    policy,
    setPolicy,
    editField,
    applyServerPreset,
    presetLabel,
    presetAutoApplied,
    locked,
    clientIssues,
    validation,
    validate,
    runnable,
    dirty,
    submit,
    submitState,
    forkFromRun,
    rs,
    loadRun,
    loadError,
    detachRun,
    control,
    controlState,
    exportRun,
    exportState,
    finalState,
    startFinalRetrain,
    archive,
    modalCandidate,
    openCandidate,
    closeCandidate,
  };
  return <Ctx.Provider value={model}>{children}</Ctx.Provider>;
}
