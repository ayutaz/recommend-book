from copy import deepcopy
import hashlib
import json

import pytest

from recommend_book.light_novel_earthstar_match_v1 import match_earthstar_member


BASE = 'records[1]/dcndl:BibResource'
TITLE = BASE + '/{http://purl.org/dc/terms/}title[1]'
PARENT = 'https://earthstar-novel.com/book/detail/opaque-Work_A/'
CHILD = 'https://earthstar-novel.com/vol/detail/opaque-Child_A/'
AUTHORITY = 'http://id.ndl.go.jp/auth/entity/01234567'


def assertion(*, title='明示親作品', member='明示親作品３ 完全副題'):
    return {'kind': 'official_work_group_reference', 'reference_id': 'earthstar-work:opaque-Work_A',
        'official_work_reference_id': 'opaque-Work_A', 'source_url': PARENT, 'title': title,
        'series_title': title, 'index_title': title,
        'index_author_credits': [{'role': 'author', 'name': '架空著者'}],
        'index_author_inclusion_basis': 'explicit_author_role_in_own_official_index_card_not_inferred_from_children',
        'publisher': None, 'label': None, 'form': None, 'isbn': None,
        'story_series_asserted': False,
        'books': [{'official_child_reference_id': 'opaque-Child_A', 'source_url': CHILD, 'title': member,
            'credits': [{'role': 'author', 'name': '架空著者'}, {'role': 'illustrator', 'name': '絵師'}],
            'explicit_role_credit_literals': [{'role': 'author', 'role_display': '著者：', 'name': '架空著者'},
                {'role': 'illustrator', 'role_display': 'イラスト：', 'name': '絵師'}],
            'publisher': None, 'label': None, 'form': None, 'isbn': None,
            'source_selector': '.c-book-archive01[1] .c-card01[1] title/explicit credits/a[href]',
            'publisher_group_membership_explicit': True, 'story_series_asserted': False,
            'edition_identity': 'unassessed', 'source_evidence': {'source_url': PARENT,
                'raw_sha256': 'b' * 64, 'locator': '.c-book-archive01[1] .c-card01[1] title/explicit credits/a[href]'}}],
        'book_group_evidence': {'heading_literal': '書籍一覧',
            'group_selector': 'section.c-book-archive01 > .c-book-archive01__inner > .c-card01-list',
            'book_count': 1, 'membership_basis': 'explicit_child_cards_inside_this_parent_book_list'},
        'source_evidence': [{'source_url': 'https://earthstar-novel.com/book/', 'raw_sha256': 'a' * 64,
                'locator': '.c-card06[1] title/explicit credits/markers'},
            {'source_url': PARENT, 'raw_sha256': 'b' * 64,
                'locator': 'h1.c-book-intro01__head; section.c-book-archive01 own book-list'}]}


def parts(*, title='明示親作品 : 完全副題. 3', volume='3', imprint='EARTH STAR NOVEL'):
    return {'source': {'source_id': 'R100000002-I1',
        'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'provider_id': 'iss-ndl-opac-national',
        'publisher': 'アース・スターエンターテイメント', 'series': imprint, 'language': 'jpn',
        'extra': {'raw_locator': 'records[1]', 'ndc_codes': ['913.6']},
        'contributors': [{'name': '架空著者', 'role': 'author',
            'identifiers': [{'source': 'ndl-authority', 'value': AUTHORITY}]}]},
        'titles': [{'value': title, 'locator': TITLE}],
        'volume': {'value': volume, 'locator': BASE + '/volume[2]'} if volume else None,
        'publisher_agents': [{'name': 'アース・スターエンターテイメント', 'locator': BASE + '/publisher[3]',
            'structured_agent': True, 'description_values': [], 'description_locators': []}],
        'literal_authors': [], 'source_raw_sha256': 'c' * 64}


def test_matches_only_contained_own_edition_with_official_workgroup_context():
    official, own = assertion(), parts()
    before = deepcopy((official, own))
    proof = match_earthstar_member(official, own)
    assert proof['title'] == '明示親作品'
    assert proof['title_origin']['source_url'] == PARENT
    assert proof['authors'] == [{'name': '架空著者', 'authority_ids': [AUTHORITY]}]
    assert proof['volume'] == '3' and proof['volume_number'] == 3
    assert proof['member_source_url'] == CHILD
    assert proof['official_workgroup']['publisher'] is None
    assert proof['official_workgroup']['label'] is None
    assert proof['official_member']['isbn'] is None
    assert proof['ndl_member']['imprint'] == 'EARTH STAR NOVEL'
    assert proof['title_match']['locators'] == [TITLE, BASE + '/volume[2]']
    assert not proof['original_work_identity_asserted']
    assert not proof['story_series_identity_asserted']
    assert not proof['paper_ebook_same_edition_asserted']
    assert not proof['official_publisher_asserted']
    assert (official, own) == before


