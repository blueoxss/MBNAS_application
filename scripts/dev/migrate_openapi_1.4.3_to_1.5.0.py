#!/usr/bin/env python3
"""Contract 1.4.3 -> 1.5.0: the public projection contract in its minimal form (WP-4A draft, WP-4B promotion, 2026-10-27).

Design: claude-fable-5-1 (WP-4 contract sections 1-2; DD-A19, DD-A20, DD-A23; scope reduced by DD-A38; DD-A42, DD-A44,
DD-A46). Implementation: claude-opus-5-5. Status: PROMOTED by WP-4B (2026-10-27) - the package openapi.json is the output
of this script (--promote) and docs/public/contracts/history/migrate_1.4.3_to_1.5.0.py is a byte copy of it (replay
convention of the history directory). WP-4B additions over the WP-4A draft (sha256 70edb8ef...): the user-model block
keys module/from/args/repeats on Block (DD-A42), UPLOAD_TYPE_UNSUPPORTED answered 422 (DD-A44 d), the gateway path
hygiene 404 NOT_FOUND (DD-A44 e) and STALE_VALIDATION on POST /runs/{run_id}/final-retrain (DD-A46).

Usage:
  python migrate_openapi_1.4.3_to_1.5.0.py IN [OUT] [--promote]
    IN   a copy of openapi-1.4.3.json (the live 1.4.3 package openapi.json may be given: it is only read)
    OUT  output file; default: IN rewritten in place (the replay convention of
         tests/public/contracts/test_contract_history.py). Rewriting the live package openapi.json in place
         (a file named openapi.json next to HISTORY.json and openapi_runner.json) is refused unless --promote.
  Run on the 1.4.3 snapshot (SNAPSHOT_1_4_3_SHA256) the script asserts the sha256 of its result
  (EXPECTED_1_5_0_SHA256): the draft is reproducible byte for byte. Output format = history format
  (json.dumps(indent=1, ensure_ascii=False), UTF-8, no trailing newline). Standard library only.

Rules applied (each one is also summarised in x-contract-changelog[0]):
  1. OWNER_ONLY members of the WP-0 DTO allowlist analysis are removed from the public components, or excluded by
     closing their parent object (additionalProperties:false) or, where the parent object stays open, by a
     propertyNames deny list. Archive* members the analysis marked OWNER_ONLY are kept: DD-A19 keeps the archive
     (customer-host research runs) with its mutation/driver documents.
  2. additionalProperties:false on every component the core console projection emits (x-public-projection lists
     them); PUBLIC members the server emits but 1.4.3 did not declare are declared so that closing keeps them.
  3. Value-mapped members named by WP-4A: checkpoints[].path -> checkpoint id + artifact_id; Health allowlists and
     capabilities without host roots; resolved protocol without resolution.paths.*.realpath and without
     search_space_resolved.etri_root; also audit.execution_configuration.path -> artifact_id and device_uuid
     (Worker, raw_metrics) deprecated in favour of device_id. Other value mappings (scrub, id substitution, run-relative
     paths) are stated in descriptions and in x-public-projection.value_rules; their schemas are unchanged.
  4. Additive: training_status, Worker.runner_id/device_id/runtime_manifest_status, RuntimeManifestStatus,
     ModelConfigRevision(+List) with GET .../config-revisions and .../config-revisions/{revision_id}/yaml,
     UploadResponse with POST /uploads/model-config, upload_id / model_yaml_text / source_sha256 (/ model_name)
     request members, Error.location (+ core_request_id), status vocabulary of the gateway and upload codes.
  5. Wording: info.description without the monolith name (DD-A26 data exception expires), relative server URL "/"
     (DD-A41; the paths carry /api/v1 themselves, so "/api/v1" would double the prefix), history paths, descriptions
     that named owner host roots.
  6. WP-4B (promotion): Block declares the user-model row keys module / from / args / repeats (DD-A42; StructureReport
     rows of an explicit user-model training keep them at the top level, search rows keep them in params);
     StructureReport declares source_sha256 and sandbox elapsed_s / exit_code (emitted PUBLIC members);
     RegistryEntry.evidence.source admits the example registry's label string;
     POST /uploads/model-config answers UPLOAD_TYPE_UNSUPPORTED with 422 (DD-A44 d; 415 is gone); x-status-vocabulary
     states the gateway path hygiene (dot segment or control character in a path parameter -> 404 NOT_FOUND, location
     gateway; DD-A44 e); POST /runs/{run_id}/final-retrain names 409 STALE_VALIDATION (DD-A46: gateway-measured inputs
     sent with the request re-validate the parent protocol).
"""
import copy
import hashlib
import json
import sys
from pathlib import Path

SNAPSHOT_1_4_3_SHA256 = 'f3fe8ea672ee106d41c5c92723f7002136c7181892933aff7653a31c83f8c738'
EXPECTED_1_5_0_SHA256 = '70e0cffaab8fafd9c7f230af9169dbbe8812b3eccf7ad7838ca3f46fa9eebc2a'
DRAFT_1_5_0_SHA256 = '70edb8ef0628f7e49bc96ac67000c520036b5e29d9e2b76439cc6e8f1d7f9d17'  # WP-4A draft (superseded)
R = '#/components/schemas/'
DATE = '2026-10-27'
AUTHOR = 'WP-4A draft + WP-4B promotion 2026-10-27 (design: claude-fable-5-1; implementation: claude-opus-5-5)'

# ---------------------------------------------------------------------------------------------------------- helpers


def ref(name, **extra):
    out = {'$ref': R + name}
    out.update(extra)
    return out


def t(types, desc=None, **extra):
    out = {'type': types}
    out.update(extra)
    if desc:
        out['description'] = desc
    return out


def anyval(desc=None):
    return {'description': desc} if desc else {}


def deny(*names):
    """propertyNames rule: the object stays open but never carries these (owner-only) member names."""
    return {'not': {'enum': sorted(names)}}


def drop(obj, key, where):
    assert key in obj['properties'], f'{where}: {key} is not declared'
    del obj['properties'][key]
    if key in obj.get('required', []):
        obj['required'].remove(key)


def add(obj, key, schema, where):
    assert key not in obj['properties'], f'{where}: {key} is already declared'
    obj['properties'][key] = schema


def replace_once(text, old, new):
    assert text.count(old) == 1, (text.count(old), old)
    return text.replace(old, new)


def error_response(desc):
    return {'description': desc, 'content': {'application/json': {'schema': ref('Error')}}}


# ------------------------------------------------------------------------------------------------- shared schemas
TRAINING_STATUS_NOTE = ('Display vocabulary only (DD-A23): run/candidate/evaluation status enums are unchanged; the '
                        'web badge "UNTRAINED · UNEVALUATED" is training_status UNTRAINED together with '
                        'evaluation_status NOT_EVALUATED.')
SHA256_PREFIXED = '^sha256:[0-9a-f]{64}$'
SHA256_OPT_PREFIX = '^(sha256:)?[0-9a-f]{64}$'
UPLOAD_ID = '^upl-[0-9a-f]{64}$'
NOT_A_HOST_PATH = ('1.5.0 public value: never an absolute host path - the path relative to the run directory (the '
                   'prefix up to and including the run id segment removed) or, for a file outside the run '
                   'directory, its file name; the file itself is downloaded through artifacts[] (checkpoint id / '
                   'artifact id), never by path.')

DEVICE_UUID_NOTE = ('Deprecated in 1.5.0: GPU UUID of a customer (runner) device, kept for 1.4.x readers; replaced by '
                    'the core-issued device_id (DD-A19 / D7) - the projection omits it once device ids are issued.')

PROJECTED = ('Artifact', 'Block', 'CandidateEvaluation', 'Generation', 'Run', 'Worker', 'Event', 'Snapshot', 'Health',
             'ValidateResponse', 'RunCreated', 'Error', 'PresetSummary', 'Preset', 'PresetList', 'RunList',
             'CandidateDetail', 'RegistryEntry', 'RegistryList', 'GeneratedCandidate', 'TrainingRunCreated',
             'StructureReport', 'BlockCatalog', 'ModelConfigRevision', 'ModelConfigRevisionList', 'UploadResponse')

