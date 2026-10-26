from copy import deepcopy
import hashlib

import pytest

from recommend_book.light_novel_orange_association_v1 import match_orange_association


PARENT = 'https://orangebunko.shueisha.co.jp/book/series/native-parent'
CHILD = 'https://orangebunko.shueisha.co.jp/book/4086800233'
CARD = 'section.content-wrap.book-wrap > div.content > div.row.col4 > article.book-box.box:nth-of-type(1)'
OWN = 'bulk-docs[1]/rdf:RDF/dcndl:BibResource[@rdf:about=https://ndlsearch.ndl.go.jp/books/R100000002-I1#material]'
DC = '/{http://purl.org/dc/elements/1.1/}'
DCT = '/{http://purl.org/dc/terms/}'


def selected():
    child = {'publisher_reference_id': 'shueisha-orange-book:4086800233', 'opaque_book_url_key': '4086800233',
        'source_unit': 'publisher_product_reference', 'source_url': CHILD, 'title': '完全な子の商品題',
        'credits': [{'role': 'author', 'name': '架空 著者'}, {'role': 'cover_illustrator', 'name': '架空 絵師'}],
        'credit_literals': [{'role_literal': '著者', 'name': '架空 著者', 'source_selector': CARD+' p.book-info-name[matching-index=1]'},
                            {'role_literal': '装画', 'name': '架空 絵師', 'source_selector': CARD+' p.book-info-name[matching-index=2]'}],
        'publisher': None, 'label': None, 'label_literals': [], 'form': None, 'isbn': None,
        'official_lightnovel_tag': None, 'media_references': None, 'story_series_asserted': False,
        'original_work_identity_asserted': False, 'paper_electronic_same_edition_asserted': False,
        'source_selector': CARD, 'source_evidence': [{'source_url': PARENT, 'raw_sha256': 'a'*64,
            'locator': CARD+' h2 / p.book-info-name / a.thumb[href]'}]}
    group = {'kind': 'official_publisher_listing_group_reference', 'source_unit': 'publisher_work_group',
        'reference_id': 'orange-taxonomy-url:'+hashlib.sha256(PARENT.encode()).hexdigest(),
        'reference_id_kind': 'local_hash_of_official_taxonomy_url', 'reference_id_is_official': False,
        'local_reference_key': {'provider': 'orange', 'source_scope': 'native_orange_series_taxonomy_urlset', 'official_parent_url': PARENT},
        'independent_official_group_id': None, 'title': '子とは違う親の掲載題', 'source_url': PARENT,
        'authors': None, 'publisher': None, 'label': None, 'form': None, 'isbn': None,
        'official_lightnovel_tag': None, 'light_novel_status': 'unknown', 'story_series_asserted': False,
        'story_series_identity_asserted': False, 'original_work_identity_asserted': False,
        'independent_volume_numbers_inferred': False, 'paper_electronic_same_edition_asserted': False,
        'members': [child], 'frame_member_count': 1, 'parent_page_membership_complete': True,
        'global_membership_complete': None, 'source_evidence': [
            {'source_url': 'https://orangebunko.shueisha.co.jp/series-sitemap.xml', 'raw_sha256': 'c'*64, 'locator': 'urlset/url/loc'},
            {'source_url': PARENT, 'raw_sha256': 'a'*64, 'locator': 'title; section.content-wrap.book-wrap > div.content > div.row.col4'}]}
    return {'group': group, 'child': child, 'source_proof_sha256': 'd'*64}


def parts(*, dated_authority=False):
    authors = [{'name': '架空著者', 'role': 'author', 'identifiers': []}]
    if dated_authority:
        authors.insert(0, {'name': '架空, 著者, 1980-', 'role': 'author', 'identifiers':
            [{'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/01234567'}]})
    return {'source': {'source_id': 'R100000002-I1', 'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'provider_id': 'iss-ndl-opac', 'record_kind': 'edition', 'language': 'jpn', 'volume': None,
        'publisher': '集英社', 'series': None, 'identifiers': [{'source': 'isbn', 'value': '9784086800235'}],
        'contributors': authors, 'extra': {'ndc_codes': ['913.6']}},
        'titles': [{'value': '完全な子の商品題', 'locator': OWN+DCT+'title[1]'},
                   {'value': '完全な子の商品題', 'locator': OWN+DC+'title[2]'}],
        'volume': None, 'source_raw_sha256': 'b'*64,
        'literal_authors': [{'name': '架空著者', 'literal': '架空著者 著', 'locator': OWN+DC+'creator[3]'}],
        'publisher_agents': [{'name': '集英社', 'locator': OWN+DCT+'publisher[4]',
                             'structured_agent': True, 'description_values': [], 'description_locators': []}]}


