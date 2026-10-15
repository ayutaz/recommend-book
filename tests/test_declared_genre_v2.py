"""Statement GUID owner case compatibility must preserve raw provenance."""
from copy import deepcopy
import hashlib

import pytest

from test_declared_genre import prepared, statement
from recommend_book.recommendation.declared_genre_v2 import (
    BASIS, DECLARED_GENRE_MAPPING, project_declared_genres,
)


def project(guid):
    claim = statement('P136', 'Q132311')
    claim['id'] = guid
    args = prepared(claim)
    before = deepcopy(args)
    result = project_declared_genres(args[0], args[1], DECLARED_GENRE_MAPPING, args[2])
    assert args == before
    assert hashlib.sha256(args[0]).hexdigest() == args[1].raw_sha256
    return result, args


@pytest.mark.parametrize('guid', ['q100$AbC-def', 'Q100$AbC-def'])
def test_owner_case_accepted_without_rewriting_guid_or_raw(guid):
    result, args = project(guid)
    assert result['version'] == 'source-declared-genre-projection-v2'
    assert BASIS == 'source_declared_P136_qid_name_correspondence_v2'
    assert result['provisional_recommendation_features'] == {'genre.fantasy': 1.0}
    assertion = result['source_declared_genres'][0]
    assert assertion['statement_id'] == assertion['raw_statement']['id'] == guid
    assert result['feature_provenance']['genre.fantasy'][0]['statement_id'] == guid
    assert result['provenance']['raw_sha256'] == args[1].raw_sha256


@pytest.mark.parametrize('guid', ['q101$AbC-def', 'Q101$AbC-def', 'Q1000$AbC-def',
                                 'q0100$AbC-def', 'q0$AbC-def', 'q100$', 'q100', 'x100$AbC'])
def test_foreign_numeric_owner_and_malformed_guid_rejected(guid):
    with pytest.raises(ValueError, match='statement identity'):
        project(guid)


def test_raw_sha_and_qualified_statement_checks_still_apply():
    claim = statement('P136', 'Q132311', qualifiers={'P580': [{'snaktype': 'somevalue'}]})
    claim['id'] = 'q100$Original-MiXeD'
    raw, record, scope = prepared(claim)
    with pytest.raises(ValueError, match='raw SHA256'):
        project_declared_genres(raw + b' ', record, DECLARED_GENRE_MAPPING, scope)
    result = project_declared_genres(raw, record, DECLARED_GENRE_MAPPING, scope)
    assert result['source_declared_genres'][0]['projection_status'] == 'held_qualifiers'
    assert result['provisional_recommendation_features'] == {}


def test_frozen_v1_continues_rejecting_lowercase_owner():
    from recommend_book.recommendation.declared_genre import project_declared_genres as v1
    claim = statement('P136', 'Q132311')
    claim['id'] = 'q100$Original-MiXeD'
    raw, record, scope = prepared(claim)
    with pytest.raises(ValueError, match='statement identity'):
        v1(raw, record, DECLARED_GENRE_MAPPING, scope)
