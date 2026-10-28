"""Contract 1.4.1 -> 1.4.2 (integration round 3, 2026-10-23; design claude-fable-5-1, implementation claude-sonnet-5-5). Additive.

No existing path, required member, status code or enum value is removed or changed in meaning. Replaying this script on
openapi-1.4.1.json yields openapi-1.4.2.json (= openapi.json) byte for byte; the script asserts the sha256 of its result when
it is run on the 1.4.1 snapshot.

Applied (WP-S6 requests R1, WP-S7 request R2, WP-S8 request 1):
  * Block design-origin catalog (DD-38, CONTRACTS_v2 section 1.4): new path GET /structure/block-catalog (static table), new
    components BlockCatalogEntry / BlockCatalogTable / BlockCatalog / BlockCatalogSummary, optional StructureReport.catalog_summary
    and optional blocks[].catalog (documents saved before 1.4.2 have neither key and stay valid).
  * Campaign archive adapter (DD-39, CONTRACTS_v2 section 4): optional ArchiveRoot.kind and the optional campaign keys of
    ArchiveRunSummary / ArchiveRun (campaign_id, campaign_arm, space, method, seed, pair, pair_done, queue_state,
    campaign_arms_total, arm_status, progress, result) with the nested components ArchiveCampaignCandidates /
    ArchiveCampaignProgress / ArchiveCampaignBestTerminal / ArchiveCampaignResult. Plain runs keep their exact shape (no key).
  * Wording only: POST /registry/{model_id}/structure-check (description, 422 description) and
    StructureReport.fixed_resolution say that a native model asked for another fixed resolution is a 422 INPUT_SIZE_INVALID
    (no document, nothing stored) on the registry route, and a FAIL document only on the candidate route / adapter level.
Not applied (see evidence_impl/WPG0C/SUMMARY.md): the Literal on Protocol.console.search.duplicate_scope (it would turn the
validator answer FIELD_INVALID into a request-level 422 and contradict the recorded decision), the /health allowlist entry for
campaign roots (server change, optional).

Usage: python migrate_1.4.1_to_1.4.2.py [path-to-copy-of-openapi-1.4.1.json]   (rewrites the given file in place)
"""
import hashlib, json, sys
from pathlib import Path

p = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / 'openapi.json')
raw = Path(p).read_bytes()
SNAPSHOT_1_4_1_SHA256 = 'd6f1ac2a9a40b4646e626616ba1975e76507728f2423c4ebb35ed4b27469a993'
EXPECTED_1_4_2_SHA256 = '9a17ae5dd7c3df0f28e1e149a73692e4cf183a124fb0fea407aea4233d707704'
d = json.loads(raw.decode('utf-8')); S = d['components']['schemas']
assert d['info']['version'] == '1.4.1', d['info']['version']
d['info']['version'] = '1.4.2'


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


def opt(schema, **kw):
    """pydantic-style optional member of the archive components: anyOf [schema, null]."""
    out = {'anyOf': [schema, {'type': 'null'}]}
    out.update(kw)
    return out


def replace_once(text, old, new):
    assert text.count(old) == 1, (text.count(old), old)
    return text.replace(old, new)


REF = '#/components/schemas/'

# ---- 1. block design-origin catalog (WP-S6 R1) -----------------------------------------------------------------------------------
FAMILY_DESC = ("Design-origin family (YOLOv5 ... YOLO26, RT-DETR, PP-HGNet, YOLO-World, YOLOE, DINOv2, LW-DETR, 'ETRI mixed operator pool', "
               "'ETRI controlled hybrid', 'PyTorch torch.nn', ...); null = unknown (출처 미상), never a guess.")