@pytest.mark.parametrize('dated_authority', [False, True])
def test_full_child_and_all_direct_writers_support_only_bibliographic_reference_association(dated_authority):
    selected_row, own = selected(), parts(dated_authority=dated_authority)
    before = deepcopy((selected_row, own))
    proof = match_orange_association(selected_row, own)
    assert proof['scope'] == 'bibliographic_reference_association'
    assert proof['source_group_reference_id'] == selected_row['group']['reference_id']
    assert proof['publisher_product_reference_id'] == selected_row['child']['publisher_reference_id']
    assert proof['official_parent_url'] == PARENT and proof['official_child_url'] == CHILD
    assert proof['official_title'] == '完全な子の商品題' and proof['matched_ndl_title'] == '完全な子の商品題'
    assert proof['official_credit_literals'] == selected_row['child']['credit_literals']
    assert proof['authors'][0]['authority_status'] == ('known' if dated_authority else 'unbound')
    assert proof['primary_locators'] and proof['publisher_source_evidence']
    assert proof['title_match']['volume'] is None
    for key in ['isbn', 'publisher', 'label', 'form', 'official_lightnovel_tag', 'media_references']:
        assert proof['official_child_unknown_fields'][key] is None
    for key in ['edition_identity_asserted', 'product_sku_identity_asserted', 'original_work_identity_asserted',
                'story_series_identity_asserted', 'light_novel_identity_asserted', 'paper_electronic_same_edition_asserted',
                'global_author_identity_asserted', 'global_author_alias_asserted', 'source_isbn_filled_from_ndl']:
        assert proof[key] is False
    assert (selected_row, own) == before


def test_explicit_own_volume_must_be_included_without_synthesizing_a_volume_for_no_volume_books():
    s, p = selected(), parts()
    p['volume'] = {'value': '３', 'locator': OWN+'/{http://ndl.go.jp/dcndl/terms/}volume[5]'}
    p['source']['volume'] = '３'
    assert match_orange_association(s, p) is None
    s['child']['title'] += '３'
    assert match_orange_association(s, p)['title_match']['volume'] == '３'
    for row in p['titles']: row['value'] += '. ３'
    assert match_orange_association(s, p)


def test_every_explicit_official_writer_is_required_without_promoting_the_cover_illustrator():
    s, p = selected(), parts()
    s['child']['credits'].insert(1, {'role': 'author', 'name': '共同 著者'})
    s['child']['credit_literals'].insert(1, {'role_literal': '著者', 'name': '共同 著者',
        'source_selector': CARD+' p.book-info-name[matching-index=2]'})
    s['child']['credit_literals'][2]['source_selector'] = CARD+' p.book-info-name[matching-index=3]'
    assert match_orange_association(s, p) is None
    p['source']['contributors'].append({'name': '共同著者', 'role': 'author', 'identifiers': []})
    p['literal_authors'].append({'name': '共同著者', 'literal': '共同著者 著', 'locator': OWN+DC+'creator[8]'})
    proof = match_orange_association(s, p)
    assert {a['official_writer'] for a in proof['authors']} == {'架空 著者', '共同 著者'}


@pytest.mark.parametrize('role', ['発売', '頒布'])
def test_sole_own_publisher_may_have_only_explicit_sales_or_distribution(role):
    s, p = selected(), parts()
    loc = OWN+DCT+'publisher[8]'
    p['source']['publisher'] += '; 頒布会社'
    p['publisher_agents'].append({'name': '頒布会社', 'locator': loc, 'structured_agent': True,
        'description_values': [role], 'description_locators': [loc+'/description[1]']})
    assert match_orange_association(s, p)


