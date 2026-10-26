from copy import deepcopy

import pytest

from recommend_book.light_novel_bibliographic_match_v1 import (
    candidate_title_keys, match_bibliographic_reference,
)

OWN = 'records[1]/recordData/dcndl:BibResource'
DCT = '/{http://purl.org/dc/terms/}'
DC = '/{http://purl.org/dc/elements/1.1/}'


def reference(provider='kodansha', **changes):
    row = {'publisher_reference_id': 'kodansha-product:0000000001',
           'source_unit': 'publisher_product_reference',
           'source_url': 'https://www.kodansha.co.jp/comic/products/0000000001',
           'title': '完全な題３ 副題', 'isbn': None, 'publisher': None, 'form': None,
           'credits': [{'role': 'author', 'name': '著者'}, {'role': 'illustrator', 'name': '絵担当'}],
           'label': '講談社ラノベ文庫', 'label_literals': ['講談社ラノベ文庫'],
           'official_lightnovel_tag': True, 'story_series_asserted': False,
           'metadata_conflict': False, 'bibliographic_variants': [],
           'source_evidence': [{'source_url': 'https://www.kodansha.co.jp/comic/labels/lanove',
                                'raw_sha256': 'a' * 64, 'locator': 'card[1]'}]}
    if provider == 'hifumi':
        row.update(publisher_reference_id='hifumi-product:opaque1', source_url='https://hifumi.co.jp/lineup/opaque1/',
                   label='サーガフォレスト', label_literals=['サーガフォレスト'])
        row['source_evidence'][0]['source_url'] = 'https://hifumi.co.jp/saga_forest/'
    row.update(changes)
    return row


def parts(provider='kodansha', *, authority=True):
    publisher, imprint = ('講談社', '講談社ラノベ文庫') if provider == 'kodansha' else ('一二三書房', 'サーガフォレスト')
    identities = [{'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/01234567'}] if authority else []
    return {'source': {'source_id': 'R100000002-I1', 'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'provider_id': 'iss-ndl-opac', 'record_kind': 'edition', 'language': 'jpn',
        'publisher': publisher, 'series': imprint + ' ; 1', 'identifiers': [],
        'contributors': [{'name': '著者', 'role': 'author', 'identifiers': identities}],
        'extra': {'ndc_codes': ['913.6']}},
        'source_raw_sha256': 'b' * 64,
        'titles': [{'value': '完全な題 : 副題. 3', 'locator': OWN + DCT + 'title[1]'}],
        'volume': {'value': '3', 'locator': OWN + '/{http://ndl.go.jp/dcndl/terms/}volume[2]'},
        'publisher_agents': [{'name': publisher, 'locator': OWN + DCT + 'publisher[3]',
            'structured_agent': True, 'description_values': [], 'description_locators': []}],
        'literal_authors': [{'name': '著者', 'literal': '著者 著', 'locator': OWN + DC + 'creator[4]'}]}


@pytest.mark.parametrize('provider', ['kodansha', 'hifumi'])
def test_correspondence_confirms_bibliographic_scope_without_isbn_or_product_identity(provider):
    ref, own = reference(provider), parts(provider)
    before = deepcopy((ref, own))
    proof = match_bibliographic_reference(provider, ref, own)
    assert proof['scope'] == 'publisher_reference_to_ndl_bibliographic_record'
    assert proof['official_lightnovel_tag'] is True
    assert proof['same_edition_identity_asserted'] is False
    assert proof['original_work_identity_asserted'] is False
    assert proof['story_series_identity_asserted'] is False
    assert proof['product_sku_identity_asserted'] is False
    assert proof['authors'][0]['authority_status'] == 'known'
    assert proof['authors'][0]['authority_ids'] == ['http://id.ndl.go.jp/auth/entity/01234567']
    assert 'isbn13' not in proof
    assert (ref, own) == before


