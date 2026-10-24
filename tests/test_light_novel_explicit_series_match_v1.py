from copy import deepcopy
import hashlib
import json

import pytest

from recommend_book.light_novel_explicit_series_match_v1 import (
    candidate_ndl_title_keys, candidate_title_keys, dated_own_author_bindings, match_cobalt_member,
)


ANNIVERSARY = 'https://orangebunko.shueisha.co.jp/feature/cobalt50th_specialbooks'
EBOOKS = 'https://ebooks.shueisha.co.jp/cobalt/'
BASE = 'records[1]/dcndl:BibResource'
TITLE = BASE + '/{http://purl.org/dc/terms/}title[1]'


def assertion(*, series='明示シリーズ', member='明示シリーズ３ 完全副題'):
    return {'kind': 'official_series_reference', 'official_series_id': '52807',
        'official_series_url': 'https://www.shueisha.co.jp/books/search/search.html?seriesid=52807',
        'series_title': series, 'publisher': None, 'label': None,
        'series_author': {'role': '著者', 'display_text': '架空著者', 'role_source': 'ebook_author_list',
            'role_label_selector': 'h2 img[alt="著者50音別書籍リスト"]',
            'author_field_selector': 'dl#word_1 dd:nth-of-type(1) > b'},
        'independent_series_evidence': {'source_key': 'cobalt_anniversary', 'selector': 'ul.anthology-list li[1]',
            'label_text': 'シリーズ本編購入はこちら'},
        'ebook_group_evidence': {'source_key': 'ebook_author_list', 'selector': 'dl#word_1 dd[1] span em'},
        'books': [{'title': member, 'source_url': 'https://ebooks.shueisha.co.jp/detail.php?book=sc1',
            'publisher_book_id': 'sc1', 'author_display_text': '架空著者', 'isbn': None,
            'source_selector': 'dl#word_1 dd[2] a > b / span'}],
        'source_evidence': [{'source_url': ANNIVERSARY, 'raw_sha256': 'a' * 64, 'locator': 'ul.anthology-list li[1]'},
                            {'source_url': EBOOKS, 'raw_sha256': 'b' * 64, 'locator': 'dl#word_1 dd[1] span em'}]}


def parts(*, title='明示シリーズ : 完全副題. 3', volume='3'):
    return {'source': {'source_id': 'R100000002-I1',
        'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'publisher': '集英社', 'series': 'コバルト文庫', 'language': 'jpn',
        'extra': {'raw_locator': 'records[1]', 'ndc_codes': ['913.6']},
        'contributors': [{'name': '架空著者', 'role': 'author', 'identifiers': [
            {'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/01234567'}]}]},
        'titles': [{'value': title, 'locator': TITLE}],
        'volume': {'value': volume, 'locator': BASE + '/volume[2]'} if volume else None,
        'publisher_agents': [{'name': '集英社', 'locator': BASE + '/publisher[3]',
            'structured_agent': True, 'description_values': [], 'description_locators': []}],
        'source_raw_sha256': 'c' * 64}


def test_complete_official_member_with_known_authority_preserves_source_boundaries():
    official, own = assertion(), parts()
    before = deepcopy((official, own))
    proof = match_cobalt_member(official, own)
    assert proof['rule_version'] == 'cobalt-explicit-series-member-match-v1'
    assert proof['official_series']['official_series_id'] == '52807'
    assert proof['official_series']['publisher'] is None and proof['official_series']['label'] is None
    assert proof['official_member']['title'] == official['books'][0]['title']
    assert proof['title_match']['volume'] == '3'
    assert proof['title_match']['locators'] == [TITLE, BASE + '/volume[2]']
    assert proof['ndl_member']['imprint'] == 'コバルト文庫'
    assert proof['ndl_member']['author']['authorities'] == ['http://id.ndl.go.jp/auth/entity/01234567']
    assert proof['paper_ebook_same_edition_asserted'] is False
    assert proof['original_work_identity_asserted'] is False
    assert proof['official_publisher_asserted'] is False
    assert proof['title_origin']['source_url'] == official['official_series_url']
    assert proof['volume_number'] == 3 and proof['publisher'] == '集英社'
    assert proof['authors'] == [{'name': '架空著者', 'authority_ids': ['http://id.ndl.go.jp/auth/entity/01234567']}]
    creator = proof['ndl_member']['author']['contributors'][0]
    assert creator['own_primary_resource_locator'] == BASE
    assert creator['projection_pointer'] == '/source/contributors/0'
    assert (official, own) == before


def test_canonical_series_name_alone_never_establishes_member():
    assert match_cobalt_member(assertion(), parts(title='明示シリーズ', volume=None)) is None


