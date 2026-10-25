from copy import deepcopy
import hashlib
import json
import unicodedata

import pytest

from recommend_book.light_novel_wikipedia_credit_v1 import (
    match_novel_credit, match_novel_credit_v2, verify_novel_credit, verify_novel_credit_v2,
)


TITLE = '悪役令嬢は溺愛ルートに入りました!?'
QID = 'Q123532166'
REVISION = 111326970
OWN = 'records[1]/dcndl:BibResource[@rdf:about=https://ndlsearch.ndl.go.jp/books/R100000002-I1]'
DC = 'http://purl.org/dc/elements/1.1/'
DCT = 'http://purl.org/dc/terms/'
DCNDL = 'http://ndl.go.jp/dcndl/terms/'
AUTHORITY = 'http://id.ndl.go.jp/auth/entity/001323408'
TEXT = """{{Infobox animanga/Header
| タイトル = 悪役令嬢は溺愛ルートに入りました!?
}}
{{Infobox animanga/Novel
| 著者 = 十夜
| イラスト = 宵マチ
| 出版社 = [[スクウェア・エニックス]]
| レーベル = [[SQEXノベル]]
}}
{{Infobox animanga/Manga
| 作者 = 十夜・宵マチ
| 作者表記 = 原作
| 作画 = さくまれん
| 出版社 = スクウェア・エニックス
| レーベル = ガンガンコミックスUP!
}}
{{Infobox animanga/Footer}}
『'''悪役令嬢は溺愛ルートに入りました!?'''』（あくやくれいじょう）は、十夜による[[日本]]の[[ライトノベル]]。イラストは宵マチが担当している。

== 漫画 ==
漫画版の原作は十夜・宵マチ。
"""


def inputs(text=TEXT):
    metadata = {'id': QID, 'kind': 'series', 'title': TITLE, 'authors': [],
        'publishers': [{'id': 'Q112398828', 'label': 'SQEXノベル'}]}
    entity = {'id': QID, 'claims': {}, 'sitelinks': {'jawiki': {'title': TITLE}}}
    page = {'pageid': 5014860, 'ns': 0, 'title': TITLE,
        'pageprops': {'wikibase_item': QID}, 'revisions': [{'revid': REVISION,
            'slots': {'main': {'contentmodel': 'wikitext', 'contentformat': 'text/x-wiki', 'content': text}}}]}
    return metadata, entity, {'query': {'pages': [page]}}


def credit():
    return verify_novel_credit(*inputs(), REVISION)


def loc(namespace, field, number):
    return OWN + '/{' + namespace + '}' + field + f'[{number}]'


def parts():
    return {'source': {'source_id': 'R100000002-I1',
        'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'provider_id': 'iss-ndl-opac-national', 'record_kind': 'edition',
        'publisher': 'スクウェア・エニックス', 'series': 'SQEXノベル', 'language': 'jpn',
        'extra': {'ndc_codes': ['913.6']},
        'contributors': [{'name': '十夜', 'role': 'author',
            'identifiers': [{'source': 'ndl-authority', 'value': AUTHORITY}]},
            {'name': '十夜', 'role': 'author', 'identifiers': []},
            {'name': '宵マチ', 'role': 'illustrator', 'identifiers': []}]},
        'titles': [{'value': TITLE + '. 1', 'locator': loc(DCT, 'title', 1)},
            {'value': TITLE, 'locator': loc(DC, 'title', 2)}],
        'volume': {'value': '1', 'locator': loc(DCNDL, 'volume', 3)},
        'literal_authors': [{'name': '十夜', 'literal': '十夜 著', 'locator': loc(DC, 'creator', 4)}],
        'publisher_agents': [{'name': 'スクウェア・エニックス', 'locator': loc(DCT, 'publisher', 5),
            'structured_agent': True, 'description_values': [], 'description_locators': []}],
        'source_raw_sha256': 'a' * 64}


def normalize(value):
    return ''.join(unicodedata.normalize('NFKC', value).casefold().split())


def test_actual_novel_and_manga_structure_keep_work_specific_credit_separate():
    source = inputs()
    before = deepcopy(source)
    proof = verify_novel_credit(*source, REVISION)
    assert proof['title'] == TITLE and proof['author_names'] == ['十夜']
    assert proof['series_entry_id'] == 'lns:wd:' + QID
    assert proof['publisher'] == 'スクウェア・エニックス' and proof['imprint'] == 'SQEXノベル'
    assert proof['imprint_wikidata'] == {'qid': 'Q112398828', 'label': 'SQEXノベル'}
    assert proof['revision_id'] == REVISION and proof['page_id'] == 5014860
    assert proof['source_url'].endswith('oldid=' + str(REVISION))
    assert '/Novel[1]/著者' in proof['field_locators']['author_names']
    assert proof['attribution']['license'] == 'CC-BY-SA-4.0'
    assert proof['author_identity_asserted'] is False and proof['P50_backfilled'] is False
    assert proof['original_work_identity_asserted'] is False
    assert source == before
    assert '宵マチ' not in proof['author_names']


