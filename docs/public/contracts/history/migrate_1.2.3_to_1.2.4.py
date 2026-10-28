"""Contract 1.2.3 -> 1.2.4 (commander, session 2): descriptions/schemas now match the shapes the server returns (S3-F8)."""
import json, sys
p = sys.argv[1] if len(sys.argv) > 1 else '/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
d = json.load(open(p)); S = d['components']['schemas']
assert d['info']['version'] == '1.2.3', d['info']['version']
d['info']['version'] = '1.2.4'
TRT_DEV = ['VERIFIED_TRT', 'VERIFIED_TRT_ADAPTER_PATH', 'FAILED_TRT', 'NOT_RUN', 'UNAVAILABLE']
TRT_EXP = ['VERIFIED_GPU', 'VERIFIED_ADAPTER_PATH', 'FAILED_GPU', 'NOT_RUN']
caps = S['RegistryEntry']['properties']['capabilities']
caps['description'] = 'Per-model capability object (open). Typed keys below; train/val/resume/export_onnx/init_modes/etc. also present.'
caps.setdefault('properties', {})
caps['additionalProperties'] = True
caps['properties']['devices'] = {'type': 'object', 'description': 'Map target id (host-gpu-0, jetson-orin-nx, jetson-orin-nano, jetson-agx-orin) -> device capability.',
  'additionalProperties': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'kind': {'enum': ['host_gpu', 'jetson']},
    'status': {'enum': TRT_DEV}, 'reason': {'type': ['string', 'null']}, 'evidence': {'type': ['string', 'null']}, 'evidence_scope': {'type': ['string', 'null']},
    'measured_on': {'type': ['object', 'null']}, 'tensorrt': {'type': ['object', 'null']}, 'tensorrt_runtimes': {'type': ['object', 'null']}},
    'required': ['status'], 'additionalProperties': True}}
caps['properties']['export_trt'] = {'type': 'object', 'properties': {'status': {'enum': TRT_EXP}, 'reason': {'type': ['string', 'null']},
    'evidence': {'type': ['string', 'null']}, 'evidence_scope': {'type': ['string', 'null'], 'description': 'own_model | adapter_path_tiny_synthetic'},
    'device': {'type': ['string', 'null']}, 'engines': {'type': ['object', 'null']}, 'tensorrt_version': {'type': ['string', 'null']}, 'deployable_to': {'type': ['array', 'null'], 'items': {'type': 'string'}}},
    'required': ['status'], 'additionalProperties': True}
caps['properties']['adapter_class'] = {'type': 'string'}
caps['properties']['adapter_module'] = {'type': 'string'}
ev = S['RegistryEntry']['properties']['evidence']
ev['description'] = 'Registry evidence (open). evidence.source = {kind: pip, package, version, record, record_sha256} or {kind: etri_notebook, notebook, notebook_blob_sha1, notebook_cells, etri_audited_head, notebook_blob_sha1_on_disk, notebook_matches_audit}.'
hc = S['Health']['properties']['capabilities']
hc['description'] = ('Open object. device_targets[] = {id, name, kind, available, reason}; search_space = {variant: etri_mixed|unavailable, root, reason, checked, fallback_policy}; '
                     'per-model maps full_state_resume/export_onnx/export_trt/devices/adapter_class.')
search = S['Protocol']['properties']['console']['properties']['search']['properties']
search['search_space_resolved'] = {'type': 'object', 'description': 'Server-written in the resolved protocol only (a client value is overwritten); hashed.',
    'properties': {'variant': {'enum': ['etri_mixed', 'native_fallback', 'not_required']}, 'etri_root': {'type': ['string', 'null']}},
    'required': ['variant'], 'additionalProperties': True}
S['ValidateResponse']['properties']['capabilities']['description'] = 'Open object. search_space = {variant, etri_root, allow_native_fallback, detail, probe {available, reason, checked}}.'
d['x-contract-changelog'].insert(0, {'version': '1.2.4', 'date': '2026-10-07', 'author': 'commander (main loop, claude-opus-5-5, session 2)',
  'changes': ['S3-F8: RegistryEntry.capabilities.devices is an object map with VERIFIED_TRT|VERIFIED_TRT_ADAPTER_PATH|FAILED_TRT|NOT_RUN|UNAVAILABLE; export_trt typed (VERIFIED_GPU|VERIFIED_ADAPTER_PATH|FAILED_GPU|NOT_RUN); evidence.source keys (package, not name); Health/Validate search_space keys and search_space_resolved {variant, etri_root} match the server.']})
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1); print('contract', d['info']['version'])