RAW_METRICS_PUBLIC = (  # analysis section 3.1 PUBLIC keys
    'ap_50', 'counts', 'per_class_ap', 'per_class_recall', 'no_predictions', 'invalid_reason', 'coco_stats',
    'evaluation_signature', 'latency_scope_base', 'postprocess_operating_point', 'eval_split', 'size_dtype',
    'size_definition', 'weights_only_sha256', 'eval_checkpoint_sha256', 'epochs', 'postprocess', 'timings',
    'evaluated_at', 'device_name', 'eval_batch', 'train_batch', 'global_step', 'samples_seen', 'epoch_target_total',
    'checkpoint_sha256s', 'checkpoint_sha256_by_kind', 'non_finite_metrics', 'fitness_rung', 'handler_fitness',
    'validation_enabled')

LOG_PAYLOAD_KEYS = (  # analysis section 3.7 PUBLIC keys + runner_id (WP-2 lease loss) + forced_policy (WP-2 retention)
    'reason_code', 'candidate_id', 'job_id', 'attempt', 'paused', 'request_id', 'arm', 'search_seed', 'generation',
    'child_run_id', 'child_status', 'structure_status', 'policy', 'forced_policy', 'bytes_freed', 'gpu_wait_s',
    'parent_run_id', 'mode', 'validation_id', 'candidate_architecture_hash', 'trainer_architecture_hash',
    'reported_sha256', 'computed_sha256', 'cap_s', 'billed_before_s', 'billed_s', 'overshoot_s', 'finalists',
    'selection_rule', 'rank', 'source', 'recipe', 'reason', 'runner_id')


def public_lineage(old, *, nullable, stored_template):
    """CandidateDetail.lineage / StructureReport.lineage: the DD-A19 public subset (closed)."""
    props = old['properties']
    detail = copy.deepcopy(props['template_detail'])
    detail['additionalProperties'] = False
    template = copy.deepcopy(props['template'])
    if stored_template:  # StructureReport copies the stored lineage: template is still the {id, family, variant} object
        template = {'anyOf': [t('string'), copy.deepcopy(detail)],
                    'description': 'Seed template id, or the stored {id, family, variant} object.'}
    return {
        'type': ['object', 'null'] if nullable else 'object',
        'additionalProperties': False,
        'properties': {
            'operation': copy.deepcopy(props['operation']),
            'method': t(['string', 'null'], 'Proposal method of a searched candidate.'),
            'parents': {'type': 'array', 'items': t('string')},
            'parent_evaluation_ids': {'type': 'array', 'items': t(['string', 'null'])},
            'template': template,
            'template_detail': detail,
            'search_space': {'type': 'object', 'additionalProperties': False,
                             'properties': {'version': t(['string', 'null']), 'variant': t(['string', 'null'])},
                             'description': 'Search-space version and variant label only.'},
            'source_candidate_id': t(['string', 'null']), 'source_evaluation_id': t(['string', 'null']),
            'source_run_id': t(['string', 'null']), 'source_arm': t(['string', 'null']),
            'source_rung': t(['string', 'null']), 'source_name': t(['string', 'null']),
            'rank': t(['integer', 'null']), 'selection_rule': t(['string', 'null']),
            'init': anyval('Initialisation of a final or explicit training trial.'),
            'recipe': t(['object', 'null'], 'Pinned recipe of a final trial.'),
            'source_template': t(['string', 'null'], 'Baseline lineage: registry id of the baseline.'),
            'training_source': t(['object', 'null'], 'Explicit training: the selected source binding.'),
            'source_architecture_hash': t(['string', 'null']),
        },
        'description': ('1.5.0 public lineage (DD-A19): operation, method, parents, parent_evaluation_ids, template '
                        '(+ template_detail), search_space {version, variant} and the final / baseline / explicit '
                        'training keys. Crossover cut/mask, mutation and repair events, prior draw, tournament '
                        'selection, parent rows, elitism flag, notes and the space/prior hashes are owner-only '
                        '(/core/v1/owner/*) and never sent.'),
    }


# ---------------------------------------------------------------------------------------------------------- migrate


