"""Edition-local direct 作/絵 bindings on an already authenticated own tree.

No file, database, network, raw XML parsing, authority resolution or canonical
metadata mutation occurs here. The caller selects/authenticates the sole own
title-bearing BibResource before supplying this exact Element and projection.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
import xml.etree.ElementTree as ET


_DC = 'http://purl.org/dc/elements/1.1/'
_DCT = 'http://purl.org/dc/terms/'
_DCNDL = 'http://ndl.go.jp/dcndl/terms/'
_FOAF = 'http://xmlns.com/foaf/0.1/'
_RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
_RDFS = 'http://www.w3.org/2000/01/rdf-schema#'
_AUTHORITY = re.compile(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/([0-9]{6,12})')
_VERSION = 'ndl-own-direct-credit-role-bindings-v1'
_NORMALIZATION = 'own_only_nfkc_whitespace_single_heading_comma_v1'


class _Hold(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise _Hold(reason)


def _semantic(text):
    return ' '.join((text or '').split())


def _blank(text):
    return not (text or '').strip()


def _key(name, *, heading):
    value = unicodedata.normalize('NFKC', name)
    if heading and ',' in value:
        pieces = value.split(',')
        _require(len(pieces) == 2 and all(p.strip() for p in pieces), 'unsupported_heading_grammar')
        value = ''.join(pieces)
    _require(',' not in value, 'compound_literal_or_heading')
    value = ''.join(value.split())
    # Minimal full-name grammar: no list separators, dates, readings, role
    # suffixes or punctuation removal. Retained letters must match completely.
    _require(bool(value) and all(unicodedata.category(c).startswith('L')
        or unicodedata.category(c) == 'Mn' for c in value), 'unsupported_name_grammar')
    return value


def _leaf(node):
    return not node.attrib and len(node) == 0 and bool(_semantic(node.text)) and _blank(node.tail)


def _value_text(node):
    # Reproduce the existing semantic title display only; readings/related
    # titles are never used to locate creators or determine a role.
    for tag in (_RDF + '}value', _FOAF + '}name', _RDFS + '}label'):
        value = node.find('.//{' + tag)
        if value is not None and value.text:
            return _semantic(value.text)
    return _semantic(node.text)


def _structured(creator, locator):
    _require(not creator.attrib and len(creator) == 1 and _blank(creator.text)
             and _blank(creator.tail), 'unsupported_structured_creator')
    agent = creator[0]
    _require(agent.tag == '{' + _FOAF + '}Agent' and _blank(agent.text) and _blank(agent.tail)
             and set(agent.attrib) == {'{' + _RDF + '}about'}, 'unsupported_agent_shape')
    uri = agent.get('{' + _RDF + '}about')
    authority = _AUTHORITY.fullmatch(uri)
    _require(authority is not None, 'unknown_authority_uri')
    names, readings = [], []
    agent_locator = locator + '/{' + _FOAF + '}Agent[1]'
    for index, child in enumerate(agent, 1):
        child_locator = agent_locator + '/' + child.tag + f'[{index}]'
        _require(_leaf(child), 'unsupported_agent_field_shape')
        if child.tag == '{' + _FOAF + '}name':
            names.append((child, child_locator))
        elif child.tag == '{' + _DCNDL + '}transcription':
            readings.append({'literal': child.text, 'locator': child_locator})
        else:
            raise _Hold('explicit_role_or_unknown_agent_field')
    _require(len(names) == 1 and len(readings) <= 1, 'duplicate_or_missing_agent_name')
    name_node, name_locator = names[0]
    name = _semantic(name_node.text)
    return {'key': _key(name, heading=True), 'authority_id': authority[1], 'authority_uri': uri,
        'structured_name_literal': name_node.text, 'projected_name': name,
        'structured_creator_locator': locator, 'structured_tag': creator.tag,
        'agent_locator': agent_locator, 'name_locator': name_locator,
        'authority_locator': agent_locator + '/@{' + _RDF + '}about',
        'transcription_evidence': readings,
        'expected_contributor': {'name': name, 'role': 'author', 'identifiers': [
            {'source': 'ndl-authority', 'value': uri, 'scope': 'agent'}]}}


def _literal(creator, locator):
    _require(_leaf(creator), 'unsupported_literal_creator')
    display = _semantic(creator.text)
    marked = re.fullmatch(r'(?P<name>.+?)\s+(?P<role>作|絵)', display)
    _require(marked is not None, 'unsupported_or_unmarked_literal_role')
    name, role = marked['name'], marked['role']
    _require(not re.search(r'(?:^|\s)(?:作|絵|著|著者|原作|イラスト)(?:\s|$)', name),
             'compound_literal_role')
    return {'key': _key(name, heading=False), 'literal_text': creator.text,
        'projected_literal': display, 'literal_name': name, 'role_literal': role,
        'literal_creator_locator': locator, 'literal_tag': creator.tag,
        'expected_contributor': {'name': name, 'role': 'author' if role == '作' else 'illustrator',
                                 'identifiers': []}}


def _bind(node, own_locator, parts):
    _require(isinstance(node, ET.Element) and node.tag == '{' + _DCNDL + '}BibResource',
             'exact_own_bibresource_required')
    source = parts['source']
    identity, url = source['source_id'], source['source_url']
    _require(re.fullmatch(r'R100000002-[A-Za-z0-9._-]+', identity)
             and url == 'https://ndlsearch.ndl.go.jp/books/' + identity
             and source['provider_id'] in {'iss-ndl-opac', 'iss-ndl-opac-national'}
             and source['record_kind'] == 'edition' and re.fullmatch(r'[0-9a-f]{64}', parts['source_raw_sha256']),
             'source_identity_or_sha_mismatch')
    about = node.get('{' + _RDF + '}about')
    _require(set(node.attrib) == {'{' + _RDF + '}about'} and about in {url, url + '#material'},
             'different_own_resource')
    expected_locator = source['extra']['raw_locator'] + '/dcndl:BibResource[@rdf:about=' + about + ']'
    _require(own_locator == expected_locator, 'different_own_document_or_locator')
    actual_titles, structured, literals, expected_contributors = [], [], [], []
    for index, child in enumerate(node, 1):
        locator = own_locator + '/' + child.tag + f'[{index}]'
        if child.tag in {'{' + _DC + '}title', '{' + _DCT + '}title'}:
            if value := _value_text(child):
                actual_titles.append({'value': value, 'locator': locator})
        if child.tag == '{' + _DCT + '}creator':
            row = _structured(child, locator)
            row['projection_pointer'] = f'/source/contributors/{len(expected_contributors)}'
            structured.append(row)
        elif child.tag == '{' + _DC + '}creator':
            row = _literal(child, locator)
            row['projection_pointer'] = f'/source/contributors/{len(expected_contributors)}'
            literals.append(row)
        elif child.tag.rsplit('}', 1)[-1] == 'creator':
            raise _Hold('unsupported_creator_namespace')
        else:
            continue
        expected_contributors.append(row['expected_contributor'])
    _require(actual_titles and parts['titles'] == actual_titles, 'own_primary_title_projection_mismatch')
    _require(structured and literals, 'missing_direct_credit_set')
    structured_keys = [r['key'] for r in structured]
    literal_keys = [r['key'] for r in literals]
    authority_ids = [r['authority_id'] for r in structured]
    _require(len(structured_keys) == len(set(structured_keys)) and len(literal_keys) == len(set(literal_keys))
             and len(authority_ids) == len(set(authority_ids)), 'duplicate_name_authority_or_credit')
    _require(set(structured_keys) == set(literal_keys), 'incomplete_one_to_one_credit_mapping')
    _require(any(r['role_literal'] == '作' for r in literals), 'no_explicit_writer')
    _require(source['contributors'] == expected_contributors, 'own_contributor_projection_mismatch')
    expected_literal_writers = [{'name': r['literal_name'], 'literal': r['projected_literal'],
        'locator': r['literal_creator_locator']} for r in literals if r['role_literal'] == '作']
    _require(parts['literal_authors'] == expected_literal_writers, 'own_literal_writer_projection_mismatch')
    bindings, writers, writer_ids, illustrator_ids = [], [], [], []
    by_key = {r['key']: r for r in literals}
    for row in structured:
        literal = by_key[row['key']]
        included = literal['role_literal'] == '作'
        bindings.append({key: deepcopy(value) for key, value in row.items()
            if key not in {'key', 'expected_contributor', 'projected_name'}} | {
                key: deepcopy(value) for key, value in literal.items()
                if key not in {'key', 'expected_contributor', 'projected_literal', 'projection_pointer'}} | {
            'literal_projection_pointer': literal['projection_pointer'],
            'comparison_key': row['key'], 'normalization_rule': _NORMALIZATION,
            'role': 'author' if included else 'illustrator', 'writer_included': included,
            'role_discriminator': 'same_own_direct_creator_one_to_one_terminal_literal',
            'inclusion_reason': 'explicit_own_作' if included else 'excluded_from_writer_by_explicit_own_絵'})
        if included:
            writer_ids.append(row['authority_id'])
            index = int(row['projection_pointer'].rsplit('/', 1)[1])
            writers.append(deepcopy(source['contributors'][index]))
        else:
            illustrator_ids.append(row['authority_id'])
    return {'status': 'bound', 'rule_version': _VERSION, 'source_id': identity, 'source_url': url,
        'source_raw_sha256': parts['source_raw_sha256'], 'own_resource_uri': about,
        'own_resource_locator': own_locator, 'writer_authority_ids': sorted(writer_ids),
        'illustrator_authority_ids': sorted(illustrator_ids), 'bindings': bindings,
        'writer_contributors': writers, 'hold_reasons': [], 'author_identity_asserted': False,
        'global_author_alias_asserted': False, 'canonical_metadata_changed': False,
        'original_work_identity_asserted': False}


def bind_own_direct_credit_roles_v2(own_node, own_locator, primary_parts):
    """Bind the complete own 作/絵 set, or return a hold with no partial IDs.

    writer_contributors is an auxiliary COPY for an opt-in membership matcher.
    It must never replace the stored/canonical SourceRecord contributors.
    """
    try:
        return _bind(own_node, own_locator, primary_parts)
    except (_Hold, KeyError, TypeError, AttributeError, ValueError, IndexError) as error:
        return {'status': 'held', 'rule_version': _VERSION, 'writer_authority_ids': [],
            'illustrator_authority_ids': [], 'bindings': [], 'writer_contributors': [],
            'hold_reasons': [str(error) if isinstance(error, _Hold) else 'malformed_own_credit_contract']}
