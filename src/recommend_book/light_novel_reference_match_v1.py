"""Pure matching of authenticated TO product references to contained NDL editions.

The caller authenticates every raw/manifest binding and supplies ALL own NDL
records carrying the ISBN. This module performs no I/O and grants no author,
original-work, series, or retail-product identity.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata

from .normalization import normalize_isbn


_SHA = re.compile(r'[0-9a-f]{64}')
_NDL_ID = re.compile(r'R100000002-[A-Za-z0-9._-]+')
_DCT_TITLE = '/{http://purl.org/dc/terms/}title['


def _key(value):
    # Retain meaningful punctuation, words, numbers and edition/SKU markers.
    return ''.join(unicodedata.normalize('NFKC', value).casefold().split())


def _parent(locator):
    position = locator.rfind('/{')
    return locator[:position] if position >= 0 else locator.rsplit('/', 1)[0]


def _publisher(parts):
    agents = parts['publisher_agents']
    if not agents or any(not row['name'] or not row['locator'] for row in agents):
        return None
    publishers = [row for row in agents if row['description_values'] == []]
    distributors = [row for row in agents if row['description_values'] == ['頒布']]
    if (len(publishers) != 1 or _key(publishers[0]['name']) != _key('TOブックス')
            or len(publishers) + len(distributors) != len(agents)):
        return None
    if distributors and any(not row['structured_agent'] for row in agents):
        return None
    if any(len(row['description_locators']) != 1 or not row['description_locators'][0]
           for row in distributors):
        return None
    if len({_parent(row['locator']) for row in agents}) != 1:
        return None
    displayed = '; '.join(dict.fromkeys(row['name'] for row in agents))
    if parts['source'].get('publisher') != displayed:
        return None
    return {'rule': 'unique_tobooks_publisher_and_explicit_distribution_only',
            'publisher_agent': {'name': publishers[0]['name'], 'locator': publishers[0]['locator']},
            'distribution_agents': sorted(({'name': row['name'], 'description': '頒布',
                'locator': row['locator'], 'description_locator': row['description_locators'][0]}
                for row in distributors), key=lambda row: (row['name'], row['locator']))}


def _titles(parts, target, own_parent):
    # dc:title main fields and nested contents cannot replace the complete title.
    titles = [row for row in parts['titles'] if _DCT_TITLE in row['locator']
              and _parent(row['locator']) == own_parent]
    if not titles:
        return None
    volume_row = parts['volume']
    volume = volume_row['value'] if volume_row else None
    if volume_row and (not volume or _parent(volume_row['locator']) != own_parent):
        return None
    selected = None
    for row in titles:
        raw = row['value']
        if not raw:
            return None
        complete = raw[:-len('. ' + volume)] if volume and raw.endswith('. ' + volume) else raw
        split = complete.split(' : ', 1)
        main, subtitle = split[0], split[1] if len(split) == 2 else None
        if not main or subtitle == '':
            return None
        locators = [row['locator']] + ([volume_row['locator']] if volume_row else [])
        variants = [main + (volume or '') + (subtitle or ''), main + (subtitle or '') + (volume or '')]
        if subtitle:
            variants += [main + (volume or '') + '~' + subtitle + '~',
                         main + '~' + subtitle + '~' + (volume or '')]
        # A direct complete title may express a volume without an ISBD suffix.
        # With a separate volume, require a complete composed variant instead
        # of accepting an unnumbered main title alone.
        if not volume:
            variants.insert(0, raw)
        if not any(_key(value) == _key(target) for value in variants):
            return None
        if selected is None:
            selected = {'rule': 'own_complete_title_all_parts_and_volume_v1',
                        'source_value': raw, 'main': main, 'subtitle': subtitle,
                        'volume': volume, 'locators': locators}
    return selected


def _candidate(parts, isbn, target):
    source = parts['source']
    identity, url = source['source_id'], source['source_url']
    if (not _NDL_ID.fullmatch(identity) or url != 'https://ndlsearch.ndl.go.jp/books/' + identity
            or not _SHA.fullmatch(parts['source_raw_sha256'])):
        return None
    if isbn not in {normalize_isbn(row['value']) for row in source['identifiers'] if row['source'] == 'isbn'}:
        return None
    ndc = sorted(set(source['extra']['ndc_codes']))
    if source.get('language') not in ('ja', 'jpn') or not ndc or any(not code.startswith('913') for code in ndc):
        return None
    publisher = _publisher(parts)
    if publisher is None:
        return None
    own_parent = _parent(publisher['publisher_agent']['locator'])
    title = _titles(parts, target, own_parent)
    if title is None:
        return None
    statements = parts.get('edition_statements', [])
    if not isinstance(statements, list) or any(not row['value'] or not row['locator']
            or _parent(row['locator']) != own_parent for row in statements):
        return None
    locators = [*title['locators'], publisher['publisher_agent']['locator']]
    locators += [locator for row in publisher['distribution_agents']
                 for locator in (row['locator'], row['description_locator'])]
    locators += [row['locator'] for row in statements]
    signature = (_key(title['volume'] or ''), tuple(ndc),
                 tuple(sorted(_key(row['value']) for row in statements)),
                 tuple(sorted(_key(row['name']) for row in publisher['distribution_agents'])))
    return {'source_id': identity, 'source_url': url, 'source_raw_sha256': parts['source_raw_sha256'],
            'title_match': title, 'publisher_match': publisher, 'language': 'jpn', 'ndc_codes': ndc,
            'edition_statements': deepcopy(statements), 'locators': sorted(set(locators)),
            '_signature': signature}


def _match(reference, primary_parts, same_isbn_parts):
    if (reference['source_unit'] != 'publisher_product_reference'
            or not reference['publisher_reference_id'].startswith('tobooks-product:')
            or reference['publisher'] != 'TOブックス'
            or reference['official_lightnovel_tag'] is not True or not reference['title']
            or reference.get('metadata_conflict') is not False
            or reference.get('bibliographic_variants') != []):
        return None
    isbn = normalize_isbn(str(reference.get('isbn') or ''))
    evidence = reference['source_evidence']
    if (not isbn or not evidence or not isinstance(evidence, list)
            or any(not _SHA.fullmatch(row['raw_sha256']) or not row['locator']
                   or not row['source_url'] for row in evidence)):
        return None
    if not isinstance(same_isbn_parts, list) or not same_isbn_parts or primary_parts not in same_isbn_parts:
        return None
    primary = _candidate(primary_parts, isbn, reference['title'])
    if primary is None:
        return None
    candidates = [_candidate(parts, isbn, reference['title']) for parts in same_isbn_parts]
    if any(row is None or row['_signature'] != primary['_signature'] for row in candidates):
        return None
    candidates.sort(key=lambda row: (row['source_id'], row['source_url'], row['source_raw_sha256']))
    if len({row['source_id'] for row in candidates}) != len(candidates):
        return None
    for row in candidates:
        del row['_signature']
    return {'rule_version': 'publisher-reference-ndl-contained-edition-tobooks-v1', 'scope': 'edition_only',
            'confirmed_scope': 'contained_bibliographic_edition_only', 'isbn13': isbn,
            'publisher_reference_id': reference['publisher_reference_id'], 'source_unit': reference['source_unit'],
            'official_source_url': reference['source_url'], 'official_lightnovel_tag': True,
            'official_credits': deepcopy(reference['credits']), 'official_label': reference.get('label'),
            'official_label_literals': deepcopy(reference.get('label_literals', [])), 'official_form': reference.get('form'),
            'author_role_status': 'unknown' if all(row.get('role') == 'unknown'
                for row in reference['credits']) else 'unassessed', 'author_identity_asserted': False,
            'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
            'product_sku_identity_asserted': False,
            'title_match': primary['title_match'], 'matched_ndl_title': primary['title_match']['source_value'],
            'publisher_match': primary['publisher_match'], 'source_raw_sha256': primary['source_raw_sha256'],
            'publisher_source_evidence': deepcopy(evidence), 'same_isbn_candidates': candidates,
            'candidate_raw_sha256s': sorted({row['source_raw_sha256'] for row in candidates}),
            'sources': [primary['source_url'], reference['source_url']]}


def match_tobooks_edition(reference, primary_parts, *, same_isbn_parts):
    """Return an edition-only proof, or hold malformed/contradictory evidence.

    Authentication is an explicit caller responsibility, including enumeration
    of all same-ISBN own records. Credits are preserved without author inference.
    """
    try:
        return _match(reference, primary_parts, same_isbn_parts)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