@pytest.mark.parametrize('imprint', ['EARTH STAR NOVEL',
    'EARTH STAR NOVEL = アース スター ノベル', 'EARTH STAR NOVEL ; ESN001'])
def test_exact_ndl_imprint_keeps_parallel_reading_and_parsed_number(imprint):
    proof = match_earthstar_member(assertion(), parts(imprint=imprint))
    assert proof is not None and proof['imprint'] == imprint
    assert proof['ndl_imprint_policy']['scope'] == 'own_ndl_imprint_only_not_official_label'


@pytest.mark.parametrize('imprint', [None, 'Bamboo comics', 'EARTH STAR COMICS',
    'EARTH STAR NOVEL外伝', 'EARTH STAR NOVEL 3', '別レーベル; EARTH STAR NOVEL'])
def test_unknown_comic_other_or_modified_imprint_is_held(imprint):
    assert match_earthstar_member(assertion(), parts(imprint=imprint)) is None


def test_accepts_only_explicit_distribution_in_addition_to_unique_publisher():
    own = parts()
    own['source']['publisher'] += '; 泰文堂'
    agent = {'name': '泰文堂', 'locator': BASE + '/publisher[4]', 'structured_agent': True,
        'description_values': ['頒布'], 'description_locators': [BASE + '/publisher[4]/description[1]']}
    own['publisher_agents'].append(agent)
    assert match_earthstar_member(assertion(), own) is not None
    agent['description_values'] = []
    assert match_earthstar_member(assertion(), own) is None
    agent['description_values'] = ['販売']
    assert match_earthstar_member(assertion(), own) is None


@pytest.mark.parametrize('change', ['foreign_publisher', 'publisher_projection', 'nested_publisher',
    'distribution_locator', 'third_party', 'language', 'mixed_ndc', 'empty_ndc', 'nested_title',
    'dc_main_only', 'foreign_volume', 'no_uri', 'illustrator', 'unknown_author', 'wrong_author'])
def test_own_primary_record_gates_cannot_be_replaced_by_other_metadata(change):
    own = parts()
    if change == 'foreign_publisher':
        own['source']['publisher'] = '竹書房'
        own['publisher_agents'][0]['name'] = '竹書房'
    elif change == 'publisher_projection': own['source']['publisher'] += '; 泰文堂'
    elif change == 'nested_publisher': own['publisher_agents'][0]['locator'] = BASE + '/contents[4]/publisher[3]'
    elif change == 'distribution_locator':
        own['source']['publisher'] += '; 泰文堂'
        own['publisher_agents'].append({'name': '泰文堂', 'locator': BASE + '/publisher[4]', 'structured_agent': True,
            'description_values': ['頒布'], 'description_locators': []})
    elif change == 'third_party':
        own['source']['source_id'] = 'R100000001-I1'
        own['source']['source_url'] = 'https://ndlsearch.ndl.go.jp/books/R100000001-I1'
    elif change == 'language': own['source']['language'] = 'eng'
    elif change == 'mixed_ndc': own['source']['extra']['ndc_codes'] += ['726.1']
    elif change == 'empty_ndc': own['source']['extra']['ndc_codes'] = []
    elif change == 'nested_title': own['titles'][0]['locator'] = BASE + '/contents[4]' + TITLE[len(BASE):]
    elif change == 'dc_main_only': own['titles'][0]['locator'] = BASE + '/{http://purl.org/dc/elements/1.1/}title[1]'
    elif change == 'foreign_volume': own['volume']['locator'] = BASE + '/contents[4]/volume[2]'
    elif change == 'no_uri': own['source']['contributors'][0]['identifiers'] = []
    elif change == 'illustrator': own['source']['contributors'][0]['role'] = 'illustrator'
    elif change == 'unknown_author': own['source']['contributors'][0]['role'] = 'unknown'
    else: own['source']['contributors'][0]['name'] = '別の著者'
    assert match_earthstar_member(assertion(), own) is None


