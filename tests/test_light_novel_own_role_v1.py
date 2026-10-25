from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from recommend_book.light_novel_own_role_v1 import bind_own_direct_credit_roles_v2


DC = 'http://purl.org/dc/elements/1.1/'
DCT = 'http://purl.org/dc/terms/'
DCNDL = 'http://ndl.go.jp/dcndl/terms/'
FOAF = 'http://xmlns.com/foaf/0.1/'
RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
IDENTITY = 'R100000002-I025102896'
URL = 'https://ndlsearch.ndl.go.jp/books/' + IDENTITY
RAW_LOCATOR = 'bulk-docs[1016]/rdf:RDF'
OWN = RAW_LOCATOR + '/dcndl:BibResource[@rdf:about=' + URL + '#material]'
WRITER = 'http://id.ndl.go.jp/auth/entity/001149924'
ARTIST = 'http://id.ndl.go.jp/auth/entity/001094546'


def fixture(*, heading='床丸, 迷人', literal='床丸迷人 作'):
    node = ET.fromstring(f'''<dcndl:BibResource xmlns:dcndl="{DCNDL}" xmlns:dct="{DCT}"
        xmlns:dc="{DC}" xmlns:foaf="{FOAF}" xmlns:rdf="{RDF}" rdf:about="{URL}#material">
      <dct:title>五年霊組こわいもの係. 1</dct:title>
      <dc:title>五年霊組こわいもの係</dc:title>
      <dct:creator><foaf:Agent rdf:about="{WRITER}"><foaf:name>{heading}</foaf:name>
        <dcndl:transcription>トコマル, マヨト</dcndl:transcription></foaf:Agent></dct:creator>
      <dct:creator><foaf:Agent rdf:about="{ARTIST}"><foaf:name>浜弓場, 双</foaf:name>
        <dcndl:transcription>ハマユミバ, ソウ</dcndl:transcription></foaf:Agent></dct:creator>
      <dc:creator>{literal}</dc:creator><dc:creator>浜弓場双 絵</dc:creator>
    </dcndl:BibResource>''')
    parts = {'source': {'source_id': IDENTITY, 'source_url': URL,
        'provider_id': 'iss-ndl-opac-national', 'record_kind': 'edition',
        'extra': {'raw_locator': RAW_LOCATOR}, 'contributors': [
            {'name': heading, 'role': 'author', 'identifiers': [
                {'source': 'ndl-authority', 'value': WRITER, 'scope': 'agent'}]},
            {'name': '浜弓場, 双', 'role': 'author', 'identifiers': [
                {'source': 'ndl-authority', 'value': ARTIST, 'scope': 'agent'}]},
            {'name': '床丸迷人', 'role': 'author', 'identifiers': []},
            {'name': '浜弓場双', 'role': 'illustrator', 'identifiers': []}]},
        'source_raw_sha256': 'a' * 64,
        'titles': [{'value': child.text, 'locator': OWN + '/' + child.tag + f'[{i}]'}
            for i, child in enumerate(node, 1) if child.tag in {f'{{{DC}}}title', f'{{{DCT}}}title'}],
        'literal_authors': [{'name': '床丸迷人', 'literal': literal,
            'locator': OWN + f'/{{{DC}}}creator[5]'}]}
    return node, parts


def bind(node, parts, locator=OWN):
    return bind_own_direct_credit_roles_v2(node, locator, parts)


