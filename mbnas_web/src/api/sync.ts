/**
 * RunSync keeps one loaded run in step with the server.
 *
 * Flow: initial authoritative snapshot -> SSE from after_seq = lastSeq. Events are deduplicated by seq,
 * events of other runs are ignored, a seq gap or `snapshot.required` triggers a snapshot refetch that
 * REPLACES the local state, and the stream is reopened from the snapshot's last_seq. Connection loss
 * reconnects with exponential backoff. When streaming is unsupported it polls the snapshot instead
 * (state is replaced, never appended, so polling cannot create duplicate points).
 *
 * All I/O is injected (SyncDeps), which keeps this class unit-testable without a network.
 */
import { ApiError } from './client';
import { classifyEvent, parseApiEvent, SseParser } from './sse';
import type { ApiEvent, Snapshot } from './types';

export type ConnStatus = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'polling' | 'closed' | 'auth_error' | 'error';

export interface SyncDeps {
  runId: string;
  fetchSnapshot: (signal: AbortSignal) => Promise<Snapshot>;
  /** Resolve to text chunks of the stream, or null when streaming is not supported (-> polling). */
  openStream: (afterSeq: number, signal: AbortSignal) => Promise<AsyncIterable<string> | null>;
  sleep: (ms: number, signal: AbortSignal) => Promise<void>;
  baseDelayMs?: number;
  maxDelayMs?: number;
  pollIntervalMs?: number;
}

export interface SyncSink {
  onSnapshot: (snap: Snapshot, reason: 'initial' | 'gap' | 'snapshot.required' | 'poll' | 'reconnect') => void;
  onEvent: (ev: ApiEvent) => void;
  onStatus: (status: ConnStatus, detail?: string) => void;
  isTerminal: () => boolean;
}

export function backoffDelay(attempt: number, base = 1000, max = 15000): number {
  return Math.min(max, base * 2 ** Math.max(0, attempt));
}

export class RunSync {
  private ctrl = new AbortController();
  private _lastSeq = 0;
  private stopped = false;

  constructor(
    private readonly deps: SyncDeps,
    private readonly sink: SyncSink,
  ) {}

  get lastSeq(): number {
    return this._lastSeq;
  }

  stop(): void {
    this.stopped = true;
    this.ctrl.abort();
    this.sink.onStatus('closed');
  }

  /** Runs until stop() or a terminal run whose stream has ended. Never throws. */
  async start(): Promise<void> {
    const sig = this.ctrl.signal;
    let attempt = 0;
    this.sink.onStatus('connecting');
    try {
      if (!(await this.resync('initial'))) return;
      while (!this.stopped) {
        let stream: AsyncIterable<string> | null;
        try {
          stream = await this.deps.openStream(this._lastSeq, sig);
        } catch (e) {
          if (this.stopped) return;
          if (this.isAuth(e)) return this.fail(e);
          this.sink.onStatus('reconnecting', this.msg(e));
          await this.deps.sleep(backoffDelay(attempt++, this.deps.baseDelayMs, this.deps.maxDelayMs), sig);
          continue;
        }
        if (stream === null) {
          await this.pollLoop();
          return;
        }
        this.sink.onStatus('live');
        const outcome = await this.consume(stream);
        if (this.stopped) return;
        if (outcome === 'applied') attempt = 0;
        if (this.sink.isTerminal() && outcome !== 'resync') {
          this.sink.onStatus('closed', 'run finished');
          return;
        }
        if (outcome === 'resync') {
          if (!(await this.resync('reconnect'))) return;
          continue;
        }
        this.sink.onStatus('reconnecting', 'stream ended');
        await this.deps.sleep(backoffDelay(attempt++, this.deps.baseDelayMs, this.deps.maxDelayMs), sig);
      }
    } catch (e) {
      if (!this.stopped) this.sink.onStatus('error', this.msg(e));
    }
  }

  private msg(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  private isAuth(e: unknown): boolean {
    return e instanceof ApiError && (e.status === 401 || e.status === 403);
  }

  private fail(e: unknown): void {
    this.sink.onStatus(this.isAuth(e) ? 'auth_error' : 'error', this.msg(e));
  }

  /** Fetch + apply an authoritative snapshot, retrying with backoff. false = gave up (auth error / stopped). */
  private async resync(reason: 'initial' | 'gap' | 'snapshot.required' | 'poll' | 'reconnect'): Promise<boolean> {
    const sig = this.ctrl.signal;
    for (let attempt = 0; !this.stopped; attempt++) {
      try {
        const snap = await this.deps.fetchSnapshot(sig);
        if (this.stopped) return false;
        this._lastSeq = snap.last_seq;
        this.sink.onSnapshot(snap, reason);
        return true;
      } catch (e) {
        if (this.stopped) return false;
        if (this.isAuth(e) || (e instanceof ApiError && e.kind === 'http' && !e.retryable)) {
          this.fail(e);
          return false;
        }
        this.sink.onStatus('reconnecting', this.msg(e));
        await this.deps.sleep(backoffDelay(attempt, this.deps.baseDelayMs, this.deps.maxDelayMs), sig);
      }
    }
    return false;
  }

  /** 'applied' = at least one event applied; 'ended' = stream finished; 'resync' = snapshot was refetched. */
  private async consume(stream: AsyncIterable<string>): Promise<'applied' | 'ended' | 'resync'> {
    const parser = new SseParser();
    let applied = false;
    try {
      for await (const chunk of stream) {
        for (const msg of parser.push(chunk)) {
          const ev = parseApiEvent(msg);
          if (!ev) continue;
          const decision = classifyEvent(this.deps.runId, this._lastSeq, ev);
          if (decision === 'apply') {
            this._lastSeq = ev.seq;
            applied = true;
            this.sink.onEvent(ev);
          } else if (decision === 'gap' || decision === 'snapshot') {
            const ok = await this.resync(decision === 'gap' ? 'gap' : 'snapshot.required');
            return ok ? 'resync' : 'ended';
          }
          // duplicate / wrong_run: ignored
        }
      }
    } catch (e) {
      if (this.stopped) return 'ended';
      if (this.isAuth(e)) {
        this.fail(e);
        this.stopped = true;
      }
    }
    return applied ? 'applied' : 'ended';
  }

  private async pollLoop(): Promise<void> {
    const sig = this.ctrl.signal;
    this.sink.onStatus('polling');
    while (!this.stopped) {
      await this.deps.sleep(this.deps.pollIntervalMs ?? 3000, sig);
      if (this.stopped) return;
      if (!(await this.resync('poll'))) return;
      if (this.sink.isTerminal()) {
        this.sink.onStatus('closed', 'run finished');
        return;
      }
    }
  }
}

/** Default sleep that rejects quietly on abort. */
export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const t = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = (): void => {
      clearTimeout(t);
      resolve();
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}
