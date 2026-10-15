from copy import deepcopy

import pytest

from recommend_book.autonomous_delivery import build_delivery, verify_baseline
from recommend_book.recommendation.engine import rank_candidates


def ranker(candidates, references, setting, **kwargs):
    return rank_candidates(candidates, references, method=setting['family'], **kwargs)


def row(work, *, rating=None, popularity=None):
    return {'work_id': work, 'title': work, 'features': {'genre.fantasy': 1},
            'rating': rating, 'read_status': 'unknown', 'popularity': popularity,
            'external_ids': [], 'author_ids': [], 'series_ids': [], 'feature_provenance': {}}


def test_popularity_winner_does_not_transfer_source_popularity_to_world():
    world = {'rows': [row('world')], 'audit': {}}
    refs = {'rows': [row('narou-ref', rating=5, popularity=100)], 'audit': {}}
    before = deepcopy((world, refs))
    result = build_delivery(world, refs, {'recommended_setting': {'id': 'popular', 'family': 'popularity'}}, ranker=ranker)
    assert result['selection']['target_setting']['family'] == 'personal'
    assert result['selection']['fallback_applied'] is True
    assert result['selection']['world_transfer_verified'] is False
    assert result['recommendations']['ranking'][0]['popularity'] is None
    assert result['recommendations']['ranking'][0]['read_status'] == 'unknown'
    assert (world, refs) == before


def test_final_page_does_not_request_user_history_or_new_ratings():
    result = build_delivery({'rows': [row('world')], 'audit': {}},
                            {'rows': [row('ref', rating=5)], 'audit': {}},
                            {'recommended_setting': {'id': 'personal', 'family': 'personal'}}, ranker=ranker)
    assert '履歴を追加' not in result['html']
    assert '入力してください' not in result['html']
    assert '読書状態未確認' in result['html']
    assert result['selection']['user_tasks_required'] == 0


def test_reference_rating_excludes_work_without_inventing_other_read_statuses():
    result = build_delivery({'rows': [row('rated'), row('other')], 'audit': {}},
                            {'rows': [row('rated', rating=4)], 'audit': {}},
                            {'recommended_setting': {'id': 'personal', 'family': 'personal'}}, ranker=ranker)
    assert [r['work_id'] for r in result['recommendations']['ranking']] == ['other']
    assert result['confirmed_unread']['audit']['eligible_count'] == 0


def test_baseline_rejects_tampering_and_escaping_paths(tmp_path):
    import hashlib, json
    source = tmp_path / 'source.json'
    source.write_text('{}', encoding='utf-8')
    manifest = tmp_path / 'baseline.json'
    manifest.write_text(json.dumps({'artifacts': [{'path': 'source.json', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}), encoding='utf-8')
    assert verify_baseline(manifest, tmp_path)['unchanged'] is True
    source.write_text('{"changed": true}', encoding='utf-8')
    with pytest.raises(ValueError, match='changed'):
        verify_baseline(manifest, tmp_path)
    manifest.write_text(json.dumps({'artifacts': [{'path': '../outside', 'sha256': '0' * 64}]}), encoding='utf-8')
    with pytest.raises(ValueError, match='escape'):
        verify_baseline(manifest, tmp_path)


def test_selection_cannot_claim_world_validation_or_accept_invalid_family():
    with pytest.raises(ValueError, match='family'):
        build_delivery({'rows': [row('world')], 'audit': {}}, {'rows': [], 'audit': {}},
                       {'recommended_setting': {'id': 'bad', 'family': 'fake'}}, ranker=ranker)


def test_boolean_assertion_cannot_authorize_unverified_target_popularity():
    with pytest.raises(ValueError, match='verified target popularity'):
        build_delivery({'rows': [row('world', popularity=100)], 'audit': {}},
                       {'rows': [row('ref', rating=5)], 'audit': {}},
                       {'recommended_setting': {'id': 'popular', 'family': 'popularity'}},
                       ranker=ranker, target_popularity_comparable=True)


def test_delivery_uses_verified_scope_eligibility_instead_of_historical_pool(tmp_path, monkeypatch):
    import json
    from recommend_book import autonomous_database, autonomous_delivery
    from recommend_book.recommendation import world, narou

    historical = {'rows': [row('edition'), row('work')], 'audit': {'work_count': 2}}
    eligible = {'rows': [row('work')], 'audit': {
        'historical_work_count': 2, 'eligible_work_count': 1,
        'scope_quarantined_count': 1, 'scope_quarantine_verified': True}}
    monkeypatch.setattr(world, 'load_world_database', lambda path: deepcopy(historical))
    monkeypatch.setattr(autonomous_database, 'load_autonomous_world_database',
                        lambda path: deepcopy(eligible), raising=False)
    monkeypatch.setattr(narou, 'load_narou_reference', lambda *args: {
        'rows': [row('ref', rating=5)], 'audit': {}})
    monkeypatch.setattr(autonomous_delivery, 'verify_baseline',
                        lambda *args: {'unchanged': True, 'verified_count': 16})
    sources = {}
    for name in ('world_database', 'narou_database', 'annotations', 'baseline_manifest'):
        sources[name] = tmp_path / (name + '.json')
        sources[name].write_text('{}', encoding='utf-8')
    sources['selection_path'] = tmp_path / 'selection.json'
    sources['selection_path'].write_text(json.dumps({
        'recommended_setting': {'id': 'personal', 'family': 'personal'}}), encoding='utf-8')
    output = tmp_path / 'delivery'
    result = autonomous_delivery.run_delivery(**sources, output=output)
    assert result['work_count'] == 1
    delivered = json.loads((output / 'world-works.json').read_text(encoding='utf-8'))
    assert [record['work_id'] for record in delivered] == ['work']
    assert json.loads((output / 'world-audit.json').read_text(encoding='utf-8'))['historical_work_count'] == 2
