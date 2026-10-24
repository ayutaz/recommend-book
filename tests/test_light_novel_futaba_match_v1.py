from copy import deepcopy
from hashlib import sha256
import json
from urllib.parse import quote

import pytest

from recommend_book.light_novel_futaba_match_v1 import (
    match_futaba_reference, match_futaba_mnovels_reference, reference_hold_reason,
)


ISBN = '9781234567897'
LABEL = 'モンスター文庫'
OWN = 'records[1]/recordData/dcndl:BibResource'
DCT = '/{http://purl.org/dc/terms/}'
DC = '/{http://purl.org/dc/elements/1.1/}'
POINTER = '/books/book_informations/0'
SEARCH = 'https://www.futabasha.co.jp/search?label_name=' + quote(LABEL)
API = ('https://book-api.futabasha.co.jp/books?v=2&request_mode=1&media=0&age_verification=3&request_from=&request_to=&keyword=&publication_form=&label_name='
       + quote(LABEL) + '&series_name=&tag_name=&grouping=&author_name=&jdcn_code=&per_page=25&page=1&display_order=0&image_size=330')


def reference():
    return {'publisher_reference_id': 'futaba-product:opaque-1', 'source_unit': 'publisher_product_reference',
        'source_url': 'https://www.futabasha.co.jp/book/opaque-1?type=1', 'title': '完全な題３ 副題',
        'publisher': None, 'isbn': ISBN, 'isbn_source_literal': ISBN, 'form': '文庫',
        'credits': [{'role': 'author', 'name': '架空著者'}, {'role': 'illustrator', 'name': '架空絵師'}],
        'credit_literals': [{'role': '著', 'name': '架空著者', 'role_field': 'author_type_name', 'locator': POINTER + '/book_authors/0'},
                            {'role': 'イラスト', 'name': '架空絵師', 'role_field': 'web_author_type_overwrite', 'locator': POINTER + '/book_authors/1'}],
        'label': LABEL, 'label_literals': [LABEL], 'official_lightnovel_tag': None,
        'story_series_asserted': False, 'metadata_conflict': False, 'bibliographic_variants': [],
        'source_scope_membership': {'source_unit': 'publisher_label_filtered_product_listing',
            'membership_basis': 'returned_by_publisher_explicit_label_filter', 'filter_parameter': 'label_name',
            'filter_literal': LABEL, 'rendered_index_url': SEARCH, 'request_url': API, 'raw_sha256': 'a' * 64,
            'source_label_id_literal': 14, 'source_label_id_locator': POINTER + '/label_id',
            'card_label_literal': LABEL, 'card_label_locator': POINTER + '/label_name',
            'official_ui_route_proof': 'monster-ui-route-proof-v1.json',
            'does_not_assert_story_series_or_original_identity': True},
        'source_evidence': [{'source_url': API, 'raw_sha256': 'a' * 64,
                             'locator': POINTER + ': book_name/isbn_code/book_authors/label_name/label_id/publication_form_value'}]}


def parts(*, authority=True):
    return {'source': {'source_id': 'R100000002-I1', 'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'provider_id': 'iss-ndl-opac', 'record_kind': 'edition', 'language': 'jpn',
        'publisher': '双葉社', 'series': LABEL + ' ; 3',
        'identifiers': [{'source': 'isbn', 'value': ISBN}],
        'contributors': [{'name': '架空著者', 'role': 'author', 'identifiers':
            [{'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/01234567'}] if authority else []}],
        'extra': {'ndc_codes': ['913.6']}}, 'source_raw_sha256': 'b' * 64,
        'titles': [{'value': '完全な題 : 副題. 3', 'locator': OWN + DCT + 'title[1]'}],
        'volume': {'value': '3', 'locator': OWN + '/{http://ndl.go.jp/dcndl/terms/}volume[2]'},
        'publisher_agents': [{'name': '双葉社', 'locator': OWN + DCT + 'publisher[3]',
                             'structured_agent': True, 'description_values': [], 'description_locators': []}],
        'literal_authors': [{'name': '架空著者', 'literal': '架空著者 著', 'locator': OWN + DC + 'creator[4]'}]}


