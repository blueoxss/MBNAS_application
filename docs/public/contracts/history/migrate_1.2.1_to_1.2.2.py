"""Contract 1.2.1 -> 1.2.2: authorship metadata only (no schema/path change). Commander, 2026-10-07 (resumed session)."""
import json, sys
p = sys.argv[1] if len(sys.argv) > 1 else '/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
d = json.load(open(p))
assert d['info']['version'] == '1.2.1', d['info']['version']
d['info']['version'] = '1.2.2'
AUTH = {
    '1.2.1': 'commander (main loop, claude-fable-5-1), 2026-10-07T02:24:23+09:00',
    '1.2.0': 'commander (main loop, claude-fable-5-1), 2026-10-07T00:11:18+09:00',
    '1.1.0': 'commander (main loop, claude-fable-5-1), 2026-10-06T19:38:25+09:00',
}
for e in d['x-contract-changelog']:
    e['author'] = AUTH[e['version']]
d['x-contract-changelog'].insert(0, {'version': '1.2.2', 'date': '2026-10-07',
    'author': 'commander (main loop, claude-opus-5-5 after the session resume), 2026-10-07',
    'changes': ['Authorship added to every x-contract-changelog entry; version history snapshots and migration scripts '
                'kept in contracts/history/ (1.0.0 = handoff file). No schema or path change.']})
d['x-contract-history'] = {'directory': 'contracts/history/', 'index': 'contracts/history/HISTORY.json',
                           'replay': 'migrate_<from>_to_<to>.py applied in order to openapi-1.0.0.json reproduces openapi.json'}
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1)
print('contract', d['info']['version'])
