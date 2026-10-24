from copy import deepcopy

import pytest

from recommend_book.light_novel_reference_match_v1 import match_tobooks_edition


ISBN = '9784758047678'
BASE = 'own/dcndl:BibResource'
DCT = '/{http://purl.org/dc/terms/}title[1]'


def reference(**updates):
    row = {'publisher_reference_id': 'tobooks-product:123', 'source_unit': 'publisher_product_reference',
           'source_url': 'https://www.tobooks.jp/contents/123', 'title': '完全な題３ 副題',
           'isbn': ISBN, 'credits': [{'role': 'unknown', 'name': '人物名'}], 'publisher': 'TOブックス',
           'label': None, 'label_literals': [], 'form': 'B6判', 'official_lightnovel_tag': True,
           'story_series_asserted': False, 'metadata_conflict': False, 'bibliographic_variants': [],
           'source_evidence': [{'source_url': 'https://www.tobooks.jp/contents',
                                'raw_sha256': 'a' * 64, 'locator': 'product_cards[1]'}]}
    return dict(row, **updates)


def parts(*, source_id='R100000002-I1', raw_sha='b' * 64, title='完全な題 : 副題. 3',
          volume='3', publisher='TOブックス', language='jpn', ndc=None, edition_statements=None):
    return {'source': {'source_id': source_id, 'source_url': 'https://ndlsearch.ndl.go.jp/books/' + source_id,
                      'identifiers': [{'source': 'isbn', 'value': ISBN}], 'publisher': publisher,
                      'language': language, 'extra': {'ndc_codes': ndc or ['913.6'], 'raw_locator': 'records[1]'}},
            'titles': [{'value': title, 'locator': BASE + DCT}],
            'volume': {'value': volume, 'locator': BASE + '/volume[2]'} if volume else None,
            'publisher_agents': [{'name': publisher, 'locator': BASE + '/publisher[3]',
                                  'structured_agent': True, 'description_values': [], 'description_locators': []}],
            'edition_statements': edition_statements or [], 'source_raw_sha256': raw_sha}


def match(ref=None, primary=None, candidates=None):
    primary = primary or parts()
    return match_tobooks_edition(ref or reference(), primary,
                                same_isbn_parts=[primary] if candidates is None else candidates)


def test_contained_edition_confirmation_preserves_unknown_roles_and_product_identity():
    ref, own = reference(), parts()
    before = deepcopy((ref, own))
    proof = match(ref, own)
    assert proof['confirmed_scope'] == 'contained_bibliographic_edition_only'
    assert proof['publisher_reference_id'] == 'tobooks-product:123'
    assert proof['official_credits'] == [{'role': 'unknown', 'name': '人物名'}]
    assert proof['author_role_status'] == 'unknown'
    assert proof['author_identity_asserted'] is False
    assert proof['original_work_identity_asserted'] is False
    assert proof['story_series_identity_asserted'] is False
    assert proof['product_sku_identity_asserted'] is False
    assert proof['title_match']['main'] == '完全な題'
    assert proof['title_match']['subtitle'] == '副題'
    assert proof['title_match']['volume'] == '3'
    assert proof['title_match']['locators'] == [BASE + DCT, BASE + '/volume[2]']
    assert proof['candidate_raw_sha256s'] == ['b' * 64]
    assert proof['publisher_source_evidence'] == ref['source_evidence']
    assert (ref, own) == before


@pytest.mark.parametrize('change', [
    {'official_lightnovel_tag': False}, {'official_lightnovel_tag': 1},
    {'source_unit': 'story_series'}, {'source_unit': 'product_reference'}, {'publisher': '別出版社'},
    {'isbn': None}, {'isbn': '9784758047670'}, {'title': '完全な題２ 副題'},
    {'title': '完全な題３'}, {'title': '完全な題３ 別の副題'},
    {'title': '完全な題３ 副題 TO限定'}, {'title': '完全な題３ 副題 特典付き'},
    {'title': '完全な題３ 副題 合本'}, {'source_evidence': []},
    {'metadata_conflict': True}, {'bibliographic_variants': [{'title': '別題'}]},
])
def test_reference_counterexamples_remain_held(change):
    assert match(reference(**change)) is None


