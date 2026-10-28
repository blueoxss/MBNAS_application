"""Contract 1.4.0 -> 1.4.1 (integration round 2, 2026-10-22; design claude-fable-5-1, implementation claude-sonnet-5-5). Additive.

Documentation-level refinements of what the real servers emit; no path, no required member, no status code and no enum value
is added or removed. Replaying this script on openapi-1.4.0.json yields openapi-1.4.1.json (= openapi.json) byte for byte; the
script asserts the sha256 of its result when it is run on the 1.4.0 snapshot.

Applied (WP-S2 requests R-S2-2..6, WP-S4 request 1 and 4, PLAN DD-26 / DD-36):
  * StructureReport: the extra keys the native (RF-DETR, ETRI hybrid) documents carry are typed as optional nullable members
    (top level, observation, edges[].evidence, blocks[], repeats_detail, effective, effective.inputs[]); evidence_source and
    diagnostics[].code descriptions list the values the server writes (evidence_source stays a free string).
  * Registry structure routes: the statuses the server answers are written down (descriptions only).
  * RegistryList: source, warning, registry_roots_skipped (DD-26) documented; RegistryEntry.evidence mentions
    registry_roots_skipped.
  * TrainingRequest.checkpoint, TrainingRequest, POST /training-runs 409 and x-status-vocabulary: DD-36 wording for
    EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED / RESUME_CHECKPOINT_MISMATCH.
  * x-status-vocabulary.structure_diagnostic_codes_added gains CLASS_COUNT_UNKNOWN and STRUCTURE_CHECK_FAILED.
Not applied (see evidence_impl/WPE/SUMMARY.md): new repeats_source enum values (R-S2-1), an evidence_source enum (R-S2-4).

Usage: python migrate_1.4.0_to_1.4.1.py [path-to-copy-of-openapi-1.4.0.json]   (rewrites the given file in place)
"""
import hashlib, json, sys
from pathlib import Path

p = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / 'openapi.json')
raw = Path(p).read_bytes()
SNAPSHOT_1_4_0_SHA256 = 'b89047dfd3d3a055d6f4d14e19c300170a62ec4328cbb625f82e757f4d621bb9'
EXPECTED_1_4_1_SHA256 = 'd6f1ac2a9a40b4646e626616ba1975e76507728f2423c4ebb35ed4b27469a993'
d = json.loads(raw.decode('utf-8')); S = d['components']['schemas']
assert d['info']['version'] == '1.4.0', d['info']['version']
d['info']['version'] = '1.4.1'


def put(node, key, value, after=None):
    """Set node[key]; a new key goes right after `after` (or at the end), an existing key keeps its position."""
    if key in node:
        node[key] = value
        return
    items, rebuilt = list(node.items()), {}
    if after is None:
        rebuilt.update(items)
        rebuilt[key] = value
    else:
        assert after in node, after
        for k, v in items:
            rebuilt[k] = v
            if k == after:
                rebuilt[key] = value
    node.clear(); node.update(rebuilt)


def nullable(*types, **kw):
    out = {'type': [*types, 'null']}
    out.update(kw)
    return out


def replace_once(text, old, new):
    assert text.count(old) == 1, (text.count(old), old)
    return text.replace(old, new)


SR = S['StructureReport']['properties']
BLK = SR['blocks']['items']['properties']

# ---- StructureReport: top-level keys of the native documents (R-S2-3) -----------------------------------------------------
put(SR, 'fixed_resolution', nullable('integer', description=(
    'Native backends: the single input size the model is built for (RF-DETR preset resolution, ETRI hybrid imgsz). A check at '
    'another size is a FAIL document with INPUT_SIZE_INVALID. Absent for YAML documents.')), after='input_size')
put(SR, 'evidence_source', dict(SR['evidence_source'], description=(
    'Where the document comes from. Values written by the server: saved_structure_check (recorded check of this exact '
    'configuration; candidate and registry views), declared_configuration (declaration only, nothing was built), '
    'executed_structure_check (the answer of an explicit check: the two POST structure routes). Free string on purpose: '
    'consumers must not fail on a value they do not know. Absent on the document of a freshly executed candidate check.')))
