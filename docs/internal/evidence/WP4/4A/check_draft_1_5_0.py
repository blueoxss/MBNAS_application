#!/usr/bin/env python3
"""WP-4A evidence: checks of the contract 1.5.0 draft (owner-side tool; reads the owner-only DTO allowlist analysis).

python -I -B check_draft_1_5_0.py [--write]   (run from anywhere; paths are resolved from this file)

1. allowlist mapping: every entry of docs/internal/analysis/DTO_ALLOWLIST_PROPOSAL.json is expanded into concrete
   member paths (" | " alternatives, ".x"/"...x" shorthands, {a,b} brace sets, {name} map keys, [] items, * wildcards,
   [type=X] event discriminators) and evaluated against the live 1.4.3 contract and the 1.5.0 draft:
   DECLARED (a declared property), OPEN (not declared, parent object open), DENIED (propertyNames deny list),
   CLOSED (not declared, parent closed), NON_SCHEMA (pseudo component, export member, note).
   Invariants: no OWNER_ONLY path is DECLARED/OPEN in 1.5.0 (except Archive*, kept by DD-A19); no PUBLIC /
   PUBLIC_MAPPED path that 1.4.3 allowed is excluded by 1.5.0 except the intended value mappings (INTENDED_DROPS).
2. structure: OpenAPI 3.1 root members, every $ref resolves, every schema object passes the JSON Schema 2020-12
   meta-schema (jsonschema), every pattern compiles, path template parameters are declared, operationIds unique,
   every response has a description, forbidden host-fact substrings absent.
3. behaviour: sample instances validated against 1.4.3 and 1.5.0 components (closure / removals / additions), the
   server-generated web fixtures (StructureReport documents, audit) validated against 1.5.0.
4. replay: the migrate script reproduces the draft byte for byte from the 1.4.3 snapshot (temporary copy).
With --write: writes CHANGES_1.5.0.md and check_draft_1_5_0.result.json next to this file.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, OrderedDict
from pathlib import Path

from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
LIVE = ROOT / 'mbnas_contracts/src/mbnas_contracts/openapi/openapi.json'
DRAFT = ROOT / 'mbnas_contracts/src/mbnas_contracts/openapi/drafts/openapi-1.5.0.draft.json'
HISTORY_143 = ROOT / 'docs/public/contracts/history/openapi-1.4.3.json'
MIGRATE = ROOT / 'scripts/dev/migrate_openapi_1.4.3_to_1.5.0.py'
ALLOWLIST = ROOT / 'docs/internal/analysis/DTO_ALLOWLIST_PROPOSAL.json'
WEB_TESTS = ROOT / 'mbnas_web/tests'
FORBIDDEN = ('/root/detection', '/data/mgjeong', '147.46.114.253', 'pc2300', 'mgjeong', 'MBNAS_server', 'etri_nas_root',
             'yolo_ga_runtime', 'MBNAS-CANARY')
NON_SCHEMA_COMPONENTS = {'_meta', 'ExportBundle', 'PublicIdentifiers'}
# PUBLIC / PUBLIC_MAPPED paths 1.5.0 excludes on purpose (value mappings named by WP-4A)
INTENDED_DROPS = {
    ('CandidateDetail', 'checkpoints[].path'): 'path -> checkpoint id + artifact_id (WP-4A list)',
    ('Run', 'resolved_protocol.resolution.paths.{}.realpath'): 'resolved protocol without realpath (WP-4A list)',
    ('CandidateDetail', 'audit.execution_configuration.path'): 'path -> audit.execution_configuration.artifact_id',
}
# OWNER_ONLY paths that 1.5.0 keeps on purpose
KEPT_OWNER_ONLY = {'ArchiveRun', 'ArchiveGeneration', 'ArchiveLineage', 'ArchiveLineageEntry', 'ArchiveCandLineage'}
R = '#/components/schemas/'
EXCLUDED = ('CLOSED', 'DENIED')
ALLOWED = ('DECLARED', 'OPEN')


# ----------------------------------------------------------------------------------------- allowlist path expansion
def tokens(path: str) -> list[str]:
    out, cur, depth = [], '', 0
    for ch in path:
        if ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
        if ch == '.' and depth == 0:
            out.append(cur)
            cur = ''
        else:
            cur += ch
    out.append(cur)
    return [t.strip() for t in out]


def expand_braces(toks: list[str]) -> list[list[str]]:
    results = [[]]
    for tok in toks:
        m = re.fullmatch(r'\{([^}]*)\}', tok)
        if m and ',' in m.group(1):
            names = [n.strip() for n in m.group(1).split(',')]
            results = [r + [n] for r in results for n in names]
        elif m:
            results = [r + ['{}'] for r in results]  # map key placeholder
        else:
            results = [r + [tok] for r in results]
    return results


def expand_entry(field_path: str) -> list[str] | None:
    """Concrete dotted paths of one allowlist key, or None when it is a note / non-schema member."""
    if field_path.startswith('('):
        return None
    alts = [a.strip() for a in field_path.split(' | ')]
    first = tokens(alts[0])
    prefix = first[:-1]
    out = []
    for alt in alts:
        if alt.startswith('...'):
            toks = prefix + tokens(alt[3:])
        elif alt.startswith('.'):
            toks = prefix + tokens(alt[1:])
        else:
            toks = tokens(alt)
        for combo in expand_braces(toks):
            out.append('.'.join(combo))
    return out


# --------------------------------------------------------------------------------------------- schema evaluation
class Doc:
    def __init__(self, doc: dict):
        self.doc = doc
        self.S = doc['components']['schemas']

    def deref(self, s):
        seen = 0
        while isinstance(s, dict) and '$ref' in s and s['$ref'].startswith(R):
            s = {**self.S[s['$ref'][len(R):]], **{k: v for k, v in s.items() if k != '$ref'}}
            seen += 1
            assert seen < 50
        return s

    def evaluate(self, schema, segs: list[str], disc=None) -> tuple[str, str]:
        """Status of the member path ``segs`` under ``schema`` (see module docstring)."""
        s = self.deref(schema)
        if not isinstance(s, dict):
            return ('OPEN', 'boolean schema') if s is not False else ('CLOSED', 'false schema')
        if not segs:
            return 'DECLARED', ''
        # AND parts: the node itself + allOf parts (+ if/then parts whose condition matches the discriminator)
        parts = [s]
        for part in s.get('allOf', []):
            part = self.deref(part)
            if 'if' in part:
                if disc is not None and self._matches(part['if'], disc):
                    parts.append(self.deref(part['then']))
            else:
                parts.append(part)
        results = [self._node(p, segs, disc) for p in parts]
        # each part: None = says nothing about members (non-object or empty)
        results = [r for r in results if r is not None]
        if not results:
            return 'OPEN', 'unconstrained'
        for r in results:
            if r[0] in EXCLUDED:
                return r
        for r in results:
            if r[0] == 'DECLARED':
                return r
        return results[0]

    @staticmethod
    def _matches(cond, disc):
        key, values = disc
        rule = cond.get('properties', {}).get(key, {})
        allowed = rule.get('enum') or ([rule['const']] if 'const' in rule else [])
        return bool(set(allowed) & set(values))

    def _branches(self, s):
        out = []
        for key in ('anyOf', 'oneOf'):
            for b in s.get(key, []):
                out.append(self.deref(b))
        return out

    def _node(self, s, segs, disc):
        branches = self._branches(s)
        if branches:  # OR over branches that can hold members
            results = []
            for b in branches:
                typ = b.get('type')
                types = typ if isinstance(typ, list) else [typ]
                if typ is not None and not ({'object', 'array'} & set(types)):
                    continue
                results.append(self.evaluate(b, segs, disc))
            if not results:
                return None
            for status in ('DECLARED', 'OPEN'):
                for r in results:
                    if r[0] == status:
                        return r
            return results[0]
        seg = segs[0]
        rest = segs[1:]
        sub_disc = None
        m = re.fullmatch(r'(.+?)\[(\w+)=([^\]]+)\]', seg)
        if m:
            seg, sub_disc = m.group(1), (m.group(2), m.group(3).split('|'))
        if seg.endswith('[]'):
            name = seg[:-2]
            res = self._member(s, name, [], None)
            if res[0] != 'DECLARED':
                return res
            child = self._child(s, name)
            child = self.deref(child)
            items = self._items(child)
            if items is None:
                return ('OPEN', f'{name} has no item schema')
            return self.evaluate(items, rest, None) if rest else ('DECLARED', '')
        if seg in ('{}', '*') or '*' in seg:
            if self._has_props_or_closed(s) is False and not s.get('properties') and 'additionalProperties' not in s:
                return None if s.get('type') not in ('object', ['object', 'null']) else ('OPEN', 'open map')
            ap = s.get('additionalProperties', True)
            if seg == '{}' or seg == '*':
                if isinstance(ap, dict):
                    return self.evaluate(ap, rest, None) if rest else ('DECLARED', 'map value')
                if ap is False and seg == '{}':
                    return ('CLOSED', 'closed map')
                # wildcard over declared members + open remainder
                results = [self.evaluate(v, rest, None) if rest else ('DECLARED', '')
                           for v in s.get('properties', {}).values()]
                if ap is not False:
                    results.append(('OPEN', 'wildcard over open object'))
                for status in ('DECLARED', 'OPEN'):
                    for r in results:
                        if r[0] == status:
                            return r
                return results[0] if results else ('CLOSED', 'no member')
            # name pattern such as *_hash / tensorrt*
            rx = re.compile('^' + re.escape(seg).replace('\\*', '.*') + '$')
            results = [self.evaluate(v, rest, None) if rest else ('DECLARED', '')
                       for k, v in s.get('properties', {}).items() if rx.match(k)]
            if ap is not False:
                results.append(('OPEN', 'pattern over open object'))
            for status in ('DECLARED', 'OPEN'):
                for r in results:
                    if r[0] == status:
                        return r
            return results[0] if results else ('CLOSED', 'no member')
        res = self._member(s, seg, rest, sub_disc)
        if res is None:
            return None
        if res[0] != 'DECLARED' or not rest and sub_disc is None:
            return res
        child = self._child(s, seg)
        if child is None:  # map value
            return self.evaluate(s['additionalProperties'], rest, sub_disc)
        if sub_disc is not None:
            # discriminated member: the parent's if/then parts constrain it; evaluate them all
            return self._discriminated(s, seg, rest, sub_disc)
        return self.evaluate(child, rest, None)

    def _discriminated(self, parent, seg, rest, disc):
        schemas = [parent['properties'][seg]]
        for part in parent.get('allOf', []):
            part = self.deref(part)
            if 'if' in part and self._matches(part['if'], disc):
                then = self.deref(part['then'])
                if seg in then.get('properties', {}):
                    schemas.append(then['properties'][seg])
        if not rest:
            return 'DECLARED', ''
        results = [self.evaluate(x, rest, None) for x in schemas]
        for r in results:
            if r[0] in EXCLUDED:
                return r
        for r in results:
            if r[0] == 'DECLARED':
                return r
        return results[0]

    @staticmethod
    def _has_props_or_closed(s):
        return bool(s.get('properties')) or s.get('additionalProperties') is False

    def _member(self, s, name, rest, disc):
        typ = s.get('type')
        types = typ if isinstance(typ, list) else [typ]
        if typ is not None and 'object' not in types:
            return None  # not an object: says nothing
        if name in s.get('properties', {}):
            return 'DECLARED', ''
        deny = (s.get('propertyNames') or {}).get('not', {}).get('enum', [])
        if name in deny:
            return 'DENIED', f'propertyNames deny {name}'
        ap = s.get('additionalProperties', True)
        if ap is False:
            return 'CLOSED', 'additionalProperties false'
        if isinstance(ap, dict):
            return 'DECLARED', 'map value'
        if typ is None and not s.get('properties') and 'propertyNames' not in s:
            return None
        return 'OPEN', 'undeclared member of an open object'

    @staticmethod
    def _child(s, name):
        return s.get('properties', {}).get(name)

    def _items(self, s):
        if 'items' in s:
            return s['items']
        for b in self._branches(s):
            if 'items' in b:
                return b['items']
        return None


def evaluate_path(doc: Doc, component: str, path: str) -> tuple[str, str]:
    if component not in doc.S:
        return 'NON_SCHEMA', 'no such component'
    if '/' in path or path.endswith(('.json', '.csv', '.jsonl', '.txt')) or path in ('README.txt', 'SHA256SUMS'):
        return 'NON_SCHEMA', 'export member'
    return doc.evaluate(doc.S[component], tokens_for_eval(path))


def tokens_for_eval(path: str) -> list[str]:
    return [t for t in tokens(path) if t != '']


# ------------------------------------------------------------------------------------------------ structural checks
def walk(node, path=''):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from walk(v, f'{path}/{k}')
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f'{path}/{i}')


SCHEMA_PARENTS = ('schema', 'items', 'additionalProperties', 'propertyNames', 'not', 'if', 'then', 'else',
                  'contains', 'unevaluatedProperties', 'unevaluatedItems')


def schema_objects(doc):
    """(pointer, schema) of every schema object of the document (components + inline path schemas)."""
    out = []

    def visit(s, ptr):
        if not isinstance(s, dict):
            return
        out.append((ptr, s))
        for key in ('properties', 'patternProperties', '$defs', 'dependentSchemas'):
            for k, v in (s.get(key) or {}).items():
                visit(v, f'{ptr}/{key}/{k}')
        for key in ('items', 'additionalProperties', 'propertyNames', 'not', 'if', 'then', 'else', 'contains'):
            if isinstance(s.get(key), dict):
                visit(s[key], f'{ptr}/{key}')
        for key in ('allOf', 'anyOf', 'oneOf', 'prefixItems'):
            for i, v in enumerate(s.get(key) or []):
                visit(v, f'{ptr}/{key}/{i}')

    for name, s in doc['components']['schemas'].items():
        visit(s, f'#/components/schemas/{name}')
    for p, item in doc['paths'].items():
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            base = f'#/paths/{p}/{method}'
            for i, prm in enumerate(op.get('parameters', [])):
                visit(prm.get('schema'), f'{base}/parameters/{i}/schema')
            for ct, media in (op.get('requestBody', {}).get('content') or {}).items():
                visit(media.get('schema'), f'{base}/requestBody/{ct}')
            for code, resp in op.get('responses', {}).items():
                for ct, media in (resp.get('content') or {}).items():
                    visit(media.get('schema'), f'{base}/responses/{code}/{ct}')
                for hn, hv in (resp.get('headers') or {}).items():
                    visit(hv.get('schema'), f'{base}/responses/{code}/headers/{hn}')
    return out


def structural(doc, raw: bytes):
    problems = []
    if not str(doc.get('openapi', '')).startswith('3.1'):
        problems.append('openapi is not 3.1.x')
    for key in ('info', 'paths', 'components'):
        if key not in doc:
            problems.append(f'missing root member {key}')
    for key in ('title', 'version'):
        if key not in doc.get('info', {}):
            problems.append(f'info.{key} missing')
    names = set(doc['components']['schemas'])
    refs = 0
    for ptr, node in walk(doc):
        if '$ref' in node and isinstance(node['$ref'], str):
            refs += 1
            ref = node['$ref']
            if not ref.startswith(R) or ref[len(R):] not in names:
                problems.append(f'unresolved $ref {ref} at {ptr}')
    meta = Draft202012Validator.META_SCHEMA
    checked = 0
    for ptr, s in schema_objects(doc):
        checked += 1
        errs = list(Draft202012Validator(meta).iter_errors(s))
        for e in errs[:3]:
            problems.append(f'meta-schema: {ptr}: {e.message[:200]}')
        if isinstance(s.get('pattern'), str):
            try:
                re.compile(s['pattern'])
            except re.error as exc:
                problems.append(f'pattern does not compile at {ptr}: {exc}')
        req, props = s.get('required'), s.get('properties')
        if isinstance(req, list) and len(set(req)) != len(req):
            problems.append(f'duplicate required at {ptr}')
        if isinstance(req, list) and s.get('additionalProperties') is False and isinstance(props, dict):
            missing = [r for r in req if r not in props]
            if missing:
                problems.append(f'required members not declared in a closed object at {ptr}: {missing}')
    ops = Counter()
    for p, item in doc['paths'].items():
        if not p.startswith('/api/v1/'):
            problems.append(f'path outside /api/v1: {p}')
        placeholders = set(re.findall(r'\{([^}]+)\}', p))
        for method, op in item.items():
            if 'operationId' in op:
                ops[op['operationId']] += 1
            declared = {prm['name'] for prm in op.get('parameters', []) if prm.get('in') == 'path'}
            if placeholders != declared:
                problems.append(f'{method.upper()} {p}: path parameters {sorted(declared)} != template {sorted(placeholders)}')
            for prm in op.get('parameters', []):
                if prm.get('in') == 'path' and prm.get('required') is not True:
                    problems.append(f'{method.upper()} {p}: path parameter {prm["name"]} not required')
            if not op.get('responses'):
                problems.append(f'{method.upper()} {p}: no responses')
            for code, resp in op.get('responses', {}).items():
                if not resp.get('description'):
                    problems.append(f'{method.upper()} {p} {code}: response without description')
    for op, n in ops.items():
        if n > 1:
            problems.append(f'duplicate operationId {op}')
    text = raw.decode('utf-8')
    for needle in FORBIDDEN:
        if needle in text:
            problems.append(f'forbidden substring {needle!r} present')
    return {'problems': problems, 'refs': refs, 'schema_objects_checked': checked, 'operation_ids': len(ops)}


# --------------------------------------------------------------------------------------------- behaviour samples
def validator(doc, name):
    return Draft202012Validator({'$ref': R + name, 'components': doc['components']})


def errors_of(doc, name, instance):
    return sorted(f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message[:160]}"
                  for e in validator(doc, name).iter_errors(instance))


SAMPLES = [  # (component, description, instance, expect_valid_143, expect_valid_150)
    ('Worker', 'runner row with pid / lease_expires_at / child_pid',
     {'id': 'runner-a', 'status': 'busy', 'device': 'cuda:0', 'pid': 123, 'lease_expires_at': '2026-10-27T00:00:00Z',
      'child_pid': 456}, True, False),
    ('Worker', '1.5.0 runner row (runner_id, device_id, runtime_manifest_status)',
     {'id': 'runner-a', 'status': 'busy', 'device': 'cuda:0', 'runner_id': 'runner-a', 'device_id': 'dev-1',
      'runtime_manifest_status': 'verified', 'loss': 0.5}, True, True),
    ('Worker', 'runtime_manifest_status outside the enum',
     {'id': 'r', 'status': 'idle', 'device': 'cpu', 'runtime_manifest_status': 'trusted'}, True, False),
    ('Generation', 'row with group_key',
     {'arm': 'GA', 'generation': 0, 'rung': 'L0', 'complete': False, 'representative_candidate_id': None,
      'comparison_group': 'g', 'group_key': 'GA|1|0|L0|g'}, True, False),
    ('Block', 'block with old_name / repair_reason / slot_kind',
     {'name': 'C2f', 'source': 'p', 'mutated': False, 'old_name': 'C3', 'repair_reason': 'x',
      'params': {'from': -1, 'repeats': 1, 'module': 'C2f', 'args': [64], 'slot_kind': 'feature'}}, True, False),
    ('Block', '1.5.0 block (no trace keys)',
     {'name': 'C2f', 'source': 'p', 'mutated': False, 'position': 3, 'source_kind': 'parent_a',
      'params': {'from': -1, 'repeats': 1, 'module': 'C2f', 'args': [64], 'scope': 'backbone', 'effective': None}},
     False, True),
    ('Block', 'source_kind repair', {'name': 'C2f', 'source': 'p', 'mutated': False, 'source_kind': 'repair'},
     False, False),
    ('GeneratedCandidate', 'generated candidate without training_status',
     {'id': 'c', 'candidate_id': 'c', 'run_id': 'r', 'name': 'n', 'family': 'f', 'evaluation_status': 'NOT_EVALUATED',
      'structure_status': 'PASS'}, True, False),
    ('GeneratedCandidate', 'generated candidate with training_status UNTRAINED',
     {'id': 'c', 'candidate_id': 'c', 'run_id': 'r', 'name': 'n', 'family': 'f', 'evaluation_status': 'NOT_EVALUATED',
      'structure_status': 'PASS', 'training_status': 'UNTRAINED'}, True, True),
    ('UploadResponse', 'gateway upload answer (mbnas_server.api.uploads)',
     {'upload_id': 'upl-' + '0' * 64, 'source_sha256': 'sha256:' + '0' * 64, 'bytes': 12}, None, True),
    ('UploadResponse', 'upload answer with a server path',
     {'upload_id': 'upl-' + '0' * 64, 'source_sha256': 'sha256:' + '0' * 64, 'bytes': 12,
      'server_path': '/srv/x.yaml'}, None, False),
    ('Error', 'gateway error with location and core_request_id',
     {'code': 'CORE_UNAVAILABLE', 'message': 'core not reachable', 'request_id': 'a', 'location': 'core',
      'core_request_id': 'b'}, True, True),
    ('Error', 'error with an unknown location', {'code': 'X', 'message': 'y', 'location': 'browser'}, True, False),
    ('ModelConfigRevision', 'original revision of an upload',
     {'revision_id': 'rev-1', 'source_sha256': 'sha256:' + 'a' * 64, 'canonical_hash': 'h', 'architecture_hash': None,
      'kind': 'original', 'created_at': '2026-10-27T00:00:00Z', 'parent_revision_id': None,
      'artifact_id': 'upl-' + 'a' * 64}, None, True),
    ('Event', 'log event with the rejected checkpoint path',
     {'seq': 1, 'type': 'log', 'run_id': 'r', 'payload': {'message': 'm', 'level': 'error', 'path': '/x/y.pt'}},
     True, False),
    ('Event', 'log event without path',
     {'seq': 1, 'type': 'log', 'run_id': 'r', 'payload': {'message': 'm', 'level': 'error', 'job_id': 'j',
                                                           'reason_code': 'CHECKPOINT_REPORT_REJECTED'}}, True, True),
    ('Health', 'allowlists with an extra (core) root',
     {'status': 'ok', 'capabilities': {}, 'versions': {}, 'devices': [], 'registry': [], 'commit_sha': 'x',
      'allowlists': {'dataset_roots': [], 'output_roots': [], 'core_output_root': ['/srv/core']}}, True, False),
    ('Health', 'capabilities.search_space with root / environment_lock with path',
     {'status': 'ok', 'capabilities': {'search_space': {'variant': 'etri_mixed', 'root': None}},
      'versions': {'environment_lock': {'present': True, 'path': '/srv/lock.json'}}, 'devices': [], 'registry': [],
      'commit_sha': 'x'}, True, False),
    ('Health', 'gateway health (core unreachable)',
     {'status': 'degraded', 'warnings': ['core: CORE_UNAVAILABLE'], 'capabilities': {}, 'versions': {}, 'devices': [],
      'registry': [], 'commit_sha': 'unknown', 'presets': [], 'worker_count': 0, 'core': 'unreachable',
      'allowlists': {'dataset_roots': [], 'output_roots': [], 'archive_roots': []},
      'gateway': {'status': 'ok', 'version': '1.4.3', 'core_url_configured': True, 'web_dist': True,
                  'store_writable': True}, 'request_id': 'r'}, True, True),
    ('Protocol', 'resolved protocol with realpath and etri_root',
     {'schema_version': 'x', 'console': {'paths': {'output_root': '/o'},
                                         'search': {'search_space_resolved': {'variant': 'etri_mixed',
                                                                            'etri_root': None}}},
      'resolution': {'paths': {'train_root': {'requested': '/d/train', 'realpath': '/mnt/d/train'}}}}, True, False),
    ('Protocol', '1.5.0 resolved protocol',
     {'schema_version': 'x', 'console': {'paths': {'output_root': '/o'},
                                         'search': {'search_space_resolved': {'variant': 'etri_mixed'}}},
      'resolution': {'canonicalization': 'c', 'hash_excluded': ['resolution.paths'], 'validated_at':
                     '2026-10-27T00:00:00Z', 'validator': 'v', 'paths': {'train_root': {'requested': '/d/train'}}}},
     True, True),
    ('RegistryList', 'registry list with registry_roots_skipped',
     {'registry': [], 'source': 'live', 'warning': None, 'registry_roots_skipped': []}, True, False),
    ('RegistryEntry', 'evidence with probe_file / weights.path / build.trace',
     {'id': 'yolo11n', 'family': 'YOLO11', 'status': 'AVAILABLE', 'capabilities': {},
      'evidence': {'probe_file': '/srv/p.json', 'weights': {'path': '/srv/w', 'sha256': 'x'},
                   'build': {'ok': True, 'trace': 'Traceback'}}}, True, False),
    ('RegistryEntry', 'evidence without owner-only members',
     {'id': 'yolo11n', 'family': 'YOLO11', 'status': 'AVAILABLE', 'capabilities': {},
      'evidence': {'version': '8.4.173', 'weights': {'sha256': 'x', 'filename': 'yolo11n.pt'},
                   'build': {'ok': True}}}, True, True),
    ('StructureReport', 'structure document whose lineage carries crossover / prior_draw',
     {'schema': 'mbnas-structure/2', 'status': 'PASS', 'lineage': {'operation': 'crossover', 'crossover': {'mask': [1]},
                                                                  'prior_draw': {}}}, True, False),
    ('StructureReport', 'structure document with a block repair trace',
     {'schema': 'mbnas-structure/2', 'status': 'PASS', 'blocks': [{'position': 0, 'repair_reason': 'x'}]}, True, False),
    ('ValidateResponse', 'capabilities.search_space with etri_root',
     {'valid': True, 'errors': [], 'protocol_hash': 'h', 'resolved': {},
      'capabilities': {'search_space': {'variant': 'etri_mixed', 'etri_root': None}}}, True, False),
    ('GeneratedCandidate', 'training_status outside UNTRAINED|TRAINED',
     {'id': 'c', 'candidate_id': 'c', 'run_id': 'r', 'name': 'n', 'family': 'f', 'evaluation_status': 'NOT_EVALUATED',
      'structure_status': 'PASS', 'training_status': 'UNEVALUATED'}, True, False),
    ('Run', 'run with budget.policy', {'id': 'r', 'status': 'RUNNING', 'protocol_hash': 'h',
                                       'budget': {'policy': 'admit_if_remaining_ge_estimate', 'unit': 's'}},
     True, False),
    ('Run', 'run with last_seq and final_retrain_runs',
     {'id': 'r', 'status': 'COMPLETED', 'protocol_hash': 'h', 'budget': {'unit': 's'}, 'last_seq': 4,
      'final_retrain_runs': [{'id': 'c', 'status': 'QUEUED', 'created_at': '2026-10-27T00:00:00Z',
                              'finalist_count': 2}]}, True, True),
]


def candidate_detail_samples():
    base = {'candidate_id': 'c', 'run_id': 'r', 'name': 'n', 'arm': 'GA', 'family': 'f', 'architecture_hash': 'h',
            'blocks': [], 'parents': [], 'evaluations': [], 'candidate_status': 'EVALUATED'}
    return [
        ('CandidateDetail', 'checkpoint row with host path',
         {**base, 'checkpoints': [{'id': 'ck-1', 'kind': 'training_full', 'epoch': 1, 'sha256': 's', 'state': 'PRESENT',
                                   'path': '/srv/run/ckpt.pt'}]}, True, False),
        ('CandidateDetail', 'checkpoint row with artifact_id',
         {**base, 'checkpoints': [{'id': 'ck-1', 'kind': 'training_full', 'epoch': 1, 'sha256': 's', 'state': 'PRESENT',
                                   'artifact_id': None, 'prune': None}]}, True, True),
        ('CandidateDetail', 'lineage with crossover / mutation_events / space_hash',
         {**base, 'lineage': {'operation': 'crossover', 'crossover': {'cut_index': 3}, 'mutation_events': [],
                              'search_space': {'version': 'v', 'variant': 'x', 'space_hash': 'h'}}}, True, False),
        ('CandidateDetail', 'public lineage',
         {**base, 'lineage': {'operation': 'crossover', 'method': 'GA', 'parents': ['a', 'b'],
                              'template': 'yolo11n', 'search_space': {'version': 'v', 'variant': 'x'}}}, True, True),
        ('CandidateDetail', 'audit with proposal_job_id / admission estimate',
         {**base, 'audit': {'orchestration': {'proposal_job_id': 'job-1', 'rungs': {
             'L0': {'state': 'DONE', 'admission': {'admitted': True, 'estimate_s': 5.0}}}}}}, True, False),
        ('CandidateDetail', 'public audit subset',
         {**base, 'training_status': 'TRAINED', 'audit': {'origin': 'search', 'orchestration': {'index': 1, 'rungs': {
             'L0': {'state': 'DONE', 'admission': {'admitted': True, 'cap_s': 10.0, 'billed_s': 5.0,
                                                   'remaining_s': 5.0}}}}}}, True, True),
        ('CandidateEvaluation', 'row with raw_metrics weights_only_path / rung_result',
         {**evaluation_row(), 'raw_metrics': {'ap_50': 0.5, 'weights_only_path': '/srv/w.pt', 'rung_result': {}}},
         True, False),
        ('CandidateEvaluation', 'row with public raw_metrics',
         {**evaluation_row(), 'raw_metrics': {'ap_50': 0.5, 'device_used': 'cpu', 'latency': {'manifest': {'name': 'g'}}},
          'training_status': 'TRAINED'}, True, True),
    ]


def evaluation_row():
    return {'id': 'e', 'candidate_id': 'c', 'name': 'n', 'family': 'f', 'arm': 'GA', 'generation': 0, 'rung': 'L0',
            'epochs': 1, 'status': 'QUEUED', 'ap': None, 'recall': None, 'latency_ms': None, 'size_mb': None,
            'fitness': None, 'device': 'cuda:0', 'precision': 'FP16', 'latency_source': 'pending', 'parents': [],
            'blocks': [], 'architecture_path': None, 'checkpoint_path': None, 'artifacts': [], 'objective_hash': 'o',
            'evaluation_hash': 'pending', 'split_hash': None, 'latency_scope': 'pending', 'comparison_group': 'g'}


def behaviour(old, new):
    rows = []
    ok = True
    for comp, desc, inst, exp_old, exp_new in SAMPLES + candidate_detail_samples():
        e_old = errors_of(old, comp, inst) if comp in old['components']['schemas'] else None
        e_new = errors_of(new, comp, inst)
        v_old = None if e_old is None else not e_old
        v_new = not e_new
        good = (exp_old is None or v_old == exp_old) and v_new == exp_new
        ok &= good
        rows.append({'component': comp, 'case': desc, 'valid_1_4_3': v_old, 'valid_1_5_0': v_new,
                     'expected_1_4_3': exp_old, 'expected_1_5_0': exp_new, 'as_expected': good,
                     'errors_1_5_0': e_new[:6]})
    return ok, rows


def fixtures(old, new):
    out = []
    for name in ('structure_v2_server_fixture.json', 'structure_native_server_fixture.json',
                 'block_catalog_server_fixture.json'):
        data = json.loads((WEB_TESTS / name).read_text())
        for key, document in data['documents'].items():
            out.append({'fixture': f'{name}:documents.{key}', 'component': 'StructureReport',
                        'errors_1_4_3': errors_of(old, 'StructureReport', document)[:8],
                        'errors_1_5_0': errors_of(new, 'StructureReport', document)[:8]})
    audit = json.loads((WEB_TESTS / 'fixtures_audit_etri_mixed.json').read_text())
    cd = {'candidate_id': 'c', 'run_id': 'r', 'name': 'n', 'arm': 'GA', 'family': 'f', 'architecture_hash': 'h',
          'blocks': [], 'parents': [], 'evaluations': [], 'candidate_status': 'EVALUATED', 'audit': audit}
    out.append({'fixture': 'fixtures_audit_etri_mixed.json (as CandidateDetail.audit)', 'component': 'CandidateDetail',
                'errors_1_4_3': errors_of(old, 'CandidateDetail', cd)[:8],
                'errors_1_5_0': errors_of(new, 'CandidateDetail', cd)[:8]})
    return out


# ------------------------------------------------------------------------------------------------------ diff
def component_diff(old, new):
    so, sn = old['components']['schemas'], new['components']['schemas']
    rows = []
    for name in sorted(set(so) | set(sn)):
        a, b = so.get(name), sn.get(name)
        if a == b:
            continue
        if a is None:
            rows.append({'component': name, 'change': 'added', 'properties': sorted((b.get('properties') or {}))})
            continue
        pa, pb = set((a.get('properties') or {})), set((b.get('properties') or {}))
        rows.append({'component': name, 'change': 'changed', 'removed': sorted(pa - pb), 'added': sorted(pb - pa),
                     'required_removed': sorted(set(a.get('required', [])) - set(b.get('required', []))),
                     'required_added': sorted(set(b.get('required', [])) - set(a.get('required', []))),
                     'additionalProperties': [a.get('additionalProperties', '<absent>'),
                                              b.get('additionalProperties', '<absent>')],
                     'nested_changes': sorted(k for k in pa & pb if a['properties'][k] != b['properties'][k])})
    return rows


def path_diff(old, new):
    po, pn = old['paths'], new['paths']
    added = sorted(f'{m.upper()} {p}' for p in pn for m in pn[p] if p not in po or m not in po[p])
    removed = sorted(f'{m.upper()} {p}' for p in po for m in po[p] if p not in pn or m not in pn[p])
    changed = sorted(f'{m.upper()} {p}' for p in po for m in po[p]
                     if p in pn and m in pn[p] and po[p][m] != pn[p][m])
    return {'added': added, 'removed': removed, 'changed': changed}


# ------------------------------------------------------------------------------------------------------ mapping
def mapping(old_doc, new_doc, allow):
    old, new = Doc(old_doc), Doc(new_doc)
    entries = []
    for comp, fields in allow.items():
        for key, entry in fields.items():
            cls = entry.get('class')
            paths = None if comp in NON_SCHEMA_COMPONENTS else expand_entry(key)
            if paths is None:
                entries.append({'component': comp, 'entry': key, 'class': cls, 'path': None,
                                'status_1_4_3': 'NON_SCHEMA', 'status_1_5_0': 'NON_SCHEMA',
                                'note': 'pseudo component / note / export member'})
                continue
            for p in paths:
                s_old = evaluate_path(old, comp, p)
                s_new = evaluate_path(new, comp, p)
                entries.append({'component': comp, 'entry': key, 'class': cls, 'path': p, 'status_1_4_3': s_old[0],
                                'status_1_5_0': s_new[0], 'detail_1_5_0': s_new[1]})
    violations = []
    for e in entries:
        if e['path'] is None:
            continue
        if e['class'] == 'OWNER_ONLY' and e['status_1_5_0'] in ALLOWED and e['component'] not in KEPT_OWNER_ONLY:
            violations.append({**e, 'why': 'OWNER_ONLY member still allowed in 1.5.0'})
        if e['class'] in ('PUBLIC', 'PUBLIC_MAPPED') and e['status_1_4_3'] in ALLOWED and \
                e['status_1_5_0'] in EXCLUDED and (e['component'], e['path']) not in INTENDED_DROPS:
            violations.append({**e, 'why': 'PUBLIC member allowed by 1.4.3 is excluded by 1.5.0'})
    return entries, violations


def replay():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / 'openapi.json'
        shutil.copy(HISTORY_143, work)
        done = subprocess.run([sys.executable, '-I', '-B', str(MIGRATE), str(work)], capture_output=True, text=True)
        same = done.returncode == 0 and work.read_bytes() == DRAFT.read_bytes()
        return {'returncode': done.returncode, 'stdout': done.stdout.strip()[-400:], 'stderr': done.stderr.strip()[-800:],
                'byte_identical_to_draft': same}


def declared_paths(doc):
    """(component, dotted member path) of every declared property (inline schemas; $ref targets are their own rows)."""
    out = set()

    def rec(s, comp, segs):
        if not isinstance(s, dict) or '$ref' in s:
            return
        for k, v in (s.get('properties') or {}).items():
            out.add((comp, '.'.join(segs + [k])))
            rec(v, comp, segs + [k])
        if isinstance(s.get('items'), dict) and segs:
            rec(s['items'], comp, segs[:-1] + [segs[-1] + '[]'])
        if isinstance(s.get('additionalProperties'), dict):
            rec(s['additionalProperties'], comp, segs + ['{}'])
        for key in ('anyOf', 'oneOf', 'allOf'):
            for b in s.get(key) or []:
                rec(b, comp, segs)
                if isinstance(b, dict) and isinstance(b.get('then'), dict):
                    rec(b['then'], comp, segs)

    for name, s in doc['components']['schemas'].items():
        rec(s, name, [])
    return out


REMOVED_SOURCE = {  # declared 1.4.3 member -> (allowlist component :: entry, class, basis)
    ('Block', 'old_name'): ('Block :: old_name', 'OWNER_ONLY', 'DD-A19 D1'),
    ('Block', 'before'): ('Block :: before | after', 'OWNER_ONLY', 'DD-A19 D1'),
    ('Block', 'after'): ('Block :: before | after', 'OWNER_ONLY', 'DD-A19 D1'),
    ('Block', 'repair_reason'): ('Block :: repair_reason', 'OWNER_ONLY', 'DD-A19 D1'),
    ('CandidateDetail', 'lineage.crossover'): ('CandidateDetail :: lineage.crossover', 'OWNER_ONLY', 'DD-A19 D2'),
    ('CandidateDetail', 'lineage.mutations'): ('(contract-only name of the mutation trace; server emits mutation_events, '
                                               'OWNER_ONLY)', '-', 'DD-A19 D2 public lineage subset'),
    ('CandidateDetail', 'lineage.repairs'): ('(contract-only name of the repair trace; server emits repair_events, '
                                             'OWNER_ONLY)', '-', 'DD-A19 D2 public lineage subset'),
    ('Generation', 'group_key'): ('Generation :: group_key', 'OWNER_ONLY', 'DD-A19 D14'),
    ('Protocol', 'console.search.search_space_resolved.etri_root'): (
        'Run :: resolved_protocol.console.search.search_space_resolved.etri_root', 'OWNER_ONLY', 'DD-A19 D3, WP-4A list'),
    ('RegistryList', 'registry_roots_skipped'): ('RegistryList :: registry_roots_skipped', 'OWNER_ONLY', 'DD-26 diagnostic'),
    ('RegistryList', 'registry_roots_skipped[].path'): ('RegistryList :: registry_roots_skipped', 'OWNER_ONLY', 'DD-26'),
    ('RegistryList', 'registry_roots_skipped[].reason'): ('RegistryList :: registry_roots_skipped', 'OWNER_ONLY', 'DD-26'),
    ('Worker', 'lease_expires_at'): ('Worker :: lease_expires_at', 'OWNER_ONLY', 'DD-A19'),
    ('Worker', 'pid'): ('Worker :: pid', 'OWNER_ONLY', 'DD-A19'),
}

MECHANISM = {'CLOSED': 'excluded: parent closed (additionalProperties false)',
             'DENIED': 'excluded: propertyNames deny list (parent stays open)',
             'DECLARED': 'kept (declared)', 'OPEN': 'still allowed (open parent)', 'NON_SCHEMA': 'not in the schema'}


def md_table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    for r in rows:
        out.append('| ' + ' | '.join(str(x).replace('|', '\\|') for x in r) + ' |')
    return '\n'.join(out)


STATIC_TAIL = """
## 6. pydantic 거울(mbnas_contracts/schemas/*) 변경 목록 — WP-4B/4C 몫(이번에 수정하지 않음)