def migrate(d):
    S = d['components']['schemas']
    P = d['paths']
    assert d['info']['version'] == '1.4.3', d['info']['version']
    for name in ('RuntimeManifestStatus', 'ModelConfigRevision', 'ModelConfigRevisionList', 'UploadResponse'):
        assert name not in S, name

    # ---- 0. info, servers, history pointers ---------------------------------------------------------------------
    d['info']['version'] = '1.5.0'
    d['info']['description'] = (
        'Canonical public contract of the MBNAS Research Console (/api/v1). The gateway (distribution mbnas-server) '
        'serves it to the web console and relays to the core control plane; core console responses, their SSE '
        'events and the public export are projected onto these schemas (see x-public-projection), and owner audit '
        'data exists only on /core/v1/owner/*. Derived from NAS_Console_API_Contract_20261006.json v1.0.0 (see '
        'x-contract-changelog). Candidate row id is evaluation_id; candidate_id is stable across promotions. Values '
        'in examples are not measured results.')
    d['servers'][0] = {'url': '/', 'description': (
        'Same-origin gateway (mbnas-server, default port 8710; DD-A41: relative, no host). Every path below already '
        'starts with /api/v1, so the server URL is the origin root.')}
    d['x-contract-history'] = {
        'directory': 'docs/public/contracts/history/',
        'index': 'docs/public/contracts/history/HISTORY.json (package copy: mbnas_contracts/openapi/HISTORY.json)',
        'replay': d['x-contract-history']['replay']}

    # ---- 1. owner-only members ----------------------------------------------------------------------------------
    drop(S['Generation'], 'group_key', 'Generation')

    w = S['Worker']
    drop(w, 'pid', 'Worker')
    drop(w, 'lease_expires_at', 'Worker')

    b = S['Block']
    for key in ('old_name', 'before', 'after', 'repair_reason'):
        drop(b, key, 'Block')
    bp = b['properties']
    bp['source_kind']['enum'].remove('repair')
    bp['source_kind']['description'] = ('1.5.0: a repaired row is reported with the source it was repaired from '
                                        '(repair folded, DD-A19); never repair.')
    bp['operation']['description'] = 'inherit | sample | mutate; a repair is folded into its source (never repair).'
    bp['params'] = {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'from': {'anyOf': [t('integer'), {'type': 'array', 'items': t('integer')}]},
            'repeats': t(['integer', 'null']), 'module': t(['string', 'null']), 'args': t(['array', 'null']),
            'scope': t(['string', 'null'], 'backbone | head'),
            'effective': t(['object', 'null'], 'Observed shapes {c_in, c_out, h_in, w_in, stride, params}.')},
        'description': ('YAML row of the block (from, repeats, module, args), its scope and the observed effective '
                        'shapes. 1.5.0: the search-space slot kind is owner-only and never sent.')}
    user_row = ('User-model rows (explicit training of a user / baseline YAML; copied from the structure check): '
                'the YAML row value at the top level; searched rows carry it in params.')
    for key, schema in (('module', t(['string', 'null'], user_row + ' Operator name of the row.')),
                        ('from', t(['integer', 'array', 'null'], user_row + ' from reference(s).', items=t('integer'))),
                        ('repeats', t(['integer', 'null'], user_row + ' Declared repeats.')),
                        ('args', t(['array', 'null'], user_row + ' Declared args.'))):
        add(b, key, schema, 'Block')  # DD-A42 (WP-4B): public user-model block table keys of the closed Block

    cd = S['CandidateDetail']
    old_lineage = cd['properties']['lineage']
    cd['properties']['lineage'] = public_lineage(old_lineage, nullable=False, stored_template=False)
    sr = S['StructureReport']
    sr['properties']['lineage'] = public_lineage(old_lineage, nullable=True, stored_template=True)

    ce = S['CandidateEvaluation']
    raw = {k: anyval() for k in RAW_METRICS_PUBLIC}
    raw.update({
        'ap': anyval('Copy of the evaluator AP (same value as the row ap).'),
        'recall': anyval('Copy of the evaluator recall (same value as the row recall).'),
        'latency': t('object', ('Latency record (per-scope statistics, operating point, post-processing, device '
                                'manifest; the manifest uuid is deprecated like device_uuid).')),
        'resolved_recipe': anyval('Recipe the adapter resolved from the user recipe; adapter-internal keys removed.'),
        'device_id': t(['string', 'null'], 'Core-issued opaque id of the measuring device (replaces device_uuid).'),
        'device_uuid': t(['string', 'null'], DEVICE_UUID_NOTE, deprecated=True),
        'device_used': t('string', 'DD-A32: cpu when a CPU-only runner mapped the assigned cuda:0 to cpu.'),
        'externalised_detail': {'type': 'object', 'propertyNames': deny('path'),
                                'description': ('DD-A33: per-sample / latency samples moved to an artifact '
                                                '(artifact_id, relpath, sha256, bytes, counts).')},
        'signature': anyval('Evaluator signature of an official evaluation row (evaluation_signature elsewhere).'),
        'source': t(['string', 'null'], 'Official evaluation rows: weights source label (official_weights).'),
        'training_mode': t(['string', 'null'], 'Official evaluation rows: evaluate_only.'),
    })
    ce['properties']['raw_metrics'] = {
        'type': 'object', 'additionalProperties': False, 'properties': raw,
        'description': ('Raw evaluator outputs, 1.5.0 allowlist (DD-A19 / analysis decision D4): measurement '
                        'values, definitions and hashes. Host paths of checkpoints and weights, the stage-trainer '
                        'result, server error strings and the GPU UUID are never sent.')}

    cd['properties']['audit'] = audit_schema(cd['properties']['audit']['description'])

    h = S['Health']
    hp = h['properties']
    hp['capabilities'] = {
        'type': 'object',
        'properties': {
            'search_space': {'type': 'object', 'additionalProperties': False,
                             'properties': {'variant': t('string', 'etri_mixed | unavailable'),
                                            'reason': t(['string', 'null'], 'Reason code or scrubbed text.'),
                                            'checked': anyval('What the torch-free probe verified.'),
                                            'fallback_policy': t(['string', 'null'])}},
            'registry_source': t(['string', 'null'], 'Label of the registry source (live | example); never a path.')},
        'description': ('Open object. device_targets[] = {id, name, kind, available, reason}; search_space = '
                        '{variant: etri_mixed|unavailable, reason, checked, fallback_policy}; per-model maps '
                        'full_state_resume/export_onnx/export_trt/devices/adapter_class; sse, idempotency, '
                        'signed_artifact_urls, execution_modes, registry_source. 1.5.0: no host root (the pool root '
                        'and registry file paths are never sent).')}
    hp['versions'] = {
        'type': 'object',
        'properties': {'environment_lock': {
            'type': ['object', 'null'], 'additionalProperties': False,
            'properties': {'present': t('boolean'), 'generated_at': t(['string', 'null']), 'torch_build': anyval(),
                           'nvidia': anyval()},
            'description': 'Environment lock summary; 1.5.0: without the lock file path.'}},
        'description': 'Open object: python, packages, api, environment_lock.'}

    ev = S['Event']
    log_props = {'message': t('string', 'Log text, scrubbed (no absolute paths, PIDs or host names).'),
                 'level': t('string', 'info | warning | error'),
                 'message_detail': t(['string', 'null'], 'Handler failure text, scrubbed.'),
                 'checks': anyval('Resume revalidation result: codes and outcomes only.')}
    for key in LOG_PAYLOAD_KEYS:
        log_props[key] = anyval()
    ev['allOf'].append({'if': {'properties': {'type': {'const': 'log'}}},
                        'then': {'properties': {'payload': {
                            'type': 'object', 'additionalProperties': False, 'required': ['message'],
                            'properties': log_props,
                            'description': ('1.5.0 log payload allowlist (DD-A19 / analysis decision D13); the '
                                            'rejected checkpoint path of a runner report is owner-only.')}}}})

    re_ = S['RegistryEntry']
    evidence = re_['properties']['evidence']
    evidence['propertyNames'] = deny('probe_file', 'registry_roots_skipped')
    evidence['properties'] = {
        'config_ref': {'type': ['object', 'null'], 'propertyNames': deny('path')},
        'weights': {'type': ['object', 'null'], 'propertyNames': deny('path')},
        'build': {'type': ['object', 'null'], 'propertyNames': deny('trace')},
        'pretrained_load': {'type': ['object', 'null'], 'propertyNames': deny('loaded_from')},
        'source': {'anyOf': [{'type': 'object', 'propertyNames': deny('notebook', 'record')}, t('string'), t('null')],
                   'description': ('Source identity object (live registry); the bundled example registry carries a '
                                   'label string instead (WP-4B; never an absolute path).')}}
    evidence['description'] = (
        'Registry evidence (open). evidence.source = {kind: pip, package, version, record_sha256} or {kind: '
        'etri_notebook, notebook_blob_sha1, notebook_cells, etri_audited_head, notebook_blob_sha1_on_disk, '
        'notebook_matches_audit}. 1.5.0: host paths (probe_file, config_ref.path, weights.path, '
        'pretrained_load.loaded_from, source.record, source.notebook), the build traceback and the unreadable-root '
        'diagnostic are owner-only; error strings are scrubbed.')
    caps = re_['properties']['capabilities']['properties']
    caps['devices']['additionalProperties']['properties']['evidence']['description'] = (
        'Evidence id (file name + sha256); never a host path.')
    caps['export_trt']['properties']['evidence']['description'] = 'Evidence id (file name + sha256); never a host path.'

    rl = S['RegistryList']
    drop(rl, 'registry_roots_skipped', 'RegistryList')
    rl['properties']['source']['description'] = (
        'Label of where the rows came from: live (the installed registry) or example (the bundled example registry; '
        'warning is then set and every row is UNVERIFIED). Never a file path.')

    items = sr['properties']['blocks']['items']
    items['propertyNames'] = deny('after', 'before', 'old_name', 'repair_reason')
    for key, schema in (('source', t(['string', 'null'])),
                        ('source_kind', t(['string', 'null'], 'Block provenance (repair folded, never repair).')),
                        ('parent_block_id', t(['string', 'null'])), ('operation', t(['string', 'null'])),
                        ('mutated', t(['boolean', 'null'])), ('recorded_effective', t(['object', 'null'])),
                        ('recorded_effective_source', t(['string', 'null']))):
        add(items, key, schema, 'StructureReport.blocks[]')

    run = S['Run']
    run['properties']['budget']['propertyNames'] = deny('policy')
    run['properties']['budget']['description'] = (
        'Frozen budget per arm/seed in GPU-seconds and the admission bookkeeping. 1.5.0: the admission policy text '
        'is owner-only and never sent.')

    pr = S['Protocol']
    ssr = pr['properties']['console']['properties']['search']['properties']['search_space_resolved']
    drop(ssr, 'etri_root', 'Protocol.console.search.search_space_resolved')
    ssr['additionalProperties'] = False
    ssr['description'] = ('Server-written in the resolved protocol only (a client value is overwritten); hashed. '
                          '1.5.0: {variant} only - no owner-host pool root is recorded.')
    add(pr, 'resolution', {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'canonicalization': t('string'), 'hash_excluded': {'type': 'array', 'items': t('string')},
            'validated_at': t('string', format='date-time'), 'validator': t('string'),
            'paths': {'type': 'object',
                      'additionalProperties': {'type': 'object', 'additionalProperties': False,
                                               'properties': {'requested': t(['string', 'null'])},
                                               'required': ['requested']}}},
        'description': ('Server-written in the resolved protocol only. 1.5.0: paths.<key> carries only the requested '
                        'path - the realpath on the validating host is never recorded or sent - and resolution.paths '
                        'is hash-excluded (listed in hash_excluded).')}, 'Protocol')

    vr = S['ValidateResponse']
    vr['properties']['capabilities'] = {
        'type': 'object',
        'properties': {'search_space': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'variant': t('string'), 'allow_native_fallback': t('boolean'),
                           'detail': anyval('Resolution note, scrubbed.'),
                           'probe': {'type': 'object', 'additionalProperties': False,
                                     'properties': {'available': t('boolean'), 'reason': t(['string', 'null']),
                                                    'checked': anyval()}}}}},
        'description': replace_once(vr['properties']['capabilities']['description'],
                                    'search_space = {variant, etri_root, allow_native_fallback, detail, probe '
                                    '{available, reason, checked}}',
                                    'search_space = {variant, allow_native_fallback, detail, probe {available, '
                                    'reason, checked}} (1.5.0: no pool root)')}
    vr['properties']['resolved']['description'] = (
        'Resolved protocol (Protocol shape) or null when invalid; 1.5.0: no resolution.paths.*.realpath and no '
        'search_space_resolved.etri_root.')

    # ---- 2. value-mapped members named by WP-4A ------------------------------------------------------------------
    cd['properties']['checkpoints'] = {'type': 'array', 'items': checkpoint_schema()}
    hp['allowlists']['additionalProperties'] = False
    hp['allowlists']['description'] = (
        'Customer-host roots only (DD-A19 / D8): the gateway host dataset/output roots the user may name in '
        'console.paths and the read-only archive roots. Core-internal roots are never listed.')
    for key in ('checkpoint_path', 'architecture_path'):
        ce['properties'][key]['description'] = NOT_A_HOST_PATH
    cd['properties']['architecture_path']['description'] = NOT_A_HOST_PATH
    w['properties']['device_uuid'] = dict(w['properties']['device_uuid'], description=DEVICE_UUID_NOTE, deprecated=True)
    w['properties']['id']['description'] = ('Runner id for a runner row (core-issued at registration); an opaque id '
                                            'for the core-host proposal worker. Never hostname:pid.')

    # ---- 3. additive ----------------------------------------------------------------------------------------------
    S['RuntimeManifestStatus'] = t('string', (
        "Runner runtime manifest check at registration: verified = the runner's operator file hashes equal core's; "
        'mismatch = at least one differs; unknown = not reported or not comparable.'),
        enum=['verified', 'mismatch', 'unknown'])
    S['ModelConfigRevision'] = {
        'type': 'object', 'additionalProperties': False,
        'required': ['revision_id', 'source_sha256', 'canonical_hash', 'architecture_hash', 'kind', 'created_at',
                     'parent_revision_id', 'artifact_id'],
        'properties': {
            'revision_id': t('string', 'Opaque core-issued revision id.', minLength=1),
            'source_sha256': t('string', 'sha256 of the stored bytes (upload / file bytes, not the canonical text).',
                               pattern=SHA256_PREFIXED),
            'canonical_hash': t(['string', 'null'], 'yaml_hash of the canonical YAML (same value as '
                                                    'StructureReport.yaml_hash); null when the text does not parse.'),
            'architecture_hash': t(['string', 'null'], 'Structural hash once built; null before a build.'),
            'kind': t('string', ('original = the uploaded / registered file; selected = the configuration bound '
                                 'by a training selection; executed = the configuration the trainer ran; generated = '
                                 'written by the proposer.'), enum=['original', 'selected', 'executed', 'generated']),
            'created_at': t('string', format='date-time'),
            'parent_revision_id': t(['string', 'null'], 'Revision this one was derived from; null for a first one.'),
            'artifact_id': t(['string', 'null'], ('Store id of the bytes (upl-<sha256> for an upload, the artifact '
                                                  'id of a run file); null when core holds only the canonical '
                                                  'text.'))},
        'description': ('One recorded revision of a candidate model configuration (F5d): raw-byte hash, canonical '
                        'hash, architecture hash, origin and time. GET .../config-revisions/{revision_id}/yaml '
                        'returns its bytes.')}
    S['ModelConfigRevisionList'] = {
        'type': 'object', 'additionalProperties': False, 'required': ['run_id', 'candidate_id', 'revisions'],
        'properties': {'run_id': t('string', minLength=1), 'candidate_id': t('string', minLength=1),
                       'revisions': {'type': 'array', 'items': ref('ModelConfigRevision'),
                                     'description': 'Oldest first.'}},
        'description': 'Configuration revisions of one candidate (GET .../config-revisions).'}
    S['UploadResponse'] = {
        'type': 'object', 'additionalProperties': False, 'required': ['upload_id', 'source_sha256', 'bytes'],
        'properties': {
            'upload_id': t('string', 'upl-<sha256 hex> (content addressed; an identical re-upload returns the same '
                                     'id).', pattern=UPLOAD_ID),
            'source_sha256': t('string', 'sha256 of the uploaded bytes.', pattern=SHA256_PREFIXED),
            'bytes': t('integer', minimum=1)},
        'description': 'POST /uploads/model-config answer; the server path of the stored bytes is never returned.'}

    training_status = t('string', None, enum=['UNTRAINED', 'TRAINED'])
    add(ce, 'training_status', dict(training_status, description=(
        'TRAINED when at least one training epoch of this trial completed (epochs >= 1) or the trial evaluated '
        'existing trained weights (training mode evaluate_only or pretrained); UNTRAINED otherwise. '
        + TRAINING_STATUS_NOTE)), 'CandidateEvaluation')
    add(cd, 'training_status', dict(training_status, description=(
        'TRAINED when any evaluation row of the candidate is TRAINED; UNTRAINED otherwise (generation-only '
        'candidates, trials before their first completed epoch). ' + TRAINING_STATUS_NOTE)), 'CandidateDetail')
    gc = S['GeneratedCandidate']
    add(gc, 'training_status', dict(training_status, description=(
        'Always UNTRAINED: generation never trains (same UNTRAINED|TRAINED vocabulary as CandidateEvaluation). '
        + TRAINING_STATUS_NOTE)), 'GeneratedCandidate')
    gc['required'].append('training_status')

    add(w, 'runner_id', t(['string', 'null'], ('Core-issued runner id (equals id on a runner row); null for the '
                                               'core-host proposal worker.')), 'Worker')
    add(w, 'device_id', t(['string', 'null'], 'Core-issued opaque id of the runner device; null when not issued.'),
        'Worker')
    add(w, 'runtime_manifest_status', ref('RuntimeManifestStatus'), 'Worker')

    e = S['Error']
    add(e, 'location', t('string', ('Component that produced the error (mbnas_contracts.errors.ErrorLocation): '
                                    'gateway, core (relayed unchanged; CORE_UNAVAILABLE when unreachable), runner '
                                    '(reported through core) or store (gateway artifact/upload store).'),
                         enum=['gateway', 'core', 'runner', 'store']), 'Error')
    add(e, 'core_request_id', t('string', 'Request id of the relayed core request when it differs from request_id.'),
        'Error')
    e['properties']['message']['description'] = 'Safe text: no host paths, roots or tracebacks.'

    ts = S['TrainingRequest']['properties']['source']
    for key, schema in upload_members().items():
        add(ts, key, schema, 'TrainingRequest.source')
    for part in ts['allOf']:
        if part['if']['properties']['kind'].get('const') == 'user':
            part['then'] = {
                'properties': {'upload_id': t('string', pattern=UPLOAD_ID),
                               'model_yaml_text': t('string', minLength=1),
                               'source_sha256': t('string', pattern=SHA256_OPT_PREFIX),
                               'model_path': t('string', minLength=1)},
                'anyOf': [{'required': ['upload_id']}, {'required': ['model_yaml_text', 'source_sha256']},
                          {'required': ['model_path']}]}
    ts['description'] = (
        'Immutable source of the new trial. baseline needs baseline_id; user needs upload_id (browser to gateway; '
        'the gateway relays model_yaml_text + source_sha256 to core, which never receives a path) or model_path '
        '(legacy owner-local server path); exactly one of the three. search and generated need run_id and '
        'candidate_id. upload_id, model_yaml_text, source_sha256 and model_name are valid for a user source only.')
    tr = S['TrainingRequest']
    tr['description'] = replace_once(tr['description'], 'user needs model_path (server-allowlisted detector YAML)',
                                     'user needs upload_id (POST /uploads/model-config; the gateway relays the text '
                                     'as model_yaml_text + source_sha256) or a legacy model_path')
    trc = S['TrainingRunCreated']['properties']['source']
    add(trc, 'source_sha256', t(['string', 'null'], 'sha256 of the uploaded YAML bytes (user source).'),
        'TrainingRunCreated.source')
    add(trc, 'model_name', t(['string', 'null'], 'File name of the uploaded YAML (user source).'),
        'TrainingRunCreated.source')

    baseline_item = pr['properties']['console']['properties']['baselines']['items']
    add(baseline_item, 'name', t('string', 'Display label supplied by the preset (for example YOLO11N).'),
        'Protocol.console.baselines[]')
    add(baseline_item, 'source', t('string', 'Origin label supplied by the preset (for example Ultralytics).'),
        'Protocol.console.baselines[]')
    S['PresetSummary']['properties']['id']['description'] = (
        'Preset id: DEFAULT_BASELINE14 (the 14 default baselines; the web console applies it automatically after '
        'connecting, DD-A21) or PILOT_SMOKE.')
    S['Preset'] = flat_preset(S['PresetSummary'])

    # PUBLIC members emitted by the server but not declared by 1.4.3 (needed before closing)
    add(ce, 'immutable', t('boolean', 'true once the row is a committed measurement.'), 'CandidateEvaluation')
    add(ce, 'source_kind', t(['string', 'null'], 'Explicit training rows: kind of the selected source.'),
        'CandidateEvaluation')
    add(ce, 'training_mode', t(['string', 'null'], 'Explicit training rows: requested training mode.'),
        'CandidateEvaluation')
    add(ce, 'performance_status', t(['string', 'null'], 'NOT_EVALUATED on rows that measured nothing.'),
        'CandidateEvaluation')
    for key in ('loss', 'train_loss', 'images_per_second', 'samples_per_second', 'throughput', 'epoch_seconds',
                'batch_seconds'):
        add(w, key, t(['number', 'null']), 'Worker')
    for key in ('native_metrics', 'losses'):
        add(w, key, t(['object', 'null'], 'Numeric training telemetry by name.'), 'Worker')
    add(run, 'last_seq', t('integer', 'Last event sequence number (GET /runs/{run_id}).', minimum=0), 'Run')
    add(run, 'final_retrain_runs', {
        'type': 'array', 'description': 'FINAL_RETRAIN children of a search run.',
        'items': {'type': 'object', 'additionalProperties': False, 'required': ['id', 'status'],
                  'properties': {'id': t('string'), 'status': t('string'),
                                 'created_at': t('string', format='date-time'),
                                 'finalist_count': t('integer', minimum=0)}}}, 'Run')
    add(ev, 'created_at', t('string', 'Storage time of the event (snapshot and replay rows).', format='date-time'),
        'Event')
    add(h, 'warnings', {'type': 'array', 'items': t('string'), 'description': 'Codes or scrubbed text.'}, 'Health')
    add(h, 'source_tree', {'type': 'object', 'additionalProperties': False,
                           'properties': {'algorithm': t(['string', 'null']), 'sha256': t(['string', 'null']),
                                          'files': t(['integer', 'null'])}}, 'Health')
    add(h, 'core', {'anyOf': [t('string', const='unreachable'),
                              {'type': 'object', 'additionalProperties': False,
                               'properties': {'status': t(['string', 'null']), 'reachable': t('boolean'),
                                              'error': t('string')}}],
                    'description': 'Gateway: reachability of core (unreachable = degraded, no core sections).'},
        'Health')
    add(h, 'gateway', {'type': 'object', 'additionalProperties': False,
                       'properties': {'status': t('string'), 'version': t(['string', 'null']),
                                      'core_url_configured': t('boolean'), 'web_dist': t('boolean'),
                                      'store_writable': t('boolean')},
                       'description': 'Gateway self status (DD-A30 web dist, artifact store).'}, 'Health')
    add(h, 'request_id', t('string'), 'Health')
    add(cd, 'current_rung', t(['string', 'null']), 'CandidateDetail')
    add(cd, 'search_seed', t(['integer', 'null']), 'CandidateDetail')
    add(cd, 'training_seed', t(['integer', 'null']), 'CandidateDetail')
    add(cd, 'generation', t(['integer', 'null'], minimum=0), 'CandidateDetail')
    for key, schema in (('generation', t(['integer', 'null'], minimum=0)),
                        ('parents', {'type': 'array', 'items': t('string')}),
                        ('architecture_hash', t(['string', 'null'])), ('search_seed', t(['integer', 'null'])),
                        ('training_seed', t(['integer', 'null'])), ('seed', t(['integer', 'null'])),
                        ('attempt', t(['integer', 'null'])), ('blocks', {'type': 'array', 'items': ref('Block')}),
                        ('architecture_path', t(['string', 'null'], NOT_A_HOST_PATH))):
        add(gc, key, schema, 'GeneratedCandidate')
    rc = S['RunCreated']
    add(rc, 'protocol_hash', t('string', minLength=1), 'RunCreated')
    add(rc, 'created', t('boolean', 'false when the idempotency key replayed an existing run.'), 'RunCreated')
    add(rc, 'kind', {'enum': ['SEARCH', 'FINAL_RETRAIN']}, 'RunCreated')
    add(sr, 'model_id', t(['string', 'null'], 'Registry model id of /registry/{model_id}/structure* documents.'),
        'StructureReport')
    add(sr, 'sandbox', {'type': ['object', 'null'], 'additionalProperties': False,
                        'properties': {'isolation': anyval('Isolation level actually used.'),
                                       'limits': t(['object', 'null'], 'Resource limits of the check process.'),
                                       'wall_timeout_s': t(['number', 'null']),
                                       'elapsed_s': t(['number', 'null'], 'Wall time of the check process (WP-4B).'),
                                       'exit_code': t(['integer', 'null'], 'Exit status of the check process (WP-4B).')},
                        'description': 'How the structure-check process was isolated (DD-A20; sandboxed results).'},
        'StructureReport')
    add(sr, 'source_sha256', t(['string', 'null'], ('User structure checks (WP-4B): sha256 hex of the checked YAML bytes '
                                                    '(the carried upload text or the model_path file).')),
        'StructureReport')
    sr['properties']['configuration']['description'] = (
        'Candidate views: which configuration the rows describe {kind execution|normalized|original|generated, '
        'yaml_hash, path, availability}. ' + NOT_A_HOST_PATH)

    # ---- 4. close every projected component -----------------------------------------------------------------------
    for name in PROJECTED:
        S[name]['additionalProperties'] = False

    # ---- 5. paths -------------------------------------------------------------------------------------------------
    sc = P['/api/v1/structure-check']['post']
    body = sc['requestBody']['content']['application/json']['schema']
    old = body['properties']
    del body['required']
    body['properties'] = {**upload_members(), 'model_path': dict(old['model_path'], description=(
        'Legacy (owner-local deployments): a YAML (up to 1 MiB) under the server model_yaml_roots / output_roots.')),
        'input_size': old['input_size'], 'class_count': old['class_count']}
    sc['description'] = (
        'The model is given as upload_id (browser to gateway: the bytes stored by POST /uploads/model-config); the '
        'gateway relays it to core as model_yaml_text + source_sha256, so core never receives a path. model_path '
        '(legacy, owner-local deployments) names a YAML (up to 1 MiB) under the server model_yaml_roots / '
        'output_roots. Exactly one of upload_id, model_yaml_text (with source_sha256) or model_path. The document is '
        'returned and not stored. Statuses: 200 for PASS, FAIL and UNVERIFIED alike; 404 UPLOAD_NOT_FOUND; 409 '
        'UPLOAD_CORRUPT; 422 for a rejected request: UPLOAD_ID_INVALID, UPLOAD_NOT_UTF8, SOURCE_SHA256_INVALID, '
        'SOURCE_SHA256_MISMATCH, the path codes of the shared server path check (PATH_NOT_ABSOLUTE, '
        'PATH_PLACEHOLDER, PATH_TRAVERSAL, PATH_OUTSIDE_ALLOWLIST, PATH_NOT_FOUND, PATH_NOT_READABLE), '
        'MODEL_CONFIG_INVALID (not a YAML mapping, not .yaml/.yml, over 1 MiB, unparsable, class_count not a '
        'positive integer), INPUT_SIZE_INVALID (input_size not an integer, < 32 or not a multiple of 32), '
        'VALIDATION_FAILED (no model, more than one model source, wrong type or unknown field); 507 '
        'STORE_UNAVAILABLE.')
    responses = sc['responses']
    responses['422']['description'] = (
        'UPLOAD_ID_INVALID, UPLOAD_NOT_UTF8, SOURCE_SHA256_INVALID, SOURCE_SHA256_MISMATCH, PATH_NOT_ABSOLUTE, '
        'PATH_PLACEHOLDER, PATH_TRAVERSAL, PATH_OUTSIDE_ALLOWLIST, PATH_NOT_FOUND, PATH_NOT_READABLE, '
        'MODEL_CONFIG_INVALID, INPUT_SIZE_INVALID, VALIDATION_FAILED')
    sc['responses'] = insert_responses(responses, {'404': error_response('UPLOAD_NOT_FOUND'),
                                                   '409': error_response('UPLOAD_CORRUPT'),
                                                   '507': error_response('STORE_UNAVAILABLE')})

    frr = P['/api/v1/runs/{run_id}/final-retrain']['post']['responses']
    frr['409']['description'] += (', STALE_VALIDATION (the gateway-measured dataset / path inputs no longer resolve the '
                                  'parent protocol to its protocol_hash; DD-A46)')

    trp = P['/api/v1/training-runs']['post']['responses']
    trp['404']['description'] += '; UPLOAD_NOT_FOUND (user source upload_id unknown to the gateway store)'
    trp['409']['description'] += ', UPLOAD_CORRUPT (stored upload no longer matches its sha256)'
    trp['422']['description'] += (', UPLOAD_ID_INVALID, UPLOAD_NOT_UTF8, SOURCE_SHA256_INVALID, SOURCE_SHA256_MISMATCH '
                                  '(user source given as an upload)')

    ids = [{'name': 'run_id', 'in': 'path', 'required': True, 'schema': t('string', minLength=1)},
           {'name': 'candidate_id', 'in': 'path', 'required': True, 'schema': t('string', minLength=1)}]
    P['/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions'] = {'get': {
        'operationId': 'listConfigRevisions',
        'summary': 'Configuration revisions of a candidate (original, selected, executed, generated)',
        'parameters': copy.deepcopy(ids),
        'responses': {
            '200': {'description': 'Revisions, oldest first',
                    'content': {'application/json': {'schema': ref('ModelConfigRevisionList')}}},
            '401': {'description': 'Authentication required'}, '403': {'description': 'Not authorized'},
            '404': error_response('RUN_NOT_FOUND or CANDIDATE_NOT_FOUND'),
            '500': error_response('RESPONSE_CONTRACT_VIOLATION or internal error')}}}
    P['/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions/{revision_id}/yaml'] = {'get': {
        'operationId': 'getConfigRevisionYaml',
        'summary': 'Stored bytes of one configuration revision (for download and the selected/executed diff)',
        'description': ('Returns the stored bytes unchanged (Content-Type application/x-yaml, X-Artifact-Sha256 = '
                        'hex sha256 of the body = the revision source_sha256 without its prefix). 404 NOT_FOUND / '
                        'ARTIFACT_NOT_READY; 409 LOAD_FAILED when the stored bytes no longer match source_sha256.'),
        'parameters': copy.deepcopy(ids) + [{'name': 'revision_id', 'in': 'path', 'required': True,
                                             'schema': t('string', minLength=1)}],
        'responses': {
            '200': {'description': 'YAML bytes of the revision',
                    'headers': {'X-Artifact-Sha256': {'description': 'hex sha256 of the body',
                                                      'schema': t('string', pattern='^[0-9a-f]{64}$')}},
                    'content': {'application/x-yaml': {'schema': t('string')}}},
            '401': {'description': 'Authentication required'}, '403': {'description': 'Not authorized'},
            '404': error_response('RUN_NOT_FOUND, CANDIDATE_NOT_FOUND, NOT_FOUND (revision) or ARTIFACT_NOT_READY'),
            '409': error_response('LOAD_FAILED (stored bytes differ from source_sha256)'),
            '500': error_response('RESPONSE_CONTRACT_VIOLATION or internal error')}}}
    raw_body = t('string', 'Raw YAML bytes; the file name comes from X-Filename or ?filename=.')
    P['/api/v1/uploads/model-config'] = {'post': {
        'operationId': 'uploadModelConfig',
        'summary': 'Upload a detector model configuration YAML (F5a; gateway store, content addressed)',
        'description': ('Raw body (any non-multipart Content-Type; file name from X-Filename or ?filename=) or '
                        'multipart/form-data with one file part (field file). The name must end in .yaml/.yml; '
                        '1 byte .. upload_max_bytes (default 1 MiB); UTF-8 text. The original bytes are stored '
                        'unchanged and addressed by their sha256 (an identical re-upload is a no-op); the server '
                        'path is never returned. Use upload_id in POST /structure-check and POST /training-runs '
                        '(source.kind user).'),
        'parameters': [{'name': 'X-Filename', 'in': 'header', 'required': False, 'schema': t('string')},
                       {'name': 'filename', 'in': 'query', 'required': False, 'schema': t('string')}],
        'requestBody': {'required': True, 'content': {
            'application/yaml': {'schema': copy.deepcopy(raw_body)},
            'application/x-yaml': {'schema': copy.deepcopy(raw_body)},
            'application/octet-stream': {'schema': copy.deepcopy(raw_body)},
            'multipart/form-data': {'schema': {'type': 'object', 'required': ['file'],
                                               'properties': {'file': t('string', contentMediaType='application/x-yaml')}}}}},
        'responses': {
            '200': {'description': 'Stored (or already present)',
                    'content': {'application/json': {'schema': ref('UploadResponse')}}},
            '401': {'description': 'Authentication required'}, '403': {'description': 'Not authorized'},
            '413': error_response('PAYLOAD_TOO_LARGE'),
            '422': error_response('UPLOAD_TYPE_UNSUPPORTED (file name not .yaml/.yml), UPLOAD_EMPTY, UPLOAD_NOT_UTF8, '
                                  'UPLOAD_INVALID (multipart without a file part)'),
            '500': error_response('internal error'),
            '507': error_response('STORE_UNAVAILABLE (upload store not writable)')}}}

    # ---- 6. vocabulary, projection index, changelog --------------------------------------------------------------
    vocab = d['x-status-vocabulary']
    new_codes = {'UPLOAD_TYPE_UNSUPPORTED': 422, 'PAYLOAD_TOO_LARGE': 413, 'UPLOAD_EMPTY': 422, 'UPLOAD_NOT_UTF8': 422,
                 'UPLOAD_INVALID': 422, 'UPLOAD_ID_INVALID': 422, 'UPLOAD_NOT_FOUND': 404, 'UPLOAD_CORRUPT': 409,
                 'STORE_UNAVAILABLE': 507, 'SOURCE_SHA256_INVALID': 422, 'SOURCE_SHA256_MISMATCH': 422,
                 'CORE_UNAVAILABLE': 503, 'CORE_BAD_RESPONSE': 502, 'CORE_AUTH_FAILED': 502, 'RATE_LIMITED': 429,
                 'ARTIFACT_NOT_READY': 404, 'LOAD_FAILED': 409}
    for code, status in new_codes.items():
        assert code not in vocab['error_codes_added'], code
        vocab['error_codes_added'][code] = status
    vocab['error_codes_note'] += (
        ' 1.5.0: uploads (POST /uploads/model-config and the upload_id of POST /structure-check and POST '
        '/training-runs) - PAYLOAD_TOO_LARGE 413, UPLOAD_TYPE_UNSUPPORTED / UPLOAD_EMPTY / UPLOAD_NOT_UTF8 / '
        'UPLOAD_INVALID / UPLOAD_ID_INVALID 422, UPLOAD_NOT_FOUND 404, UPLOAD_CORRUPT 409, STORE_UNAVAILABLE 507; '
        'carried text - SOURCE_SHA256_INVALID / SOURCE_SHA256_MISMATCH 422; gateway relay (Error.location core) - '
        'CORE_UNAVAILABLE 503, CORE_BAD_RESPONSE 502, CORE_AUTH_FAILED 502; gateway limits - RATE_LIMITED 429 '
        '(Retry-After); stored bytes - ARTIFACT_NOT_READY 404, LOAD_FAILED 409. Gateway path hygiene (DD-A44 e): a '
        'request whose path has a dot segment (. or .., raw or percent-encoded such as %2E%2E) or whose path parameter '
        'contains a control character (C0, DEL or C1) is answered 404 NOT_FOUND with location gateway; it never '
        'reaches another route, core or the static web fallback. POST /runs/{run_id}/final-retrain - STALE_VALIDATION '
        '409 when the gateway-measured host inputs sent with the request no longer resolve the parent run protocol to '
        'its protocol_hash (DD-A46; the same freshness rule as POST /runs).')
    d['x-public-projection'] = {
        'since': '1.5.0',
        'authority': ('This document is the public DTO authority (DD-A19, DD-A20): every member that leaves core '
                      'through /core/v1/console/*, its SSE events or the public export is declared here; owner '
                      'audit DTOs exist only on /core/v1/owner/*. Stored rows are never rewritten.'),
        'projected_components': list(PROJECTED),
        'rules': [
            'closed object (additionalProperties false): copy only the declared properties',
            'open object: copy every member except the names in propertyNames.not.enum',
            ('recurse through $ref, items, additionalProperties (map values), anyOf/oneOf branches and the allOf '
             'if/then branch whose condition matches (Event.payload by Event.type)'),
            'then apply the value rules below; nulls are pruned where a schema does not admit null'],
        'value_rules': {
            'scrub': ('free text (Error.message, Error.errors[].message, ValidateResponse errors/warnings messages, '
                      'Health.warnings, RegistryEntry.reason, RegistryList.warning, log message / message_detail, '
                      'audit.fallback_reason, evidence *.error): absolute paths, pid=<n> and host names replaced'),
            'ids': ('worker identifiers are core-issued runner ids (never hostname:pid); device_uuid members are '
                    'deprecated in favour of the core-issued device_id (Worker, raw_metrics)'),
            'paths': ('checkpoint_path / architecture_path / configuration.path: run-relative path or file name, '
                      'never an absolute host path; checkpoints are referenced by checkpoint id and artifact_id'),
            'urls': 'Artifact url / refresh_url and snapshot_url point at the gateway /api/v1',
            'repair': 'Block / StructureReport block source_kind and operation never say repair (folded)'}}
    d['x-contract-changelog'].insert(0, {'version': '1.5.0', 'date': DATE, 'author': AUTHOR, 'changes': CHANGES})