entry_props = {
    'origin_family': nullable('string', description=FAMILY_DESC),
    'introduced_in': nullable('string', description='Version/venue that introduced the block; non-null only together with `reference`; null = unknown.'),
    'role': nullable('string', description='One-line role (Korean); null when the module is not catalogued.'),
    'reference': nullable('string', description='Paper / docs URL, or an ETRI repository path; null when none is known.'),
    'notes': nullable('string', description="Evidence and the explicit word 'unknown' for what could not be established."),
}
BLOCK_CATALOG = {
    'type': 'object', 'additionalProperties': True,
    'description': ('blocks[].catalog (DD-38, CONTRACTS_v2 1.4): the design origin of one block, copied from the static table served by '
                    'GET /structure/block-catalog. Every key is present; every value may be null (unknown, never estimated).'),
    'required': ['catalog_key', 'origin_family', 'introduced_in', 'role', 'reference', 'notes', 'source_path'],
    'properties': {
        'catalog_key': nullable('string', description=("Key in GET /structure/block-catalog (an `entries` module name, or '<backend>:<class>' in "
                                                       '`native_entries`); null when the block is not catalogued.')),
        **entry_props,
        'source_path': nullable('string', description=(
            'File of the class inside the installed package, relative to site-packages. Filled only by documents an explicit check '
            '(POST structure-check routes) produced; null on declared-only GET documents, for uncatalogued modules and for classes '
            'outside site-packages (ETRI mixed blocks).')),
    },
}
BLOCK_CATALOG_SUMMARY = {
    'type': 'object', 'additionalProperties': False, 'required': ['by_origin_family', 'unknown'],
    'description': ('Blocks per known origin family and blocks without one (sum = number of blocks). A declaration-only native document '
                    'without blocks carries {by_origin_family: {}, unknown: 0}.'),
    'properties': {'by_origin_family': {'type': 'object', 'additionalProperties': {'type': 'integer', 'minimum': 1}},
                   'unknown': {'type': 'integer', 'minimum': 0}},
}
BLOCK_CATALOG_ENTRY = {'type': 'object', 'additionalProperties': False, 'required': list(entry_props), 'properties': entry_props,
                       'description': 'One row of the static block design-origin table.'}
BLOCK_CATALOG_TABLE = {
    'type': 'object', 'additionalProperties': False, 'required': ['schema', 'entries', 'native_entries', 'families'],
    'description': ('Static block design-origin table (search/block_catalog.py). Data only: nothing is imported or built, and '
                    '`source_path` is not part of it.'),
    'properties': {
        'schema': {'type': 'string', 'const': 'mbnas-block-catalog/1'},
        'entries': {'type': 'object', 'additionalProperties': {'$ref': REF + 'BlockCatalogEntry'},
                    'description': 'YAML-backend operators and torch.nn classes by module name (`nn.` prefix stripped).'},
        'native_entries': {'type': 'object', 'additionalProperties': {'$ref': REF + 'BlockCatalogEntry'},
                           'description': "Native-backend modules by '<backend>:<class>' (for example rfdetr:LWDETR)."},
        'families': {'type': 'array', 'items': {'type': 'string'}, 'description': 'The origin families that occur in the table.'}},
}

path = {'get': {
    'operationId': 'getStructureBlockCatalog',
    'summary': 'Static block design-origin table (no import, no model)',
    'description': ("Module name -> {origin_family, introduced_in, role, reference, notes}; `native_entries` are keyed '<backend>:<class>'. "
                    'Data only: nothing is imported or built, and `source_path` is not part of it (it exists only on documents an explicit '
                    'structure check produced). The web uses it instead of a hard-coded table.'),
    'responses': {'200': {'description': 'The static catalog', 'content': {'application/json': {'schema': {'$ref': REF + 'BlockCatalogTable'}}}},
                  '401': {'description': 'Authentication required'}, '403': {'description': 'Not authorized'}}}}
put(d['paths'], '/api/v1/structure/block-catalog', path, after='/api/v1/structure-check')

put(S, 'BlockCatalogEntry', BLOCK_CATALOG_ENTRY, after='StructureReport')
put(S, 'BlockCatalogTable', BLOCK_CATALOG_TABLE, after='BlockCatalogEntry')
put(S, 'BlockCatalog', BLOCK_CATALOG, after='BlockCatalogTable')
put(S, 'BlockCatalogSummary', BLOCK_CATALOG_SUMMARY, after='BlockCatalog')

