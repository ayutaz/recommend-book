import json

import pytest

from recommend_book.recommendation.pipeline import build_profile, load_history, render_recommendations


def test_profile_preserves_low_ratings_and_missing_features():
    profile = build_profile([
        {"work_id": "a", "rating": 5, "features": {"genre.fantasy": 1}},
        {"work_id": "b", "rating": 1, "features": {"genre.fantasy": 1}},
        {"work_id": "c", "rating": 4, "features": {}},
    ])
    assert profile["rated_count"] == 3
    assert profile["featureless_rated_count"] == 1
    assert profile["features"]["genre.fantasy"]["mean_rating"] == 3
    assert profile["features"]["genre.fantasy"]["low_count"] == 1


def test_history_requires_explicit_identity_and_read_state(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps([{"title": "A title", "read_status": "read"}]))
    with pytest.raises(ValueError, match="identity"):
        load_history(path)
    path.write_text(json.dumps([{"work_id": "a", "read_status": "unread"}]))
    assert load_history(path)[0]["read_status"] == "unread"


def test_history_does_not_convert_unknown_to_unread(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps([{"work_id": "a", "read_status": "unknown"}]))
    assert load_history(path)[0]["read_status"] == "unknown"


def test_report_escapes_private_titles_and_keeps_unknown_status():
    page = render_recommendations({"ranking": [{"work_id": "w", "title": '<script>alert(1)</script>',
        "read_status": "unknown", "score": 4.1, "score_status": "supported",
        "features": {"theme.growth": 1}, "explanations": []}], "audit": {}}, {})
    assert '<script>alert(1)</script>' not in page
    assert '&lt;script&gt;' in page
    assert '読書状態未確認' in page
    assert '実評価' in page


def test_pipeline_publishes_real_engine_evaluation_without_changing_inputs(tmp_path, monkeypatch):
    from recommend_book.recommendation import narou, world
    from recommend_book.recommendation.pipeline import run_recommendation
    def row(work, rating):
        return {"work_id": work, "title": work, "rating": rating,
                "features": {"genre.fantasy": 1}, "native_features": {"narou.theme.growth": 1},
                "read_status": "unknown", "author_ids": [], "series_ids": [],
                "external_ids": [], "feature_provenance": {}, "popularity": 1}
    monkeypatch.setattr(world, 'load_world_database', lambda path: {'rows': [row('world', None)], 'audit': {}})
    monkeypatch.setattr(narou, 'load_narou_reference', lambda *args, **kwargs:
                        {'rows': [row(str(i), 1 + i % 5) for i in range(10)], 'audit': {}})
    paths = [tmp_path / name for name in ('world.db', 'narou.db', 'annotations.json', 'bridge.json')]
    for path in paths:
        path.write_bytes(b'protected fixture')
    result = run_recommendation(world_database=paths[0], narou_database=paths[1],
        annotations=paths[2], bridge=paths[3], output=tmp_path / 'result')
    assert result['recommendation_count'] == 1
    assert result['target_quality'] == 'unverified'
    assert all(path.read_bytes() == b'protected fixture' for path in paths)
    manifest = json.loads((tmp_path / 'result' / 'manifest.json').read_text())
    assert 'native-theme-evaluation.json' in manifest['artifact_sha256']
    native = json.loads((tmp_path / 'result' / 'native-theme-evaluation.json').read_text())
    assert native['feature_space'] == 'narou_native_theme_features'
    assert native['quality_limits']['independent_proof'] is False
    with pytest.raises(ValueError, match='new directory'):
        run_recommendation(world_database=paths[0], narou_database=paths[1],
            annotations=paths[2], bridge=paths[3], output=tmp_path / 'result')
