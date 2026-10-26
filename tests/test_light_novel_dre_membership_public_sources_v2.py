"""Public DRE attribution is narrow and never mutates stored proofs."""
from copy import deepcopy
import json

import pytest

from recommend_book.recommendation.scale_site_v1 import public_work as public_entry

NDL = 'https://ndlsearch.ndl.go.jp/books/R100000002-I1'
OFFICIAL = 'https://drecom-media.jp/drenovels/series/example'
RULES = ('dre-explicit-dual-source-ndl-series-membership-v2',
         'dre-explicit-dual-source-ndl-series-membership-v3',
         'dre-explicit-dual-source-ndl-series-membership-v4')


def row(rule=RULES[0], *, sources=None):
    return {'entry_id': 'lne:ndl:R100000002-I1', 'work_id': 'lne:ndl:R100000002-I1',
        'entry_kind': 'edition', 'family': 'light_novel', 'title': '完全題', 'qid': None,
        'source_url': NDL, 'relation_resolution': 'edition_series_membership_verified',
        'series_membership_evidence': {'rule_version': rule, 'scope': 'edition_membership_only',
            'sources': [NDL, OFFICIAL] if sources is None else sources, 'volume': None, 'volume_number': None,
            'private_raw_path': 'PRIVATE_RAW', 'raw_sha256': 'a' * 64},
        'volume': None, 'volume_number': None, 'authors': [], 'genres': [], 'aliases': []}


@pytest.mark.parametrize('rule', RULES)
def test_known_DRE_versions_expose_exact_official_source_preserve_unknown_volume_and_proof(rule):
    original = row(rule); before = deepcopy(original)
    shown = public_entry(original)
    assert shown['membership_source'] == {'source_url': OFFICIAL, 'source_kind': 'publisher_official_series',
        'attribution': 'シリーズ所属の書誌照合: 出版社の公式掲載資料', 'scope': 'edition_membership_only'}
    assert shown.get('volume_number') is None and shown.get('volume') is None
    assert 'membership_part' not in shown
    assert original == before
    assert original['series_membership_evidence']['volume_number'] is None
    assert 'PRIVATE_RAW' not in json.dumps(shown) and 'raw_sha256' not in json.dumps(shown)


@pytest.mark.parametrize('rule', RULES)
@pytest.mark.parametrize('url', [
    'https://evil.test/drenovels/series/example',
    'https://drecom-media.jp.evil.test/drenovels/series/example',
    'http://drecom-media.jp/drenovels/series/example',
    'https://drecom-media.jp/drecomics/series/example',
    'https://drecom-media.jp/drenovels/product/157',
    'https://drecom-media.jp/drenovels/series/example?scope=other',
    'https://drecom-media.jp/drenovels/series/example#fragment',
    'https://drecom-media.jp/drenovels/series/',
    'javascript:alert(1)',
])
def test_invalid_official_origin_or_series_route_fails_closed(rule, url):
    assert 'membership_source' not in public_entry(row(rule, sources=[NDL, url]))


@pytest.mark.parametrize('rule', RULES)
@pytest.mark.parametrize('sources', [
    [NDL, OFFICIAL, 'https://drecom-media.jp/drenovels/series/another'],
    [NDL, OFFICIAL, OFFICIAL],
    [NDL], [], 'https://drecom-media.jp/drenovels/series/example',
    {'official': OFFICIAL}, [NDL, {'source_url': OFFICIAL}],
])
def test_nonunique_or_malformed_official_source_list_fails_closed(rule, sources):
    assert 'membership_source' not in public_entry(row(rule, sources=sources))


@pytest.mark.parametrize('rule', ['dre-explicit-dual-source-ndl-series-membership-v1',
    'dre-explicit-dual-source-ndl-series-membership-v5', 'unknown', None])
def test_unknown_rule_cannot_borrow_official_source(rule):
    value = row(rule); value['series_membership_evidence']['official_series_url'] = OFFICIAL
    assert 'membership_source' not in public_entry(value)


def test_unverified_relation_does_not_publish_membership_source():
    value = row(); value['relation_resolution'] = 'original_work_and_story_series_unresolved'
    assert 'membership_source' not in public_entry(value)