`tests/owner/unit/test_schemas_contract.py`는 component마다 모델 존재(`CONTRACT_MODELS`), 계약 required ⊆ 모델 required,
계약 최상위 properties ⊆ 모델 properties, enum 동일, additionalProperties 정책을 검사한다. 승격 시 필요한 변경:

| file:class | field / 설정 | 변경 |
|---|---|---|
| schemas/records.py:Block | `old_name`(required), `before`, `after`, `repair_reason` | 삭제; `params` 닫힌 모델(from, repeats, module, args, scope, effective); `model_config` extra=forbid |
| schemas/enums.py:BlockSourceKind | `REPAIR` | 삭제(계약 Block.source_kind enum과 동일 검사, test_schemas_contract 131행) |
| schemas/records.py:Generation | `group_key` | 삭제; extra=forbid |
| schemas/records.py:Worker | `pid`, `lease_expires_at` | 삭제 |
| schemas/records.py:Worker | `runner_id`, `device_id`, `runtime_manifest_status`, `loss`, `train_loss`, `images_per_second`, `samples_per_second`, `throughput`, `epoch_seconds`, `batch_seconds`, `native_metrics`, `losses` | 추가(선택); `device_uuid` deprecated; extra=forbid |
| schemas/enums.py (신규) | `RuntimeManifestStatus` | verified / mismatch / unknown StrEnum + `CONTRACT_MODELS` 등록(문자열 enum component라 `test_required_fields_exist_and_are_required`의 `js["properties"]` 가정을 비-object component에서 건너뛰게 조정 필요) |
| schemas/records.py:CandidateEvaluation | `training_status`, `immutable`, `source_kind`, `training_mode`, `performance_status` | 추가(선택); `raw_metrics` 허용목록 모델 또는 dict 유지(계약 검증으로 강제); extra=forbid |
| schemas/records.py:GeneratedCandidate | `training_status`(required, UNTRAINED\\|TRAINED), `generation`, `parents`, `architecture_hash`, `search_seed`, `training_seed`, `seed`, `attempt`, `blocks`, `architecture_path` | 추가; extra=forbid |
| schemas/records.py:CandidateLineage | `crossover`, `mutations`, `repairs` | 삭제; `method`, `parents`, `parent_evaluation_ids`, `template_detail`, `search_space`, `source_*`, `rank`, `selection_rule`, `init`, `recipe`, `source_template`, `training_source`, `source_architecture_hash` 추가; extra=forbid |
| schemas/records.py:CandidateDetail | `training_status`, `current_rung`, `search_seed`, `training_seed`, `generation` | 추가(선택); `checkpoints` 항목 모델(path 없음, `artifact_id`), `audit` 공개 부분집합 모델 또는 dict; extra=forbid |
| schemas/records.py:Run | `last_seq`, `final_retrain_runs` | 추가; extra=forbid |
| schemas/records.py:Event | `created_at` | 추가; log payload 허용목록(선택: `_EVENT_PAYLOAD_MODELS[LOG]`); extra=forbid |
| schemas/records.py:StructureReport | `model_id`, `sandbox` | 추가; `lineage` 공개 lineage 모델; extra=forbid |
| schemas/records.py:Artifact, Snapshot, BlockCatalog | — | extra=forbid |
| schemas/api.py:Health | `warnings`, `source_tree`, `core`, `gateway`, `request_id` | 추가; extra=forbid; `Allowlists` extra=forbid |
| schemas/api.py:RegistryList | `registry_roots_skipped` | 삭제(owner 진단 → /core/v1/owner/*); extra=forbid |
| schemas/api.py:RegistryEntry, ValidateResponse, PresetSummary(→Preset), PresetList, RunList | — | extra=forbid |
| schemas/api.py:RunCreated | `protocol_hash`, `created`, `kind` | 추가; extra=forbid |
| schemas/api.py:Error | `location`(gateway\\|core\\|runner\\|store = `errors.ErrorLocation`), `core_request_id` | 추가; extra=forbid |
| schemas/api.py:TrainingSource | `upload_id`, `model_yaml_text`, `source_sha256`, `model_name` | 추가 + user 규칙(정확히 하나: upload_id / model_yaml_text+source_sha256 / model_path; 새 필드는 user 전용) — core `training_runs.selection.TrainingSource`는 upload_id 외 3개를 이미 가짐 |
| schemas/api.py:TrainingRunCreated | `source` | 응답용 binding 모델(kind, run_id, candidate_id, baseline_id, model_path, source_sha256, model_name). 현재 core `POST /training-runs`는 `contract_json`을 거치지 않고 `req.source.binding_dump()`를 그대로 JSONResponse로 보내므로(거울 검증·`project_public` 모두 우회) 업로드 학습 응답의 source_sha256/model_name이 1.4.3 계약(닫힌 source)에는 위반이었다 — 1.5.0이 선언; WP-4B는 이 응답도 투영 경로로 보낼 것 |
| schemas/protocol.py:Protocol | `resolution` | 추가(계약 최상위 property 1:1 검사 대상) |
| schemas/protocol.py:BaselineSelection | `name`, `source` | 추가(선택) |
| schemas/api.py (신규) | `UploadResponse`, `ModelConfigRevision`, `ModelConfigRevisionList` | 닫힌 모델 + `CONTRACT_MODELS` 등록 |

## 7. 교차 패키지 요청

- WP-4B(core): `x-public-projection` 규칙대로 투영 함수(콘솔 응답·SSE·export 공통); validator가 새 검증부터 `search_space_resolved.etri_root`·`resolution.paths.*.realpath`를 쓰지 않게 하고 `mbnas_contracts/protocol_hash.py` `RESOLUTION_HASH_EXCLUDED`에 `resolution.paths` 추가(WP-4 §1.2; contracts 파일이지만 openapi/** 밖이라 미수정); 저장된 1.4.x resolved protocol의 null etri_root·realpath는 투영에서 제거(저장 행·기존 hash 불변); `training_status` 계산; runner_id/device_id 발급; `RegistryList.source`·`capabilities.registry_source` 라벨화; Block repair 접기; checkpoint `artifact_id`; config_revisions 테이블과 두 GET 경로; 사용자 모델 block 행(StructureReport 유래 키)은 닫힌 Block에서 module/from/args가 params 밖이라 빠짐 → params로 옮겨 투영할지 결정 필요.
- WP-3B/5B(gateway): `/runs/{id}/candidates/{cid}/config-revisions`·`.../{revision_id}/yaml` 중계 라우트(바이트 그대로, `X-Artifact-Sha256`); Health 병합 키(core, gateway, request_id)는 계약에 선언됨.
- WP-4C(tests): test_schemas_contract 갱신(additionalProperties 정책: Run·CandidateEvaluation·RegistryEntry 닫힘, BlockSourceKind, registry_roots_skipped 543-551행, 신규 4 component), test_contract_history에 1.5.0 이력·전용 검사 추가, canary 투영 테스트.
- WP-4D/6C(web): `npm run gen:types` 재생(생성 자체는 exit 0 확인), contract.test.ts fixture 갱신(Worker pid, Generation group_key, Block old_name 등 1.5.0에서 거부), CandidateView의 old_name/before/after/repair 열 제거.
- WP-7(scripts/dev/check_import_closure.py 소유자): 승격 후 `DATA_EXCEPTIONS`의 openapi.json `MBNAS_server` 예외 삭제(1.5.0 info.description에 없음, 초안 forbidden 0건 확인); 패키저는 `openapi/drafts/`를 공개 ZIP에 넣을지 결정(내용은 공개 계약 초안, host fact 0건).
- docs/public/contracts/history/HISTORY.json 소유자: 승격 시 패키지 HISTORY.json과 note 정정문·1.5.0 항목 동기화.

## 8. 하지 않은 것 / 확인 못 함

- live `openapi.json` 교체, pydantic 거울·core·web 수정, `docs/public/contracts/history/` 쓰기: 지시에 따라 하지 않음.
- WP-4 §1.5 export public 프로파일 파라미터, §1.6 openapi_runner.json 이력 등록, Health.devices·device_resolved uuid 치환 스키마화, CandidateEvaluation 경로 멤버의 id 필드 추가: DD-A38 최소형 범위 밖으로 보고 설명문 규칙만 둠.
- owner/public 테스트 스위트를 1.5.0 초안으로 돌린 영향 측정: 확인 못 함(계약 경로가 패키지 데이터로 고정되어 있어 교체 없이는 불가).
- openapi-spec-validator: runtime/venv·wheelhouse에 없음 → 미사용; 대신 Redocly lint(음성 대조 포함)와 2020-12 meta-schema 검사.
- `audit`/`raw_metrics`/log payload 허용목록은 1.4.1 기준 분석 + 현재 코드 grep으로 보강했으나 모든 생산 경로를 실행으로 확인하지는 않음(투영이 알려지지 않은 공개 키를 떨어뜨릴 수 있음).
"""

def write_changes_md(result, old_doc, new_doc):
    s = result['mapping_summary']
    own = [e for e in result['mapping'] if e['class'] == 'OWNER_ONLY']
    own_paths = [e for e in own if e['path'] is not None]
    own_non = [e for e in own if e['path'] is None]
    decl_143 = [e for e in own_paths if e['status_1_4_3'] == 'DECLARED']
    decl_143_proj = [e for e in decl_143 if e['component'] not in KEPT_OWNER_ONLY]
    a, b = declared_paths(old_doc), declared_paths(new_doc)
    removed = sorted(a - b)
    added = sorted(b - a)
    per_comp_added = Counter(c for c, _ in added)
    top_added = sorted((c, p) for c, p in added if '.' not in p and '[' not in p)
    mapped = [e for e in result['mapping'] if e['class'] == 'PUBLIC_MAPPED' and e['status_1_5_0'] in EXCLUDED]
    L = []
    w = L.append
    w('# 계약 1.5.0 초안 — 1.4.3 대비 변경 보고 (WP-4A)')
    w('')
    w('이 파일은 `check_draft_1_5_0.py --write`가 생성한다(표·수치는 실행 결과). 판정 근거: '
      '`check_draft_1_5_0.result.json`.')
    w('')
    w(md_table(['항목', '값'], [
        ['상태', 'DRAFT — live `openapi.json`은 1.4.3 그대로(교체 금지 지시)'],
        ['초안', f"`{result['draft']}` sha256 `{result['draft_sha256']}`"],
        ['기준(live 1.4.3)', f"`mbnas_contracts/src/mbnas_contracts/openapi/openapi.json` sha256 `{result['live_sha256']}`"],
        ['1.4.3 보존본', '`docs/public/contracts/history/openapi-1.4.3.json` (live와 byte 동일, HISTORY versions 마지막 항목)'],
        ['생성기', '`scripts/dev/migrate_openapi_1.4.3_to_1.5.0.py` (표준 라이브러리만; 1.4.3 스냅샷에서 실행 시 결과 sha256을 assert)'],
        ['재생', '`python -I -B scripts/dev/migrate_openapi_1.4.3_to_1.5.0.py <1.4.3 사본> [OUT]` → ' +
         ('byte 동일 확인' if result['replay']['byte_identical_to_draft'] else '불일치')],
        ['HISTORY', '`openapi/HISTORY.json`의 `drafts[0]`(status `draft`); `versions`는 1.4.3에서 끝남(기존 이력 테스트 불변)'],
    ]))
    w('')
    w('## 1. "OWNER_ONLY 969개" 수치 정정')
    w('')
    cnt = result['allowlist_counts']
    w(f"`DTO_ALLOWLIST_PROPOSAL.json`의 969는 **전체 항목 수**다: PUBLIC {cnt['PUBLIC']} / PUBLIC_MAPPED "
      f"{cnt['PUBLIC_MAPPED']} / OWNER_ONLY {cnt['OWNER_ONLY']} (`_meta` 의사 항목 5개 포함; md 3행과 일치). "
      f"OWNER_ONLY는 {cnt['OWNER_ONLY']}개 항목이고, 그중 {len(own_non)}개는 스키마 경로가 아니다(`_meta` 범례 1, "
      f"ExportBundle `(not exported) jobs…` 1, PublicIdentifiers 2). 나머지 {s['OWNER_ONLY']['allowlist_entries'] - len(own_non)}개 "
      f"항목은 ` | `·`.x`·`{{a,b}}` 표기를 풀면 **{len(own_paths)}개 구체 경로**가 된다.")
    w('')
    w(md_table(['구분', '경로 수', '내용'], [
        ['1.4.3에서 선언된 스키마 키', len(decl_143),
         f"투영 대상 {len(decl_143_proj)}개 + Archive* {len(decl_143) - len(decl_143_proj)}개"],
        ['1.4.3에서 스키마 키가 아님(열린 객체 아래 서버 추가 키)', s['OWNER_ONLY']['status_1_4_3'].get('OPEN', 0),
         'raw_metrics.*, lineage.*, audit.*, evidence.*, Worker.child_pid, log payload path 등'],
        ['스키마에 전혀 없음(의사 항목)', len(own_non), '_meta / ExportBundle / PublicIdentifiers'],
    ]))
    w('')
    st = s['OWNER_ONLY']['status_1_5_0']
    w(f"1.5.0 결과: 닫힌 부모로 배제 {st.get('CLOSED', 0)}, propertyNames 거부 {st.get('DENIED', 0)}, "
      f"유지 {st.get('DECLARED', 0)}(Archive* — DD-A19가 아카이브의 mutation/driver 문서 유지를 결정), "
      f"여전히 허용 {st.get('OPEN', 0)}. 검사기 위반 {len(result['mapping_violations'])}건.")
    w('')
    def outcome(e):
        if e['status_1_4_3'] == 'DECLARED' and e['status_1_5_0'] in EXCLUDED:
            return 'removed from the schema; ' + MECHANISM[e['status_1_5_0']]
        if e['component'] in KEPT_OWNER_ONLY:
            return 'kept (DD-A19 archive)'
        return MECHANISM[e['status_1_5_0']]

    w(md_table(['component', 'member path', '1.4.3', '1.5.0'],
               [[e['component'], f"`{e['path']}`", e['status_1_4_3'], outcome(e)] for e in own_paths]))
    w('')
    w('스키마 밖 OWNER_ONLY 항목: ' + '; '.join(f"{e['component']} :: `{e['entry']}`" for e in own_non))
    w('')
    w('## 2. 제거된 선언 멤버 (1.4.3에 선언, 1.5.0에 없음)')
    w('')
    w(md_table(['component', 'member', 'allowlist 출처', 'class', '근거'],
               [[c, f'`{p}`', *REMOVED_SOURCE.get((c, p), ('?', '?', '?'))] for c, p in removed]))
    w('')
    w('추가로 값 매핑(PUBLIC_MAPPED) 중 WP-4A 목록 항목은 스키마에서 배제했다(의도된 배제, 검사기 INTENDED_DROPS):')
    w('')
    w(md_table(['component', 'path', '대체'], [[e['component'], f"`{e['path']}`",
                                                INTENDED_DROPS.get((e['component'], e['path']), '?')] for e in mapped]))
    w('')
    w('## 3. 추가')
    w('')
    pd = result['path_diff']
    w('경로: ' + ', '.join(f'`{p}`' for p in pd['added']) + '. 변경된 기존 경로: ' +
      ', '.join(f'`{p}`' for p in pd['changed']) + ' (upload_id/model_yaml_text/source_sha256/model_name, 404/409/422/507 코드).')
    w('')
    new_comps = [r['component'] for r in result['component_diff'] if r['change'] == 'added']
    w('새 component: ' + ', '.join(f'`{c}`' for c in new_comps) + '.')
    w('')
    w(f'선언 멤버 경로 추가 {len(added)}개(중첩 포함; component별: ' +
      ', '.join(f'{c} {n}' for c, n in sorted(per_comp_added.items())) + '). 최상위 추가 멤버:')
    w('')
    w(md_table(['component', 'member'], [[c, f'`{p}`'] for c, p in top_added]))
    w('')
    w('`Preset`의 추가 6개는 allOf → 평탄화(PresetSummary 멤버 + protocol; 의미 동일)이고, 나머지 추가는 닫힌 객체가 '
      '기존 PUBLIC 멤버를 잃지 않도록 선언한 것(서버가 이미 보내던 키)과 WP-4A 목록의 additive 항목이다.')
    w('')
    w('## 4. additionalProperties:false')
    w('')
    w('투영 대상 component(최상위 닫힘, `x-public-projection.projected_components`): ' +
      ', '.join(f'`{c}`' for c in new_doc['x-public-projection']['projected_components']) + '.')
    w('')
    w('중첩 닫힘: Block.params, CandidateEvaluation.raw_metrics, CandidateDetail.lineage/audit(하위 config_estimate, '
      'proposal, generation, orchestration(+rungs.*, admission, final, training_seed_policy), execution_configuration, '
      'structure_history[]), CandidateDetail.checkpoints[](prune, deleted[]), StructureReport.lineage/sandbox, '
      'Health.allowlists/capabilities.search_space/versions.environment_lock/source_tree/core/gateway, '
      'ValidateResponse.capabilities.search_space(+probe), Event log payload, Run.final_retrain_runs[], '
      'Protocol.console.search.search_space_resolved, Protocol.resolution(+paths.{}). propertyNames 거부 목록(열린 채 유지): '
      'RegistryEntry.evidence(+config_ref/weights/build/pretrained_load/source), StructureReport.blocks[], Run.budget, '
      'raw_metrics.externalised_detail. 투영 비대상(열린 채): Protocol(사용자 입력), Archive*(gateway 로컬 아카이브, DD-A19), '
      '요청 스키마(이미 닫힘).')
    w('')
    w('## 5. 검증 결과')
    w('')
    sb = result['structural_1_5_0']
    w(md_table(['검사', '결과'], [
        ['allowlist 매핑 불변식(OWNER_ONLY 미허용 / PUBLIC 미배제)', f"위반 {len(result['mapping_violations'])}"],
        ['구조 자가검사(루트 멤버, $ref 해소, 2020-12 meta-schema, pattern, 경로 파라미터, operationId, 응답 description, 금지 문자열)',
         f"문제 {len(sb['problems'])} (schema 객체 {sb['schema_objects_checked']}개, $ref {sb['refs']}개); 1.4.3 기준선 문제 "
         f"{len(result['structural_1_4_3']['problems'])}: {'; '.join(result['structural_1_4_3']['problems'])}"],
        ['행동 표본(닫힘/제거/추가가 실제로 검증기에서 작동)', f"{sum(r['as_expected'] for r in result['behaviour'])}/{len(result['behaviour'])} 기대대로"],
        ['서버 생성 web fixture(StructureReport 문서 12)', f"1.4.3에서 유효한 문서는 1.5.0에서도 유효: "
         f"{all(not f['errors_1_5_0'] for f in result['fixtures'] if f['component'] == 'StructureReport' and not f['errors_1_4_3'])}"],
        ['audit fixture(ETRI mixed)', '1.5.0 오류 = OWNER_ONLY 키만(config_estimate.rules/selection_policy, etri_audit_model)'],
        ['재생(byte 동일)', str(result['replay']['byte_identical_to_draft'])],
    ]))
    w('')
    w('외부 검증(보고서 본문에 명령과 결과): Redocly openapi-core 1.34.20 lint(struct 등) 0 error/0 warning — 음성 대조(고의 손상본) '
      '4 error 검출; openapi-typescript 7.13.0 생성 exit 0; 생성 타입 + 사용 프로브 tsc 5.9.3 --strict exit 0(제거 멤버는 '
      '@ts-expect-error로 부재 확인). openapi-spec-validator는 runtime/venv·wheelhouse에 없음 → 미사용(확인 못 함).')
    w('')
    (HERE / 'CHANGES_1.5.0.md').write_text('\n'.join(L) + '\n' + STATIC_TAIL, encoding='utf-8')

def main(argv):
    raw_new = DRAFT.read_bytes()
    old_doc = json.loads(LIVE.read_text())
    new_doc = json.loads(raw_new)
    allow = json.loads(ALLOWLIST.read_text())
    entries, violations = mapping(old_doc, new_doc, allow)
    struct_old = structural(old_doc, LIVE.read_bytes())
    struct_new = structural(new_doc, raw_new)
    beh_ok, beh = behaviour(old_doc, new_doc)
    fx = fixtures(old_doc, new_doc)
    rep = replay()
    result = OrderedDict(
        draft=str(DRAFT.relative_to(ROOT)), draft_sha256=hashlib.sha256(raw_new).hexdigest(),
        live_sha256=hashlib.sha256(LIVE.read_bytes()).hexdigest(), live_version=old_doc['info']['version'],
        draft_version=new_doc['info']['version'],
        allowlist_counts=dict(Counter(e.get('class') for c in allow.values() for e in c.values())),
        allowlist_entries=sum(len(c) for c in allow.values()),
        mapping_summary=summary(entries), mapping_violations=violations,
        structural_1_4_3=struct_old, structural_1_5_0=struct_new,
        behaviour_ok=beh_ok, behaviour=beh, fixtures=fx, replay=rep,
        component_diff=component_diff(old_doc, new_doc), path_diff=path_diff(old_doc, new_doc),
        mapping=entries)
    ok = not violations and not struct_new['problems'] and beh_ok and rep['byte_identical_to_draft']
    result['ok'] = ok
    if '--write' in argv:
        (HERE / 'check_draft_1_5_0.result.json').write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n')
        write_changes_md(result, old_doc, new_doc)
    print(json.dumps({k: result[k] for k in ('draft_sha256', 'live_sha256', 'allowlist_entries', 'allowlist_counts',
                                             'mapping_summary', 'behaviour_ok', 'ok')}, indent=1, ensure_ascii=False))
    print('violations:', len(violations), 'structural problems 1.5.0:', len(struct_new['problems']),
          '1.4.3:', len(struct_old['problems']), 'replay:', rep['byte_identical_to_draft'])
    for v in violations[:40]:
        print('  VIOLATION', v['component'], v['path'], v['class'], v['status_1_4_3'], '->', v['status_1_5_0'], v['why'])
    for p in struct_new['problems'][:40]:
        print('  STRUCT', p)
    for b in beh:
        if not b['as_expected']:
            print('  BEHAVIOUR', b['component'], b['case'], b['valid_1_4_3'], b['valid_1_5_0'], b['errors_1_5_0'][:3])
    return 0 if ok else 1


def summary(entries):
    out = {}
    for cls in ('OWNER_ONLY', 'PUBLIC_MAPPED', 'PUBLIC'):
        sel = [e for e in entries if e['class'] == cls]
        out[cls] = {'allowlist_entries': len({(e['component'], e['entry']) for e in sel}),
                    'concrete_paths': len([e for e in sel if e['path'] is not None]),
                    'non_schema_entries': len([e for e in sel if e['path'] is None]),
                    'status_1_4_3': dict(Counter(e['status_1_4_3'] for e in sel if e['path'] is not None)),
                    'status_1_5_0': dict(Counter(e['status_1_5_0'] for e in sel if e['path'] is not None))}
    return out


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
