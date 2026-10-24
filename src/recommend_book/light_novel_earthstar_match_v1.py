"""Pure own-NDL member matching to authenticated EarthStar work-group cards.

The caller authenticates the source projection and sole own NDL resource. A
positive result establishes only a bibliographic member in a publisher group,
never an original-work identity, a complete story series or a paper/ebook pair.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re
import unicodedata

from .light_novel_explicit_series_match_v1 import (
    _AUTHORITY, _person_key, _suffix, dated_own_author_bindings,
)
from .light_novel_reference_match_v1 import _key, _parent, _titles


_PARENT = re.compile(r'https://earthstar-novel\.com/book/detail/([A-Za-z0-9_-]+)/')
_CHILD = re.compile(r'https://earthstar-novel\.com/vol/detail/([A-Za-z0-9_-]+)/')
_INDEX = re.compile(r'https://earthstar-novel\.com/book/(?:page/[1-9][0-9]*/)?')
_NDL = re.compile(r'R100000002-[A-Za-z0-9._-]+')
_SHA = re.compile(r'[0-9a-f]{64}')
_GROUP = 'section.c-book-archive01 > .c-book-archive01__inner > .c-card01-list'
_WRITER_BASIS = 'explicit_author_role_in_own_official_index_card_not_inferred_from_children'


def _unknowns(row):
    return all(key in row and row[key] is None for key in ('publisher', 'label', 'form', 'isbn'))


def _official_scope(assertion):
    parent = _PARENT.fullmatch(assertion['source_url'])
    if not parent:
        return None
    reference = assertion['reference_id']
    direct = reference == 'earthstar-work:' + parent[1]
    adapted = (assertion.get('index_id') == 'earthstar-explicit-work-group'
        and reference == 'publisher-series:' + hashlib.sha256(assertion['source_url'].encode()).hexdigest()[:24])
    if (not parent or assertion['kind'] != 'official_work_group_reference'
            or not (direct or adapted)
            or assertion['official_work_reference_id'] != parent[1]
            or not assertion['title'] or not _unknowns(assertion)
            or assertion.get('story_series_asserted') is not False
            or assertion.get('series_title', assertion['title']) != assertion['title']
            or assertion['index_author_inclusion_basis'] != _WRITER_BASIS):
        return None
    authors = assertion['index_author_credits']
    if not authors or any(c['role'] != 'author' or not _person_key(c['name']) for c in authors):
        return None
    if len({_person_key(c['name']) for c in authors}) != len(authors):
        return None
    evidence = assertion['source_evidence']
    if (not evidence or any(not _SHA.fullmatch(e['raw_sha256']) or not e['locator']
            or not (_INDEX.fullmatch(e['source_url']) or e['source_url'] == assertion['source_url'])
            for e in evidence)):
        return None
    parents = [e for e in evidence if e['source_url'] == assertion['source_url']]
    if (len(parents) != 1 or 'h1.c-book-intro01__head' not in parents[0]['locator']
            or not any(_INDEX.fullmatch(e['source_url']) for e in evidence)):
        return None
    group = assertion['book_group_evidence']
    books = assertion['books']
    if (not books or group['heading_literal'] != '書籍一覧' or group['group_selector'] != _GROUP
            or group['membership_basis'] != 'explicit_child_cards_inside_this_parent_book_list'
            or group['book_count'] != len(books)):
        return None
    seen = set()
    for book in books:
        child = _CHILD.fullmatch(book['source_url'])
        ev = book['source_evidence']
        literals = book['explicit_role_credit_literals']
        if (not child or child[1] != book['official_child_reference_id'] or book['source_url'] in seen
                or not book['title'] or not _unknowns(book)
                or book['publisher_group_membership_explicit'] is not True
                or book['story_series_asserted'] is not False or book['edition_identity'] != 'unassessed'
                or not book['source_selector'] or ev['locator'] != book['source_selector']
                or ev['source_url'] != assertion['source_url'] or ev['raw_sha256'] != parents[0]['raw_sha256']
                or [{'role': c['role'], 'name': c['name']} for c in literals] != book['credits']
                or any(c['role'] == 'author' and c['role_display'] != '著者：' for c in literals)):
            return None
        seen.add(book['source_url'])
    return authors


def _publisher(parts):
    agents = parts['publisher_agents']
    publishers = [a for a in agents if a['description_values'] == []]
    distribution = [a for a in agents if a['description_values'] == ['頒布']]
    if (len(publishers) != 1 or _key(publishers[0]['name']) != _key('アース・スターエンターテイメント')
            or len(publishers) + len(distribution) != len(agents)
            or any(not a['name'] or not a['locator'] for a in agents)
            or len({_parent(a['locator']) for a in agents}) != 1
            or parts['source']['publisher'] != '; '.join(dict.fromkeys(a['name'] for a in agents))):
        return None
    if distribution and (any(not a['structured_agent'] for a in agents)
            or any(len(a['description_locators']) != 1
                   or not a['description_locators'][0].startswith(a['locator'] + '/') for a in distribution)):
        return None
    return {'rule': 'unique_earthstar_publisher_with_explicit_distribution_only',
        'publisher_agent': {'name': publishers[0]['name'], 'locator': publishers[0]['locator']},
        'distribution_agents': [{'name': a['name'], 'description': '頒布', 'locator': a['locator'],
            'description_locator': a['description_locators'][0]} for a in distribution],
        'official_product_publisher_inferred': False}


def _imprint(parts, own):
    literal = parts['source'].get('series')
    if not isinstance(literal, str) or not literal:
        return None
    # Retain the established parsed-series separator behavior. No number,
    # suffix, arbitrary slash, or story word is removed from an imprint form.
    primary = unicodedata.normalize('NFKC', literal).split(';', 1)[0].strip()
    forms = [s.strip() for s in primary.split(' = ')]
    if _key('EARTH STAR NOVEL') not in {_key(s) for s in forms}:
        return None
    return {'rule': 'exact_own_ndl_earth_star_novel_imprint',
        'scope': 'own_ndl_imprint_only_not_official_label', 'source_value': literal,
        'parsed_primary_imprint': primary, 'explicit_parallel_forms': forms,
        'own_primary_resource_locator': own, 'projection_pointer': '/source/series'}


def _authors(parts, names, own):
    output = []
    dated_used = False
    for name in names:
        direct = []
        for index, contributor in enumerate(parts['source']['contributors']):
            if contributor['role'] != 'author' or _person_key(contributor['name']) != _person_key(name):
                continue
            uris = sorted({i['value'] for i in contributor['identifiers']
                if i['source'] == 'ndl-authority' and _AUTHORITY.fullmatch(i['value'])})
            if uris:
                direct.append({'name': contributor['name'], 'role': 'author', 'authority_ids': uris,
                    'own_primary_resource_locator': own, 'projection_pointer': f'/source/contributors/{index}',
                    'locator_scope': 'own_primary_resource_and_normalized_projection_pointer'})
        if direct:
            output.append((name, direct))
            continue
        bound = dated_own_author_bindings(parts, name)
        if not bound:
            return None
        dated_used = True
        output.append((name, bound))
    return output, dated_used


def _match(assertion, parts):
    official_authors = _official_scope(assertion)
    if official_authors is None:
        return None
    source = parts['source']
    identity = source['source_id']
    ndc = source['extra']['ndc_codes']
    if (not _NDL.fullmatch(identity) or source['source_url'] != 'https://ndlsearch.ndl.go.jp/books/' + identity
            or not _SHA.fullmatch(parts['source_raw_sha256']) or source.get('language') not in {'jpn', 'ja'}
            or source.get('provider_id') not in {'iss-ndl-opac', 'iss-ndl-opac-national'}
            or not ndc or any(not re.fullmatch(r'913(?:\.[0-9]+)?', n) for n in ndc)):
        return None
    publisher = _publisher(parts)
    if publisher is None:
        return None
    own = _parent(publisher['publisher_agent']['locator'])
    if not parts['titles'] or any(not r['value'] or _parent(r['locator']) != own for r in parts['titles']):
        return None
    imprint = _imprint(parts, own)
    if imprint is None:
        return None
    names = [c['name'] for c in official_authors]
    bound = _authors(parts, names, own)
    if bound is None:
        return None
    authors, dated_used = bound
    matches = []
    for book in assertion['books']:
        child_names = {_person_key(c['name']) for c in book['credits'] if c['role'] == 'author'}
        if child_names != {_person_key(n) for n in names}:
            continue
        title = _titles(parts, book['title'], own)
        if title is None:
            suffix = _suffix(book['title'], assertion['title'])
            title = _titles(parts, suffix, own) if suffix else None
            if title:
                title = {**title, 'rule': 'explicit_workgroup_prefix_plus_complete_ndl_title',
                    'official_workgroup_prefix': assertion['title']}
        if title:
            matches.append((book, title))
    if len(matches) != 1:
        return None
    book, title = matches[0]
    volume = parts['volume']['value'] if parts['volume'] else None
    numeric = unicodedata.normalize('NFKC', volume) if volume else ''
    authorities = sorted({uri for _, rows in authors for a in rows for uri in a['authority_ids']})
    return {'rule_version': 'earthstar-explicit-workgroup-member-match-v2' if dated_used
            else 'earthstar-explicit-workgroup-member-match-v1',
        'scope': 'own_ndl_bibliographic_member_in_explicit_publisher_workgroup',
        'title': assertion['title'], 'title_origin': {'kind': 'publisher_explicit_workgroup_h1_title',
            'source_url': assertion['source_url'], 'locator': 'h1.c-book-intro01__head'},
        'authors': [{'name': n, 'authority_ids': sorted({u for a in rows for u in a['authority_ids']})}
            for n, rows in authors],
        'volume': volume, 'volume_number': int(numeric) if re.fullmatch(r'[0-9]+', numeric) else None,
        'ndc_codes': deepcopy(ndc), 'imprint': source['series'], 'publisher': source['publisher'],
        'matched_ndl_title': title['source_value'], 'official_member_title': book['title'],
        'member_source_url': book['source_url'], 'title_match': title,
        'official_workgroup': {k: deepcopy(assertion[k]) for k in ('kind', 'reference_id',
            'official_work_reference_id', 'source_url', 'title', 'index_title', 'index_author_credits',
            'publisher', 'label', 'form', 'isbn', 'story_series_asserted')},
        'official_member': deepcopy(book), 'official_source_evidence': deepcopy(assertion['source_evidence']),
        'official_member_group_witness': deepcopy(assertion['book_group_evidence']),
        'ndl_member': {'source_id': identity, 'source_url': source['source_url'],
            'source_raw_sha256': parts['source_raw_sha256'], 'own_primary_resource_locator': own,
            'complete_title': title['source_value'], 'imprint': source['series'],
            'author': {'authorities': authorities, 'contributors': [a for _, rows in authors for a in rows]}},
        'ndl_publisher_policy': publisher, 'ndl_imprint_policy': imprint,
        'official_publisher_asserted': False, 'official_label_asserted': False,
        'paper_ebook_same_edition_asserted': False, 'original_work_identity_asserted': False,
        'story_series_identity_asserted': False, 'global_author_alias_asserted': False,
        'global_author_identity_asserted': False,
        'author_identity_scope': 'edition_bound_dated_own_heading_and_explicit_writer_literal_only' if dated_used
            else 'known_ndl_authority_on_same_name_own_creator_only'}


def match_earthstar_member(assertion, primary_parts):
    """Return a contained work-group membership proof, or keep the candidate held."""
    try:
        return _match(assertion, primary_parts)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