def upload_members():
    return {
        'upload_id': t('string', ('Browser to gateway: id from POST /uploads/model-config. The gateway replaces it '
                                  'with model_yaml_text + source_sha256 before relaying to core.'), pattern=UPLOAD_ID),
        'model_yaml_text': t('string', ('Gateway to core: the uploaded YAML text (UTF-8, up to 1 MiB), with '
                                        'source_sha256. A browser sends upload_id instead.'), minLength=1),
        'source_sha256': t('string', ('sha256 of the original upload bytes (UploadResponse.source_sha256; the '
                                      'sha256: prefix is optional); required with model_yaml_text.'),
                           pattern=SHA256_OPT_PREFIX),
        'model_name': t('string', ('Optional file name of the uploaded model (used for the scale rule and the '
                                   'document name).'), minLength=1)}


def insert_responses(responses, extra):
    """Responses with ``extra`` merged in ascending status-code order (deterministic)."""
    merged = dict(responses)
    for code, value in extra.items():
        assert code not in merged, code
        merged[code] = value
    return {code: merged[code] for code in sorted(merged)}


def checkpoint_schema():
    return {
        'type': 'object', 'additionalProperties': False, 'required': ['id', 'kind', 'epoch', 'sha256', 'state'],
        'properties': {
            'id': t('string', ('Checkpoint id (ck-<hex>): the public checkpoint reference that replaces the host path '
                               '(1.5.0).'), minLength=1),
            'run_id': t('string'), 'candidate_id': t('string'), 'rung': t(['string', 'null']),
            'epoch': t('integer', minimum=0),
            'kind': t('string', enum=['training_full', 'eval_ema', 'weights_only', 'best']),
            'sha256': t('string'), 'bytes': t('integer', minimum=0), 'hashes': t(['object', 'null']),
            'state': t('string', 'PRUNED = recorded deletion; MISSING = gone without a record.',
                       enum=['PRESENT', 'PRUNED', 'MISSING']),
            'pruned_at': t(['string', 'null']), 'created_at': t('string'), 'updated_at': t('string'),
            'prune': {'type': ['object', 'null'], 'additionalProperties': False,
                      'properties': {'policy': t(['string', 'null']), 'reason': t(['string', 'null']),
                                     'deleted': {'type': 'array', 'items': {
                                         'type': 'object', 'additionalProperties': False,
                                         'properties': {'bytes': t(['integer', 'null'], minimum=0)}}},
                                     'bytes_freed': t(['integer', 'null'], minimum=0), 'kept': t(['string', 'null']),
                                     'candidate_status': t(['string', 'null']), 'note': t(['string', 'null'])},
                      'description': 'Retention record; deleted[] lists sizes only (no paths).'},
            'artifact_id': t(['string', 'null'], ('Artifact store id of the checkpoint bytes (gateway store); null '
                                                  'until they are registered there.'))},
        'description': '1.5.0: a checkpoint row without its host path (path -> id / artifact_id).'}


