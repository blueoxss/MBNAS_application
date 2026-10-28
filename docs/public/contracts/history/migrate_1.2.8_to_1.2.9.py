"""Contract 1.2.8 -> 1.2.9 (commander, session 2): generation.updated SSE event for open generations (S9-F3). Additive."""
import json, sys
p = sys.argv[1] if len(sys.argv) > 1 else '/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
d = json.load(open(p)); S = d['components']['schemas']
assert d['info']['version'] == '1.2.8', d['info']['version']
d['info']['version'] = '1.2.9'
ev = S['Event']
types = ev['properties']['type']['enum']
types.insert(types.index('generation.completed'), 'generation.updated')
for clause in ev['allOf']:
    t = clause['if']['properties']['type']
    if t.get('const') == 'generation.completed':
        clause['if']['properties']['type'] = {'enum': ['generation.updated', 'generation.completed']}
ev['description'] += (' generation.updated carries the current Generation row (live counts) after each change while the generation is open;'
                      ' generation.completed is sent exactly once, on the transition to complete. Clients upsert both by generation key.')
d['x-contract-changelog'].insert(0, {'version': '1.2.9', 'date': '2026-10-08', 'author': 'commander (main loop, claude-opus-5-5, session 2)',
  'changes': ['Event type generation.updated with a Generation payload for open generations (S9-F3); generation.completed semantics unchanged.']})
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1); print('contract', d['info']['version'])