@pytest.mark.parametrize('member', ['明示親作品２ 完全副題', '明示親作品３',
    '明示親作品３ 別副題', '明示親作品外伝３ 完全副題',
    '明示親作品３ 完全副題【電子版限定】', '明示親作品３ 完全副題 上'])
def test_keeps_volume_spinoff_words_all_subtitle_words_and_edition_markers(member):
    assert match_earthstar_member(assertion(member=member), parts()) is None


def test_only_entire_explicit_canonical_prefix_may_compose_with_complete_ndl_title():
    own = parts(title='完全副題. 3')
    proof = match_earthstar_member(assertion(member='明示親作品 完全副題３'), own)
    assert proof['title_match']['official_workgroup_prefix'] == '明示親作品'
    assert match_earthstar_member(assertion(title='明示親作品外伝', member='明示親作品 完全副題３'), own) is None


@pytest.mark.parametrize('change', ['unknown_parent_author', 'child_author', 'unknown_child_author',
    'opaque_id', 'child_url', 'child_parent', 'membership', 'heading', 'group_count',
    'missing_sha', 'different_parent_sha', 'official_publisher', 'official_label', 'official_form', 'official_isbn'])
def test_official_metadata_scope_is_explicit_and_unknowns_stay_unknown(change):
    official = assertion()
    child = official['books'][0]
    if change == 'unknown_parent_author': official['index_author_credits'][0]['role'] = 'unknown'
    elif change == 'child_author': child['credits'][0]['name'] = '別の著者'
    elif change == 'unknown_child_author': child['credits'][0]['role'] = 'unknown'
    elif change == 'opaque_id': official['official_work_reference_id'] = 'other'
    elif change == 'child_url': child['source_url'] = 'https://example.com/vol/1'
    elif change == 'child_parent': child['source_evidence']['source_url'] = PARENT.replace('opaque-Work_A', 'other')
    elif change == 'membership': child['publisher_group_membership_explicit'] = False
    elif change == 'heading': official['book_group_evidence']['heading_literal'] = '関連書籍'
    elif change == 'group_count': official['book_group_evidence']['book_count'] = 2
    elif change == 'missing_sha': official['source_evidence'][0]['raw_sha256'] = ''
    elif change == 'different_parent_sha': child['source_evidence']['raw_sha256'] = 'd' * 64
    elif change == 'official_publisher': official['publisher'] = 'アース・スターエンターテイメント'
    elif change == 'official_label': official['label'] = 'EARTH STAR NOVEL'
    elif change == 'official_form': official['form'] = '小説'
    else: official['isbn'] = '9781234567897'
    assert match_earthstar_member(official, parts()) is None


def test_ambiguous_complete_member_matches_are_held():
    official = assertion()
    child = deepcopy(official['books'][0])
    child.update(official_child_reference_id='opaque-Child_B', source_url=CHILD.replace('_A/', '_B/'))
    official['books'].append(child)
    official['book_group_evidence']['book_count'] = 2
    assert match_earthstar_member(official, parts()) is None


def test_edition_bound_dated_author_fallback_requires_explicit_own_writer_literal():
    own = parts()
    own['source']['contributors'][0]['name'] = '架空, 著者, 1965-'
    own['literal_authors'] = [{'name': '架空著者', 'literal': '架空著者 著',
        'locator': BASE + '/{http://purl.org/dc/elements/1.1/}creator[4]'}]
    proof = match_earthstar_member(assertion(), own)
    assert proof['author_identity_scope'] == 'edition_bound_dated_own_heading_and_explicit_writer_literal_only'
    assert proof['global_author_alias_asserted'] is False
    own['literal_authors'][0]['locator'] = BASE + '/contents[4]/{http://purl.org/dc/elements/1.1/}creator[4]'
    assert match_earthstar_member(assertion(), own) is None


def test_direct_known_authority_path_precedes_dated_fallback(monkeypatch):
    monkeypatch.setattr('recommend_book.light_novel_earthstar_match_v1.dated_own_author_bindings',
        lambda *args: pytest.fail('fallback must not replace direct evidence'))
    assert match_earthstar_member(assertion(), parts()) is not None


def test_pure_matcher_performs_no_file_io(monkeypatch):
    monkeypatch.setattr('builtins.open', lambda *args, **kwargs: pytest.fail('unexpected I/O'))
    assert match_earthstar_member(assertion(), parts()) is not None