for key, schema, after in [
    ('native_config', nullable('object', additionalProperties=True, description=(
        'Native backends (DECLARED): the model configuration the document was built from (for example encoder, '
        'out_feature_indexes, dec_layers, hidden_dim, patch_size for RF-DETR; family, imgsz, patch, dim, depth, heads for the '
        'ETRI hybrid). Never an observed value.')), 'configuration'),
    ('observed_scopes', nullable('array', description=(
        'Native backends (OBSERVED): hook records of the container modules that have no block of their own (RF-DETR backbone '
        'Joiner and transformer): [{module_path, module_class, calls, inputs, outputs}] with the input/output shapes seen. [] '
        'when there is none.'),
        items={'type': 'object', 'additionalProperties': True, 'properties': {
            'module_path': nullable('string'), 'module_class': nullable('string'), 'calls': nullable('integer'),
            'inputs': nullable('array', items={'type': 'array', 'items': nullable('integer')}),
            'outputs': nullable('array', items={'type': 'array', 'items': nullable('integer')})}}), 'edges_unobserved'),
    ('repeat_groups', nullable('array', description=(
        'Native backends: repeated units that were split into one block each: [{group, declared, declared_basis, observed, '
        'blocks[]}]; declared is the configured count (null when the configuration has none), observed the counted length of '
        'the module container, blocks the ids of the unit blocks.'),
        items={'type': 'object', 'additionalProperties': True, 'properties': {
            'group': nullable('string'), 'declared': nullable('integer'), 'declared_basis': nullable('string'),
            'observed': nullable('integer'), 'blocks': nullable('array', items={'type': 'string'})}}), 'observed_scopes'),
    ('parameters_unattributed', nullable('integer', description=(
        'Native backends: model parameters minus the sum of the block parameters (signed). 0 means every parameter is '
        'attributed to a listed block; the server never hides a remainder.')), 'repeat_groups'),
    ('parameter_free_modules_not_listed', nullable('array', items={'type': 'string'}, description=(
        'Native backends: paths of parameter-free modules that were not given a block of their own (activation, dropout and '
        'similar); [] when none were skipped.')), 'parameters_unattributed'),
    ('runtime_requirements', nullable('object', additionalProperties=True, description=(
        'UNVERIFIED native documents caused by a missing runtime: {python, packages[]} the check needs (for example '
        'rfdetr==1.11.2). null otherwise.'),
        properties={'python': nullable('string'), 'packages': nullable('array', items={'type': 'string'})}),
     'parameter_free_modules_not_listed'),
    ('scale_source', nullable('string', enum=['yaml', 'file_name', 'file_name_suffix', 'first_scale_key', None], description=(
        'YAML documents of the registry and the user-YAML routes: where the model scale came from. yaml = the file names a '
        'valid scale; file_name = guessed from the file name like ultralytics; file_name_suffix = a trailing -<letter>; '
        'first_scale_key = the first key of scales (the parser fallback). null when the YAML has no scales.')),
     'runtime_requirements')]:
    put(SR, key, schema, after=after)

# observation: fx_basis, source_blob_sha1 (R-S2-3); fx_error never existed in the contract and is no longer emitted
OBS = SR['observation']['properties']
put(OBS, 'fx_basis', nullable('string', description=(
    'Native backends: why fx_traceable is false. torch.fx is not called; the constant is taken from the adapter inventory, not '
    're-measured by the check.')), after='fx_traceable')
put(OBS, 'source_blob_sha1', nullable('string', description=(
    'ETRI hybrid: git blob sha1 of the notebook the module source comes from (recorded, not recomputed by the check).')),
    after='dinov2_weights')