def test_explicit_series_prefix_composes_with_complete_ndl_member_title():
    own = parts(title='完全副題. 3')
    proof = match_cobalt_member(assertion(member='明示シリーズ 完全副題３'), own)
    assert proof['title_match']['rule'] == 'explicit_series_prefix_plus_complete_ndl_title'
    assert proof['title_match']['official_series_prefix'] == '明示シリーズ'
    assert proof['title_match']['main'] == '完全副題'
    assert proof['title_match']['volume'] == '3'
    assert match_cobalt_member(assertion(series='明示シリーズ外伝', member='明示シリーズ 完全副題３'), own) is None


@pytest.mark.parametrize('member', ['明示シリーズ２ 完全副題', '明示シリーズ３',
    '明示シリーズ３ 別の副題', '明示シリーズ３ 完全副題【電子版限定】',
    '明示シリーズ外伝３ 完全副題', '別シリーズ３ 完全副題'])
def test_no_volume_subtitle_series_suffix_or_edition_marker_is_removed(member):
    assert match_cobalt_member(assertion(member=member), parts()) is None


def test_all_member_words_preserved_when_edition_marker_actually_matches():
    own = parts(title='明示シリーズ : 完全副題【電子版限定】. 3')
    proof = match_cobalt_member(assertion(member='明示シリーズ３ 完全副題【電子版限定】'), own)
    assert proof is not None and proof['paper_ebook_same_edition_asserted'] is False


@pytest.mark.parametrize('change', ['role', 'name', 'uri', 'literal_only'])
def test_own_author_requires_known_authority_and_exact_explicit_writer_name(change):
    own = parts()
    if change == 'role': own['source']['contributors'][0]['role'] = 'illustrator'
    elif change == 'name': own['source']['contributors'][0]['name'] = '別の著者'
    elif change == 'uri': own['source']['contributors'][0]['identifiers'][0]['value'] = 'http://id.ndl.go.jp/auth/entity/notanid'
    else:
        own['source']['contributors'][0]['identifiers'] = []
        own['literal_authors'] = [{'name': '架空著者', 'literal': '架空著者 著', 'locator': BASE + '/creator[4]'}]
    assert match_cobalt_member(assertion(), own) is None


@pytest.mark.parametrize('change', ['official_role', 'official_id', 'official_url', 'member_url',
    'member_author', 'missing_source_pin', 'inferred_publisher', 'inferred_label'])
def test_official_scope_and_unknown_publisher_are_not_guessed(change):
    official = assertion()
    if change == 'official_role': official['series_author']['role'] = 'unknown'
    elif change == 'official_id': official['official_series_id'] = '99999'
    elif change == 'official_url': official['official_series_url'] += '&extra=1'
    elif change == 'member_url': official['books'][0]['source_url'] = 'https://example.com/sc1'
    elif change == 'member_author': official['books'][0]['author_display_text'] = '別の著者'
    elif change == 'missing_source_pin': official['source_evidence'][0]['raw_sha256'] = ''
    elif change == 'inferred_publisher': official['publisher'] = '集英社'
    else: official['label'] = 'コバルト文庫'
    assert match_cobalt_member(official, parts()) is None


@pytest.mark.parametrize('change', ['publisher', 'co_publisher', 'language', 'ndc', 'contents', 'main_only'])
def test_own_bibliographic_policy_is_not_official_product_assertion(change):
    own = parts()
    if change == 'publisher':
        own['source']['publisher'] = '別出版社'
        own['publisher_agents'][0]['name'] = '別出版社'
    elif change == 'co_publisher':
        own['source']['publisher'] = '集英社; 別会社'
        own['publisher_agents'].append({'name': '別会社', 'locator': BASE + '/publisher[4]',
            'structured_agent': True, 'description_values': [], 'description_locators': []})
    elif change == 'language': own['source']['language'] = 'eng'
    elif change == 'ndc': own['source']['extra']['ndc_codes'] = ['726.1']
    elif change == 'contents': own['titles'][0]['locator'] = BASE + '/contents[4]' + '/{http://purl.org/dc/terms/}title[1]'
    else: own['titles'][0]['locator'] = BASE + '/{http://purl.org/dc/elements/1.1/}title[1]'
    assert match_cobalt_member(assertion(), own) is None


def test_publisher_can_have_only_explicit_distribution_agents():
    own = parts()
    own['source']['publisher'] = '集英社; 明示流通'
    agent = {'name': '明示流通', 'locator': BASE + '/publisher[4]', 'structured_agent': True,
             'description_values': ['頒布'], 'description_locators': [BASE + '/publisher[4]/description[1]']}
    own['publisher_agents'].append(agent)
    assert match_cobalt_member(assertion(), own) is not None
    agent['description_values'] = ['発売']
    assert match_cobalt_member(assertion(), own) is None


