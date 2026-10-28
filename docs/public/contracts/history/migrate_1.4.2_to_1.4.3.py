"""Contract 1.4.2 -> 1.4.3 (S05 fix round 1, 2026-10-26; design claude-fable-5-1, implementation claude-sonnet-5-5). Wording only.

No path, member, required list, enum value or status code is added, removed or changed. POST /training-runs already answers
422 with five codes of the training selection (training/selection.py, api/routes_training.py) that the contract did not name;
this round names them with their meaning. Replaying this script on openapi-1.4.2.json yields openapi-1.4.3.json
(= openapi.json) byte for byte; the script asserts the sha256 of its result when it is run on the 1.4.2 snapshot.

Applied:
  * x-status-vocabulary.error_codes_added: DEVICE_UNSUPPORTED, EVAL_ONLY_UNSUPPORTED, VALIDATION_DISABLED_UNSUPPORTED,
    INITIAL_WEIGHTS_REQUIRED, RESUME_SOURCE_REQUIRED (all 422, POST /training-runs) and a one-line meaning of each in
    x-status-vocabulary.error_codes_note.
  * POST /training-runs 422 response description names the same five codes.

Usage: python migrate_1.4.2_to_1.4.3.py [path-to-copy-of-openapi-1.4.2.json]   (rewrites the given file in place)
"""
import hashlib, json, sys
from pathlib import Path

p = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / 'openapi.json')
raw = Path(p).read_bytes()
SNAPSHOT_1_4_2_SHA256 = '9a17ae5dd7c3df0f28e1e149a73692e4cf183a124fb0fea407aea4233d707704'
EXPECTED_1_4_3_SHA256 = 'f3fe8ea672ee106d41c5c92723f7002136c7181892933aff7653a31c83f8c738'
d = json.loads(raw.decode('utf-8'))
assert d['info']['version'] == '1.4.2', d['info']['version']
d['info']['version'] = '1.4.3'


def replace_once(text, old, new):
    assert text.count(old) == 1, (text.count(old), old)
    return text.replace(old, new)


NEW_CODES = {  # code -> status; all raised by TrainingSelectionError (default status 422)
    'DEVICE_UNSUPPORTED': 422,
    'EVAL_ONLY_UNSUPPORTED': 422,
    'VALIDATION_DISABLED_UNSUPPORTED': 422,
    'INITIAL_WEIGHTS_REQUIRED': 422,
    'RESUME_SOURCE_REQUIRED': 422,
}
vocab = d['x-status-vocabulary']
for code, status in NEW_CODES.items():
    assert code not in vocab['error_codes_added'], code
    vocab['error_codes_added'][code] = status
vocab['error_codes_note'] = vocab['error_codes_note'] + (
    ' POST /training-runs 422 (training selection, named since 1.4.3): DEVICE_UNSUPPORTED - the resolved training device is not cuda:0 '
    '(the worker exposes its assigned GPU as cuda:0); EVAL_ONLY_UNSUPPORTED - mode evaluate_only is accepted only for a verified '
    'official ultralytics baseline (a baseline of another backend, a user model and a search/generated candidate are refused; candidate '
    'checkpoints are evaluated from their run); VALIDATION_DISABLED_UNSUPPORTED - validation=false is implemented for the ultralytics '
    'backend only; INITIAL_WEIGHTS_REQUIRED - mode pretrained or evaluate_only found no existing server weights file (give checkpoint '
    'for a user or candidate model; a baseline needs registered official weights); RESUME_SOURCE_REQUIRED - mode resume needs '
    'source.run_id and source.candidate_id of the existing training run.')

r422 = d['paths']['/api/v1/training-runs']['post']['responses']['422']
r422['description'] = replace_once(
    r422['description'],
    'BASELINE_UNAVAILABLE, path codes',
    'BASELINE_UNAVAILABLE, DEVICE_UNSUPPORTED, EVAL_ONLY_UNSUPPORTED, VALIDATION_DISABLED_UNSUPPORTED, INITIAL_WEIGHTS_REQUIRED, '
    'RESUME_SOURCE_REQUIRED (meanings in x-status-vocabulary.error_codes_note), path codes')

CHANGES = [
    "S05 fix round 1 (wording only): POST /training-runs 422 names the five training-selection codes it already returned - DEVICE_UNSUPPORTED, EVAL_ONLY_UNSUPPORTED, VALIDATION_DISABLED_UNSUPPORTED, INITIAL_WEIGHTS_REQUIRED, RESUME_SOURCE_REQUIRED - in its response description, and x-status-vocabulary.error_codes_added (all 422) / error_codes_note list them with a one-line meaning. No path, member, required list, enum value or status code changes."
]
d['x-contract-changelog'].insert(0, {'version': '1.4.3', 'date': '2026-10-26',
  'author': 'integration 2026-10-26 (design: claude-fable-5-1; implementation: claude-sonnet-5-5)', 'changes': CHANGES})

out = json.dumps(d, ensure_ascii=False, indent=1)
Path(p).write_text(out, encoding='utf-8')
print('contract', d['info']['version'])
if hashlib.sha256(raw).hexdigest() == SNAPSHOT_1_4_2_SHA256:
    got = hashlib.sha256(out.encode('utf-8')).hexdigest()
    if EXPECTED_1_4_3_SHA256 != '__FILL__':
        assert got == EXPECTED_1_4_3_SHA256, f'replay mismatch: {got} != {EXPECTED_1_4_3_SHA256}'
        print('replay ok: sha256', got)
    else:
        print('sha256 (fill into EXPECTED_1_4_3_SHA256):', got)