# edges[].evidence (R-S2-3)
put(SR['edges']['items']['properties'], 'evidence', nullable('object', additionalProperties=True, description=(
    'Native backends: why the edge exists. kind tensor_identity = the same tensor object left the source and entered the target '
    '(OBSERVED through hooks; tensors names the arguments, shape the tensor shape); kind code_fixed+shape = a flow fixed in the '
    'model code (citation names where) that the hooked shapes corroborate or contradict (corroboration is the explanation, '
    'corroborated the verdict).'),
    properties={'kind': nullable('string'), 'tensors': nullable('array', items={'type': 'string'}),
                'shape': nullable('array', items=nullable('integer')), 'citation': nullable('string'),
                'corroboration': nullable('string'), 'corroborated': nullable('boolean')}), after='connection_source')

# diagnostics[].code (R-S2-5)
SR['diagnostics']['items']['properties']['code']['description'] = (
    'e.g. SHAPE_MISMATCH, MERGE_INPUT_MISMATCH, HEAD_INPUT_INVALID, CLASS_COUNT_INVALID, INPUT_SIZE_INVALID, BLOCK_UNSUPPORTED, '
    'STRUCTURE_RUNTIME_UNAVAILABLE (UNVERIFIED: the CPU runtime or the offline weights are missing), STRUCTURE_CHECK_FAILED (a '
    'non-environment exception ended the build or forward; the check did not complete), STRUCTURE_CHECK_NOT_RECORDED, '
    'CLASS_COUNT_UNKNOWN (native candidate whose run dataset YAML could not be read, so no check ran), '
    'GENE_REPRESENTATION_NOT_APPLICABLE (UNVERIFIED: this model has no gene representation for the requested view; not a '
    'failure).')

# ---- blocks[] (R-S2-3, R-S2-1 note, R-S2-2) ----------------------------------------------------------------------------------
put(BLK, 'native_kind', nullable('string', enum=['module', 'buffer', None], description=(
    'Native backends: module = a torch module with a forward; buffer = a registered tensor or parameter table shown as a '
    'block (learned query tables); null for YAML blocks.')), after='native_path')
put(BLK, 'native_config', nullable('object', additionalProperties=True, description=(
    'Native backends (DECLARED): configuration values that belong to this block only; null when there are none.')),
    after='declared')
put(BLK, 'note', nullable('string', description='Native backends: a remark about how the block was observed; null when none.'),
    after='native_config')
RD = BLK['repeats_detail']['properties']
put(RD, 'declared_basis', nullable('string', description='Native backends: which configuration value declared is (for example dec_layers).'))
put(RD, 'observed_basis', nullable('string', description=(
    'Native backends: what observed counts (for example len(transformer.decoder.layers), Embedding.num_embeddings for a learned '
    'query table). Read it before treating observed as a layer count.')))
put(RD, 'group', nullable('string', description='Native backends: repeat_groups[].group this block is a unit of; null otherwise.'))
put(RD, 'unit_index', nullable('integer', description='Native backends: zero-based position of the block inside its group.'))
EFF = BLK['effective']['properties']
EFF['repeats_source']['description'] = (
    'Where the observed repeat count was read (.m, .blocks, sequential_wrapper, sequential, single_module, unobserved). Counts '
    'that fit none of these (ModuleList length, number of output feature maps, rows of a learned query table) are reported as null '
    'here and named in repeats_detail.observed_basis; the enum has no value for them in 1.4.1.')
put(EFF, 'calls', nullable('integer', description='Native backends: how many times the forward hook saw the module run during the reference forward.'),
    after='observed_at_failure')
put(EFF, 'output_shapes', nullable('array', items={'type': 'array', 'items': nullable('integer')}, description=(
    'Native backends: the exact tensor shapes of the first call, batch axis included (outputs strips the batch axis). RF-DETR '
    'heads carry a leading decoder-layer axis here.')), after='calls')
put(EFF, 'observed_children', nullable('array', description=(
    'Native backends: container modules (ModuleList/ModuleDict) have no forward; the first elements were hooked instead: '
    '[{path, module, calls, inputs, outputs}].'), items={'type': 'object', 'additionalProperties': True, 'properties': {
        'path': nullable('string'), 'module': nullable('string'), 'calls': nullable('integer'),
        'inputs': nullable('array', items={'type': 'array', 'items': nullable('integer')}),
        'outputs': nullable('array', items={'type': 'array', 'items': nullable('integer')})}}), after='output_shapes')