def test_core_adapter_reference_hash_requires_exact_url_and_provider_index():
    official = assertion()
    official['index_id'] = 'earthstar-explicit-work-group'
    official['reference_id'] = 'publisher-series:' + hashlib.sha256(PARENT.encode()).hexdigest()[:24]
    assert match_earthstar_member(official, parts()) is not None
    official['index_id'] = 'another-provider'
    assert match_earthstar_member(official, parts()) is None
    official['index_id'] = 'earthstar-explicit-work-group'
    official['reference_id'] = 'publisher-series:' + '0' * 24
    assert match_earthstar_member(official, parts()) is None


@pytest.mark.parametrize('provider', [None, 'missing', 'third-party'])
def test_own_ndl_provider_is_required_and_must_be_reviewed(provider):
    own = parts()
    if provider == 'missing':
        own['source'].pop('provider_id')
    else:
        own['source']['provider_id'] = provider
    assert match_earthstar_member(assertion(), own) is None


def extended_parts(title, dc_title, volume='2'):
    own = parts(title=title, volume=volume)
    own['titles'].append({'value': dc_title,
        'locator': BASE + '/{http://purl.org/dc/elements/1.1/}title[9]'})
    if volume:
        own['volume']['locator'] = BASE + '/{http://ndl.go.jp/dcndl/terms/}volume[10]'
    return own


def sale_agent(own):
    own['source']['publisher'] += '; 泰文堂'
    locator = BASE + '/{http://purl.org/dc/terms/}publisher[16]'
    row = {'name': '泰文堂', 'locator': locator, 'structured_agent': True,
        'description_values': ['発売'], 'description_locators': [locator
            + '/{http://xmlns.com/foaf/0.1/}Agent[1]/{http://purl.org/dc/terms/}description[3]']}
    own['publisher_agents'].append(row)
    return row


def test_explicit_taibundo_sale_is_separate_from_the_unique_publisher():
    own = parts()
    sale = sale_agent(own)
    proof = match_earthstar_member(assertion(), own)
    assert proof['rule_version'] == 'earthstar-explicit-workgroup-member-match-v3'
    policy = proof['ndl_publisher_policy']
    assert policy['publisher_agent']['name'] == 'アース・スターエンターテイメント'
    assert policy['sales_agents'] == [{'name': '泰文堂', 'description': '発売',
        'locator': sale['locator'], 'description_locator': sale['description_locators'][0]}]
    assert not proof['official_publisher_asserted']


@pytest.mark.parametrize('change', ['unknown_role', 'mixed_role', 'other_seller', 'unstructured',
    'missing_description_locator', 'foreign_description', 'other_description_namespace',
    'two_actual_publishers', 'duplicate_seller'])
def test_sale_expansion_requires_the_explicit_same_own_agent_role(change):
    own = parts()
    sale = sale_agent(own)
    if change == 'unknown_role': sale['description_values'] = []
    elif change == 'mixed_role': sale['description_values'] = ['発売', '出版']
    elif change == 'other_seller': sale['name'] = '別社'
    elif change == 'unstructured': sale['structured_agent'] = False
    elif change == 'missing_description_locator': sale['description_locators'] = []
    elif change == 'foreign_description':
        sale['description_locators'][0] = BASE + '/contents[4]/{http://purl.org/dc/terms/}description[3]'
    elif change == 'other_description_namespace':
        sale['description_locators'][0] = sale['description_locators'][0].replace(
            'http://purl.org/dc/terms/', 'https://example.com/fake/')
    elif change == 'two_actual_publishers':
        own['publisher_agents'].append({**deepcopy(own['publisher_agents'][0]), 'name': '別出版社'})
        own['source']['publisher'] += '; 別出版社'
    else: own['publisher_agents'].append(deepcopy(sale))
    assert match_earthstar_member(assertion(), own) is None


@pytest.mark.parametrize('raw,dc,member,kind', [
    ('明示親作品 = The complete title. 2', '明示親作品', '明示親作品２', 'explicit_parallel_title'),
    ('明示親作品 = The complete title. : 完全副題. 2', '明示親作品 : 完全副題',
        '明示親作品２～完全副題～', 'explicit_parallel_title'),
    ('明示親作品!? 2', '明示親作品!?', '明示親作品!?２', 'explicit_repeated_volume'),
    ('明示親作品 : 完全副題? 2', '明示親作品 : 完全副題?',
        '明示親作品２～完全副題?～', 'explicit_repeated_volume'),
])
def test_fallback_retains_complete_own_dc_dct_parallel_subtitle_and_explicit_volume(raw, dc, member, kind):
    official, own = assertion(member=member), extended_parts(raw, dc)
    before = deepcopy((official, own))
    proof = match_earthstar_member(official, own)
    assert proof['rule_version'] == 'earthstar-explicit-workgroup-member-match-v3'
    title = proof['title_match']
    assert title['source_value'] == raw
    assert title['own_dc_title_evidence'] == [own['titles'][1]]
    assert title['own_dct_title_evidence'] == [own['titles'][0]]
    assert title['fallback_kind'] == kind
    assert title['volume'] == '2'
    assert title['locators'] == [TITLE, own['titles'][1]['locator'], own['volume']['locator']]
    assert title['parallel_translation_identity'] == 'unknown'
    assert not proof['original_work_identity_asserted']
    assert not proof['story_series_identity_asserted']
    assert (official, own) == before


