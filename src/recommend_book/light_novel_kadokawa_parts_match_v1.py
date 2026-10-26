"""Pure title correspondence for one authenticated own contained part title.

This helper does not flatten contents into primary titles or grant admission.
The KADOKAWA caller still checks ISBN, writer, imprint, language and genre.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata

from .light_novel_reference_match_v1 import _key, _parent


def _title_key(value):
    value = unicodedata.normalize('NFKC', value).casefold().replace('〜', '~')
    return ''.join(chr(ord(c) + 0x60) if '\u3041' <= c <= '\u3096' else c for c in value
        if not c.isspace() and not unicodedata.category(c).startswith('P'))


def _direct(own, namespace, name, locator):
    return re.fullmatch(re.escape(own + '/{' + namespace + '}' + name) + r'\[[1-9][0-9]*\]', locator)


def _match(parts, target):
    if (parts.get('own_part_information_count') != 1
            or parts.get('own_part_information_structure_valid') is not True
            or parts.get('own_volume_count') != 1):
        return None
    contained = parts.get('own_part_information_titles', [])
    if len(contained) != 1:
        return None
    agents = parts['publisher_agents']
    if (len(agents) != 1 or agents[0]['name'] != 'KADOKAWA'
            or agents[0]['description_values'] != [] or agents[0]['description_locators'] != []
            or parts['source']['publisher'] != 'KADOKAWA'):
        return None
    own = _parent(agents[0]['locator'])
    if not _direct(own, 'http://purl.org/dc/terms/', 'publisher', agents[0]['locator']):
        return None
    titles = parts['titles']
    dc = [r for r in titles if _direct(own, 'http://purl.org/dc/elements/1.1/', 'title', r['locator'])]
    dct = [r for r in titles if _direct(own, 'http://purl.org/dc/terms/', 'title', r['locator'])]
    if len(titles) != 2 or len(dc) != 1 or len(dct) != 1 or not dc[0]['value'] or not dct[0]['value']:
        return None
    volume = parts['volume']
    if (not volume or not volume['value']
            or not _direct(own, 'http://ndl.go.jp/dcndl/terms/', 'volume', volume['locator'])):
        return None
    part = contained[0]
    container = part['part_information_locator']
    description = part['description_locator']
    if (not part['value'] or not _direct(own, 'http://ndl.go.jp/dcndl/terms/', 'partInformation', container)
            or description != container + '/{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description[1]'
            or part['locator'] != description + '/{http://purl.org/dc/terms/}title[1]'):
        return None
    # The complete DCT field and independent DC main must agree on this
    # literal volume. No numeric conversion or inferred part-title role.
    main, literal_volume = dc[0]['value'], volume['value']
    if _key(dct[0]['value']) not in {_key(main + '. ' + literal_volume), _key(main + ' ' + literal_volume)}:
        return None
    composed = main + literal_volume + part['value']
    if _title_key(composed) != _title_key(target):
        return None
    return {'rule': 'own_primary_title_explicit_volume_and_unique_part_information_v1',
        'scope': 'own_primary_and_unique_contained_part_title_to_official_edition_title',
        'source_value': dct[0]['value'], 'main': main, 'volume': literal_volume,
        'dc_title': deepcopy(dc[0]), 'dct_title': deepcopy(dct[0]),
        'explicit_volume': deepcopy(volume), 'part_information_title': deepcopy(part),
        'composed_title': composed, 'official_complete_title': target,
        'part_title_is_volume_title_asserted': False,
        'locators': [dct[0]['locator'], dc[0]['locator'], volume['locator'], container, description, part['locator']]}


def match_unique_own_part_information_title(primary_parts, official_title):
    """Return a separate contained-title witness, or hold incomplete evidence."""
    try:
        return _match(primary_parts, official_title)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