put(EFF, 'buffers', nullable('array', description='Native backends: for a buffer block, [{name, shape}] of the tensor.',
    items={'type': 'object', 'additionalProperties': True, 'properties': {
        'name': nullable('string'), 'shape': nullable('array', items=nullable('integer'))}}), after='observed_children')
put(EFF, 'tokens_out', nullable('integer', description=(
    'ETRI hybrid: token count of a block whose output is a token sequence [1, tokens, dim] (h_out/w_out stay null).')),
    after='buffers')
INP = EFF['inputs']['items']['properties']
INP['source']['description'] = (
    'Index of the source block; -1 = the model input image (same convention as the YAML from field). null when the tensor could '
    'not be attributed to a block.')
put(INP, 'source_path', nullable('string', description=(
    'Native backends: native path of the source module (model_input for the image). Present when the source is known.')),
    after='source')
put(INP, 'name', nullable('string', description='Native backends: the hooked argument name (arg0, or the keyword).'), after='source_path')
put(INP, 'shape', nullable('array', items=nullable('integer'), description='Native backends: the full tensor shape, batch axis included.'),
    after='name')

# ---- registry / user structure routes: statuses and stored-evidence rule (R-S2-4, R-S2-6; descriptions only) ----------------
PATHS = d['paths']
for route in ('/api/v1/registry/{model_id}/structure', '/api/v1/registry/{model_id}/structure-check'):
    verb = 'get' if route.endswith('/structure') else 'post'
    ok = PATHS[route][verb]['responses']['200']['content']['application/json']['schema']['allOf'][1]['properties']['evidence_source']
    ok['description'] = (
        'declared_configuration when no check was ever recorded (declaration only, status UNVERIFIED, diagnostic '
        'STRUCTURE_CHECK_NOT_RECORDED); saved_structure_check for the stored check of the model (GET); executed_structure_check for '
        'the document an explicit POST has just produced. Free string, see StructureReport.evidence_source.')
chk = PATHS['/api/v1/registry/{model_id}/structure-check']['post']
chk['description'] = (
    'Builds the model on CPU with the backend-specific observation (Ultralytics parse + forward hooks, or native forward hooks for '
    'RF-DETR and ETRI hybrid) and returns the document. Only a PASS or FAIL document that carries an observation (the build and '
    'forward really ran) is stored under <output_root>/registry_structure/<model_id>/; a static FAIL without observation (for '
    'example a native model asked for another resolution than its fixed one: FAIL INPUT_SIZE_INVALID) and an UNVERIFIED document '
    'are returned but never overwrite the recorded evidence. Statuses: 200 for PASS, FAIL and UNVERIFIED alike (the answer is in the '
    'status field); 404 MODEL_NOT_FOUND; 422 INPUT_SIZE_INVALID only for an input_size that is not an integer, below 32 or not a '
    'multiple of 32; 422 VALIDATION_FAILED for an unknown body field or a body that is not a JSON object.')
chk['responses']['422']['description'] = ('INPUT_SIZE_INVALID (input_size not an integer, < 32 or not a multiple of 32), VALIDATION_FAILED '
                                          '(unknown body field or body not a JSON object)')
usr = PATHS['/api/v1/structure-check']['post']
usr['description'] = (
    'model_path must be a YAML file (up to 1 MiB) inside the server allowlist (output roots, weights directory, ETRI_NAS root). '
    'The document is returned and not stored. Statuses: 200 for PASS, FAIL and UNVERIFIED alike; 422 for a rejected request: '
    'the path codes of the shared server path check (PATH_NOT_ABSOLUTE, PATH_PLACEHOLDER, PATH_TRAVERSAL, PATH_OUTSIDE_ALLOWLIST, '
    'PATH_NOT_FOUND, PATH_NOT_READABLE), MODEL_CONFIG_INVALID (not a YAML mapping, not .yaml/.yml, over 1 MiB, unparsable, class_count '
    'not a positive integer), INPUT_SIZE_INVALID (input_size not an integer, < 32 or not a multiple of 32), VALIDATION_FAILED '
    '(missing model_path, wrong type or unknown field).')
