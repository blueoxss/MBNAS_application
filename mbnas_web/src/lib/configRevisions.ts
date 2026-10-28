/**
 * Model configuration revisions (contract 1.5.0): labels, the default diff pair, the line diff of two revisions'
 * stored YAML bytes and the hash verdict of downloaded bytes. Pure functions.
 *
 * Hash vocabulary (never mixed up in the UI):
 *  - source_sha256    sha256 of the stored BYTES (the upload / file bytes; what a download must hash to);
 *  - canonical_hash   yaml_hash of the canonical YAML dict (structure identity; differs from the bytes hash by definition);
 *  - architecture_hash structural hash of the built model (null before a build).
 */
import type { ModelConfigRevision, RevisionKind } from '../api/types';
import { hexDigest } from './structure';

export const REVISION_KIND_TEXT: Readonly<Record<RevisionKind, string>> = {
  original: '원본 (업로드·등록 파일)',
  selected: '선택본 (학습 선택에 묶인 구성)',
  executed: '실행본 (학습기가 실행한 구성)',
  generated: '생성본 (생성기가 쓴 구성)',
};

/** "#2 executed" — the 1-based position in the server's oldest-first list plus the kind. */
export function revisionLabel(revisions: readonly ModelConfigRevision[], rev: ModelConfigRevision): string {
  const index = revisions.findIndex((r) => r.revision_id === rev.revision_id);
  return `#${index + 1} ${rev.kind}`;
}

/** Label of a parent revision id: the listed revision it names, or the raw id when it is not in the list; '—' for none. */
export function parentLabel(revisions: readonly ModelConfigRevision[], parentId: string | null): string {
  if (!parentId) return '—';
  const hit = revisions.find((r) => r.revision_id === parentId);
  return hit ? `${revisionLabel(revisions, hit)} (${parentId})` : `${parentId} (목록에 없음)`;
}

/**
 * The pair the diff opens with: the selected and the executed revision when both exist (선택본↔실행본), otherwise
 * the last two revisions (older left), otherwise the only one against itself. Null for an empty list.
 */
export function defaultDiffPair(revisions: readonly ModelConfigRevision[]): [string, string] | null {
  if (revisions.length === 0) return null;
  const last = (kind: RevisionKind): ModelConfigRevision | undefined => [...revisions].reverse().find((r) => r.kind === kind);
  const selected = last('selected'), executed = last('executed');
  if (selected && executed && selected.revision_id !== executed.revision_id) return [selected.revision_id, executed.revision_id];
  if (revisions.length === 1) return [revisions[0].revision_id, revisions[0].revision_id];
  return [revisions[revisions.length - 2].revision_id, revisions[revisions.length - 1].revision_id];
}

/** File name for the exact bytes of a revision: `<candidate>-<kind>-<revision>.yaml`, reduced to safe characters. */
export function revisionFileName(candidateId: string, rev: Pick<ModelConfigRevision, 'kind' | 'revision_id'>): string {
  const safe = (v: string): string => v.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^[._]+/, '').slice(0, 80) || 'x';
  return `${safe(candidateId)}-${safe(rev.kind)}-${safe(rev.revision_id)}.yaml`;
}

// ---- bytes -> text -----------------------------------------------------------------------------------------------

/** UTF-8 text of stored bytes; `exact` is false when the bytes are not valid UTF-8 (replacement characters were used). */
export function decodeYaml(bytes: ArrayBuffer | Uint8Array): { text: string; exact: boolean } {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  try {
    return { text: new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(view), exact: true };
  } catch {
    return { text: new TextDecoder('utf-8', { fatal: false, ignoreBOM: true }).decode(view), exact: false };
  }
}

/** Lines of a text split at '\n' only: a '\r' stays part of its line (so CRLF vs LF shows up as a change, as in the bytes). */
export function splitLines(text: string): { lines: string[]; finalNewline: boolean } {
  if (text === '') return { lines: [], finalNewline: false };
  const finalNewline = text.endsWith('\n');
  const lines = text.split('\n');
  if (finalNewline) lines.pop();
  return { lines, finalNewline };
}

// ---- line diff ---------------------------------------------------------------------------------------------------

export type DiffOp = 'same' | 'del' | 'add';
export interface DiffLine {
  op: DiffOp;
  text: string;
  /** 1-based line number in the left (A) text; null for an added line */
  left: number | null;
  /** 1-based line number in the right (B) text; null for a removed line */
  right: number | null;
}
export interface LineDiff {
  lines: DiffLine[];
  added: number;
  removed: number;
  /** the two texts are equal line for line (including the final newline) */
  identical: boolean;
  /** one side ends with a newline and the other does not (a byte difference that has no line of its own) */
  finalNewlineDiffers: boolean;
  /** the changed middle part was too large to align (more than MAX_CELLS): shown as a whole replacement */
  coarse: boolean;
}