@pytest.mark.parametrize('change', ['dc_missing', 'dc_wrong_main', 'dc_missing_subtitle',
    'dct_extra_subtitle', 'dc_other_namespace', 'dct_other_namespace', 'nested_dc', 'nested_dct',
    'foreign_volume', 'wrong_volume_namespace', 'missing_volume', 'wrong_volume', 'year_suffix',
    'second_conflicting_dc', 'second_conflicting_dct', 'empty_parallel', 'japanese_parallel',
    'ambiguous_parallel', 'ambiguous_parallel_subtitle', 'official_missing_subtitle', 'official_other_volume'])
def test_parallel_fallback_holds_missing_ambiguous_or_unbound_parts(change):
    own = extended_parts('明示親作品 = The complete title. : 完全副題. 2', '明示親作品 : 完全副題')
    official = assertion(member='明示親作品２～完全副題～')
    if change == 'dc_missing': own['titles'].pop()
    elif change == 'dc_wrong_main': own['titles'][1]['value'] = '別作品 : 完全副題'
    elif change == 'dc_missing_subtitle': own['titles'][1]['value'] = '明示親作品'
    elif change == 'dct_extra_subtitle': own['titles'][0]['value'] = '明示親作品 = English : 完全副題追加. 2'
    elif change == 'dc_other_namespace': own['titles'][1]['locator'] = BASE + '/{https://example.com/}title[9]'
    elif change == 'dct_other_namespace': own['titles'][0]['locator'] = BASE + '/{https://example.com/}title[8]'
    elif change == 'nested_dc': own['titles'][1]['locator'] = BASE + '/contents[4]/' + own['titles'][1]['locator'][len(BASE)+1:]
    elif change == 'nested_dct': own['titles'][0]['locator'] = BASE + '/contents[4]/' + TITLE[len(BASE)+1:]
    elif change == 'foreign_volume': own['volume']['locator'] = BASE + '/contents[4]/volume[10]'
    elif change == 'wrong_volume_namespace': own['volume']['locator'] = BASE + '/{https://example.com/}volume[10]'
    elif change == 'missing_volume': own['volume'] = None
    elif change == 'wrong_volume': own['volume']['value'] = '3'
    elif change == 'year_suffix': own['titles'][0]['value'] = '明示親作品 = English : 完全副題 2022'
    elif change == 'second_conflicting_dc': own['titles'].append({**own['titles'][1], 'value': '明示親作品 : 別副題'})
    elif change == 'second_conflicting_dct': own['titles'].append({**own['titles'][0], 'value': '明示親作品 = English : 別副題. 2'})
    elif change == 'empty_parallel': own['titles'][0]['value'] = '明示親作品 =  : 完全副題. 2'
    elif change == 'japanese_parallel': own['titles'][0]['value'] = '明示親作品 = 別の日本語題 : 完全副題. 2'
    elif change == 'ambiguous_parallel': own['titles'][0]['value'] = '明示親作品 = English = Other : 完全副題. 2'
    elif change == 'ambiguous_parallel_subtitle': own['titles'][0]['value'] = '明示親作品 = English:unparsed subtitle : 完全副題. 2'
    elif change == 'official_missing_subtitle': official['books'][0]['title'] = '明示親作品２'
    else: official['books'][0]['title'] = '明示親作品３～完全副題～'
    assert match_earthstar_member(official, own) is None


@pytest.mark.parametrize('dc,volume,raw', [
    ('明示親作品!?', '3', '明示親作品!? 2'),
    ('明示親作品', '2', '明示親作品!? 2'),
    ('明示親作品!?', '2', '明示親作品!? 2022'),
    ('明示親作品!?', '上', '明示親作品!? 上'),
])
def test_repeated_volume_is_not_an_arbitrary_numeric_or_word_removal(dc, volume, raw):
    assert match_earthstar_member(assertion(member='明示親作品!?２'), extended_parts(raw, dc, volume)) is None