usr['responses']['422']['description'] = ('PATH_NOT_ABSOLUTE, PATH_PLACEHOLDER, PATH_TRAVERSAL, PATH_OUTSIDE_ALLOWLIST, PATH_NOT_FOUND, '
                                          'PATH_NOT_READABLE, MODEL_CONFIG_INVALID, INPUT_SIZE_INVALID, VALIDATION_FAILED')

# ---- GET /registry: documented top-level members and the DD-26 diagnostic -------------------------------------------------------
RL = S['RegistryList']['properties']
put(RL, 'source', nullable('string', description=(
    'Entry point the rows came from (mbnas_server.registry.load_registry) or, when it failed, the path of the example registry '
    '(then warning is set and every row is UNVERIFIED).')))
put(RL, 'warning', nullable('string', description='Why the example registry is served instead of the real one; null when the real registry loaded.'))
put(RL, 'registry_roots_skipped', nullable('array', description=(
    'DD-26: registry evidence roots (and their registry / evidence sub-directories) that exist but this server process cannot read '
    '(for example a root-owned directory on a non-root host): [{path, reason}]. Their evidence is ignored, so rows that depend on '
    'it stay UNVERIFIED while readable roots still verify theirs. [] when every root is readable.'),
    items={'type': 'object', 'additionalProperties': True, 'properties': {
        'path': {'type': 'string'}, 'reason': {'type': 'string'}}, 'required': ['path', 'reason']}))
S['RegistryEntry']['properties']['evidence']['description'] += (
    ' When a registry root was unreadable (DD-26), evidence.registry_roots_skipped = [{path, reason}] names it (present only then) and '
    'an UNVERIFIED row says so in reason.')

# ---- DD-36: explicit checkpoint on POST /training-runs mode=resume -----------------------------------------------------------
TR = S['TrainingRequest']
TR['properties']['checkpoint']['description'] = (
    'Default null; only meaningful for mode resume. Omitted or null: the ordinary run Resume path (the same checks as '
    'POST /runs/{run_id}/resume; a run paused before its first epoch has no full-state yet and is resumed from its start). A path: '
    'it must equal the latest training_full checkpoint this server recorded for the candidate; the candidate has no recorded '
    'training_full at all -> 409 EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED (an external file is never imported into a run); a '
    'recorded one that differs -> 409 RESUME_CHECKPOINT_MISMATCH.')
TR['description'] = replace_once(TR['description'],
    '(the checkpoint, if given, must be that path); a candidate without a recorded training_full state is refused with 409 '
    'EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED (no external full-state import).',
    '(a request without checkpoint takes the ordinary run Resume path; a checkpoint path, if given, must be that latest recorded '
    'path: 409 RESUME_CHECKPOINT_MISMATCH when it differs, 409 EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED when the candidate has no '
    'recorded training_full at all - no external full-state import).')
r409 = PATHS['/api/v1/training-runs']['post']['responses']['409']
r409['description'] = (
    'IDEMPOTENCY_CONFLICT, IDEMPOTENCY_KEY_MISMATCH, RESUME_RECIPE_MISMATCH, RESUME_CHECKPOINT_MISMATCH (mode resume, a checkpoint '
    'path was sent and differs from the latest recorded training_full), EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED (mode resume, a '
    'checkpoint path was sent and the candidate has no recorded training_full at all)')

# ---- x-status-vocabulary ---------------------------------------------------------------------------------------------------------
V = d['x-status-vocabulary']
assert V['structure_diagnostic_codes_added'] == ['GENE_REPRESENTATION_NOT_APPLICABLE', 'MERGE_INPUT_MISMATCH', 'HEAD_INPUT_INVALID']
V['structure_diagnostic_codes_added'] = V['structure_diagnostic_codes_added'] + ['CLASS_COUNT_UNKNOWN', 'STRUCTURE_CHECK_FAILED']
V['structure_diagnostic_codes_note'] += (
    ' CLASS_COUNT_UNKNOWN (1.4.1): a native candidate whose run dataset YAML cannot be read, so the check did not run (UNVERIFIED). '
    'STRUCTURE_CHECK_FAILED (1.4.1): a non-environment exception ended the build or forward (the YAML path uses it as well). '
    'CLASS_COUNT_INVALID, HEAD_INPUT_INVALID and SHAPE_MISMATCH are reused for native heads and edges.')