def test_observed_direct_roleless_agents_and_separate_writer_artist_literals():
    node, parts = fixture()
    original_node, original_parts = ET.tostring(node), deepcopy(parts)
    result = bind(node, parts)
    assert result['status'] == 'bound'
    assert result['writer_authority_ids'] == ['001149924']
    assert result['illustrator_authority_ids'] == ['001094546']
    writer, artist = result['bindings']
    assert writer['role_literal'] == '作' and writer['writer_included'] is True
    assert artist['role_literal'] == '絵' and artist['writer_included'] is False
    assert writer['structured_name_literal'] == '床丸, 迷人'
    assert writer['authority_uri'] == WRITER and writer['comparison_key'] == '床丸迷人'
    assert writer['literal_text'] == '床丸迷人 作'
    assert writer['structured_creator_locator'].endswith(f'/{{{DCT}}}creator[3]')
    assert writer['name_locator'].endswith(f'/{{{FOAF}}}name[1]')
    assert writer['literal_creator_locator'].endswith(f'/{{{DC}}}creator[5]')
    assert writer['projection_pointer'] == '/source/contributors/0'
    assert writer['transcription_evidence'][0]['literal'] == 'トコマル, マヨト'
    assert result['writer_contributors'] == [parts['source']['contributors'][0]]
    assert result['author_identity_asserted'] is False and result['canonical_metadata_changed'] is False
    assert ET.tostring(node) == original_node and parts == original_parts
    result['writer_contributors'][0]['name'] = '変更'
    assert parts == original_parts


def test_only_nfkc_whitespace_and_single_heading_comma_are_normalized():
    node, parts = fixture(heading='床丸， 迷人')
    assert bind(node, parts)['status'] == 'bound'


@pytest.mark.parametrize('mutation', ['missing_writer', 'missing_artist', 'orphan_structured',
    'orphan_literal', 'compound_names', 'compound_roles', 'literal_no_whitespace', 'unknown_role',
    'literal_nested', 'literal_link', 'literal_wrong_namespace', 'structured_role',
    'unknown_agent_child', 'duplicate_name', 'duplicate_agent', 'duplicate_creator',
    'duplicate_literal', 'duplicate_authority', 'normalized_name_collision', 'date_heading',
    'kana_only_name', 'partial_name', 'name_resource_link', 'extra_authority_attribute',
    'unmarked_creator', 'nested_writer', 'wrong_resource', 'wrong_source_id', 'wrong_raw_sha',
    'projection_extra_creator', 'projection_wrong_role', 'projection_wrong_authority',
    'literal_projection_other_parent', 'title_projection_other_parent'])
