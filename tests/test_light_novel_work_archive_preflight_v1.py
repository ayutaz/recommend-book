"""No native/model replay; real file-byte and authority-chain checks."""
import hashlib
import json
from pathlib import Path

import pytest

from recommend_book import light_novel_work_archive_preflight_v1 as guard


@pytest.fixture
def closure(tmp_path, monkeypatch):
    def write(name, value):
        p = tmp_path / name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
        return p, hashlib.sha256(p.read_bytes()).hexdigest()
    monkeypatch.setattr(guard, 'ROOT', tmp_path)
    archive_pins = {}; codes = {}
    for name in ['scale_classification_v1.py', 'scale_content_classification_v1.py',
                 'scale_content_classification_v2.py', 'scale_content_classification_v3.py']:
        current, sha = write('src/recommend_book/' + name, ('# frozen ' + name).encode())
        frozen_name = 'runtime/runtime/src/recommend_book/' + name if name.endswith('_v1.py') else 'frozen/' + name
        frozen, _ = write(frozen_name, current.read_bytes())
        codes[name] = (current, frozen, sha)
        archive_pins[str(frozen)] = sha
    current, frozen, sha = codes['scale_content_classification_v2.py']
    archive_pins[str(current)] = sha
    archive, archive_sha = write('archive.json', {
        'version': 'scale-content-native-classification-export-v2',
        'runtime_preparation': str(tmp_path / 'runtime'), 'artifact_sha256': archive_pins,
        'output_verifier_sha256': sha, 'frozen_output_verifier': str(frozen)})
    current, frozen, sha = codes['scale_content_classification_v3.py']
    authority, authority_sha = write('authority.json', {
        'version': 'scale-content-native-classification-export-v3',
        'artifact_sha256': {str(current): sha, str(frozen): sha},
        'output_verifier_sha256': sha, 'frozen_output_verifier': str(frozen),
        'verified_projection': {'rows': [{'labels': ['NEVER IMPORT THESE']} ]}})
    monkeypatch.setattr(guard, 'V3_AUTHORITY', 'authority.json')
    monkeypatch.setattr(guard, 'V3_AUTHORITY_SHA256', authority_sha)
    raw, raw_sha = write('raw.json', {'entities': {}})
    license_raw, license_sha = write('license.bin', b'official license snapshot')
    conditions, conditions_sha = write('conditions.json', {'version': 'light-novel-source-conditions-v1',
        'snapshots': [{'raw_path': str(license_raw), 'raw_sha256': license_sha}]})
    pin = {'raw_path': str(raw), 'raw_sha256': raw_sha}
    metadata, metadata_sha = write('metadata.json', {'id': 'Q110895870',
        'schema_version': 'light-novel-work-metadata-v3', 'provenance': {
            **pin, 'conditions_path': str(conditions), 'conditions_sha256': conditions_sha,
            'linked_provenance': {'Q2': pin}},
        'work_admission_proof': {'type_authorities': {'Q8261': pin}}})
    bridge, bridge_sha = write('bridge.json', {'version': guard.BRIDGE_VERSION,
        'source': {'manifest_path': str(archive), 'manifest_sha256': archive_sha,
                   'metadata_path': str(metadata), 'metadata_sha256': metadata_sha}})
    monkeypatch.setattr(guard, 'BRIDGE_SHA256', bridge_sha)
    return bridge, bridge_sha, codes, {'authority': authority, 'archive': archive,
        'metadata': metadata, 'raw': raw, 'conditions': conditions, 'license': license_raw}, write


def test_all_eager_code_and_nested_metadata_are_authenticated_without_cache(closure):
    path, sha, codes, paths, _ = closure
    result = guard.check_preflight(path, sha)
    for current, frozen, expected in codes.values():
        assert result['artifact_sha256'][str(current)] == expected
        assert result['artifact_sha256'][str(frozen)] == expected
    assert str(paths['license']) in result['artifact_sha256']
    assert result['cache_allowed'] is False and result['full_replay_required'] is True
    assert result['independent_v3_authority_use'] == 'projection_source_code_only'
    assert 'NEVER IMPORT THESE' not in json.dumps(result)


@pytest.mark.parametrize('name', ['scale_classification_v1.py', 'scale_content_classification_v1.py',
                                 'scale_content_classification_v2.py', 'scale_content_classification_v3.py'])
@pytest.mark.parametrize('which', ['current', 'frozen'])
def test_each_current_or_frozen_projection_tamper_fails_before_replay(closure, name, which):
    path, sha, codes, _, _ = closure
    codes[name][0 if which == 'current' else 1].write_bytes(b'# changed')
    with pytest.raises(ValueError, match='SHA differs'): guard.check_preflight(path, sha)


@pytest.mark.parametrize('name', ['authority', 'archive', 'metadata', 'raw', 'conditions', 'license'])
def test_nested_authority_and_source_tamper_rejected(closure, name):
    path, sha, _, paths, _ = closure
    paths[name].write_bytes(b'{}')
    with pytest.raises(ValueError, match='SHA differs'): guard.check_preflight(path, sha)


def test_no_unknown_bridge_pin(closure):
    with pytest.raises(ValueError, match='Unknown bounded'):
        guard.check_preflight(closure[0], '0' * 64)


def test_wrapper_calls_original_full_verifier_after_preflight(closure, monkeypatch):
    from recommend_book import light_novel_work_archive_bridge_v1 as old
    calls = []
    def verify(path, sha):
        calls.append((path, sha)); return {'rows': ['original frozen result']}
    monkeypatch.setattr(old, 'verify_feature_export', verify)
    assert guard.verify_feature_export(closure[0], closure[1]) == {'rows': ['original frozen result']}
    assert calls == [(closure[0], closure[1])]


def test_wrapper_never_calls_full_verifier_on_preflight_failure(closure, monkeypatch):
    from recommend_book import light_novel_work_archive_bridge_v1 as old
    def forbidden(*args): raise AssertionError('must hold before replay')
    monkeypatch.setattr(old, 'verify_feature_export', forbidden)
    closure[2]['scale_content_classification_v3.py'][0].write_bytes(b'altered')
    with pytest.raises(ValueError, match='SHA differs'):
        guard.verify_feature_export(closure[0], closure[1])


def test_wrapper_holds_changes_during_full_replay(closure, monkeypatch):
    from recommend_book import light_novel_work_archive_bridge_v1 as old
    def verify(*args):
        closure[3]['license'].write_bytes(b'changed during replay')
        return {'rows': ['must not return']}
    monkeypatch.setattr(old, 'verify_feature_export', verify)
    with pytest.raises(ValueError, match='SHA differs'):
        guard.verify_feature_export(closure[0], closure[1])
