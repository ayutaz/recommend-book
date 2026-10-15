import json
from hashlib import sha256

import pytest

from recommend_book.recommendation.personal_poc import join_owned_profile, load_pinned_json, world_candidates


def test_snapshot_is_bound_to_expected_bytes_not_its_own_manifest(tmp_path):
    path = tmp_path / 'snapshot.json'
    original = b'{"safe": true}'
    path.write_bytes(original)
    expected = sha256(original).hexdigest()
    assert load_pinned_json(path, expected) == {'safe': True}
    path.write_text(json.dumps({'safe': False}))
    with pytest.raises(ValueError, match='Snapshot hash differs'):
        load_pinned_json(path, expected)


def test_profile_join_requires_exact_private_identifier_and_unchanged_rating():
    owned = [{'work_id': 'owned:secret-a', 'title': 'same title', 'rating': 5, 'features': {}},
             {'work_id': 'owned:secret-b', 'title': 'same title', 'rating': 2, 'features': {}}]
    profile = [{'work_id': 'public:a', 'rating': 5, 'features': {'genre.fantasy': 1},
                'feature_provenance': {}, 'author_ids': ['wd:author'], 'series_ids': [],
                'external_ids': [{'source': 'narou', 'value': 'secret-a'}]}]
    joined = join_owned_profile(owned, profile)
    assert joined[0]['work_id'] == 'owned:secret-a'
    assert joined[0]['features'] == {'genre.fantasy': 1}
    assert joined[1]['features'] == {}
    assert owned[0]['features'] == {}
    profile[0]['rating'] = 1
    with pytest.raises(ValueError, match='Original assessment differs'):
        join_owned_profile(owned, profile)


def test_world_projection_drops_old_scores_and_all_unapproved_extra_fields():
    old = {'ranking': [{'work_id': 'public:x', 'title': 'Novel', 'authors': [],
                        'display_title_aliases': [], 'read_status': 'unknown',
                        'content_labels': ['genre.fantasy'], 'score': 999,
                        'explanations': [{'reference_work_id': 'SECRET'}], 'secret': 'PRIVATE'}]}
    rows = world_candidates(old, {'genre.fantasy': 'Fantasy'})
    assert rows[0]['features'] == {'genre.fantasy': 1.0}
    assert 'score' not in rows[0] and 'explanations' not in rows[0]
    assert 'SECRET' not in json.dumps(rows) and 'PRIVATE' not in json.dumps(rows)
    old['ranking'][0]['content_labels'] = ['style.poetic']
    with pytest.raises(ValueError, match='Unsupported candidate label'):
        world_candidates(old, {'style.poetic': 'Poetic'})
