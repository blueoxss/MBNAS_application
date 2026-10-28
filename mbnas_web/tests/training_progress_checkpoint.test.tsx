/**
 * DD-27 (WP-W1): a PAUSED / unevaluated row has no checkpoint_path; TrainingProgress falls back to the candidate detail's
 * checkpoint rows. Contract 1.5.0: a checkpoint row names its file by id (ck-…) + sha256 and carries no path; the console
 * shows the id (a 1.4.x row with only a path still shows the path).
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '../src/api/client';
import { TrainingProgress, latestTrainingFull } from '../src/components/TrainingProgress';
import { cand } from './fixtures';

afterEach(cleanup);

const ck = (epoch: number, state: string, kind = 'training_full', extra: Record<string, unknown> = {}): Record<string, unknown> => ({ id: `ck-${kind}-${epoch}`, kind, epoch, state, path: `/runs/r/ckpt/${kind}_e${String(epoch).padStart(4, '0')}.pt`, sha256: `${String(epoch).repeat(64).slice(0, 64)}`, created_at: `2026-10-22T00:00:0${epoch}Z`, ...extra });
const clientWith = (checkpoints: unknown, fail = false): { client: ApiClient; detail: ReturnType<typeof vi.fn> } => {
  const detail = vi.fn(async () => { if (fail) throw new Error('detail endpoint down'); return { candidate_id: 'c', checkpoints }; });
  return { client: { candidateDetail: detail } as unknown as ApiClient, detail };
};
const paused = (over: Record<string, unknown> = {}): ReturnType<typeof cand> => cand({ candidate_id: 'paused-1', name: 'paused-1', status: 'SEARCH_COMPLETE', checkpoint_path: null, checkpoint_state: null, progress: { epoch: 2, epoch_target_total: 10 }, ...over } as never);

describe('latestTrainingFull', () => {
  it('takes the highest-epoch training_full row in state PRESENT or PRUNED and ignores other kinds', () => {
    expect(latestTrainingFull([ck(1, 'PRUNED'), ck(2, 'PRESENT'), ck(3, 'PRESENT', 'training_best')])).toMatchObject({ epoch: 2, state: 'PRESENT', path: '/runs/r/ckpt/training_full_e0002.pt' });
    expect(latestTrainingFull([ck(1, 'PRESENT'), ck(4, 'PRUNED')])).toMatchObject({ epoch: 4, state: 'PRUNED' });
  });
  it('uses a MISSING row only when nothing else exists, and returns null without any training_full row', () => {
    expect(latestTrainingFull([ck(1, 'PRESENT'), ck(2, 'MISSING')])).toMatchObject({ epoch: 1, state: 'PRESENT' });
    expect(latestTrainingFull([ck(2, 'MISSING')])).toMatchObject({ epoch: 2, state: 'MISSING' });
    expect(latestTrainingFull([ck(2, 'PRESENT', 'training_best')])).toBeNull();
    expect(latestTrainingFull(undefined)).toBeNull();
    expect(latestTrainingFull([{ kind: 'training_full', epoch: 1, state: 'PRESENT' }])).toBeNull(); // neither id nor path -> nothing to show
  });
});

describe('TrainingProgress checkpoint fallback', () => {
  it('shows the latest training_full checkpoint (id + sha256) from the candidate checkpoint records and names the source', async () => {
    const { client, detail } = clientWith([ck(1, 'PRUNED'), ck(2, 'PRESENT')]);
    render(<TrainingProgress candidates={[paused()]} events={[]} onOpen={() => undefined} client={client} runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId('progress-checkpoint')).toHaveTextContent('ck-training_full-2 · sha256 222222222222…'));
    expect(screen.getByTestId('progress-checkpoint')).not.toHaveTextContent('/runs/r/ckpt'); // never the path when an id exists
    expect(screen.getByTestId('progress-checkpoint-source')).toHaveTextContent('후보 checkpoint 기록 (training_full, epoch 2)');
    expect(screen.getByText('PRESENT')).toBeInTheDocument();
    expect(detail).toHaveBeenCalledWith('run-1', 'paused-1');
  });

  it('a PRUNED latest row is shown as PRUNED (the file no longer exists, and that is said)', async () => {
    const { client } = clientWith([ck(3, 'PRUNED')]);
    render(<TrainingProgress candidates={[paused()]} events={[]} onOpen={() => undefined} client={client} runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId('progress-checkpoint')).toHaveTextContent('ck-training_full-3'));
    expect(screen.getByText('PRUNED')).toBeInTheDocument();
  });

  it('a 1.5.0 row without any path is usable by its id; a 1.4.x row with only a path still shows that path', async () => {
    const noPath = { ...ck(4, 'PRESENT') };
    delete noPath.path;
    expect(latestTrainingFull([noPath])).toMatchObject({ id: 'ck-training_full-4', path: null, epoch: 4 });
    const { client } = clientWith([{ kind: 'training_full', epoch: 5, state: 'PRESENT', path: '/runs/r/ckpt/legacy_e0005.pt', sha256: 'f'.repeat(64) }]);
    render(<TrainingProgress candidates={[paused()]} events={[]} onOpen={() => undefined} client={client} runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId('progress-checkpoint')).toHaveTextContent('/runs/r/ckpt/legacy_e0005.pt'));
  });

  it('the evaluation row checkpoint_path wins and the detail is not even requested', async () => {
    const { client, detail } = clientWith([ck(2, 'PRESENT')]);
    render(<TrainingProgress candidates={[paused({ checkpoint_path: '/runs/r/eval/best.pt', checkpoint_state: 'PRESENT' })]} events={[]} onOpen={() => undefined} client={client} runId="run-1" />);
    expect(screen.getByTestId('progress-checkpoint')).toHaveTextContent('/runs/r/eval/best.pt');
    expect(screen.getByTestId('progress-checkpoint-source')).toHaveTextContent('평가 행의 checkpoint_path');
    await Promise.resolve();
    expect(detail).not.toHaveBeenCalled();
  });

  it('without any recorded checkpoint it stays 미기록 and says the record is absent (no guessed path)', async () => {
    const { client, detail } = clientWith([ck(2, 'PRESENT', 'training_best')]);
    render(<TrainingProgress candidates={[paused()]} events={[]} onOpen={() => undefined} client={client} runId="run-1" />);
    await waitFor(() => expect(detail).toHaveBeenCalled());
    expect(screen.getByTestId('progress-checkpoint')).toHaveTextContent('미기록');
    expect(screen.getByTestId('progress-checkpoint-source')).toHaveTextContent('기록 없음');
  });

  it('reports a failed detail request instead of hiding it', async () => {
    const { client } = clientWith([], true);
    render(<TrainingProgress candidates={[paused()]} events={[]} onOpen={() => undefined} client={client} runId="run-1" />);
    await waitFor(() => expect(screen.getByTestId('progress-checkpoint-source')).toHaveTextContent('조회 실패: detail endpoint down'));
    expect(screen.getByTestId('progress-checkpoint')).toHaveTextContent('미기록');
  });

  it('does nothing when there is no connection or run (no request, 미기록)', () => {
    const { detail } = clientWith([ck(2, 'PRESENT')]);
    render(<TrainingProgress candidates={[paused()]} events={[]} onOpen={() => undefined} client={null} runId="run-1" />);
    expect(screen.getByTestId('progress-checkpoint')).toHaveTextContent('미기록');
    expect(detail).not.toHaveBeenCalled();
  });
});