def test_full_bundle_title_is_not_stripped_and_products_remain_distinct():
    own = parts(title='完全な題 : 副題 TO限定セット. 3')
    assert match(reference(), own) is None
    first = match(reference(title='完全な題３ 副題 TO限定セット'), own)
    second = match(reference(title='完全な題３ 副題 TO限定セット', publisher_reference_id='tobooks-product:456'), own)
    assert first['publisher_reference_id'] != second['publisher_reference_id']
    assert first['product_sku_identity_asserted'] is False


def test_non_primary_title_cannot_supply_missing_complete_title_or_volume():
    own = parts()
    own['titles'][0]['locator'] = BASE + '/contents[4]' + DCT
    assert match(primary=own) is None
    own = parts(title='完全な題 : 副題', volume=None)
    assert match(primary=own) is None


@pytest.mark.parametrize('change', [{'publisher': '別出版社'}, {'language': 'eng'}, {'ndc': ['726.1']}])
def test_primary_source_scope_counterexamples_remain_held(change):
    assert match(primary=parts(**change)) is None


def test_only_explicit_distribution_can_accompany_unique_publisher():
    own = parts()
    own['source']['publisher'] = 'TOブックス; 別会社'
    own['publisher_agents'].append({'name': '別会社', 'locator': BASE + '/publisher[4]',
        'structured_agent': True, 'description_values': ['頒布'], 'description_locators': [BASE + '/publisher[4]/description[1]']})
    proof = match(primary=own)
    assert proof['publisher_match']['distribution_agents'][0]['name'] == '別会社'
    own['publisher_agents'][1]['description_values'] = []
    assert match(primary=own) is None
    own['publisher_agents'][1]['description_values'] = ['発売']
    assert match(primary=own) is None
    own['publisher_agents'][1]['description_values'] = ['頒布']
    own['publisher_agents'][1]['structured_agent'] = False
    assert match(primary=own) is None


@pytest.mark.parametrize('change', [
    {'title': '別の題 : 副題. 3'}, {'volume': '2'}, {'publisher': '別出版社'},
    {'language': 'eng'}, {'ndc': ['913.5']},
    {'edition_statements': [{'value': '改訂第2版', 'locator': BASE + '/edition[4]'}]},
])
def test_conflicting_same_isbn_record_is_held(change):
    own = parts()
    other = parts(source_id='R100000002-I2', raw_sha='c' * 64, **change)
    assert match(primary=own, candidates=[own, other]) is None


def test_consistent_parallel_records_keep_all_candidate_proofs():
    own = parts()
    other = parts(source_id='R100000002-I2', raw_sha='c' * 64, language='ja')
    proof = match(primary=own, candidates=[own, other])
    assert proof['candidate_raw_sha256s'] == ['b' * 64, 'c' * 64]
    assert len(proof['same_isbn_candidates']) == 2
    assert all(row['locators'] for row in proof['same_isbn_candidates'])
    assert proof == match(primary=own, candidates=[other, own])


def test_missing_primary_or_unpinned_candidate_is_held():
    own = parts()
    assert match(primary=own, candidates=[]) is None
    assert match(primary=own, candidates=[parts(source_id='R100000002-I2')]) is None
    other = parts(source_id='R100000002-I2', raw_sha='')
    assert match(primary=own, candidates=[own, other]) is None


def test_complete_title_volume_and_edition_statements_are_not_discarded():
    own = parts()
    own['titles'].append({'value': '別の完全題. 3', 'locator': BASE + '/{http://purl.org/dc/terms/}title[2]'})
    assert match(primary=own) is None
    own = parts()
    own['titles'][0]['locator'] = BASE + '/{http://purl.org/dc/elements/1.1/}title[1]'
    assert match(primary=own) is None
    own = parts(edition_statements=[{'value': '改訂第2版', 'locator': BASE + '/edition[4]'}])
    other = parts(source_id='R100000002-I2', raw_sha='c' * 64,
                  edition_statements=[{'value': '改訂第2版', 'locator': BASE + '/edition[4]'}])
    proof = match(primary=own, candidates=[own, other])
    assert proof['same_isbn_candidates'][0]['edition_statements'] == own['edition_statements']


def test_pure_matcher_never_opens_files(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError('matcher must not perform I/O')
    monkeypatch.setattr('builtins.open', reject)
    assert match() is not None
