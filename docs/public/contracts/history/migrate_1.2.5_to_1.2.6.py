"""Contract 1.2.5 -> 1.2.6 (commander, session 2): Run.costs profile/overshoot keys from the set-6 fixes (S6-F1/F2)."""
import json, sys
p = sys.argv[1] if len(sys.argv) > 1 else '/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
d = json.load(open(p)); S = d['components']['schemas']
assert d['info']['version'] == '1.2.5', d['info']['version']
d['info']['version'] = '1.2.6'
c = S['Run']['properties']['costs']
c['properties']['profile_gpu_seconds']['description'] = ('Physical GPU seconds of device profiling: BENCHMARK jobs and the inline latency measurement inside training jobs '
    '(ledger kind profile, meta.source inline_latency). Never billed to a search arm and never charged against a cap. Runs created before 2026-10-07 22:xx keep inline profiling inside train.')
c['properties']['overshoot_gpu_seconds'] = {'type': 'number', 'minimum': 0, 'description': 'Sum of the per-cap overshoots max(0, consumed - cap). Per-cap keys are overshoot_gpu_seconds.<GA|IndependentRandom>.seed_<seed>, overshoot_gpu_seconds.Baseline, overshoot_gpu_seconds.Final (present only when the cap exists and time was charged to it; 0.0 is a measured value). The first attempt that crosses a cap logs reason BUDGET_OVERSHOOT {arm, search_seed, job_id, candidate_id, attempt, cap_s, billed_before_s, billed_s, overshoot_s}.'}
c['properties']['final_gpu_seconds_children'] = {'type': 'number', 'minimum': 0, 'description': 'On a SEARCH run: physical GPU seconds of its FINAL_RETRAIN child runs.'}
c['additionalProperties'] = {'type': 'number', 'minimum': 0}
d['x-contract-changelog'].insert(0, {'version': '1.2.6', 'date': '2026-10-07', 'author': 'commander (main loop, claude-opus-5-5, session 2)',
  'changes': ['Run.costs: profile_gpu_seconds includes inline latency profiling (never billed); overshoot_gpu_seconds (+ per-cap keys); final_gpu_seconds_children documented; log reason BUDGET_OVERSHOOT.']})
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1); print('contract', d['info']['version'])
