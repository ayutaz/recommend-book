"""Pure, edition-scoped matching to eight authenticated Cobalt series groups.

Candidate keys are only an overinclusive index. Positive matches use complete
own titles, explicit official author roles and known own NDL author authorities.
The caller authenticates all raw bindings before entering this module.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata

from .light_novel_reference_match_v1 import _key, _parent, _titles


_IDS = {'52807', '52812', '47089', '52670', '52520', '52973', '69979', '52881'}
_ANNIVERSARY = 'https://orangebunko.shueisha.co.jp/feature/cobalt50th_specialbooks'
_EBOOKS = 'https://ebooks.shueisha.co.jp/cobalt/'
_BOOK = re.compile(r'https://ebooks\.shueisha\.co\.jp/detail\.php\?book=([A-Za-z0-9]+)')
_AUTHORITY = re.compile(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/[0-9]{6,12}')
_SHA = re.compile(r'[0-9a-f]{64}')
_NDL_ID = re.compile(r'R100000002-[A-Za-z0-9._-]+')


def _broad_key(value):
    return ''.join(c for c in _key(value) if not unicodedata.category(c).startswith('P'))


def _person_key(value):
    return _broad_key(value)


def _suffix(member, series):
    """Strip only a complete explicit series prefix, never a number or suffix."""
    target = _key(series)
    if not target or not _key(member).startswith(target):
        return None
    for end in range(1, len(member) + 1):
        if _key(member[:end]) == target:
            return member[end:].strip() or None
    return None


def candidate_title_keys(assertion):
    """Index complete official members and explicit-series-prefix suffixes."""
    try:
        result = set()
        for book in assertion['books']:
            title = book['title']
            if title:
                result.add(_broad_key(title))
            suffix = _suffix(title, assertion['series_title'])
            if suffix:
                result.add(_broad_key(suffix))
        return result - {''}
    except (KeyError, TypeError, AttributeError):
        return set()


def candidate_ndl_title_keys(source):
    """Index saved complete-title/volume constructions; this grants no proof."""
    try:
        result = set()
        volume = source.get('volume') or ''
        for row in source['titles']:
            raw = row['value']
            result.add(_broad_key(raw))
            complete = raw[:-len('. ' + volume)] if volume and raw.endswith('. ' + volume) else raw
            split = complete.split(' : ', 1)
            main, subtitle = split[0], split[1] if len(split) == 2 else ''
            if main:
                result.update(_broad_key(value) for value in (
                    main + volume + subtitle, main + subtitle + volume,
                    main + volume + '~' + subtitle + '~' if subtitle else main + volume))
        return result - {''}
    except (KeyError, TypeError, AttributeError):
        return set()


def _publisher(parts):
    agents = parts['publisher_agents']
    publishers = [row for row in agents if row['description_values'] == []]
    distributors = [row for row in agents if row['description_values'] == ['頒布']]
    if (len(publishers) != 1 or publishers[0]['name'] != '集英社'
            or len(publishers) + len(distributors) != len(agents)
            or any(not row['name'] or not row['locator'] for row in agents)):
        return None
    if distributors and (any(not row['structured_agent'] for row in agents)
            or any(len(row['description_locators']) != 1 or not row['description_locators'][0]
                   for row in distributors)):
        return None
    if (parts['source']['publisher'] != '; '.join(dict.fromkeys(row['name'] for row in agents))
            or len({_parent(row['locator']) for row in agents}) != 1):
        return None
    return {'rule': 'ndl_member_publisher_policy_shueisha_only',
            'official_product_publisher_inferred': False,
            'publisher_agent': {'name': publishers[0]['name'], 'locator': publishers[0]['locator']},
            'distribution_agents': [{'name': row['name'], 'description': '頒布', 'locator': row['locator'],
                'description_locator': row['description_locators'][0]} for row in distributors]}


def _official_scope(assertion):
    identity = assertion['official_series_id']
    author = assertion['series_author']
    evidence = assertion['source_evidence']
    return (assertion['kind'] == 'official_series_reference' and identity in _IDS
        and assertion['official_series_url'] == 'https://www.shueisha.co.jp/books/search/search.html?seriesid=' + identity
        and bool(assertion['series_title']) and assertion['publisher'] is None and assertion['label'] is None
        and author['role'] == '著者' and bool(author['display_text'])
        and author['role_source'] == 'ebook_author_list'
        and author['role_label_selector'] == 'h2 img[alt="著者50音別書籍リスト"]'
        and bool(author['author_field_selector'])
        and assertion['independent_series_evidence']['source_key'] == 'cobalt_anniversary'
        and assertion['independent_series_evidence']['label_text'] == 'シリーズ本編購入はこちら'
        and bool(assertion['independent_series_evidence']['selector'])
        and assertion['ebook_group_evidence']['source_key'] == 'ebook_author_list'
        and bool(assertion['ebook_group_evidence']['selector'])
        and len(evidence) == 2 and {row['source_url'] for row in evidence} == {_ANNIVERSARY, _EBOOKS}
        and all(_SHA.fullmatch(row['raw_sha256']) and row['locator'] for row in evidence))


def _match(assertion, parts):
    if not _official_scope(assertion):
        return None
    source = parts['source']
    identity = source['source_id']
    if (not _NDL_ID.fullmatch(identity)
            or source['source_url'] != 'https://ndlsearch.ndl.go.jp/books/' + identity
            or not _SHA.fullmatch(parts['source_raw_sha256'])
            or source.get('language') not in {'jpn', 'ja'}):
        return None
    ndc = source['extra']['ndc_codes']
    if not ndc or any(not value.startswith('913') for value in ndc):
        return None
    publisher = _publisher(parts)
    if publisher is None:
        return None
    own_locator = _parent(publisher['publisher_agent']['locator'])
    author_name = assertion['series_author']['display_text']
    own_authors = []
    for index, contributor in enumerate(source['contributors']):
        if contributor['role'] != 'author' or _person_key(contributor['name']) != _person_key(author_name):
            continue
        uris = sorted({row['value'] for row in contributor['identifiers']
            if row['source'] == 'ndl-authority' and _AUTHORITY.fullmatch(row['value'])})
        if uris:
            own_authors.append({'name': contributor['name'], 'role': 'author', 'authority_ids': uris,
                'own_primary_resource_locator': own_locator,
                'projection_pointer': f'/source/contributors/{index}',
                'locator_scope': 'own_primary_resource_and_normalized_projection_pointer'})
    if not own_authors:
        return None
    matches = []
    for book in assertion['books']:
        url_match = _BOOK.fullmatch(book['source_url'])
        if (not url_match or url_match[1] != book['publisher_book_id'] or not book['source_selector']
                or _person_key(book['author_display_text']) != _person_key(author_name) or not book['title']):
            return None
        title = _titles(parts, book['title'], own_locator)
        if title is None:
            suffix = _suffix(book['title'], assertion['series_title'])
            title = _titles(parts, suffix, own_locator) if suffix else None
            if title is not None:
                title = {**title, 'rule': 'explicit_series_prefix_plus_complete_ndl_title',
                         'official_series_prefix': assertion['series_title']}
        if title is not None:
            matches.append((book, title))
    if len(matches) != 1:
        return None
    book, title = matches[0]
    volume = parts['volume']['value'] if parts['volume'] else None
    numeric = unicodedata.normalize('NFKC', volume) if volume else ''
    authorities = sorted({uri for row in own_authors for uri in row['authority_ids']})
    return {'rule_version': 'cobalt-explicit-series-member-match-v1',
        'scope': 'ndl_bibliographic_member_to_explicit_official_series',
        'title': assertion['series_title'],
        'title_origin': {'kind': 'publisher_explicit_series_title',
            'source_url': assertion['official_series_url'],
            'locator': assertion['independent_series_evidence']['selector']},
        'authors': [{'name': author_name, 'authority_ids': authorities}],
        'volume': volume, 'volume_number': int(numeric) if re.fullmatch(r'[0-9]+', numeric) else None,
        'ndc_codes': deepcopy(ndc), 'imprint': source.get('series'), 'publisher': source['publisher'],
        'matched_ndl_title': title['source_value'], 'official_member_title': book['title'],
        'member_source_url': book['source_url'], 'title_match': title,
        'official_series': {key: deepcopy(assertion[key]) for key in (
            'official_series_id', 'official_series_url', 'series_title', 'series_author', 'publisher', 'label')},
        'official_member': deepcopy(book), 'official_source_evidence': deepcopy(assertion['source_evidence']),
        'official_series_witness': deepcopy(assertion['independent_series_evidence']),
        'official_member_group_witness': deepcopy(assertion['ebook_group_evidence']),
        'ndl_member': {'source_id': identity, 'source_url': source['source_url'],
            'source_raw_sha256': parts['source_raw_sha256'], 'own_primary_resource_locator': own_locator,
            'complete_title': title['source_value'], 'imprint': source.get('series'),
            'author': {'authorities': authorities, 'contributors': own_authors}},
        'ndl_publisher_policy': publisher, 'official_publisher_asserted': False,
        'paper_ebook_same_edition_asserted': False, 'original_work_identity_asserted': False,
        'author_identity_scope': 'known_ndl_authority_on_same_name_own_creator_only'}


def match_cobalt_member(assertion, primary_parts):
    """Return a member proof or hold; publisher/label unknowns stay unknown."""
    try:
        return _match(assertion, primary_parts)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