def test_old_exact_unnumbered_title_path_does_not_infer_a_volume():
    proof = match_earthstar_member(assertion(member='明示親作品!?２'),
        extended_parts('明示親作品!? 2', '明示親作品!?', None))
    assert proof['rule_version'] == 'earthstar-explicit-workgroup-member-match-v1'
    assert proof['volume'] is None and proof['volume_number'] is None


def test_new_parallel_and_sale_formats_compose_without_inference_or_io(monkeypatch):
    own = extended_parts('明示親作品 = English : 完全副題. 2', '明示親作品 : 完全副題')
    sale_agent(own)
    before = deepcopy(own)
    monkeypatch.setattr('builtins.open', lambda *args, **kwargs: pytest.fail('unexpected I/O'))
    proof = match_earthstar_member(assertion(member='明示親作品２～完全副題～'), own)
    assert proof['rule_version'] == 'earthstar-explicit-workgroup-member-match-v3'
    assert proof['title_match']['explicit_parallel_forms'][0]['value'] == 'English'
    assert proof['ndl_publisher_policy']['sales_agents'][0]['description'] == '発売'
    assert proof['official_workgroup']['publisher'] is None
    assert proof['official_workgroup']['label'] is None
    assert own == before


def test_strict_complete_title_match_precedes_any_new_title_fallback(monkeypatch):
    monkeypatch.setattr('recommend_book.light_novel_earthstar_match_v1._fallback_titles',
        lambda *args: pytest.fail('fallback must not replace a strict proof'))
    assert match_earthstar_member(assertion(), parts())['rule_version'].endswith('-v1')


def test_fallback_can_only_compose_the_entire_explicit_group_prefix():
    own = extended_parts('別の完全題 = Another complete title. 2', '別の完全題')
    proof = match_earthstar_member(assertion(member='明示親作品 別の完全題２'), own)
    assert proof['title_match']['official_workgroup_prefix'] == '明示親作品'
    assert proof['title_match']['fallback_kind'] == 'explicit_parallel_title'
    assert proof['rule_version'].endswith('-v3')
    assert match_earthstar_member(assertion(member='明示親作品外伝 別の完全題２'), own) is None


@pytest.mark.parametrize('form,expected', [
    ('direct', '309cc5208c1edb1da13b676e15f620c8d364df794216dbc42ac73fdde617a5e3'),
    ('dated', 'c8f8c6268853a87ee71bbfdd7b25ed43ae701ea279d401e7dd679efee2c1e217'),
    ('distribution', '1e0db1aacf1c52ef6f7b51678671f3ecf74db8e88579e2b78728478518f3fa59'),
])
def test_existing_successful_proofs_are_byte_identical(form, expected):
    own = parts()
    if form == 'dated':
        own['source']['contributors'][0]['name'] = '架空, 著者, 1965-'
        own['literal_authors'] = [{'name': '架空著者', 'literal': '架空著者 著',
            'locator': BASE + '/{http://purl.org/dc/elements/1.1/}creator[4]'}]
    elif form == 'distribution':
        own['source']['publisher'] += '; 泰文堂'
        own['publisher_agents'].append({'name': '泰文堂', 'locator': BASE + '/publisher[4]',
            'structured_agent': True, 'description_values': ['頒布'],
            'description_locators': [BASE + '/publisher[4]/description[1]']})
    proof = match_earthstar_member(assertion(), own)
    assert hashlib.sha256(json.dumps(proof, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest() == expected


@pytest.mark.parametrize('raw,dc,member', [
    ('明示親作品 = English. 2', '明示親作品', '明示親作品２'),
    ('明示親作品 = English : 完全副題. 2', '明示親作品 : 完全副題', '明示親作品２～完全副題～'),
    ('明示親作品!? 2', '明示親作品!?', '明示親作品!?２'),
])
def test_existing_candidate_keys_include_new_positive_formats(raw, dc, member):
    from recommend_book.light_novel_explicit_series_match_v1 import candidate_ndl_title_keys, candidate_title_keys
    own, official = extended_parts(raw, dc), assertion(member=member)
    own['source']['titles'] = [{'value': raw}, {'value': dc}]
    own['source']['volume'] = '2'
    assert candidate_ndl_title_keys(own['source']) & candidate_title_keys(official)