def audit_schema(old_description):
    rung = {'type': 'object', 'additionalProperties': False,
            'properties': {'state': t(['string', 'null']), 'evaluation_id': t(['string', 'null']), 'gate': anyval(),
                           'reason_code': t(['string', 'null']), 'job_id': t(['string', 'null']),
                           'performance_status': t(['string', 'null']),
                           'admission': {'type': ['object', 'null'], 'additionalProperties': False,
                                         'properties': {'admitted': t(['boolean', 'null']),
                                                        'reason_code': t(['string', 'null']),
                                                        'arm': t(['string', 'null']),
                                                        'search_seed': t(['integer', 'null']),
                                                        'cap_s': t(['number', 'null']),
                                                        'billed_s': t(['number', 'null']),
                                                        'remaining_s': t(['number', 'null'])},
                                         'description': 'Decision code and cap / billed / remaining only.'}}}
    obj = lambda props, desc=None: dict(  # noqa: E731
        {'type': ['object', 'null'], 'additionalProperties': False, 'properties': props},
        **({'description': desc} if desc else {}))
    return {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'origin': t(['string', 'null']), 'label': anyval(), 'performance': anyval(), 'params': anyval(),
            'flops': anyval(), 'imgsz': anyval(), 'architecture_hash_scheme': t(['string', 'null']),
            'structure_signature_sha256': t(['string', 'null']), 'search_space_variant': t(['string', 'null']),
            'config_estimate': obj({k: anyval() for k in (
                'scope', 'proof_of_model_family', 'backbone_family', 'backbone_operator_counts',
                'backbone_attention_rows', 'neck_operator_counts', 'neck_attention_rows', 'detect_rows',
                'head_preserved', 'label')}, 'Operator statistics counted from the YAML.'),
            'built': t(['object', 'null'], 'CPU build operator audit (structure-check result).'),
            'fallback_reason': t(['string', 'null'], 'Reason code (no paths or exception text).'),
            'structure': {'anyOf': [ref('StructureReport'), t('null')]},
            'structure_history': {'type': 'array', 'items': {
                'type': 'object', 'additionalProperties': False,
                'properties': {'checked_at': t(['string', 'null']), 'yaml_hash': t(['string', 'null']),
                               'status': t(['string', 'null']), 'observation': anyval(),
                               'archived_at': t(['string', 'null'])}}},
            'proposal': obj({'genotype': anyval(), 'yaml_hash': t(['string', 'null']), 'params': anyval(),
                             'flops': anyval()}),
            'generation': obj({'index': t(['integer', 'null']), 'seed': t(['integer', 'null']),
                               'requested_method': anyval(), 'effective_method': t(['string', 'null']),
                               'duplicate_scope': t(['string', 'null']), 'execution': t(['string', 'null']),
                               'attempt': t(['integer', 'null']),
                               'worker_id': t(['string', 'null'], 'Core-issued runner / worker id.')}),
            'orchestration': obj({
                'baseline_id': t(['string', 'null']), 'comparison_group': t(['string', 'null']), 'init': anyval(),
                'requested_input_shape': anyval(), 'resolved_input_shape': anyval(), 'index': t(['integer', 'null']),
                'invalid_reason': t(['string', 'null']),
                'rungs': {'type': 'object', 'additionalProperties': rung, 'description': 'By rung id.'},
                'final': obj({'source_candidate_id': t(['string', 'null']),
                              'source_evaluation_id': t(['string', 'null']), 'source_run_id': t(['string', 'null']),
                              'rank': t(['integer', 'null']), 'recipe': anyval(),
                              'training_seed_policy': obj({'id': anyval(), 'base': anyval(),
                                                           'base_source': anyval(), 'requested': anyval(),
                                                           'final_id': anyval()},
                                                          'User seed policy; the RNG derivations are owner-only.')})}),
            'execution_configuration': obj({'artifact_id': t(['string', 'null']), 'sha256': t(['string', 'null']),
                                            'yaml_hash': t(['string', 'null']),
                                            'class_count': t(['integer', 'null'])},
                                           'The executed configuration is referenced by artifact id (no path).'),
            'training_source': t(['object', 'null'], 'User-selected source binding (explicit training).'),
            'source': t(['object', 'null'], 'User-selected source binding (explicit training).')},
        'description': (old_description + ' 1.5.0: public evidence subset (DTO allowlist section 3.3, DD-A19): '
                        'audit rule texts, the ETRI audit model output, internal job links, admission estimator '
                        'internals, RNG seed derivations and file paths are owner-only.')}


