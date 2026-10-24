from copy import deepcopy
import hashlib

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
    agent['description_values'] = ['発売']
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
