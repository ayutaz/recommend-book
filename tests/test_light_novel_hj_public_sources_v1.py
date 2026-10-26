"""Expose both sources for the explicit HJ edition-membership witness."""
from copy import deepcopy
import json

import pytest

from recommend_book.recommendation.scale_site_v1 import public_work


def row():
    return {
        'entry_id': 'lne:ndl:R100000002-I1', 'entry_kind': 'edition',
        'family': 'light_novel', 'title': '試験作品 2',
        'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'authors': [], 'aliases': [], 'genres': [],
        'relation_resolution': 'edition_series_membership_verified',
        'series_membership_evidence': {
            'rule_version': 'hj-wikipedia-novel-isbn-edition-membership-v1',
            'scope': 'edition_membership_only',
            'official_series_url': 'https://firecross.jp/hjbunko/series/71',
            'private_raw_path': 'PRIVATE_RAW',
            'wikipedia_attribution': {
                'credit': 'Wikipedia contributors: 試験作品',
                'source_url': 'https://ja.wikipedia.org/w/index.php?oldid=91',
                'history_url': 'https://ja.wikipedia.org/w/index.php?title=Test&action=history',
                'license': 'CC-BY-SA-4.0',
                'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
                'changes': 'Explicit novel official-link metadata extracted; no article prose reproduced',
                'quote_allowed': False,
            },
        },
    }


def test_hj_public_membership_has_publisher_and_wikipedia_attribution_without_private_proof():
    value = row(); original = deepcopy(value)
    shown = public_work(value)
    source = shown['membership_source']
    assert source['publisher_source_url'] == 'https://firecross.jp/hjbunko/series/71'
    assert source['source_url'] == value['series_membership_evidence']['wikipedia_attribution']['source_url']
    assert source['license'] == 'CC-BY-SA-4.0'
    assert source['attribution'] == 'Wikipedia contributors: 試験作品'
    assert source['history_url'].endswith('&action=history')
    assert source['changes']
    assert 'PRIVATE_RAW' not in json.dumps(shown)
    assert value == original


@pytest.mark.parametrize('change', ['rule', 'scope', 'relation', 'origin', 'comic', 'revision', 'license', 'quote'])
def test_unrecognized_or_incomplete_hj_witness_is_not_published(change):
    value = row(); proof = value['series_membership_evidence']
    if change == 'rule': proof['rule_version'] = 'unknown'
    elif change == 'scope': proof['scope'] = 'original_work_identity'
    elif change == 'relation': value['relation_resolution'] = 'original_work_and_story_series_unresolved'
    elif change == 'origin': proof['official_series_url'] = 'https://firecross.jp.evil.test/hjbunko/series/71'
    elif change == 'comic': proof['official_series_url'] = 'https://firecross.jp/comic/series/71'
    elif change == 'revision': proof['wikipedia_attribution']['source_url'] = 'https://evil.test/w/index.php?oldid=91'
    elif change == 'license': proof['wikipedia_attribution']['license_url'] = 'https://evil.test/license'
    elif change == 'quote': proof['wikipedia_attribution']['quote_allowed'] = True
    assert 'membership_source' not in public_work(value)
