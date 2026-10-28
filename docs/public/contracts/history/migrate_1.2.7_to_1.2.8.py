"""Contract 1.2.7 -> 1.2.8 (commander, session 2): set-8 server fields and set-9/10 display fields. Additive."""
import json, sys
p = sys.argv[1] if len(sys.argv) > 1 else '/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
d = json.load(open(p)); S = d['components']['schemas']
assert d['info']['version'] == '1.2.7', d['info']['version']
d['info']['version'] = '1.2.8'
ce = S['CandidateEvaluation']['properties']
ce['postprocess_runtime'] = {'anyOf': [{'enum': ['torchvision_cuda_nms', 'python_loop', 'no_nms']}, {'type': 'null'}],
    'description': 'Implementation of the post-processing timed in forward_and_postprocess (S8-F5). Rows with different values are not comparable latencies.'}
ce['checkpoint_state'] = {'anyOf': [{'enum': ['PRESENT', 'PRUNED', 'MISSING']}, {'type': 'null'}],
    'description': 'State of checkpoint_path on disk: PRUNED = removed by the run retention policy (recorded), MISSING = absent without a record (S10-F4).'}
ce['gate']['description'] = ('Gate decision {eligible, reason_code, checks[{name, passed, value, threshold, op}]}. op is >=, <= or in; for op "in" threshold is a list '
    '(e.g. the latency_source check: measured, or calibrated_proxy when proxy_may_certify_final_deployment is true).')
hc = S['Protocol']['properties']
d['x-protocol-notes'] = d.get('x-protocol-notes', {})
d['x-protocol-notes']['hard_constraints.proxy_may_certify_final_deployment'] = ('boolean (FIELD_INVALID otherwise). true lets a calibrated_proxy latency satisfy the hard latency gate; '
    'no calibrated proxy pipeline exists on this host (warning PROXY_PIPELINE_UNAVAILABLE) and final selection always requires measured.')
S['ValidateResponse']['properties']['warnings']['description'] = 'Warnings {field, message, code}; codes include PROXY_PIPELINE_UNAVAILABLE, SEARCH_SPACE_FALLBACK, HORIZON_ADJUSTED, LEGACY_ALIAS.'
d['x-contract-changelog'].insert(0, {'version': '1.2.8', 'date': '2026-10-08', 'author': 'commander (main loop, claude-opus-5-5, session 2)',
  'changes': ['CandidateEvaluation.postprocess_runtime (S8-F5) and checkpoint_state (S10-F4); gate.checks op "in" with list threshold (latency_source check, S8-F2); proxy_may_certify_final_deployment boolean note and PROXY_PIPELINE_UNAVAILABLE warning (S8-F3).']})
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1); print('contract', d['info']['version'])