def test_same_isbn_full_bibliography_keeps_unknown_individual_publisher_and_limits_identity_scope():
    ref, own = reference(), parts()
    before = deepcopy((ref, own))
    proof = match_futaba_reference(ref, own)
    assert proof['scope'] == 'edition_only'
    assert proof['confirmed_scope'] == 'contained_bibliographic_edition_only'
    assert proof['same_isbn_bibliographic_correspondence_asserted'] is True
    assert proof['isbn13'] == ISBN and proof['official_title'] == ref['title']
    assert proof['official_label'] == LABEL and proof['official_form'] == '文庫'
    assert proof['official_lightnovel_tag'] is None and proof['official_publisher'] is None
    assert proof['catalogue_owner'] == '双葉社' and proof['individual_publisher_agreement'] == 'unknown'
    assert proof['authors'][0]['authority_status'] == 'known'
    assert proof['official_credit_literals'] == ref['credit_literals']
    assert proof['publisher_source_evidence'] == ref['source_evidence']
    assert proof['source_scope_membership'] == ref['source_scope_membership']
    assert proof['primary_isbn_evidence'][0]['projection_pointer'] == '/source/identifiers/0'
    assert proof['title_match']['volume'] == '3' and proof['primary_locators']
    policy = proof['label_classification_context']
    assert policy['source_url'] == 'https://www.futabasha.co.jp/recruit/2027/interview_comic-2.html'
    assert policy['section_heading_literal'] == '第二コミック出版部の仕事について'
    assert policy['checked_date'] == '2026-10-25' and 'raw_sha256' not in policy
    for key in ['product_sku_identity_asserted', 'printing_identity_asserted', 'same_edition_identity_asserted',
                'paper_ebook_same_edition_asserted', 'original_work_identity_asserted', 'story_series_identity_asserted',
                'global_author_alias_asserted', 'global_author_identity_asserted']:
        assert proof[key] is False
    assert 'volume' not in proof and 'series_id' not in proof
    assert (ref, own) == before


def test_unknown_authority_requires_same_own_writer_literal_and_existing_author_contributor():
    own = parts(authority=False)
    proof = match_futaba_reference(reference(), own)
    assert proof['authors'][0]['authority_status'] == 'unknown'
    assert proof['authors'][0]['authority_ids'] == []
    own['literal_authors'] = []
    assert match_futaba_reference(reference(), own) is None


def test_known_own_authority_takes_precedence_without_needing_a_literal_fallback():
    own = parts()
    own['literal_authors'] = []
    proof = match_futaba_reference(reference(), own)
    assert proof['authors'][0]['binding_rule'] == 'same_name_own_author_with_known_authority'
    assert proof['authors'][0]['authority_status'] == 'known'


@pytest.mark.parametrize('kind', ['isbn', 'publisher', 'imprint', 'volume', 'language', 'ndc', 'provider',
                                 'ambiguous_title', 'nested_title', 'unknown_role'])
def test_wrong_own_record_facts_do_not_grant_correspondence(kind):
    own = parts()
    if kind == 'isbn': own['source']['identifiers'][0]['value'] = '9781234567880'
    elif kind == 'publisher': own['source']['publisher'] = own['publisher_agents'][0]['name'] = '別出版社'
    elif kind == 'imprint': own['source']['series'] = 'モンスターコミックス ; 3'
    elif kind == 'volume': own['volume']['value'] = '2'
    elif kind == 'language': own['source']['language'] = 'eng'
    elif kind == 'ndc': own['source']['extra']['ndc_codes'] = ['913.6', '726.1']
    elif kind == 'provider': own['source']['provider_id'] = None
    elif kind == 'ambiguous_title': own['titles'].append({'value': '別の完全な題. 3', 'locator': OWN + DCT + 'title[2]'})
    elif kind == 'nested_title': own['titles'][0]['locator'] = OWN + '/contents[1]' + DCT + 'title[1]'
    else:
        own = parts(authority=False)
        own['source']['contributors'][0]['role'] = 'illustrator'
    assert match_futaba_reference(reference(), own) is None