def test_ambiguous_unsupported_or_nonown_credits_fail_closed(mutation):
    node, parts = fixture()
    if mutation == 'missing_writer': node.remove(node[4])
    elif mutation == 'missing_artist': node.remove(node[5])
    elif mutation == 'orphan_structured': node[4].text = '別人 作'
    elif mutation == 'orphan_literal': node[4].text = '床丸迷人以外 作'
    elif mutation == 'compound_names': node[4].text = '床丸迷人・別人 作'
    elif mutation == 'compound_roles': node[4].text = '床丸迷人 作・絵'
    elif mutation == 'literal_no_whitespace': node[4].text = '床丸迷人作'
    elif mutation == 'unknown_role': node[4].text = '床丸迷人 原作'
    elif mutation == 'literal_nested': ET.SubElement(node[4], f'{{{FOAF}}}name').text = '床丸迷人 作'
    elif mutation == 'literal_link': node[4].set(f'{{{RDF}}}resource', WRITER)
    elif mutation == 'literal_wrong_namespace': node[4].tag = '{https://example.test/}creator'
    elif mutation == 'structured_role': ET.SubElement(node[2][0], f'{{{DCNDL}}}role').text = '絵'
    elif mutation == 'unknown_agent_child': ET.SubElement(node[2][0], f'{{{DCT}}}description').text = '著者'
    elif mutation == 'duplicate_name': node[2][0].append(deepcopy(node[2][0][0]))
    elif mutation == 'duplicate_agent': node[2].append(deepcopy(node[2][0]))
    elif mutation == 'duplicate_creator': node.append(deepcopy(node[2]))
    elif mutation == 'duplicate_literal': node.append(deepcopy(node[4]))
    elif mutation == 'duplicate_authority': node[3][0].set(f'{{{RDF}}}about', WRITER)
    elif mutation == 'normalized_name_collision': node[3][0][0].text = '床 丸, 迷人'
    elif mutation == 'date_heading': node[2][0][0].text = '床丸, 迷人, 1982-'
    elif mutation == 'kana_only_name': node[2][0][0].text = 'トコマル, マヨト'
    elif mutation == 'partial_name': node[2][0][0].text = '床丸'
    elif mutation == 'name_resource_link': node[2][0][0].set(f'{{{RDF}}}resource', WRITER)
    elif mutation == 'extra_authority_attribute': node[2][0].set(f'{{{RDF}}}resource', ARTIST)
    elif mutation == 'unmarked_creator': node[5].text = '浜弓場双'
    elif mutation == 'nested_writer':
        writer = node[4]; node.remove(writer)
        ET.SubElement(node, f'{{{DCT}}}hasPart').append(writer)
    elif mutation == 'wrong_resource': node.set(f'{{{RDF}}}about', URL + '#holdings')
    elif mutation == 'wrong_source_id': parts['source']['source_id'] = 'R100000002-Iother'
    elif mutation == 'wrong_raw_sha': parts['source_raw_sha256'] = 'bad'
    elif mutation == 'projection_extra_creator': parts['source']['contributors'].append(deepcopy(parts['source']['contributors'][0]))
    elif mutation == 'projection_wrong_role': parts['source']['contributors'][1]['role'] = 'illustrator'
    elif mutation == 'projection_wrong_authority': parts['source']['contributors'][0]['identifiers'][0]['value'] = ARTIST
    elif mutation == 'literal_projection_other_parent': parts['literal_authors'][0]['locator'] = 'related/' + parts['literal_authors'][0]['locator']
    elif mutation == 'title_projection_other_parent': parts['titles'][0]['locator'] = 'related/' + parts['titles'][0]['locator']
    result = bind(node, parts)
    assert result['status'] == 'held' and result['hold_reasons']
    assert result['writer_authority_ids'] == result['illustrator_authority_ids'] == []
    assert result['bindings'] == result['writer_contributors'] == []


def test_same_document_and_exact_own_locator_are_mandatory():
    node, parts = fixture()
    assert bind(node, parts, OWN.replace('1016', '1017'))['status'] == 'held'
    assert bind(node, parts, 'related/' + OWN)['status'] == 'held'


def test_no_writer_cannot_be_made_from_only_illustrator_bindings():
    node, parts = fixture()
    node[4].text = '床丸迷人 絵'
    parts['source']['contributors'][2]['role'] = 'illustrator'
    parts['literal_authors'] = []
    result = bind(node, parts)
    assert result['status'] == 'held' and result['writer_authority_ids'] == []


def test_every_explicit_writer_is_returned_for_callers_exact_p349_set_check():
    node, parts = fixture()
    node[5].text = '浜弓場双 作'
    parts['source']['contributors'][3]['role'] = 'author'
    parts['literal_authors'].append({'name': '浜弓場双', 'literal': '浜弓場双 作',
        'locator': OWN + f'/{{{DC}}}creator[6]'})
    result = bind(node, parts)
    assert result['status'] == 'bound'
    assert result['writer_authority_ids'] == ['001094546', '001149924']
    assert result['illustrator_authority_ids'] == []
    assert len(result['writer_contributors']) == 2


def test_same_name_cannot_have_both_explicit_roles_even_with_same_authority():
    node, parts = fixture()
    extra = ET.SubElement(node, f'{{{DC}}}creator')
    extra.text = '床丸迷人 絵'
    result = bind(node, parts)
    assert result['status'] == 'held' and result['bindings'] == []


def test_authority_id_collision_is_rejected_across_accepted_uri_namespaces():
    node, parts = fixture()
    node[3][0].set(f'{{{RDF}}}about', 'https://id.ndl.go.jp/auth/ndlna/001149924')
    assert bind(node, parts)['status'] == 'held'