def test_ambiguous_member_urls_are_held():
    official = assertion()
    official['books'].append({**official['books'][0], 'source_url': 'https://ebooks.shueisha.co.jp/detail.php?book=sc2',
                              'publisher_book_id': 'sc2'})
    assert match_cobalt_member(official, parts()) is None


def test_candidate_helpers_are_overinclusive_without_creating_positive_proof():
    official = assertion(member='明示シリーズ 完全副題３')
    own = parts(title='完全副題. 3')
    assert candidate_title_keys(official) & candidate_ndl_title_keys(own['source'] | {
        'titles': [{'value': '完全副題. 3'}], 'volume': '3'})
    weak = parts(title='完全副題. 3')
    weak['source']['contributors'][0]['identifiers'] = []
    assert match_cobalt_member(official, weak) is None
    assert candidate_title_keys({}) == candidate_ndl_title_keys({}) == set()


@pytest.mark.parametrize('volume,number', [('０３', 3), ('上', None), ('第3巻', None)])
def test_volume_number_uses_only_an_explicit_digit_literal(volume, number):
    own = parts(title='明示シリーズ : 完全副題. ' + volume, volume=volume)
    official = assertion(member='明示シリーズ' + volume + ' 完全副題')
    proof = match_cobalt_member(official, own)
    assert proof['volume'] == volume and proof['volume_number'] == number


def test_candidate_keys_preserve_numbers_series_suffixes_and_all_subtitle_words():
    official = assertion(series='明示シリーズ外伝', member='明示シリーズ外伝３ 完全副題')
    keys = candidate_title_keys(official)
    assert '3完全副題' in keys
    assert '完全副題' not in keys
    assert '明示シリーズ3完全副題' not in keys
    source = {'titles': [{'value': '完全副題 : 省けない語. 3'}], 'volume': '3'}
    keys = candidate_ndl_title_keys(source)
    assert '完全副題3省けない語' in keys
    assert '完全副題3' not in keys


def test_pure_functions_do_not_open_files(monkeypatch):
    monkeypatch.setattr('builtins.open', lambda *a, **k: pytest.fail('unexpected I/O'))
    assert match_cobalt_member(assertion(), parts()) is not None


def dated_author_parts(*, heading='架空, 著者, 1965-', literal='架空著者 著',
                       namespace='http://purl.org/dc/elements/1.1/'):
    own = parts()
    own['source']['contributors'][0]['name'] = heading
    own['source']['contributors'].append({'name': '架空著者', 'role': 'author', 'identifiers': []})
    own['literal_authors'] = [{'name': '架空著者', 'literal': literal,
        'locator': BASE + '/{' + namespace + '}creator[4]'}]
    return own


@pytest.mark.parametrize('heading,birth,death', [('架空, 著者, 1965-', '1965', None),
    ('架空, 著者, 1965-2020', '1965', '2020')])
def test_edition_bound_dated_author_heading_requires_same_own_writer_literal(heading, birth, death):
    own = dated_author_parts(heading=heading)
    before = deepcopy(own)
    proof = match_cobalt_member(assertion(), own)
    assert proof['rule_version'] == 'cobalt-explicit-series-member-match-v2'
    assert proof['authors'][0]['authority_ids'] == ['http://id.ndl.go.jp/auth/entity/01234567']
    author = proof['ndl_member']['author']['contributors'][0]
    evidence = author['edition_bound_corroboration']
    assert evidence['authority_heading'] == heading
    assert evidence['birth_year'] == birth and evidence['death_year'] == death
    assert evidence['literal_credit'] == own['literal_authors'][0]
    assert evidence['authority_projection_pointer'] == '/source/contributors/0'
    assert evidence['scope'] == 'this_own_bibliographic_record_only'
    assert proof['global_author_alias_asserted'] is False
    assert proof['global_author_identity_asserted'] is False
    assert own == before


@pytest.mark.parametrize('literal,namespace', [('架空著者 著者', 'http://purl.org/dc/elements/1.1/'),
    ('架空著者 作', 'http://purl.org/dc/terms/'), ('架空著者 原作', 'http://purl.org/dc/terms/')])
def test_dated_heading_accepts_only_explicit_own_writer_roles(literal, namespace):
    assert match_cobalt_member(assertion(), dated_author_parts(literal=literal, namespace=namespace)) is not None