/** Cells of the LCS table above which the middle part is shown as a whole replacement instead of being aligned. */
export const MAX_CELLS = 4_000_000;

/**
 * Line diff of two texts: common prefix and suffix are kept as context, the changed middle is aligned with a longest
 * common subsequence (classic dynamic programming; YAML model files are small) and emitted as removals then additions per
 * gap. Deterministic; no external dependency.
 */
export function lineDiff(a: string, b: string): LineDiff {
  const A = splitLines(a), B = splitLines(b);
  const x = A.lines, y = B.lines;
  let start = 0;
  while (start < x.length && start < y.length && x[start] === y[start]) start += 1;
  let endX = x.length, endY = y.length;
  while (endX > start && endY > start && x[endX - 1] === y[endY - 1]) { endX -= 1; endY -= 1; }
  const out: DiffLine[] = [];
  for (let i = 0; i < start; i += 1) out.push({ op: 'same', text: x[i], left: i + 1, right: i + 1 });
  const mx = x.slice(start, endX), my = y.slice(start, endY);
  const n = mx.length, m = my.length;
  let coarse = false;
  const del = (i: number): void => { out.push({ op: 'del', text: mx[i], left: start + i + 1, right: null }); };
  const add = (j: number): void => { out.push({ op: 'add', text: my[j], left: null, right: start + j + 1 }); };
  if (n > 0 && m > 0 && (n + 1) * (m + 1) <= MAX_CELLS) {
    // lcs[i][j] = LCS length of mx[i..] and my[j..] (suffix table, so the walk below goes forwards)
    const w = m + 1;
    const lcs = new Uint32Array((n + 1) * w);
    for (let i = n - 1; i >= 0; i -= 1) {
      for (let j = m - 1; j >= 0; j -= 1) {
        lcs[i * w + j] = mx[i] === my[j] ? lcs[(i + 1) * w + j + 1] + 1 : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1]);
      }
    }
    let i = 0, j = 0;
    const gapDel: number[] = [], gapAdd: number[] = [];
    const flush = (): void => { gapDel.forEach(del); gapAdd.forEach(add); gapDel.length = 0; gapAdd.length = 0; };
    while (i < n || j < m) {
      if (i < n && j < m && mx[i] === my[j]) {
        flush();
        out.push({ op: 'same', text: mx[i], left: start + i + 1, right: start + j + 1 });
        i += 1; j += 1;
      } else if (j >= m || (i < n && lcs[(i + 1) * w + j] >= lcs[i * w + j + 1])) {
        gapDel.push(i); i += 1;
      } else {
        gapAdd.push(j); j += 1;
      }
    }
    flush();
  } else {
    coarse = n > 0 && m > 0;
    for (let i = 0; i < n; i += 1) del(i);
    for (let j = 0; j < m; j += 1) add(j);
  }
  for (let k = 0; endX + k < x.length; k += 1) out.push({ op: 'same', text: x[endX + k], left: endX + k + 1, right: endY + k + 1 });
  const added = out.filter((l) => l.op === 'add').length, removed = out.filter((l) => l.op === 'del').length;
  const finalNewlineDiffers = A.finalNewline !== B.finalNewline && (x.length > 0 || y.length > 0);
  return { lines: out, added, removed, identical: added === 0 && removed === 0 && !finalNewlineDiffers, finalNewlineDiffers, coarse };
}

// ---- downloaded bytes vs the revision record -----------------------------------------------------------------------

export interface BytesVerdict {
  /** sha256 of the received bytes (WebCrypto); null when it could not be computed (insecure context) */
  computed: string | null;
  /** `X-Artifact-Sha256` the server sent with the bytes (hex) */
  header: string | null;
  /** the revision's recorded `source_sha256` (hex) */
  recorded: string | null;
  /**
   * The received bytes hash to the recorded source_sha256 (and to the header when one was sent): true / false. null when
   * the bytes could not be hashed or nothing was recorded: then nothing is claimed (the header alone proves nothing).
   */
  matches: boolean | null;
}

export function bytesVerdict(computed: string | null, header: string | null, recorded: unknown): BytesVerdict {
  const rec = hexDigest(recorded), head = hexDigest(header);
  const matches = computed !== null && rec !== null ? computed === rec && (head === null || head === computed) : null;
  return { computed, header: head, recorded: rec, matches };
}
