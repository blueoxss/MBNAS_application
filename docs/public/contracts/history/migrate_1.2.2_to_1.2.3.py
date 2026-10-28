"""Contract 1.2.2 -> 1.2.3 (commander, 2026-10-07 session 2): additive fields from the set-3 / set-1 fixes."""
import json, sys
p = sys.argv[1] if len(sys.argv) > 1 else '/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
d = json.load(open(p)); S = d['components']['schemas']
assert d['info']['version'] == '1.2.2', d['info']['version']
d['info']['version'] = '1.2.3'
search = S['Protocol']['properties']['console']['properties']['search']['properties']
search['allow_native_fallback'] = {'type': 'boolean', 'description': 'Optional, server default false. Opt-in to the native-operator fallback search space when the ETRI mixed pool cannot load; otherwise validation fails with SEARCH_SPACE_UNAVAILABLE. Hashed.'}
search['search_space_resolved'] = {'type': 'object', 'description': 'Server-written (resolved protocol only): {variant: etri_mixed|native_fallback|not_required, etri_root, space_version, space_hash_definition}. Hashed.',
                                   'properties': {'variant': {'enum': ['etri_mixed', 'native_fallback', 'not_required']}, 'etri_root': {'type': ['string', 'null']}}, 'additionalProperties': True}
caps = S['RegistryEntry']['properties']['capabilities']
caps['description'] = ('Per-model capability. Includes devices[] (per target: id, status VERIFIED_GPU|VERIFIED_ADAPTER_PATH|FAILED_GPU|NOT_RUN|UNAVAILABLE, reason, evidence), '
                       'export_trt {status VERIFIED_GPU|VERIFIED_ADAPTER_PATH|FAILED_GPU|NOT_RUN, reason, evidence}, adapter_class, adapter_module, init_modes, train/val/resume/export_onnx.')
S['RegistryEntry']['properties']['evidence']['description'] += ' evidence.source = pip {name, version, record_sha256} or ETRI {notebook_blob_sha1, audited_head}.'
S['Health']['properties']['capabilities']['description'] = 'Includes device_targets[] (every declared target with available and reason), search_space {variant, space_version}, devices, adapter classes.'
S['ValidateResponse']['properties']['capabilities']['description'] = 'Includes search_space {variant, reason}.'
d['x-contract-changelog'].insert(0, {'version': '1.2.3', 'date': '2026-10-07', 'author': 'commander (main loop, claude-opus-5-5, session 2)',
  'changes': ['Protocol.console.search.allow_native_fallback (default false) and server-written search_space_resolved; codes SEARCH_SPACE_UNAVAILABLE (error) / SEARCH_SPACE_FALLBACK (warning).',
              'RegistryEntry.capabilities devices[], export_trt from evidence (NOT_RUN_BY_REGISTRY retired), adapter_class/adapter_module; evidence.source.',
              'Health.capabilities device_targets / search_space; ValidateResponse.capabilities.search_space.',
              'Search space hash definition mbnas-mixed-space/2: content of executed ETRI modules, not the ETRI git HEAD (stored v1 rows unchanged).']})
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1); print('contract', d['info']['version'])
