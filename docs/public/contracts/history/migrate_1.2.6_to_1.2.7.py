"""Contract 1.2.6 -> 1.2.7 (commander, session 2): exact boundary instead of a placeholder time (S6-F6). Description only."""
import json, sys
p = sys.argv[1] if len(sys.argv) > 1 else '/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
d = json.load(open(p)); c = d['components']['schemas']['Run']['properties']['costs']['properties']
assert d['info']['version'] == '1.2.6', d['info']['version']
d['info']['version'] = '1.2.7'
c['profile_gpu_seconds']['description'] = c['profile_gpu_seconds']['description'].replace(
    'Runs created before 2026-10-07 22:xx keep inline profiling inside train.',
    'Runs created before 2026-10-07T13:03:46Z (the first post-fix run is run-e4c9b809b433) keep inline profiling inside their train rows and have no profile_gpu_seconds.')
assert '22:xx' not in c['profile_gpu_seconds']['description']
c['overshoot_gpu_seconds']['description'] += ' Absent on runs whose costs were last refreshed before contract 1.2.6; present (0.0 when not exceeded) after the next refresh.'
d['x-contract-changelog'].insert(0, {'version': '1.2.7', 'date': '2026-10-07', 'author': 'commander (main loop, claude-opus-5-5, session 2)',
  'changes': ['S6-F6: exact pre-fix boundary (2026-10-07T13:03:46Z) for inline profiling; overshoot keys absent until a post-1.2.6 refresh. Description only.']})
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1); print('contract', d['info']['version'])
