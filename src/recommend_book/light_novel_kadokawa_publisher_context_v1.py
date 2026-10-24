"""Assessed, dated catalogue-owner context for same-ISBN KAD edition matches.

The public factual contract contains no private artifacts or PDF content.
Named historical companies are a bounded policy population, not publisher
aliases, an all-era corporate identity map, or individual book assertions.
"""
from __future__ import annotations

import re

from .light_novel_reference_match_v1 import _key, _parent


_COMPANIES = ('アスキー・メディアワークス', 'エンターブレイン', '角川学芸出版', '角川書店',
    '角川プロダクション', '角川マガジンズ', '中経出版', '富士見書房', 'メディアファクトリー')
_ALLOWED = frozenset((*_COMPANIES, 'KADOKAWA'))
_ENDPOINT = 'https://www.kadokawa.co.jp/product/search/'
_PRODUCT = re.compile(r'https://www\.kadokawa\.co\.jp/product/([0-9]{12})/')
_SHA = re.compile(r'[0-9a-f]{64}')


def assessed_history_context():
    """Return immutable-scope facts, never an edition publisher or alias map."""
    return {'schema_version': 'kadokawa-assessed-publisher-context-v1',
        'announcement_date': '2013-10-01', 'date_precision': 'day',
        'source_url': 'https://group.kadokawa.co.jp/documents/topics/20131001_kado.pdf',
        'source_sha256': '0582e31c8b292b13042afd9d143c377ef5c528cfd3c409af0be04e8ffc220189',
        'source_pages': [1], 'locator': 'PDF page 1 date/header and named nine subsidiaries in first paragraph',
        'fact': 'KADOKAWA announced a merger with the nine named consolidated subsidiaries on this date.',
        'named_subsidiaries': list(_COMPANIES),
        'scope': 'fixed_assessed_names_for_same_isbn_edition_catalogue_context_only',
        'all_era_alias_asserted': False, 'individual_book_publisher_asserted': False,
        'publisher_identity_asserted': False, 'label_history_map_asserted': False,
        'rights_or_licence_succession_asserted': False}


def _direct(own, namespace, name, locator):
    return re.fullmatch(re.escape(own + '/{' + namespace + '}' + name) + r'\[[1-9][0-9]*\]', locator)


def _match(reference, parts):
    if (reference.get('catalogue_owner') != 'KADOKAWA'
            or reference.get('publisher_field_basis') != 'catalogue_owner_context'
            or 'individual_publisher' not in reference or reference['individual_publisher'] is not None):
        return None
    product = _PRODUCT.fullmatch(reference['source_url'])
    evidence = reference['evidence']
    if (not product or reference['publisher_book_id'] != 'kadokawa:' + product[1]
            or evidence['source_url'] != _ENDPOINT or not _SHA.fullmatch(evidence['source_sha256'])
            or not re.fullmatch(r'/result/book/[0-9]+', evidence['json_pointer'])):
        return None
    agents = parts['publisher_agents']
    publishers = [a for a in agents if a['description_values'] == []]
    distributors = [a for a in agents if a['description_values'] in (['発売'], ['頒布'])]
    if (len(publishers) != 1 or publishers[0]['name'] not in _ALLOWED
            or publishers[0]['description_locators'] != []
            or len(publishers) + len(distributors) != len(agents)
            or any(not a['name'] or not a['locator'] for a in agents)
            or parts['source']['publisher'] != '; '.join(dict.fromkeys(a['name'] for a in agents))):
        return None
    own = _parent(publishers[0]['locator'])
    if any(not _direct(own, 'http://purl.org/dc/terms/', 'publisher', a['locator']) for a in agents):
        return None
    if distributors and any(not a['structured_agent'] for a in agents):
        return None
    for agent in distributors:
        if (len(agent['description_locators']) != 1
                or not re.fullmatch(re.escape(agent['locator'])
                    + r'/\{http://xmlns\.com/foaf/0\.1/\}Agent\[1\]'
                    + r'/\{http://purl\.org/dc/terms/\}description\[[1-9][0-9]*\]', agent['description_locators'][0])):
            return None
    return {'rule': 'assessed_catalogue_owner_context_and_independent_own_publisher_v1',
        'catalogue_owner': 'KADOKAWA', 'publisher_field_basis': 'catalogue_owner_context',
        'individual_publisher': None,
        'official_context_evidence': {k: evidence[k] for k in ('source_url','source_sha256','json_pointer')},
        'ndl_publisher_policy': {'publisher_agent': {'name': publishers[0]['name'], 'locator': publishers[0]['locator']},
            'distribution_agents': [{'name': a['name'], 'role': a['description_values'][0],
                'locator': a['locator'], 'description_locator': a['description_locators'][0]} for a in distributors],
            'own_primary_resource_locator': own},
        'history_context': assessed_history_context(),
        'individual_publisher_inferred': False, 'publisher_identity_asserted': False,
        'global_publisher_alias_asserted': False, 'label_history_map_asserted': False}


def match_publisher_context(reference, primary_parts):
    """Return a separate context witness after caller raw authentication."""
    try:
        return _match(reference, primary_parts)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def title_uses_complete_own_dct(parts, title_match, context):
    """A context extension cannot use a short DC alias to omit a DCT subtitle."""
    try:
        own = context['ndl_publisher_policy']['own_primary_resource_locator']
        dct = [r for r in parts['titles'] if _direct(own,'http://purl.org/dc/terms/','title',r['locator'])]
        dc = [r for r in parts['titles'] if _direct(own,'http://purl.org/dc/elements/1.1/','title',r['locator'])]
        if (len(dct) != 1 or title_match['source_value'] != dct[0]['value']
                or title_match['locators'][0] != dct[0]['locator']
                or len(dct) + len(dc) != len(parts['titles'])):
            return False
        volume_row = parts['volume']
        volume = volume_row['value'] if volume_row else ''
        if volume_row and (not volume or not _direct(own,'http://ndl.go.jp/dcndl/terms/','volume',volume_row['locator'])):
            return False
        complete = dct[0]['value']
        if volume and complete.endswith('. ' + volume):
            complete = complete[:-len('. ' + volume)]
        elif volume and title_match['rule'] == 'direct_primary_title':
            # A direct unnumbered alias cannot bypass the explicit-volume gate.
            if not complete.endswith(' ' + volume):
                return False
            complete = complete[:-len(' ' + volume)]
        # NDL's independent DC value may be just the ISBD main title. The
        # selected proof still has to originate in the complete DCT field,
        # so this cannot let an official title omit its subtitle via DC.
        main = complete.split(' : ', 1)[0]
        allowed_dc = {_key(complete), _key(main)}
        return bool(complete and main) and all(_key(r['value']) in allowed_dc for r in dc)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
