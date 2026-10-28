"""Dataset-free generation retains ordinary validation freshness and safe public metadata."""
from harness import auth


def test_stale_generation_validation_is_conflict_not_missing_dataset_error(client, services, tmp_roots, monkeypatch):
    protocol = {'schema_version': 'nas-policy/1', 'console': {
        'paths': {'output_root': str(tmp_roots['runs'] / 'out')},
        'search': {'evaluation_enabled': False, 'nc': 1, 'input_size': 64, 'families': ['yolo11n'],
                   'seeds': [9], 'candidate_count': 1, 'max_attempts': 2, 'allow_native_fallback': True}}}
    validation = client.post('/api/v1/protocols/validate', json={'protocol': protocol}, headers=auth()).json()
    assert validation['valid'], validation
    assert 'dataset' not in validation['resolved']
    # DD-A46: POST /runs relayed by the gateway carries its host inputs, so core calls revalidate(resolved, gateway)
    monkeypatch.setattr(services, 'revalidate', lambda *_a, **_k: (True, 'changed-validation-hash', []))
    response = client.post('/api/v1/runs', json={
        'protocol': validation['resolved'], 'protocol_hash': validation['protocol_hash'],
        'validation_id': validation['validation_id'], 'idempotency_key': 'stale-generation'}, headers=auth())
    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'STALE_VALIDATION'


def test_artifact_metadata_does_not_publish_internal_objects():
    from mbnas_core.artifacts.registry import artifact_view
    view = artifact_view({'id': 'a', 'run_id': 'r', 'kind': 'other', 'state': 'READY',
        'meta': {'origin': 'native_training', 'epoch': None, 'filename': 'train_batch0.jpg',
                 'source_code': 'private implementation', 'traceback': 'private stack',
                 'candidate_id': {'unexpected': 'object'}}}, base_url='http://test', key=None, ttl_s=60)
    assert view['metadata'] == {'origin': 'native_training', 'epoch': None, 'filename': 'train_batch0.jpg'}