@pytest.mark.parametrize('mutation', ['qid', 'sitelink', 'namespace', 'disambiguation', 'revision',
    'second_page', 'second_revision', 'contentmodel', 'wd_author', 'metadata_author', 'wd_imprint'])
def test_article_identity_and_existing_author_or_imprint_conflicts_hold(mutation):
    metadata, entity, payload = inputs()
    page = payload['query']['pages'][0]
    if mutation == 'qid': page['pageprops']['wikibase_item'] = 'Q2'
    elif mutation == 'sitelink': entity['sitelinks']['jawiki']['title'] = '別作品'
    elif mutation == 'namespace': page['ns'] = 1
    elif mutation == 'disambiguation': page['pageprops']['disambiguation'] = ''
    elif mutation == 'revision': page['revisions'][0]['revid'] += 1
    elif mutation == 'second_page': payload['query']['pages'].append(deepcopy(page))
    elif mutation == 'second_revision': page['revisions'].append(deepcopy(page['revisions'][0]))
    elif mutation == 'contentmodel': page['revisions'][0]['slots']['main']['contentmodel'] = 'json'
    elif mutation == 'wd_author': entity['claims']['P50'] = [{'rank': 'normal', 'mainsnak': {'snaktype': 'somevalue'}}]
    elif mutation == 'metadata_author': metadata['authors'] = [{'id': 'Q2', 'label': '別著者'}]
    elif mutation == 'wd_imprint': metadata['publishers'][0]['label'] = '別レーベル'
    with pytest.raises(ValueError): verify_novel_credit(metadata, entity, payload, REVISION)


@pytest.mark.parametrize('text', [
    TEXT.replace('| 著者 = 十夜', '| 作者 = 十夜'),
    TEXT.replace('| 著者 = 十夜', '| 著者 = 宵マチ'),
    TEXT.replace('| 著者 = 十夜', '| 著者 = 十夜\n| 著者 = 十夜'),
    TEXT.replace('| 著者 = 十夜', '| 著者 = 十夜\n| 原作 = 他人'),
    TEXT.replace('| 著者 = 十夜', '| 著者 = 十夜・他人'),
    TEXT.replace('| レーベル = [[SQEXノベル]]\n', ''),
    TEXT.replace('| 出版社 = [[スクウェア・エニックス]]\n', ''),
    TEXT.replace('| タイトル = ' + TITLE, '| タイトル = ' + TITLE + '外伝'),
    TEXT.replace('は、十夜による', 'は、別人による'),
    TEXT.replace('は、十夜による', 'は、十夜2による'),
    TEXT.replace("『'''" + TITLE + "'''』", "『'''" + TITLE + "外伝'''』"),
    TEXT.replace('{{Infobox animanga/Footer}}', '{{Infobox animanga/Novel|著者=十夜}}\n{{Infobox animanga/Footer}}'),
    TEXT + '\n{{Copyright notice}}',
])
def test_missing_ambiguous_or_adaptation_credit_is_not_repaired_from_other_fields(text):
    with pytest.raises(ValueError): verify_novel_credit(*inputs(text), REVISION)


def test_complete_direct_ndl_membership_preserves_author_credit_not_person_identity():
    article, own = credit(), parts()
    before = deepcopy((article, own))
    proof = match_novel_credit(article, own, normalize)
    assert proof['scope'] == 'edition_membership_only'
    assert proof['edition_id'] == 'lne:ndl:R100000002-I1'
    assert proof['series_entry_id'] == 'lns:wd:' + QID
    assert proof['volume_number'] == 1 and proof['matched_authority_ids'] == ['001323408']
    concordance = proof['author_credit_concordance'][0]
    assert concordance['name_literal'] == '十夜' and concordance['authority_uri'] == AUTHORITY
    assert concordance['literal_writer']['literal'] == '十夜 著'
    assert proof['author_identity_asserted'] is False and proof['P50_backfilled'] is False
    assert proof['original_work_identity_asserted'] is False
    assert proof['imprint'] == {'qid': 'Q112398828', 'label': 'SQEXノベル'}
    assert (article, own) == before
    encoded = json.dumps(proof, ensure_ascii=False)
    assert 'raw_path' not in encoded and 'manifest_path' not in encoded and 'author_qid' not in encoded