SR = S['StructureReport']['properties']
put(SR, 'catalog_summary', {'anyOf': [{'$ref': REF + 'BlockCatalogSummary'}, {'type': 'null'}], 'description': (
    'Design-origin distribution of the blocks (DD-38): by_origin_family counts the blocks per known family, unknown the blocks whose '
    'blocks[].catalog.origin_family is null. Absent on documents saved before contract 1.4.2.')}, after='blocks')
BLK = SR['blocks']['items']['properties']
put(BLK, 'catalog', {'anyOf': [{'$ref': REF + 'BlockCatalog'}, {'type': 'null'}], 'description': (
    'Design origin of the block (DD-38): origin_family, introduced_in, role, reference, notes from the static catalog; source_path only '
    'on documents an explicit check produced. Absent on documents saved before contract 1.4.2 (absent is not "unknown").')},
    after='section_basis')

# ---- 2. campaign archive adapter (WP-S7 R2) --------------------------------------------------------------------------------------
ROOT = S['ArchiveRoot']['properties']
put(ROOT, 'kind', {'type': 'string', 'description': (
    'Only on campaign roots (MBNAS_ARCHIVE_CAMPAIGN_ROOTS): "campaign". Plain roots have no such key (their response is unchanged).')})

S_INT, S_STR, S_NUM, S_BOOL = {'type': 'integer'}, {'type': 'string'}, {'type': 'number'}, {'type': 'boolean'}
cand_props = {
    'total': opt(S_INT), 'ok': opt(S_INT), 'eval_fail': opt(S_INT), 'degenerate': opt(S_INT), 'cache_hit': opt(S_INT),
    'by_status': opt({'type': 'object', 'additionalProperties': {'type': 'integer'}}),
}
CAMPAIGN_CANDIDATES = {
    'type': 'object', 'additionalProperties': True, 'required': list(cand_props), 'properties': cand_props,
    'description': ('Candidate counts of a campaign arm from the ledger rows of kind cand (ledger/eval_timing.jsonl): total rows, status ok / '
                    'eval_fail / degenerate, rows with cache_hit true, and every status with its count.')}
prog_props = {
    'generations_seen': opt(S_INT, description='Distinct generations in the ledger; null without a ledger.'),
    'last_generation': opt(S_INT), 'candidates': opt({'$ref': REF + 'ArchiveCampaignCandidates'}),
    'last_eval_at': opt(S_STR, description='UTC ISO time (...Z) of the last candidate evaluation in the ledger.'),
    'last_event_at': opt(S_STR, description='UTC ISO time (...Z) of the last ledger event of any kind.'),
    'ledger_rows': opt(S_INT), 'cost_s': opt(S_NUM), 'cost_search_s': opt(S_NUM),
    'search_budget_s': opt(S_NUM), 'arm_budget_s': opt(S_NUM),
    'source': opt(S_STR, description='"ledger/eval_timing.jsonl" when a ledger was read, otherwise null.'),
}
CAMPAIGN_PROGRESS = {
    'type': 'object', 'additionalProperties': True, 'required': list(prog_props), 'properties': prog_props,
    'description': ('Ledger-derived progress of a campaign arm. A value comes only from ledger/eval_timing.jsonl and '
                    'ledger/cost_ledger.json (arm_result.json once the arm ended); a missing ledger gives null, never 0.')}
bt_props = {'candidate_id': opt(S_STR), 'val_map50_95': opt(S_NUM), 'small_recall': opt(S_NUM), 'latency_v2': opt(S_NUM),
            'params_deploy': opt(S_NUM)}
CAMPAIGN_BEST_TERMINAL = {
    'type': 'object', 'additionalProperties': True, 'required': list(bt_props), 'properties': bt_props,
    'description': 'Best terminal (final-tier) result of a finished arm, from arm_result.json best_terminal.'}
