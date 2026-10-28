/**
 * Server-sent-events consumer pieces that work over fetch streaming (so the Authorization header can be
 * sent; native EventSource cannot).
 *
 *  - SseParser: incremental parser for `id:` / `event:` / `data:` lines; handles CRLF, LF and lone CR,
 *    multi-line `data:` (joined with "\n"), comment lines (": ...") and chunk boundaries anywhere.
 *  - parseApiEvent: decodes the JSON Event ({seq, type, run_id, payload}); malformed -> null.
 *  - classifyEvent: dedupe/ordering decision against the run id and the last applied seq.
 *  - responseChunks: async iterator of decoded text chunks from a fetch Response body.
 */
import type { ApiEvent } from './types';

export interface SseMessage {
  id: string | null;
  event: string;
  data: string;
  retry: number | null;
}

export class SseParser {
  private buf = '';
  private data: string[] = [];
  private event = '';
  private id: string | null = null;
  private retry: number | null = null;
  private started = false;

  /** Feed a decoded text chunk; returns the messages completed by it. */
  push(chunk: string): SseMessage[] {
    let text = this.buf + chunk;
    if (!this.started) {
      if (text.startsWith('﻿')) text = text.slice(1);
      if (text.length > 0) this.started = true;
    }
    const out: SseMessage[] = [];
    let i = 0;
    let lineStart = 0;
    while (i < text.length) {
      const ch = text[i];
      if (ch === '\n' || ch === '\r') {
        if (ch === '\r' && i + 1 >= text.length) break; // may be the first half of CRLF; wait for more input
        const line = text.slice(lineStart, i);
        i += ch === '\r' && text[i + 1] === '\n' ? 2 : 1;
        lineStart = i;
        const msg = this.line(line);
        if (msg) out.push(msg);
      } else i++;
    }
    this.buf = text.slice(lineStart);
    return out;
  }

  private line(line: string): SseMessage | null {
    if (line === '') return this.dispatch();
    if (line.startsWith(':')) return null; // comment / heartbeat
    const idx = line.indexOf(':');
    const field = idx === -1 ? line : line.slice(0, idx);
    let value = idx === -1 ? '' : line.slice(idx + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    switch (field) {
      case 'data':
        this.data.push(value);
        break;
      case 'event':
        this.event = value;
        break;
      case 'id':
        if (!value.includes('\0')) this.id = value;
        break;
      case 'retry':
        if (/^\d+$/.test(value)) this.retry = Number(value);
        break;
      default:
        break;
    }
    return null;
  }

  private dispatch(): SseMessage | null {
    const had = this.data.length > 0;
    const msg: SseMessage = { id: this.id, event: this.event || 'message', data: this.data.join('\n'), retry: this.retry };
    this.data = [];
    this.event = '';
    this.id = null;
    this.retry = null;
    return had ? msg : null;
  }
}

const EVENT_TYPES = new Set(['candidate.updated', 'candidate.evaluated', 'generation.updated', 'generation.completed', 'run.updated', 'worker.updated', 'log', 'snapshot.required']);

/** Decode a message into an ApiEvent; returns null when it is not a well-formed Event. */
export function parseApiEvent(msg: SseMessage): ApiEvent | null {
  let j: unknown;
  try {
    j = JSON.parse(msg.data);
  } catch {
    return null;
  }
  if (!j || typeof j !== 'object') return null;
  const o = j as Record<string, unknown>;
  const seq = typeof o.seq === 'number' ? o.seq : msg.id !== null && /^\d+$/.test(msg.id) ? Number(msg.id) : NaN;
  const type = typeof o.type === 'string' ? o.type : msg.event;
  if (!Number.isInteger(seq) || seq < 1 || typeof o.run_id !== 'string' || !EVENT_TYPES.has(type)) return null;
  const payload = o.payload && typeof o.payload === 'object' && !Array.isArray(o.payload) ? (o.payload as Record<string, never>) : {};
  return { seq, type: type as ApiEvent['type'], run_id: o.run_id, payload };
}

export type EventDecision = 'apply' | 'duplicate' | 'wrong_run' | 'gap' | 'snapshot';

/**
 * - wrong_run : event belongs to another run -> ignore
 * - snapshot  : server asked for a resync (snapshot.required) -> fetch authoritative snapshot
 * - duplicate : seq <= lastSeq -> ignore (at-least-once delivery)
 * - gap       : seq > lastSeq + 1 -> events were lost; never apply, fetch snapshot
 * - apply     : exactly lastSeq + 1
 */
export function classifyEvent(runId: string, lastSeq: number, ev: ApiEvent): EventDecision {
  if (ev.run_id !== runId) return 'wrong_run';
  if (ev.type === 'snapshot.required') return 'snapshot';
  if (ev.seq <= lastSeq) return 'duplicate';
  if (ev.seq > lastSeq + 1) return 'gap';
  return 'apply';
}

/** Yield decoded text chunks from a streaming Response; ends when the stream ends or is aborted. */
export async function* responseChunks(res: Response): AsyncGenerator<string> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) yield dec.decode(value, { stream: true });
    }
    const tail = dec.decode();
    if (tail) yield tail;
  } finally {
    try {
      await reader.cancel();
    } catch {
      /* already closed */
    }
  }
}

/** True when this runtime can read response bodies as streams. */
export function streamingSupported(): boolean {
  return typeof ReadableStream !== 'undefined' && typeof TextDecoder !== 'undefined';
}