@pytest.mark.parametrize('mutation', ['language', 'mixed_ndc', 'publisher', 'imprint', 'multiple_imprint',
    'nonedition', 'source_url', 'raw_sha', 'volume_missing', 'volume_mismatch', 'volume_parent',
    'subtitle', 'related_title', 'wrong_title_namespace', 'dc_conflict', 'author_name', 'author_role',
    'extra_writer', 'missing_authority', 'ambiguous_authority', 'literal_missing', 'literal_original',
    'literal_parent', 'literal_namespace', 'publisher_namespace'])
def test_ndl_each_gate_must_be_direct_complete_and_unambiguous(mutation):
    own = parts()
    source = own['source']
    if mutation == 'language': source['language'] = 'eng'
    elif mutation == 'mixed_ndc': source['extra']['ndc_codes'].append('726.1')
    elif mutation == 'publisher': source['publisher'] = own['publisher_agents'][0]['name'] = '別出版社'
    elif mutation == 'imprint': source['series'] = 'ガンガンコミックスUP!'
    elif mutation == 'multiple_imprint': source['series'] += '; 別レーベル'
    elif mutation == 'nonedition': source['record_kind'] = 'work'
    elif mutation == 'source_url': source['source_url'] += '/other'
    elif mutation == 'raw_sha': own['source_raw_sha256'] = 'invalid'
    elif mutation == 'volume_missing': own['volume'] = None
    elif mutation == 'volume_mismatch': own['volume']['value'] = '2'
    elif mutation == 'volume_parent': own['volume']['locator'] = 'related/' + own['volume']['locator']
    elif mutation == 'subtitle': own['titles'][0]['value'] = TITLE + ' : 消せない副題. 1'
    elif mutation == 'related_title': own['titles'][0]['locator'] = 'related/' + own['titles'][0]['locator']
    elif mutation == 'wrong_title_namespace': own['titles'][0]['locator'] = loc('https://example.test/', 'title', 1)
    elif mutation == 'dc_conflict': own['titles'][1]['value'] = '別主題'
    elif mutation == 'author_name': source['contributors'][0]['name'] += '別人'
    elif mutation == 'author_role': source['contributors'][0]['role'] = 'illustrator'
    elif mutation == 'extra_writer': source['contributors'].append({'name': '別著者', 'role': 'author', 'identifiers': []})
    elif mutation == 'missing_authority': source['contributors'][0]['identifiers'] = []
    elif mutation == 'ambiguous_authority': source['contributors'][0]['identifiers'].append({'source': 'ndl-authority', 'value': AUTHORITY[:-1] + '9'})
    elif mutation == 'literal_missing': own['literal_authors'] = []
    elif mutation == 'literal_original': own['literal_authors'][0]['literal'] = '十夜 原作'
    elif mutation == 'literal_parent': own['literal_authors'][0]['locator'] = 'related/' + own['literal_authors'][0]['locator']
    elif mutation == 'literal_namespace': own['literal_authors'][0]['locator'] = loc('https://example.test/', 'creator', 4)
    elif mutation == 'publisher_namespace': own['publisher_agents'][0]['locator'] = loc(DC, 'publisher', 5)
    assert match_novel_credit(credit(), own, normalize) is None


def test_full_isbd_subtitle_matches_only_when_wikipedia_title_also_retains_every_word():
    title = TITLE + ' 完全副題'
    metadata, entity, payload = inputs(TEXT.replace(TITLE, title))
    metadata['title'] = entity['sitelinks']['jawiki']['title'] = payload['query']['pages'][0]['title'] = title
    article = verify_novel_credit(metadata, entity, payload, REVISION)
    own = parts()
    own['titles'][0]['value'] = TITLE + ' : 完全副題. 1'
    assert match_novel_credit(article, own, normalize) is not None
    assert match_novel_credit(credit(), own, normalize) is None