res_props = {
    'stop_reason': opt(S_STR), 'final_stop_reason': opt(S_STR), 'n_eval': opt(S_INT), 'terminal_count': opt(S_INT),
    'flags': opt({'type': 'array', 'items': {}}), 'cost_s': opt(S_NUM),
    'best_terminal': opt({'$ref': REF + 'ArchiveCampaignBestTerminal'}),
}
CAMPAIGN_RESULT = {
    'type': 'object', 'additionalProperties': True, 'required': list(res_props), 'properties': res_props,
    'description': ('arm_result.json of a finished campaign arm (the key exists only when that file was read). stop_reason, '
                    'final_stop_reason, n_eval, terminal_count and flags are the file values as written by the campaign runner.')}
put(S, 'ArchiveCampaignCandidates', CAMPAIGN_CANDIDATES, after='ArchiveHas')
put(S, 'ArchiveCampaignProgress', CAMPAIGN_PROGRESS, after='ArchiveCampaignCandidates')
put(S, 'ArchiveCampaignBestTerminal', CAMPAIGN_BEST_TERMINAL, after='ArchiveCampaignProgress')
put(S, 'ArchiveCampaignResult', CAMPAIGN_RESULT, after='ArchiveCampaignBestTerminal')

CAMPAIGN_KEYS = [
    ('campaign_id', dict(S_STR, description='campaign.json campaign_id (the directory name when the file is unreadable). Campaign arms only.')),
    ('campaign_arm', dict(S_STR, description='"<space>/<method>/<seed_dir>", for example S2/ga/seed_42. Campaign arms only.')),
    ('space', dict(S_STR, description='Search space directory name (arms/<space>/...). Campaign arms only.')),
    ('method', dict(S_STR, description='Search method directory name (ga | random). Campaign arms only.')),
    ('seed', opt(S_INT, description='seed_N -> N; null when the directory is not named seed_N. Campaign arms only.')),
    ('pair', opt(S_INT, description='queue.json arms[].k: GA and Random of one pair share the value. Campaign arms only.')),
    ('pair_done', opt(S_BOOL, description='Whether queue.json pair_done lists the pair; null without queue.json. Campaign arms only.')),
    ('queue_state', opt(S_STR, description='queue.json arms[].state (done | running | queued ...). Campaign arms only.')),
    ('campaign_arms_total', opt(S_INT, description='Length of queue.json arms, waiting arms without a directory included. Campaign arms only.')),
    ('arm_status', opt(S_STR, description=('arm_result.json status verbatim (ARM_COMPLETED, NO_TERMINAL_RESULT, ARM_INTERRUPTED, BLOCKED_<reason>); '
                                           'null while there is no result file. Campaign arms only.'))),
    ('progress', {'$ref': REF + 'ArchiveCampaignProgress'}),
    ('result', {'$ref': REF + 'ArchiveCampaignResult'}),
]
for comp in ('ArchiveRunSummary', 'ArchiveRun'):
    props = S[comp]['properties']
    last = 'has'
    for key, schema in CAMPAIGN_KEYS:
        put(props, key, dict(schema), after=last)
        last = key
    # none of them is required: a plain run has no campaign key
    assert not set(k for k, _ in CAMPAIGN_KEYS) & set(S[comp]['required'])

lst = d['paths']['/api/v1/archive/runs']['get']
lst['description'] = lst['description'] + (
    ' Directories named in MBNAS_ARCHIVE_CAMPAIGN_ROOTS (12-search campaigns) add one read-only run per arm (family visdrone-campaign, run id '
    '<campaign_id>__<space>__<method>__<seed_dir>) with the optional campaign_* keys; their root entry carries kind "campaign". '
    'Without that setting the response is exactly as before.')

# ---- 3. wording: native fixed resolution is a 422 on the registry route (WP-S8 request 1) ----------------------------------------
PATHS = d['paths']
chk = PATHS['/api/v1/registry/{model_id}/structure-check']['post']
chk['description'] = replace_once(
    chk['description'],
    'a static FAIL without observation (for example a native model asked for another resolution than its fixed one: FAIL '
    'INPUT_SIZE_INVALID) and an UNVERIFIED document are returned but never overwrite the recorded evidence.',
    'a static FAIL without observation and an UNVERIFIED document are returned but never overwrite the recorded evidence.')
