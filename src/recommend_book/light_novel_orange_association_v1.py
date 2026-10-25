"""Pure association of an authenticated Orange child and an own NDL record.

This associates bibliographic references only. It supplies no ISBN, retail
product, edition, story, original-work, author-alias or light-novel identity.
The caller authenticates raw evidence and holds every competing candidate.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re
import unicodedata

from .light_novel_bibliographic_match_v1 import _publisher
from .light_novel_explicit_series_match_v1 import _AUTHORITY, dated_own_author_bindings
from .light_novel_orange_groups_v1 import _BOOK, _GROUP_SELECTOR, _PARENT, _ROLES, _SITEMAP
from .light_novel_reference_match_v1 import _parent


_SHA = re.compile(r'[0-9a-f]{64}')
_NDL = re.compile(r'R100000002-[A-Za-z0-9._-]+')
_CARD = re.compile(re.escape(_GROUP_SELECTOR)+r' > article\.book-box\.box:nth-of-type\([1-9][0-9]*\)')
_WRITER = re.compile(r'(?P<name>.+?)\s*[\[（(]?\s*(著者|著|作者|作)\s*[\]）)]?')
_DC = 'http://purl.org/dc/elements/1.1/'
_DCT = 'http://purl.org/dc/terms/'


def _key(value):
    # Keep punctuation, words, dates and all title/volume markers.
    return ''.join(unicodedata.normalize('NFKC', value).split())


def _direct(locator, own, field, namespaces):
    return any(re.fullmatch(re.escape(own+'/{'+ns+'}'+field)+r'\[[1-9][0-9]*\]', locator)
               for ns in namespaces)


def _official(selected):
    group, child = selected['group'], selected['child']
    url = group['source_url']
    if (not _PARENT.fullmatch(url) or not _SHA.fullmatch(selected['source_proof_sha256'])
            or group['reference_id'] != 'orange-taxonomy-url:'+hashlib.sha256(url.encode()).hexdigest()
            or group['kind'] != 'official_publisher_listing_group_reference' or group['source_unit'] != 'publisher_work_group'
            or group['reference_id_kind'] != 'local_hash_of_official_taxonomy_url' or group['reference_id_is_official'] is not False
            or group['local_reference_key'] != {'provider': 'orange', 'source_scope': 'native_orange_series_taxonomy_urlset', 'official_parent_url': url}
            or group['independent_official_group_id'] is not None or not group['title']
            or any(group[k] is not None for k in ('authors', 'publisher', 'label', 'form', 'isbn', 'official_lightnovel_tag'))
            or group['light_novel_status'] != 'unknown' or group['parent_page_membership_complete'] is not True
            or group['global_membership_complete'] is not None
            or any(group[k] is not False for k in ('story_series_asserted', 'story_series_identity_asserted',
                'original_work_identity_asserted', 'independent_volume_numbers_inferred', 'paper_electronic_same_edition_asserted'))
            or group['frame_member_count'] != len(group['members'])
            or len([m for m in group['members'] if m == child]) != 1
            or len([m for m in group['members'] if m['publisher_reference_id'] == child['publisher_reference_id']]) != 1):
        return None
    evidence = group['source_evidence']
    if (len(evidence) != 2 or evidence[0]['source_url'] != _SITEMAP or evidence[0]['locator'] != 'urlset/url/loc'
            or evidence[1]['source_url'] != url or evidence[1]['locator'] != 'title; '+_GROUP_SELECTOR
            or any(not _SHA.fullmatch(e['raw_sha256']) for e in evidence)):
        return None
    book = _BOOK.fullmatch(child['source_url'])
    card = child['source_selector']
    if (not book or child['publisher_reference_id'] != 'shueisha-orange-book:'+book[1]
            or child['opaque_book_url_key'] != book[1] or child['source_unit'] != 'publisher_product_reference'
            or not _CARD.fullmatch(card) or not isinstance(child['title'], str) or not child['title'].strip()
            or any(child[k] is not None for k in ('publisher', 'label', 'form', 'isbn', 'official_lightnovel_tag', 'media_references'))
            or child['label_literals'] != []
            or any(child[k] is not False for k in ('story_series_asserted', 'original_work_identity_asserted', 'paper_electronic_same_edition_asserted'))
            or child['source_evidence'] != [{'source_url': url, 'raw_sha256': evidence[1]['raw_sha256'],
                'locator': card+' h2 / p.book-info-name / a.thumb[href]'}]
            or len(child['credits']) != len(child['credit_literals'])):
        return None
    writers = []
    for number, (credit, literal) in enumerate(zip(child['credits'], child['credit_literals'], strict=True), 1):
        if (credit != {'role': _ROLES.get(literal['role_literal'], 'unknown'), 'name': literal['name']}
                or not isinstance(credit['name'], str) or not _key(credit['name'])
                or literal['source_selector'] != card+f' p.book-info-name[matching-index={number}]'):
            return None
        if credit['role'] == 'author':
            writers.append(credit['name'])
    if not writers or len({_key(n) for n in writers}) != len(writers):
        return None
    return group, child, writers


def _title(parts, target, own):
    titles = parts['titles']
    if (not titles or not any(_direct(r['locator'], own, 'title', (_DCT,)) for r in titles)
            or any(not r['value'] or not _direct(r['locator'], own, 'title', (_DCT, _DC)) for r in titles)):
        return None
    row = parts['volume']
    volume = row['value'] if row else None
    if row and (not volume or not _direct(row['locator'], own, 'volume', ('http://ndl.go.jp/dcndl/terms/',))):
        return None
    for title in titles:
        value = title['value']
        variants = [value]
        if volume:
            suffix = '. '+volume
            base = value[:-len(suffix)] if value.endswith(suffix) else value
            variants = [base+volume]
            # An explicitly complete native title ending in the own volume is
            # also complete; an unnumbered main title alone is never accepted.
            if _key(value).endswith(_key(volume)):
                variants.append(value)
        if not any(_key(v) == _key(target) for v in variants):
            return None
    return {'rule': 'all_own_direct_complete_titles_and_optional_explicit_volume_nfkc_whitespace_only_v1',
            'source_value': titles[0]['value'], 'volume': volume, 'official_complete_title': target,
            'locators': [r['locator'] for r in titles]+([row['locator']] if row else [])}


def _writers(parts, official_names, own):
    literals = parts['literal_authors']
    by_name = {}
    for row in literals:
        marked = _WRITER.fullmatch(row['literal'])
        if (not marked or not _direct(row['locator'], own, 'creator', (_DC, _DCT))
                or _key(marked['name']) != _key(row['name'])):
            return None
        by_name.setdefault(_key(row['name']), []).append(row)
    if set(by_name) != {_key(n) for n in official_names}:
        return None
    contributors = parts['source']['contributors']
    covered, output = set(), []
    for name in official_names:
        direct = [i for i, c in enumerate(contributors) if c['role'] == 'author' and _key(c['name']) == _key(name)]
        if not direct:
            return None
        bound = dated_own_author_bindings(parts, name)
        indices = set(direct)
        for binding in bound:
            pointer = binding['projection_pointer']
            if not re.fullmatch(r'/source/contributors/(?:0|[1-9][0-9]*)', pointer):
                return None
            indices.add(int(pointer.rsplit('/', 1)[1]))
        uris = {identifier['value'] for index in indices for identifier in contributors[index]['identifiers']
                if identifier['source'] == 'ndl-authority' and _AUTHORITY.fullmatch(identifier['value'])}
        if len(uris) > 1:
            return None
        covered.update(indices)
        output.append({'official_writer': name, 'own_writer_literals': deepcopy(by_name[_key(name)]),
            'contributor_projection_pointers': [f'/source/contributors/{i}' for i in sorted(indices)],
            'authority_status': 'known' if uris else 'unbound', 'authority_ids': sorted(uris),
            'edition_bound_authority_corroboration': deepcopy(bound), 'global_person_identity_asserted': False})
    if {i for i, c in enumerate(contributors) if c['role'] == 'author'} != covered:
        return None
    return output


def _match(selected, parts):
    official = _official(selected)
    if official is None:
        return None
    group, child, names = official
    source = parts['source']
    identity = source['source_id']
    ndc = source['extra']['ndc_codes']
    if (not _NDL.fullmatch(identity) or source['source_url'] != 'https://ndlsearch.ndl.go.jp/books/'+identity
            or source.get('provider_id') not in {'iss-ndl-opac', 'iss-ndl-opac-national'} or source.get('record_kind') != 'edition'
            or source.get('language') not in {'ja', 'jpn'} or not _SHA.fullmatch(parts['source_raw_sha256'])
            or not ndc or any(not re.fullmatch(r'913(?:\.[0-9]+)?', code) for code in ndc)):
        return None
    publisher = _publisher(parts, '集英社')
    if publisher is None:
        return None
    own = _parent(publisher['publisher_agent']['locator'])
    if not re.search(r'/dcndl:BibResource\[@rdf:about='+re.escape(source['source_url'])+r'(?:#[^\]\s]+)?\]$', own):
        return None
    title = _title(parts, child['title'], own)
    authors = _writers(parts, names, own)
    if title is None or authors is None:
        return None
    locators = set(title['locators']) | {publisher['publisher_agent']['locator']}
    locators.update(value for row in publisher['distribution_agents'] for value in (row['locator'], row['description_locator']))
    locators.update(row['locator'] for a in authors for row in a['own_writer_literals'])
    return {'rule_version': 'orange-explicit-child-ndl-bibliographic-reference-association-v1',
        'provider': 'orange', 'scope': 'bibliographic_reference_association',
        'source_group_reference_id': group['reference_id'], 'source_group_title': group['title'],
        'publisher_product_reference_id': child['publisher_reference_id'],
        'official_parent_url': group['source_url'], 'official_child_url': child['source_url'], 'official_title': child['title'],
        'official_credits': deepcopy(child['credits']), 'official_credit_literals': deepcopy(child['credit_literals']),
        'official_child_unknown_fields': {k: None for k in ('isbn', 'publisher', 'label', 'form', 'official_lightnovel_tag', 'media_references')},
        'source_proof_sha256': selected['source_proof_sha256'],
        'publisher_source_evidence': deepcopy(group['source_evidence']+child['source_evidence']),
        'source_id': identity, 'source_url': source['source_url'], 'source_raw_sha256': parts['source_raw_sha256'],
        'own_primary_resource_locator': own, 'title_match': title, 'matched_ndl_title': title['source_value'],
        'authors': authors, 'publisher_match': publisher, 'own_publisher': source['publisher'], 'own_imprint': source.get('series'),
        'language': source['language'], 'ndc_codes': deepcopy(ndc), 'primary_locators': sorted(locators),
        'ambiguous_candidates_must_be_held_by_caller': True,
        'source_isbn_filled_from_ndl': False, 'edition_identity_asserted': False, 'same_edition_identity_asserted': False,
        'product_sku_identity_asserted': False, 'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
        'light_novel_identity_asserted': False, 'light_novel_status': 'unknown', 'paper_electronic_same_edition_asserted': False,
        'global_author_identity_asserted': False, 'global_author_alias_asserted': False}


def match_orange_association(selected: dict, primary_parts: dict) -> dict | None:
    """Associate one exact child; caller holds all competing group candidates.

    Both arguments must already have passed their own scoped raw verifier.
    Unknown/inconsistent facts fail closed. Neither argument is mutated.
    """
    try:
        return _match(selected, primary_parts)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return None