@pytest.mark.parametrize('kind', ['isbn', 'isbn_literal', 'label', 'label_null', 'filter', 'label_id', 'role', 'role_literal', 'form', 'publisher'])
def test_official_unknown_or_incompatible_source_facts_are_held(kind):
    ref = reference()
    if kind == 'isbn': ref['isbn'] = None
    elif kind == 'isbn_literal': ref['isbn_source_literal'] = None
    elif kind == 'label': ref['label'] = 'Mノベルス'
    elif kind == 'label_null':
        ref['label'] = ref['source_scope_membership']['card_label_literal'] = None
        ref['label_literals'] = []
    elif kind == 'filter': ref['source_scope_membership']['request_url'] = API.replace('media=0', 'media=2')
    elif kind == 'label_id': ref['source_scope_membership']['source_label_id_literal'] = 15
    elif kind == 'role': ref['credits'][0]['role'] = 'unknown'
    elif kind == 'role_literal': ref['credit_literals'][0]['role'] = 'イラスト'
    elif kind == 'form': ref['form'] = 'コミック'
    else: ref['publisher'] = '双葉社'
    assert match_futaba_reference(ref, parts()) is None


@pytest.mark.parametrize('role', ['発売', '頒布'])
def test_only_explicit_own_sales_or_distribution_may_accompany_sole_publisher(role):
    own = parts()
    loc = OWN + DCT + 'publisher[5]'
    own['source']['publisher'] += '; 別社'
    own['publisher_agents'].append({'name': '別社', 'locator': loc, 'structured_agent': True,
        'description_values': [role], 'description_locators': [loc + '/description[1]']})
    assert match_futaba_reference(reference(), own)
    own['publisher_agents'][1]['description_values'] = []
    assert match_futaba_reference(reference(), own) is None


def test_all_official_authors_must_bind_and_illustrator_never_substitutes():
    ref = reference()
    ref['credits'].append({'role': 'author', 'name': '共著者'})
    ref['credit_literals'].append({'role': '著', 'name': '共著者', 'role_field': 'author_type_name', 'locator': POINTER + '/book_authors/2'})
    own = parts()
    assert match_futaba_reference(ref, own) is None
    own['source']['contributors'].append({'name': '共著者', 'role': 'author', 'identifiers': []})
    own['literal_authors'].append({'name': '共著者', 'literal': '共著者 著', 'locator': OWN + DC + 'creator[5]'})
    proof = match_futaba_reference(ref, own)
    assert [a['authority_status'] for a in proof['authors']] == ['known', 'unknown']