chk['description'] = replace_once(
    chk['description'],
    '422 INPUT_SIZE_INVALID only for an input_size that is not an integer, below 32 or not a multiple of 32; ',
    '422 INPUT_SIZE_INVALID for an input_size that is not an integer, below 32 or not a multiple of 32 and, for a native model '
    '(RF-DETR, ETRI hybrid), for a multiple of 32 that differs from the fixed resolution of that model (decided from the static '
    'declaration before anything is built: no document is returned and nothing is stored); ')
chk['responses']['422']['description'] = replace_once(
    chk['responses']['422']['description'],
    'INPUT_SIZE_INVALID (input_size not an integer, < 32 or not a multiple of 32), ',
    'INPUT_SIZE_INVALID (input_size not an integer, < 32, not a multiple of 32, or - native models - different from the fixed resolution '
    'of the model), ')
fr = SR['fixed_resolution']
fr['description'] = replace_once(
    fr['description'],
    'A check at another size is a FAIL document with INPUT_SIZE_INVALID. Absent for YAML documents.',
    'POST /registry/{model_id}/structure-check answers another size with 422 INPUT_SIZE_INVALID (no document); the candidate '
    'structure-check route and adapter-level checks still return a FAIL document with the diagnostic INPUT_SIZE_INVALID. Absent '
    'for YAML documents.')

# ---- changelog ---------------------------------------------------------------------------------------------------------------------
CHANGES = json.loads(r'''[
 "Round 3 (DD-38): new path GET /api/v1/structure/block-catalog (bearer; the static block design-origin table {schema mbnas-block-catalog/1, entries, native_entries, families}; nothing is imported or built), new components BlockCatalogEntry, BlockCatalogTable, BlockCatalog, BlockCatalogSummary, and the optional members StructureReport.catalog_summary and StructureReport.blocks[].catalog ({catalog_key, origin_family, introduced_in, role, reference, notes, source_path}; source_path only on documents an explicit check produced; documents saved before 1.4.2 have neither key and stay valid).",
 "Round 3 (DD-39): campaign archive adapter keys, all optional so plain runs keep their exact shape: ArchiveRoot.kind (\"campaign\" on campaign roots only); ArchiveRunSummary and ArchiveRun campaign_id, campaign_arm, space, method, seed, pair, pair_done, queue_state, campaign_arms_total, arm_status, progress, result; new components ArchiveCampaignCandidates, ArchiveCampaignProgress, ArchiveCampaignBestTerminal, ArchiveCampaignResult. GET /archive/runs description mentions MBNAS_ARCHIVE_CAMPAIGN_ROOTS.",
 "Round 3 (wording only, DD-41 follow-up of WP-S8): POST /registry/{model_id}/structure-check and its 422 description, and StructureReport.fixed_resolution, now say what the server does: a native model (RF-DETR, ETRI hybrid) asked for a multiple of 32 other than its fixed resolution is a 422 INPUT_SIZE_INVALID without a document; the FAIL document with INPUT_SIZE_INVALID remains the answer of the candidate structure-check route and of adapter-level checks. No status code, field or enum value changes."
]''')
d['x-contract-changelog'].insert(0, {'version': '1.4.2', 'date': '2026-10-23',
  'author': 'integration 2026-10-23 (design: claude-fable-5-1; implementation: claude-sonnet-5-5)', 'changes': CHANGES})

out = json.dumps(d, ensure_ascii=False, indent=1)
Path(p).write_text(out, encoding='utf-8')
print('contract', d['info']['version'])
if hashlib.sha256(raw).hexdigest() == SNAPSHOT_1_4_1_SHA256:
    got = hashlib.sha256(out.encode('utf-8')).hexdigest()
    if EXPECTED_1_4_2_SHA256 != '__FILL__':
        assert got == EXPECTED_1_4_2_SHA256, f'replay mismatch: {got} != {EXPECTED_1_4_2_SHA256}'
        print('replay ok: sha256', got)
    else:
        print('sha256 (fill into EXPECTED_1_4_2_SHA256):', got)