def flat_preset(summary):
    props = copy.deepcopy(summary['properties'])
    props['protocol'] = ref('Protocol', description=(
        'Template protocol. console.paths are the deployment-configured example roots, or /ABSOLUTE/PATH/TO/... '
        'placeholders that validation rejects (PATH_PLACEHOLDER) until replaced; never owner-host paths.'))
    return {'type': 'object', 'properties': props, 'required': list(summary['required']) + ['protocol'],
            'description': 'PresetSummary members plus protocol (1.5.0: flattened from allOf so that it can be closed).'}


CHANGES = [
    ('WP-4A projection contract (minimal form, DD-A38). Removed owner-only members (DTO allowlist OWNER_ONLY, '
     'DD-A19): Generation.group_key; Worker.pid, lease_expires_at (child_pid excluded by closing); Block.old_name, '
     'before, after, repair_reason (old_name leaves Block.required) and Block.params.slot_kind; '
     'CandidateDetail.lineage crossover / mutations / repairs (and the undeclared trace keys); '
     'RegistryList.registry_roots_skipped; Protocol console.search.search_space_resolved.etri_root.'),
    ('Owner-only members excluded by closing their parent object: CandidateEvaluation.raw_metrics (allowlist), '
     'CandidateDetail.lineage and StructureReport.lineage (public subset), CandidateDetail.audit (evidence '
     'subset), Health.capabilities.search_space (no root), Health.versions.environment_lock (no path), Event log '
     'payload (key allowlist, no path); by propertyNames deny lists where the object stays open: RegistryEntry '
     'evidence (probe_file, registry_roots_skipped, config_ref.path, weights.path, build.trace, '
     'pretrained_load.loaded_from, source.record / notebook), StructureReport.blocks[] (before, after, old_name, '
     'repair_reason), Run.budget (policy).'),
    ('Archive* members marked OWNER_ONLY by the analysis (mutation, stop_rule, transition, orphan_check) are kept: '
     'DD-A19 keeps the archive with its mutation/driver documents.'),
    ('additionalProperties false on every projected component (x-public-projection.projected_components); members '
     'the server already emitted but 1.4.3 did not declare are declared (CandidateEvaluation immutable / '
     'source_kind / training_mode / performance_status, Worker training telemetry, Run last_seq / '
     'final_retrain_runs, Event created_at, Health warnings / source_tree / core / gateway / request_id, '
     'CandidateDetail current_rung / search_seed / training_seed / generation, GeneratedCandidate generation / '
     'parents / architecture_hash / seeds / attempt / blocks / architecture_path, RunCreated protocol_hash / '
     'created / kind, StructureReport model_id / sandbox and blocks[] provenance keys); Preset is flattened from allOf.'),
    ('Value-mapped members: CandidateDetail.checkpoints[] items without path (id is the checkpoint reference, new '
     'artifact_id, prune.deleted[] sizes only); Health.allowlists customer-host roots only; resolved protocol '
     'resolution declared with paths.<key> = {requested} only (no realpath; resolution.paths hash-excluded); '
     'CandidateDetail.audit.execution_configuration without path (artifact_id); Block source_kind loses repair '
     '(folded); path members documented as never absolute host paths; Worker.device_uuid and '
     'raw_metrics.device_uuid deprecated in favour of device_id.'),
    ('Added: training_status (UNTRAINED|TRAINED) on CandidateEvaluation and CandidateDetail (optional) and on '
     'GeneratedCandidate (required, always UNTRAINED) (DD-A23; status enums unchanged); Worker runner_id / device_id / '
     'runtime_manifest_status and component RuntimeManifestStatus (verified|mismatch|unknown); components '
     'ModelConfigRevision and ModelConfigRevisionList with GET /runs/{run_id}/candidates/{candidate_id}/'
     'config-revisions and .../config-revisions/{revision_id}/yaml (application/x-yaml bytes); component '
     'UploadResponse with POST /uploads/model-config; upload_id / model_yaml_text / source_sha256 / model_name on '
     'POST /structure-check and TrainingRequest.source (source_sha256 / model_name echoed in '
     'TrainingRunCreated.source); Error.location (gateway|core|runner|store) and Error.core_request_id; upload, '
     'gateway relay, rate-limit and stored-byte codes in x-status-vocabulary.'),
    ('Wording: info.description names the console and the gateway distribution (DD-A26 data exception expires); '
     'servers[0] is the relative same-origin gateway root (DD-A41; the port-8000 development URL is gone); '
     'x-contract-history points at docs/public/contracts/history; descriptions '
     'no longer name owner-host roots; x-public-projection states the projection rules; preset baselines declare '
     'their name / source labels and PresetSummary.id names DEFAULT_BASELINE14.'),
    ('WP-4B promotion additions: Block declares the user-model row keys module, from, args and repeats (DD-A42); '
     'StructureReport declares source_sha256 and sandbox elapsed_s / exit_code (PUBLIC members the core already '
     'emitted, so closing keeps them); RegistryEntry.evidence.source also admits the label string of the bundled '
     'example registry; POST /uploads/model-config answers UPLOAD_TYPE_UNSUPPORTED with 422 instead of '
     '415 (DD-A44 d); x-status-vocabulary.error_codes_note states the gateway path hygiene (dot segment or control '
     'character in a path parameter -> 404 NOT_FOUND, location gateway; DD-A44 e); POST /runs/{run_id}/final-retrain '
     'names 409 STALE_VALIDATION (DD-A46).'),
]