def test_exact_complete_titles_and_pure_no_io_contract(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError('pure matcher must not perform I/O')
    monkeypatch.setattr('builtins.open', reject)
    assert match_futaba_reference(reference(), parts())
    ref = reference()
    ref['title'] += ' 特装版'
    assert match_futaba_reference(ref, parts()) is None


def mnovels_case(label='Ｍノベルス', *, page=1, ordinal=0):
    ref,own=reference(),parts()
    pointer=f'/books/book_informations/{ordinal}'
    encoded=quote(label)
    api=API.replace(quote(LABEL),encoded).replace('&page=1&',f'&page={page}&')
    ref.update(label=label,label_literals=[label],form='単行本')
    ref['source_evidence'][0].update(source_url=api,locator=pointer+': book_name/isbn_code/book_authors/label_name/label_id/publication_form_value')
    for index,literal in enumerate(ref['credit_literals']):
        literal['locator']=pointer+f'/book_authors/{index}'
    ref['source_scope_membership'].update(filter_literal=label,
        rendered_index_url=SEARCH.replace(quote(LABEL),encoded),request_url=api,
        source_label_id_literal=12 if label=='Ｍノベルス' else 21,source_label_id_locator=pointer+'/label_id',
        card_label_literal=label,card_label_locator=pointer+'/label_name',
        official_ui_route_proof='futaba-mnovels-observed-label-route')
    own['source']['series']=label+' ; 3'
    return ref,own


@pytest.mark.parametrize('label,page,ordinal',[
    ('Ｍノベルス',1,0),('Ｍノベルス',14,9),('Ｍノベルスｆ',1,0),('Ｍノベルスｆ',12,5),
])
def test_mnovels_full_label_query_terminal_card_and_explicit_roles_preserve_unknown_classification(label,page,ordinal):
    ref,own=mnovels_case(label,page=page,ordinal=ordinal)
    before=deepcopy((ref,own))
    assert reference_hold_reason(ref) is None
    proof=match_futaba_mnovels_reference(ref,own)
    assert proof['provider']=='futaba-mnovels'
    assert proof['scope']=='publisher_reference_to_ndl_bibliographic_record'
    assert proof['light_novel_status']=='unknown' and proof['official_lightnovel_tag'] is None
    assert proof['official_label']==label and proof['official_form']=='単行本'
    assert proof['official_publisher'] is None and proof['official_publisher_asserted'] is False
    assert proof['official_credit_literals']==ref['credit_literals']
    assert proof['source_scope_membership']==ref['source_scope_membership']
    assert 'label_classification_context' not in proof and 'confirmed_scope' not in proof
    assert proof['same_edition_identity_asserted'] is False and proof['story_series_identity_asserted'] is False
    assert (ref,own)==before


@pytest.mark.parametrize('change',['page_over','terminal_card','label_id','query','unknown_isbn',
    'role_literal','role_unknown','source_tag','source_publisher','source_form','mixed_imprint'])
def test_mnovels_source_scope_and_imprint_cannot_be_inferred_or_promoted(change):
    ref,own=mnovels_case()
    if change=='page_over':ref,own=mnovels_case(page=15)
    elif change=='terminal_card':ref,own=mnovels_case('Ｍノベルスｆ',page=12,ordinal=6)
    elif change=='label_id':ref['source_scope_membership']['source_label_id_literal']=21
    elif change=='query':ref['source_evidence'][0]['source_url']=ref['source_evidence'][0]['source_url'].replace('media=0','media=2')
    elif change=='unknown_isbn':ref['isbn']=None;ref['isbn_source_literal']=None
    elif change=='role_literal':ref['credit_literals'][0]['role']='イラスト'
    elif change=='role_unknown':ref['credits'][0]['role']='unknown';ref['credit_literals'][0]['role']='作'
    elif change=='source_tag':ref['official_lightnovel_tag']=True
    elif change=='source_publisher':ref['publisher']='双葉社'
    elif change=='source_form':ref['form']='コミック'
    else:own['source']['series']='Ｍノベルス = 別レーベル ; 3'
    assert match_futaba_mnovels_reference(ref,own) is None


def test_mnovels_reuses_all_own_isbn_title_volume_writer_publisher_language_and_ndc_gates():
    for change in ('isbn','title','volume','writer','publisher','language','ndc'):
        ref,own=mnovels_case()
        if change=='isbn':own['source']['identifiers'][0]['value']='9781234567880'
        elif change=='title':ref['title']='完全な題３'
        elif change=='volume':own['volume']['value']='2'
        elif change=='writer':own['source']['contributors'][0]['name']='別著者';own['literal_authors']=[]
        elif change=='publisher':own['source']['publisher']=own['publisher_agents'][0]['name']='別社'
        elif change=='language':own['source']['language']='eng'
        else:own['source']['extra']['ndc_codes']=['726.1']
        assert match_futaba_mnovels_reference(ref,own) is None,change


@pytest.mark.parametrize('authority,digest',[
    (True,'edfe767aa343a287ab84514c88f4387228db3860a1a87681380826d4c557f7af'),
    (False,'5032e4940d6caa1be0d320954694b21dd5841785be595774773de9842cccbad3'),
])
def test_monster_complete_proof_is_unchanged_by_mnovels_profile(authority,digest):
    proof=match_futaba_reference(reference(),parts(authority=authority))
    assert sha256(json.dumps(proof,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==digest