@pytest.mark.parametrize('heading', ['架空著者, 1965-', '架空, 著者, 1965- 小説家',
    '架空, 著者, 1965年-', '架空, 著者, 1965-?', '架空, 著者, 65-',
    '架空, 著者, 1965-2020, 別名', '別の姓, 著者, 1965-',
    ' , 架空著者, 1965-', '架空著者, , 1965-'])
def test_dated_heading_does_not_strip_other_suffix_or_guess_other_creators(heading):
    assert match_cobalt_member(assertion(), dated_author_parts(heading=heading)) is None


@pytest.mark.parametrize('change', ['no_literal', 'literal_other_name', 'literal_no_role', 'literal_editor',
    'literal_other_parent', 'literal_other_namespace', 'literal_nested', 'authority_missing',
    'authority_other_namespace', 'creator_editor', 'multiple_uris', 'multiple_same_name_authorities'])
def test_dated_heading_corroboration_counterexamples_remain_held(change):
    own = dated_author_parts()
    contributor = own['source']['contributors'][0]
    if change == 'no_literal': own['literal_authors'] = []
    elif change == 'literal_other_name': own['literal_authors'][0]['name'] = '別の著者'
    elif change == 'literal_no_role': own['literal_authors'][0]['literal'] = '架空著者'
    elif change == 'literal_editor': own['literal_authors'][0]['literal'] = '架空著者 編者'
    elif change == 'literal_other_parent': own['literal_authors'][0]['locator'] = 'other/{http://purl.org/dc/terms/}creator[4]'
    elif change == 'literal_other_namespace': own['literal_authors'][0]['locator'] = BASE + '/{http://example.com/}creator[4]'
    elif change == 'literal_nested': own['literal_authors'][0]['locator'] = BASE + '/contents/{http://purl.org/dc/terms/}creator[4]'
    elif change == 'authority_missing': contributor['identifiers'] = []
    elif change == 'authority_other_namespace': contributor['identifiers'][0]['value'] = 'https://example.com/auth/entity/01234567'
    elif change == 'creator_editor': contributor['role'] = 'editor'
    elif change == 'multiple_uris': contributor['identifiers'].append({'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/09999999'})
    else:
        other = deepcopy(contributor)
        other['identifiers'][0]['value'] = 'http://id.ndl.go.jp/auth/entity/09999999'
        own['source']['contributors'].append(other)
    assert match_cobalt_member(assertion(), own) is None


def test_strict_direct_author_proof_is_unchanged_even_when_fallback_would_be_ambiguous():
    strict = match_cobalt_member(assertion(), parts())
    assert hashlib.sha256(json.dumps(strict, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest() == '6722d827238889fd10644dcda47e2cd909f86ae4e34b39449e4646b9a1064991'
    own = parts()
    own['source']['contributors'] += dated_author_parts()['source']['contributors']
    own['literal_authors'] = dated_author_parts()['literal_authors']
    second = deepcopy(own['source']['contributors'][1])
    second['identifiers'][0]['value'] = 'http://id.ndl.go.jp/auth/entity/09999999'
    own['source']['contributors'].append(second)
    assert match_cobalt_member(assertion(), own) == strict


def test_dated_binding_is_reusable_but_never_supplies_a_provider_publisher_policy():
    own = dated_author_parts()
    own['source']['publisher'] = '別出版社'
    own['publisher_agents'][0]['name'] = '別出版社'
    before = deepcopy(own)
    bindings = dated_own_author_bindings(own, '架空著者')
    assert len(bindings) == 1
    assert bindings[0]['authority_ids'] == ['http://id.ndl.go.jp/auth/entity/01234567']
    assert bindings[0]['edition_bound_corroboration']['literal_credit']['name'] == '架空著者'
    assert bindings[0]['edition_bound_corroboration']['authority_heading'] == '架空, 著者, 1965-'
    assert bindings[0]['own_primary_resource_locator'] == BASE
    assert match_cobalt_member(assertion(), own) is None
    assert own == before


@pytest.mark.parametrize('change', ['missing_sha', 'other_resource', 'two_own_parents', 'malformed'])
def test_generic_dated_bindings_hold_unbound_or_ambiguous_input(change):
    own = dated_author_parts()
    if change == 'missing_sha': own['source_raw_sha256'] = ''
    elif change == 'other_resource': own['titles'][0]['locator'] = 'other/{http://purl.org/dc/terms/}title[1]'
    elif change == 'two_own_parents': own['publisher_agents'].append({**own['publisher_agents'][0], 'locator': 'other/publisher[3]'})
    else: own = {}
    assert dated_own_author_bindings(own, '架空著者') == []