def test_unknown_authority_requires_both_own_explicit_writer_and_matching_contributor():
    own = parts(authority=False)
    proof = match_bibliographic_reference('kodansha', reference(), own)
    assert proof['authors'][0]['authority_ids'] == []
    assert proof['authors'][0]['authority_status'] == 'unknown'
    assert proof['authors'][0]['literal'] == '著者 著'
    for change in ('no_literal', 'editor', 'nested_literal', 'contributor_name', 'contributor_role'):
        changed = deepcopy(own)
        if change == 'no_literal':
            changed['literal_authors'] = []
        elif change == 'editor':
            changed['literal_authors'][0]['literal'] = '著者 編'
        elif change == 'nested_literal':
            changed['literal_authors'][0]['locator'] = OWN + '/contents[1]' + DC + 'creator[4]'
        elif change == 'contributor_name':
            changed['source']['contributors'][0]['name'] = '別人'
        else:
            changed['source']['contributors'][0]['role'] = 'illustrator'
        assert match_bibliographic_reference('kodansha', reference(), changed) is None


def test_dated_heading_needs_existing_uri_and_same_own_writer_literal():
    own = parts()
    own['source']['contributors'][0]['name'] = '著, 者, 1990-'
    proof = match_bibliographic_reference('kodansha', reference(), own)
    assert proof['authors'][0]['authority_status'] == 'known'
    assert proof['authors'][0]['binding_rule'] == 'dated_own_heading_and_explicit_writer_literal'
    own['literal_authors'] = []
    assert match_bibliographic_reference('kodansha', reference(), own) is None


@pytest.mark.parametrize('field,value', [('title', '完全な題２ 副題'), ('title', '完全な題３'),
    ('title', '完全な題３ 別副題'), ('title', '完全な題３ 副題 合本'),
    ('label', '講談社ラノベ文庫コミックス'), ('official_lightnovel_tag', None),
    ('official_lightnovel_tag', 1), ('credits', [{'role': 'unknown', 'name': '著者'}]),
    ('credits', [{'role': 'illustrator', 'name': '著者'}]), ('metadata_conflict', True),
    ('bibliographic_variants', [{'title': '別題'}])])
def test_reference_scope_and_complete_title_counterexamples_are_held(field, value):
    assert match_bibliographic_reference('kodansha', reference(**{field: value}), parts()) is None


@pytest.mark.parametrize('field,value', [('language', 'eng'), ('series', '別文庫'),
    ('publisher', '別社'), ('record_kind', 'work'), ('provider_id', 'external-provider')])
def test_primary_bibliographic_scope_counterexamples_are_held(field, value):
    own = parts()
    own['source'][field] = value
    assert match_bibliographic_reference('kodansha', reference(), own) is None


def test_nonfiction_mixed_ndc_and_contents_title_never_supply_correspondence():
    own = parts()
    own['source']['extra']['ndc_codes'] = ['913.6', '726.1']
    assert match_bibliographic_reference('kodansha', reference(), own) is None
    own = parts()
    own['titles'][0]['locator'] = OWN + '/contents[4]' + DCT + 'title[1]'
    assert match_bibliographic_reference('kodansha', reference(), own) is None


@pytest.mark.parametrize('role', ['発売', '頒布'])
def test_only_explicit_structured_sales_or_distribution_can_accompany_sole_publisher(role):
    own = parts()
    location = OWN + DCT + 'publisher[5]'
    own['source']['publisher'] += '; 別社'
    own['publisher_agents'].append({'name': '別社', 'locator': location, 'structured_agent': True,
        'description_values': [role], 'description_locators': [location + '/description[1]']})
    assert match_bibliographic_reference('kodansha', reference(), own)
    own['publisher_agents'][1]['description_values'] = []
    assert match_bibliographic_reference('kodansha', reference(), own) is None
    own['publisher_agents'][1]['description_values'] = ['不明']
    assert match_bibliographic_reference('kodansha', reference(), own) is None


def test_candidate_keys_are_only_complete_title_constructions_and_cannot_grant_proof():
    source = {'titles': [{'value': '完全な題 : 副題. 3'}], 'volume': '3'}
    assert '完全な題3副題' in candidate_title_keys(source)
    assert '完全な題' not in candidate_title_keys(source)
    assert match_bibliographic_reference('overlap', reference(), parts()) is None