def test_a_consistently_foreign_primary_locator_is_not_this_ndl_record():
    s, p = selected(), parts()
    foreign = OWN.replace('R100000002-I1#', 'R100000002-I2#')
    for row in p['titles']+p['literal_authors']+p['publisher_agents']:
        row['locator'] = row['locator'].replace(OWN, foreign)
    assert match_orange_association(s, p) is None


@pytest.mark.parametrize('kind', ['parent_heading_only', 'partial_title', 'punctuation', 'extra_title', 'nested_title',
                                 'missing_writer_literal', 'nested_writer_literal', 'extra_writer_literal', 'extra_author',
                                 'authority_conflict', 'wrong_publisher', 'unknown_publisher_role', 'wrong_language', 'wrong_ndc',
                                 'wrong_provider', 'wrong_source_id'])
def test_incomplete_conflicting_or_foreign_own_metadata_is_held(kind):
    s, p = selected(), parts()
    if kind == 'parent_heading_only':
        for row in p['titles']: row['value'] = s['group']['title']
    elif kind == 'partial_title':
        for row in p['titles']: row['value'] = '完全な子'
    elif kind == 'punctuation':
        for row in p['titles']: row['value'] = '完全な子の・商品題'
    elif kind == 'extra_title': p['titles'].append({'value': '別の完全題', 'locator': OWN+DCT+'title[6]'})
    elif kind == 'nested_title': p['titles'][0]['locator'] = OWN+'/contents[1]'+DCT+'title[1]'
    elif kind == 'missing_writer_literal': p['literal_authors'] = []
    elif kind == 'nested_writer_literal': p['literal_authors'][0]['locator'] = OWN+'/contents[1]'+DC+'creator[3]'
    elif kind == 'extra_writer_literal': p['literal_authors'].append({'name': '余分著者', 'literal': '余分著者 著', 'locator': OWN+DC+'creator[7]'})
    elif kind == 'extra_author': p['source']['contributors'].append({'name': '余分著者', 'role': 'author', 'identifiers': []})
    elif kind == 'authority_conflict': p['source']['contributors'][0]['identifiers'] = [
        {'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/01234567'},
        {'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/07654321'}]
    elif kind == 'wrong_publisher': p['source']['publisher'] = p['publisher_agents'][0]['name'] = '他社'
    elif kind == 'unknown_publisher_role':
        p['source']['publisher'] += '; 他社'
        p['publisher_agents'].append({'name': '他社', 'locator': OWN+DCT+'publisher[7]', 'structured_agent': True,
            'description_values': [], 'description_locators': []})
    elif kind == 'wrong_language': p['source']['language'] = 'eng'
    elif kind == 'wrong_ndc': p['source']['extra']['ndc_codes'] = ['913.6', '726.1']
    elif kind == 'wrong_provider': p['source']['provider_id'] = None
    else: p['source']['source_id'] = 'R100000001-other'
    assert match_orange_association(s, p) is None


@pytest.mark.parametrize('kind', ['group_id', 'child_id', 'child_url', 'child_role', 'role_literal', 'source_sha',
                                 'source_parent', 'source_locator', 'source_isbn', 'source_label', 'ambiguous_child'])
def test_selected_source_assertion_and_explicit_role_evidence_are_required(kind):
    s, p = selected(), parts()
    if kind == 'group_id': s['group']['reference_id'] = 'orange-taxonomy-url:'+'0'*64
    elif kind == 'child_id': s['child']['publisher_reference_id'] = 'shueisha-orange-book:forged'
    elif kind == 'child_url': s['child']['source_url'] = 'https://example.org/book/4086800233'
    elif kind == 'child_role': s['child']['credits'][0]['role'] = 'unknown'
    elif kind == 'role_literal': s['child']['credit_literals'][0]['role_literal'] = '装画'
    elif kind == 'source_sha': s['child']['source_evidence'][0]['raw_sha256'] = '0'*64
    elif kind == 'source_parent': s['child']['source_evidence'][0]['source_url'] = CHILD
    elif kind == 'source_locator': s['child']['credit_literals'][0]['source_selector'] = CARD+' p.description'
    elif kind == 'source_isbn': s['child']['isbn'] = '9784086800235'
    elif kind == 'source_label': s['child']['label'] = 'オレンジ文庫'
    else:
        s['group']['members'].append(deepcopy(s['child']));s['group']['frame_member_count'] = 2
    assert match_orange_association(s, p) is None