def test_explicit_sales_agent_is_preserved_without_becoming_second_publisher():
    own = parts()
    seller = {'name': '明示発売社', 'locator': loc(DCT, 'publisher', 6), 'structured_agent': True,
        'description_values': ['発売'], 'description_locators': [loc(DCT, 'publisher', 6)
            + '/{http://xmlns.com/foaf/0.1/}Agent[1]/{http://purl.org/dc/terms/}description[2]']}
    own['publisher_agents'].append(seller)
    own['source']['publisher'] += '; 明示発売社'
    proof = match_novel_credit(credit(), own, normalize)
    assert proof['publisher_match']['distribution_agents'][0]['role'] == '発売'
    seller['description_locators'][0] = seller['locator'] + '/related/{http://purl.org/dc/terms/}description[2]'
    assert match_novel_credit(credit(), own, normalize) is None
    seller['description_values'] = []
    assert match_novel_credit(credit(), own, normalize) is None


def test_private_caller_annotations_do_not_enter_public_membership_proof():
    article = credit()
    article['raw_path'] = 'private-artifact-sentinel'
    article['attribution']['artifact_path'] = 'private-artifact-sentinel'
    article['field_locators']['raw_path'] = 'private-artifact-sentinel'
    article['imprint_wikidata']['raw_path'] = 'private-artifact-sentinel'
    proof = match_novel_credit(article, parts(), normalize)
    assert proof is not None and 'private-artifact-sentinel' not in json.dumps(proof)


@pytest.mark.parametrize('revision', [True, '111326970', 0])
def test_revision_pin_is_a_positive_integer_not_a_coercible_value(revision):
    with pytest.raises(ValueError): verify_novel_credit(*inputs(), revision)


def v2_inputs():
    text = TEXT.replace(TITLE, '後宮の烏').replace('十夜', '白川紺子').replace('SQEXノベル', '集英社オレンジ文庫')
    text = text.replace('スクウェア・エニックス', '集英社').replace('[[ライトノベル]]', '[[ライト文芸]]')
    metadata, entity, payload = inputs(text)
    metadata['title'] = entity['sitelinks']['jawiki']['title'] = payload['query']['pages'][0]['title'] = '後宮の烏'
    metadata['publishers'][0]['label'] = '集英社オレンジ文庫'
    return metadata, entity, payload


def v2_credit():
    return verify_novel_credit_v2(*v2_inputs(), REVISION)


def v2_parts():
    own = parts()
    own['source'].update(publisher='集英社', series='集英社オレンジ文庫 ; し1-13')
    own['publisher_agents'][0]['name'] = '集英社'
    own['source']['contributors'] = [
        {'name': '白川, 紺子, 1982-', 'role': 'author', 'identifiers': [
            {'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/001127879'}]},
        {'name': '白川紺子', 'role': 'author', 'identifiers': []}]
    own['titles'][0]['value'] = '後宮の烏. 2'
    own['titles'][1]['value'] = '後宮の烏'
    own['volume']['value'] = '2'
    own['literal_authors'][0].update(name='白川紺子', literal='白川紺子 著')
    return own


def test_v1_preexisting_proofs_are_byte_equivalent_and_new_v2_is_explicit():
    article = credit()
    membership = match_novel_credit(article, parts(), normalize)
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
    assert digest(article) == '4e44ec53e357af7a2b33a8a90451f1c93701eaf765b9ae994732ba3d6de52c51'
    assert digest(membership) == 'b4fbdd30ad7340357730f7e32c0081d455e3abbe4f7d3ea99909af55f002f96c'
    with pytest.raises(ValueError): verify_novel_credit(*v2_inputs(), REVISION)
    assert match_novel_credit(v2_credit(), v2_parts(), normalize) is None
    assert match_novel_credit_v2(article, parts(), normalize) is None


def test_v2_light_bungei_credit_and_own_dated_writer_are_bibliographic_only():
    article, own = v2_credit(), v2_parts()
    before = deepcopy((article, own))
    proof = match_novel_credit_v2(article, own, normalize)
    assert article['rule_version'] == 'wikipedia-work-specific-novel-credit-v2'
    assert article['source_work_type_literal'] == 'ライト文芸'
    assert article['light_novel_classification_asserted'] is False
    assert proof['rule_version'] == 'ndl-edition-wikipedia-novel-credit-membership-v2'
    assert proof['matched_authority_ids'] == ['001127879']
    binding = proof['author_credit_concordance'][0]['edition_bound_corroboration']
    assert binding['authority_heading'] == '白川, 紺子, 1982-'
    assert binding['birth_year'] == '1982' and binding['literal_credit']['literal'] == '白川紺子 著'
    assert proof['imprint_match']['source_value'] == '集英社オレンジ文庫 ; し1-13'
    assert proof['imprint_match']['imprint_literal'] == '集英社オレンジ文庫'
    assert proof['imprint_match']['numbering_literal'] == 'し1-13'
    assert proof['imprint_match']['projection_pointer'] == '/source/series'
    assert proof['author_identity_asserted'] is False and proof['P50_backfilled'] is False
    assert proof['light_novel_classification_asserted'] is False
    assert (article, own) == before