def main(argv):
    promote = '--promote' in argv
    args = [a for a in argv if a != '--promote']
    if not 1 <= len(args) <= 2:
        sys.exit(__doc__)
    src = Path(args[0])
    dst = Path(args[1]) if len(args) == 2 else src
    live = dst.name == 'openapi.json' and (dst.parent / 'HISTORY.json').is_file() and \
        (dst.parent / 'openapi_runner.json').is_file()
    if live and not promote:
        sys.exit(f'refusing to rewrite the live package contract {dst} (draft stage; pass --promote at promotion)')
    raw = src.read_bytes()
    d = json.loads(raw.decode('utf-8'))
    migrate(d)
    out = json.dumps(d, ensure_ascii=False, indent=1)
    dst.write_text(out, encoding='utf-8')
    print('contract', d['info']['version'], '->', dst)
    if hashlib.sha256(raw).hexdigest() == SNAPSHOT_1_4_3_SHA256:
        got = hashlib.sha256(out.encode('utf-8')).hexdigest()
        if EXPECTED_1_5_0_SHA256 != '__FILL__':
            assert got == EXPECTED_1_5_0_SHA256, f'replay mismatch: {got} != {EXPECTED_1_5_0_SHA256}'
            print('replay ok: sha256', got)
        else:
            print('sha256 (fill into EXPECTED_1_5_0_SHA256):', got)


if __name__ == '__main__':
    main(sys.argv[1:])
