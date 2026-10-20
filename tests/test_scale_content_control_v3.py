"""Continuation accepts reviewed publications without changing old contracts."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book import scale_content_control_v3 as control


def save(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receipt(tmp_path, *, export_version=3, receipt_version=3, parallel=8,
            export_features=90, export_content=81):
    export = tmp_path / 'export.json'
    export_sha = save(export, {
        'version': f'scale-content-native-classification-export-v{export_version}',
        'coordinator_sha256': 'coordinator', 'gate': {
            'passed': True, 'actual_feature_adopted_works': export_features,
            'actual_content_non_genre_feature_works': export_content},
        'verified_projection': {
            'rows': [{'qid': f'Q{i}'} for i in range(1, 97)],
            'actual_feature_adopted_works': export_features,
            'actual_content_non_genre_feature_works': export_content}})
    run = tmp_path / 'run.json'
    run_sha = save(run, {'run_id': 'run', 'selected': 100,
        'initial_concurrency': parallel, 'final_concurrency': parallel,
        'failed': 0, 'pending': 0, 'stopped': False, 'stop_reasons': []})
    imported = tmp_path / 'import.json'
    import_sha = save(imported, {'stage': 'actual_catalogue_import_complete',
        'actual_process': {'actual_exit_code': 0, 'process_exit_observed': True, 'pid': 123},
        'classification_manifest_sha256': export_sha,
        'result': {'raw_classified': 96, 'adopted': 90, 'failed': 0}})
    document = {'version': f'scale-content-window-control-v{receipt_version}',
        'run_id': 'run', 'coordinator_sha256': 'coordinator', 'window_index': 2,
        'gate': control.actual_gate(100, 96, 90, 81), 'control_artifacts': {
            name: {'path': str(path.resolve()), 'sha256': digest}
            for name, path, digest in (
                ('export', export, export_sha), ('run', run, run_sha),
                ('import_report', imported, import_sha))}}
    path = tmp_path / 'control.json'
    return path, save(path, document), document


@pytest.mark.parametrize('version', [2, 3])
def test_actual_imported_public_gate_can_continue(tmp_path, version):
    path, digest, expected = receipt(tmp_path, export_version=version, receipt_version=version)
    assert control.verify_control_receipt(path, digest) == expected


def test_new_publication_requires_new_control_contract(tmp_path):
    path, digest, _ = receipt(tmp_path, export_version=3, receipt_version=2)
    with pytest.raises(ValueError):
        control.verify_control_receipt(path, digest)


def test_internal_content_export_cannot_authorize_continuation(tmp_path):
    path, digest, _ = receipt(tmp_path, export_version=1)
    with pytest.raises(ValueError):
        control.verify_control_receipt(path, digest)


def test_changed_import_observation_invalidates_control(tmp_path):
    path, digest, _ = receipt(tmp_path)
    save(tmp_path / 'import.json', {'stage': 'failed'})
    with pytest.raises(ValueError, match='artifact changed'):
        control.verify_control_receipt(path, digest)


@pytest.mark.parametrize('features,content', [(0, 0), (89, 81), (90, 80)])
def test_pinned_export_counts_must_match_actual_import_control(tmp_path, features, content):
    path, digest, _ = receipt(tmp_path, export_features=features, export_content=content)
    with pytest.raises(ValueError, match='publication counts'):
        control.verify_control_receipt(path, digest)


@pytest.mark.parametrize('parallel', [4, 8])
def test_eight_slot_preparer_uses_actual_new_public_gate(tmp_path, parallel):
    path, digest, expected = receipt(tmp_path, parallel=parallel)
    script = Path(__file__).parents[1] / 'scripts/prepare_scale_content_chunk_v3.py'
    spec = importlib.util.spec_from_file_location('new_content_prepare', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.trial_control(path, digest) == expected
