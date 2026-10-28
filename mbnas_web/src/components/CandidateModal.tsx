import { useApp } from '../state/context';
import { rec, type Candidate } from '../api/types';
import { generatedAsCandidate } from '../lib/generation';
import { candidateList } from '../state/reducer';
import { CandidateView } from './CandidateView';
import { Modal } from './Modal';

/** 'evaluation' for a measured evaluation row; '기록' otherwise (a generated candidate carries evaluation_status NOT_EVALUATED). */
export function candidateRecordLabel(candidate: Pick<Candidate, 'status'> | Readonly<Record<string, unknown>>): string {
  const r = rec(candidate);
  return r.status === 'EVALUATED' && r.evaluation_status !== 'NOT_EVALUATED' ? 'evaluation' : '기록';
}

/** Candidate details dialog opened from a chart point (click / Enter / Space) or a table row. */
export function CandidateModal(): JSX.Element | null {
  const app = useApp();
  const wanted = app.modalCandidate;
  if (!wanted || !app.rs.runId) return null;
  const generated = app.rs.generatedCandidates.find((candidate) => candidate.candidate_id === wanted.candidate_id);
  const c = app.rs.candidates[wanted.id] ?? (generated ? generatedAsCandidate(generated) : wanted);
  const comparisonCandidates: Candidate[] = [...candidateList(app.rs), ...app.rs.generatedCandidates.map(generatedAsCandidate)];
  return (
    <Modal titleId="cand-title" onClose={app.closeCandidate}>
      <div className="modal-head">
        <div>
          <h2 id="cand-title" style={{ margin: 0 }}>
            {c.name}
          </h2>
          <p className="muted wrap-any mono" style={{ margin: 0 }}>
            {candidateRecordLabel(c)} {c.id}
          </p>
        </div>
        <button className="btn" type="button" onClick={app.closeCandidate} data-autofocus data-testid="modal-close">
          Close (Esc)
        </button>
      </div>
      <CandidateView c={c} run={app.rs.run} runId={app.rs.runId} all={comparisonCandidates} client={app.client} onTrainingCreated={(id) => { app.closeCandidate(); void app.loadRun(id); app.setScreen('monitor'); }} />
    </Modal>
  );
}