def test_v2_numbering_structure_is_not_limited_to_previously_observed_six_numbers():
    own = v2_parts()
    own['source']['series'] = '集英社オレンジ文庫 ; し1-21'
    assert match_novel_credit_v2(v2_credit(), own, normalize) is not None


@pytest.mark.parametrize('series', ['別レーベル ; し1-13', '集英社オレンジ文庫 ; し1-13 ; し1-14',
    '集英社オレンジ文庫 ; 別レーベル', '集英社オレンジ文庫 ; し1-13注記',
    '集英社オレンジ文庫 ; し0-13', '集英社オレンジ文庫 ; し1-0',
    '集英社オレンジ文庫 ; し1--13', '集英社オレンジ文庫 ; あ1-13'])
def test_v2_imprint_numbering_rejects_other_labels_ambiguous_or_unknown_suffix(series):
    own = v2_parts()
    own['source']['series'] = series
    assert match_novel_credit_v2(v2_credit(), own, normalize) is None


@pytest.mark.parametrize('mutation', ['extra_writer', 'other_authority', 'two_same_name_authorities',
    'ambiguous_date', 'literal_parent', 'literal_namespace', 'original_literal', 'missing_literal',
    'name_punctuation_alias', 'date_suffix', 'uncovered_heading'])
def test_v2_dated_fallback_accounts_for_all_own_writers_without_global_name_alias(mutation):
    own = v2_parts()
    contributors = own['source']['contributors']
    if mutation == 'extra_writer': contributors.append({'name': '別著者', 'role': 'author', 'identifiers': []})
    elif mutation == 'other_authority': contributors.append({'name': '別人, 著者, 1982-', 'role': 'author',
        'identifiers': [{'source': 'ndl-authority', 'value': AUTHORITY}]})
    elif mutation == 'two_same_name_authorities': contributors.append({**deepcopy(contributors[0]),
        'identifiers': [{'source': 'ndl-authority', 'value': AUTHORITY}]})
    elif mutation == 'ambiguous_date': contributors.append({**deepcopy(contributors[0]), 'name': '白川, 紺子, 1983-'})
    elif mutation == 'literal_parent': own['literal_authors'][0]['locator'] = 'related/' + own['literal_authors'][0]['locator']
    elif mutation == 'literal_namespace': own['literal_authors'][0]['locator'] = loc('https://example.test/', 'creator', 4)
    elif mutation == 'original_literal': own['literal_authors'][0]['literal'] = '白川紺子 原作'
    elif mutation == 'missing_literal': own['literal_authors'] = []
    elif mutation == 'name_punctuation_alias': contributors[0]['name'] = '白川, 紺・子, 1982-'
    elif mutation == 'date_suffix': contributors[0]['name'] += ' 著者'
    elif mutation == 'uncovered_heading': contributors.append({'name': '白川, 紺子, 1982-', 'role': 'author', 'identifiers': []})
    assert match_novel_credit_v2(v2_credit(), own, normalize) is None


@pytest.mark.parametrize('mutation', ['title', 'volume', 'ndc', 'language', 'publisher', 'lead_author', 'manga_only'])
def test_v2_does_not_weaken_other_source_or_own_bibliographic_gates(mutation):
    own = v2_parts()
    if mutation in {'lead_author', 'manga_only'}:
        metadata, entity, payload = v2_inputs()
        slot = payload['query']['pages'][0]['revisions'][0]['slots']['main']
        if mutation == 'lead_author': slot['content'] = slot['content'].replace('は、白川紺子による', 'は、別人による')
        else: slot['content'] = slot['content'].replace('| 著者 = 白川紺子', '| 原作 = 白川紺子')
        with pytest.raises(ValueError): verify_novel_credit_v2(metadata, entity, payload, REVISION)
        return
    if mutation == 'title': own['titles'][0]['value'] = '後宮の烏 外伝. 2'
    elif mutation == 'volume': own['volume']['value'] = '3'
    elif mutation == 'ndc': own['source']['extra']['ndc_codes'].append('726.1')
    elif mutation == 'language': own['source']['language'] = 'eng'
    elif mutation == 'publisher': own['source']['publisher'] = own['publisher_agents'][0]['name'] = '別出版社'
    assert match_novel_credit_v2(v2_credit(), own, normalize) is None
