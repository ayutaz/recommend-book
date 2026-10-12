"""Exercise the real SQL overlay and ranker across a canonical-ID change."""
import json

import pytest

from test_recommendation_overlay import prepared
from recommend_book.recommendation.pipeline import run_recommendation


@pytest.fixture
def pipeline_inputs(prepared, tmp_path, monkeypatch):
    from recommend_book.recommendation import narou
    reference = {'work_id': 'reference', 'title': 'Reference', 'rating': 5,
                 'features': {'genre.fantasy': 1}, 'native_features': {},
                 'author_ids': [], 'series_ids': [], 'external_ids': [],
                 'feature_provenance': {}, 'read_status': 'unknown', 'popularity': None}
    monkeypatch.setattr(narou, 'load_narou_reference', lambda *args, **kwargs:
                        {'rows': [reference], 'audit': {'fixture': True}})
    narou_db = tmp_path / 'narou.db'
    annotations = tmp_path / 'annotations.json'
    bridge = tmp_path / 'bridge.json'
    for path in (narou_db, annotations, bridge):
        path.write_bytes(b'protected reference fixture')
    return {'world_database': prepared['base_database'], 'narou_database': narou_db,
            'annotations': annotations, 'bridge': bridge, 'output': tmp_path / 'recommendations',
            'evaluate': False, 'enriched_database': prepared['enriched_database'],
            'delta_inputs': prepared['delta_inputs'], 'delta_manifest': prepared['delta_manifest'],
            'delta_results': prepared['results'], 'delta_run_manifest': prepared['run_manifest'],
            'delta_terminal_proof': prepared['terminal_proof']}


@pytest.mark.parametrize('state', ['read', 'unread', 'unknown'])
def test_old_id_history_follows_verified_crosswalk(pipeline_inputs, prepared, tmp_path, state):
    old_id = next(row['work_id'] for row in prepared['base_rows'] if row['title'] == 'Book one')
    new_id = json.loads(prepared['delta_inputs'].read_text())['work_id']
    history = tmp_path / 'history.json'
    original = [{'work_id': old_id, 'read_status': state}]
    history.write_text(json.dumps(original))
    run_recommendation(**pipeline_inputs, history=history, require_confirmed_unread=state == 'unread')
    output = pipeline_inputs['output']
    ranking = json.loads((output / 'recommendations.json').read_text())
    by_id = {row['work_id']: row for row in ranking['ranking']}
    assert old_id not in by_id
    if state == 'read':
        assert new_id not in by_id
        assert any(row['work_id'] == new_id and 'read_history_work_id' in row['reasons']
                   for row in ranking['audit']['excluded'])
    else:
        assert by_id[new_id]['read_status'] == state
        assert by_id[new_id]['features'] == {'genre.fantasy': 1.0}
    audit = json.loads((output / 'read-history-audit.json').read_text())
    assert audit['original_records'] == original and audit['changed_identity_count'] == 1
    assert audit['record_mapping'][0]['old_work_id'] == old_id
    assert audit['record_mapping'][0]['new_work_id'] == new_id
    assert json.loads(history.read_text()) == original
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['classification_overlay']['changed_work_count'] == 1
    assert 'world-works.json' in manifest['artifact_sha256']
    assert 'src/recommend_book/recommendation/overlay.py' in manifest['execution_artifact_sha256']
    assert manifest['target_domain_recommendation_quality'] == 'unverified'


@pytest.mark.parametrize('old_state,new_state,expected', [
    ('read', 'unread', 'read'), ('unread', 'unknown', 'unknown'), ('unread', 'unread', 'unread')])
def test_old_and_new_history_aliases_merge_conservatively(pipeline_inputs, prepared, tmp_path,
                                                        old_state, new_state, expected):
    old_id = next(row['work_id'] for row in prepared['base_rows'] if row['title'] == 'Book one')
    new_id = json.loads(prepared['delta_inputs'].read_text())['work_id']
    history = tmp_path / 'history.json'
    history.write_text(json.dumps([{'work_id': old_id, 'read_status': old_state},
                                   {'work_id': new_id, 'read_status': new_state}]))
    run_recommendation(**pipeline_inputs, history=history)
    result = json.loads((pipeline_inputs['output'] / 'recommendations.json').read_text())
    matched = [row for row in result['ranking'] if row['work_id'] == new_id]
    if expected == 'read':
        assert not matched
    else:
        assert matched[0]['read_status'] == expected
    audit = json.loads((pipeline_inputs['output'] / 'read-history-audit.json').read_text())
    assert audit['merged_identity_groups'][0]['source_indices'] == [0, 1]
    assert audit['merged_identity_groups'][0]['resolved_read_status'] == expected


def test_overlay_rejects_partial_arguments_before_creating_output(pipeline_inputs):
    pipeline_inputs.pop('delta_terminal_proof')
    with pytest.raises(ValueError, match='all six'):
        run_recommendation(**pipeline_inputs)
    assert not pipeline_inputs['output'].exists()


def test_legacy_duplicate_history_does_not_gain_new_semantics(pipeline_inputs, tmp_path):
    history = tmp_path / 'history.json'
    history.write_text(json.dumps([{'work_id': 'duplicate', 'read_status': 'read'},
                                   {'work_id': 'duplicate', 'read_status': 'unread'}]))
    with pytest.raises(ValueError, match='duplicate'):
        run_recommendation(**pipeline_inputs, history=history)
    assert not pipeline_inputs['output'].exists()


def test_known_rating_in_alias_group_excludes_without_averaging(pipeline_inputs, prepared, tmp_path):
    old_id = next(row['work_id'] for row in prepared['base_rows'] if row['title'] == 'Book one')
    new_id = json.loads(prepared['delta_inputs'].read_text())['work_id']
    history = tmp_path / 'history.json'
    original = [{'work_id': old_id, 'read_status': 'unknown', 'rating': 1},
                {'work_id': new_id, 'read_status': 'unread', 'rating': 5}]
    history.write_text(json.dumps(original))
    run_recommendation(**pipeline_inputs, history=history)
    result = json.loads((pipeline_inputs['output'] / 'recommendations.json').read_text())
    assert new_id not in {row['work_id'] for row in result['ranking']}
    audit = json.loads((pipeline_inputs['output'] / 'read-history-audit.json').read_text())
    assert audit['original_records'] == original
    assert audit['merged_identity_groups'][0]['rating_source_index'] == 0
    assert all(row['rating'] is None for row in result['ranking'])


def test_overlay_source_changed_during_render_is_not_published(pipeline_inputs, prepared, monkeypatch):
    from recommend_book.recommendation import pipeline
    original_render = pipeline.render_recommendations
    source = prepared['delta_manifest'].parent / 'canonical-crosswalk.source.json'
    def mutation(*args):
        page = original_render(*args)
        source.write_bytes(source.read_bytes() + b' ')
        return page
    monkeypatch.setattr(pipeline, 'render_recommendations', mutation)
    with pytest.raises(ValueError, match='overlay source changed'):
        run_recommendation(**pipeline_inputs)
    assert not pipeline_inputs['output'].exists()
    assert not list(pipeline_inputs['output'].parent.glob('recommendations.staging-*'))