put(V, 'error_codes_note', (
    'POST /training-runs mode=resume (DD-36): a request without checkpoint is not a 409 for a missing full-state, it takes the '
    'ordinary run Resume path. EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED (409) is raised only when a checkpoint path was sent and the '
    'candidate has no recorded training_full; RESUME_CHECKPOINT_MISMATCH (409, existing since 1.3.x) when a path was sent and differs '
    'from the latest recorded one. FINAL_RETRAIN_NOT_APPLICABLE (409): POST /runs/{id}/final-retrain on a run whose resolved '
    'console.search.evaluation_enabled is false.'), after='error_codes_added')

CHANGES = json.loads(r'''[
 "Round 2: StructureReport documents the keys the native documents carry beyond CONTRACTS_v2 as optional nullable members: top level fixed_resolution, native_config, observed_scopes, repeat_groups, parameters_unattributed, parameter_free_modules_not_listed, runtime_requirements, scale_source (enum yaml|file_name|file_name_suffix|first_scale_key|null); observation.fx_basis and source_blob_sha1; edges[].evidence; blocks[].native_kind, native_config, note; repeats_detail.declared_basis, observed_basis, group, unit_index; effective.calls, output_shapes, observed_children, buffers, tokens_out; effective.inputs[].source_path, name, shape (source -1 = model input image). No enum value is added; repeats_source keeps its six values and the description says where other native counts are named (repeats_detail.observed_basis).",
 "Round 2: evidence_source stays a free string; its description lists the values the server writes (saved_structure_check, declared_configuration, executed_structure_check). The three registry/user structure routes document the statuses the server answers (200 for PASS, FAIL and UNVERIFIED; 422 INPUT_SIZE_INVALID only for a malformed input_size; 422 VALIDATION_FAILED for unknown/ill-typed body fields; the path codes of POST /structure-check) and that only a check that really ran is stored.",
 "Round 2: RegistryList documents source, warning and registry_roots_skipped ([{path, reason}] of unreadable registry roots, DD-26); RegistryEntry.evidence mentions evidence.registry_roots_skipped. x-status-vocabulary.structure_diagnostic_codes_added gains CLASS_COUNT_UNKNOWN and STRUCTURE_CHECK_FAILED.",
 "Round 2 (DD-36, wording only, no new code or field): TrainingRequest.checkpoint, the TrainingRequest description, the POST /training-runs 409 description and x-status-vocabulary.error_codes_note state that a resume request without checkpoint takes the ordinary run Resume path, that EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED needs a sent path and no recorded training_full, and that RESUME_CHECKPOINT_MISMATCH means a sent path differs from the latest recorded one."
]''')
d['x-contract-changelog'].insert(0, {'version': '1.4.1', 'date': '2026-10-22',
  'author': 'integration 2026-10-22 (design: claude-fable-5-1; implementation: claude-sonnet-5-5)', 'changes': CHANGES})

out = json.dumps(d, ensure_ascii=False, indent=1)
Path(p).write_text(out, encoding='utf-8')
print('contract', d['info']['version'])
if hashlib.sha256(raw).hexdigest() == SNAPSHOT_1_4_0_SHA256:
    got = hashlib.sha256(out.encode('utf-8')).hexdigest()
    if EXPECTED_1_4_1_SHA256 != '__FILL__':
        assert got == EXPECTED_1_4_1_SHA256, f'replay mismatch: {got} != {EXPECTED_1_4_1_SHA256}'
        print('replay ok: sha256', got)
    else:
        print('sha256 (fill into EXPECTED_1_4_1_SHA256):', got)
