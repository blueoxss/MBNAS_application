"""Contract 1.2.4 -> 1.2.5 (commander, session 2): ValidateResponse.resolved_diff is empty for invalid results (S4-F4); EPOCHS_ADDED_MANUAL documented."""
import json, sys
p = sys.argv[1] if len(sys.argv) > 1 else '/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
d = json.load(open(p)); S = d['components']['schemas']
assert d['info']['version'] == '1.2.4', d['info']['version']
d['info']['version'] = '1.2.5'
rd = S['ValidateResponse']['properties']['resolved_diff']
rd['description'] = rd.get('description', '') + ' Empty when valid=false: an invalid result makes no resolution claim.'
S['ValidateResponse']['properties']['errors']['description'] = ('Validation errors {field, message, code}. Codes include EPOCHS_ADDED_MANUAL (a requested rungs[i].epochs_added '
    'that differs from the computed cumulative delta; an equal echo is accepted), SEARCH_SPACE_UNAVAILABLE, WEIGHTS_SUM_NOT_ONE, ANCHORS_INVALID, and the PLAN.md §4 list.')
d['x-contract-changelog'].insert(0, {'version': '1.2.5', 'date': '2026-10-07', 'author': 'commander (main loop, claude-opus-5-5, session 2)',
  'changes': ['ValidateResponse.resolved_diff is empty for invalid results (S4-F4); EPOCHS_ADDED_MANUAL and other codes documented on errors. No shape change.']})
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1); print('contract', d['info']['version'])
