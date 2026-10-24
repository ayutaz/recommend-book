"""Pure contained-ISBN bibliography matching for authenticated Futaba cards.

The caller authenticates the official reference and sole own NDL resource.
An ISBN/title/author/imprint correspondence grants no SKU, printing, original
work, story-series or global author identity. No I/O is performed here.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

from .light_novel_bibliographic_match_v1 import _authors, _publisher
from .light_novel_earthstar_match_v1 import _fallback_titles
from .light_novel_explicit_series_match_v1 import _person_key
from .light_novel_futaba_references_v1 import _FIRST, _LABEL, _ROLES, _SEARCH, _isbn
from .light_novel_reference_match_v1 import _key, _parent, _titles
from .normalization import normalize_isbn


_SHA = re.compile(r'[0-9a-f]{64}')
_NDL = re.compile(r'R100000002-[A-Za-z0-9._-]+')
_PRODUCT = re.compile(r'https://www\.futabasha\.co\.jp/book/([A-Za-z0-9_-]+)\?type=[123]')
_CARD = re.compile(r'(/books/book_informations/(?:[0-9]|1[0-9]|2[0-4])): .+')
_CLASSIFICATION_CONTEXT = {
    'source_url': 'https://www.futabasha.co.jp/recruit/2027/interview_comic-2.html',
    'section_heading_literal': '第二コミック出版部の仕事について',
    'checked_date': '2026-10-25', 'label': 'モンスター文庫',
    'evidence_scope': 'official_label_level_lightnovel_publishing_context_not_individual_product_genre',
    'evidence_status': 'primary_web_page_checked_by_root_raw_not_loaded_here',
}


def _official(reference):
    product = _PRODUCT.fullmatch(reference['source_url'])
    if (not product or reference['publisher_reference_id'] != 'futaba-product:' + product[1]
            or reference['source_unit'] != 'publisher_product_reference'
            or not isinstance(reference['title'], str) or not reference['title'].strip()
            or reference['label'] != _LABEL or reference['label_literals'] != [_LABEL]
            or reference['form'] != '文庫' or reference['publisher'] is not None
            or reference['official_lightnovel_tag'] is not None
            or reference.get('story_series_asserted') is not False
            or reference.get('metadata_conflict') is not False or reference.get('bibliographic_variants') != []):
        return None
    isbn = _isbn(reference['isbn_source_literal'])
    if not isbn or reference['isbn'] != isbn:
        return None
    evidence = reference['source_evidence']
    if not isinstance(evidence, list) or len(evidence) != 1:
        return None
    witness = evidence[0]
    card = _CARD.fullmatch(witness['locator'])
    query = parse_qs(urlsplit(witness['source_url']).query, keep_blank_values=True)
    page = query.get('page')
    if (not card or not _SHA.fullmatch(witness['raw_sha256']) or not page or len(page) != 1
            or not re.fullmatch(r'(?:[1-9]|1[0-6])', page[0])
            or witness['source_url'] != _FIRST.replace('&page=1&', '&page=' + page[0] + '&')):
        return None
    pointer = card[1]
    if page[0] == '16' and int(pointer.rsplit('/', 1)[1]) >= 3:
        return None
    membership = reference['source_scope_membership']
    expected = {'source_unit': 'publisher_label_filtered_product_listing',
        'membership_basis': 'returned_by_publisher_explicit_label_filter', 'filter_parameter': 'label_name',
        'filter_literal': _LABEL, 'rendered_index_url': _SEARCH, 'request_url': witness['source_url'],
        'raw_sha256': witness['raw_sha256'], 'source_label_id_literal': 14,
        'source_label_id_locator': pointer + '/label_id', 'card_label_literal': _LABEL,
        'card_label_locator': pointer + '/label_name', 'does_not_assert_story_series_or_original_identity': True}
    if (any(membership.get(k) != v for k, v in expected.items())
            or type(membership.get('source_label_id_literal')) is not int
            or membership.get('does_not_assert_story_series_or_original_identity') is not True
            or not isinstance(membership.get('official_ui_route_proof'), str) or not membership['official_ui_route_proof']):
        return None
    credits, literals = reference['credits'], reference['credit_literals']
    if not isinstance(credits, list) or not isinstance(literals, list) or len(credits) != len(literals):
        return None
    for index, (credit, literal) in enumerate(zip(credits, literals)):
        if (credit != {'role': _ROLES.get(literal['role'], 'unknown'), 'name': literal['name']}
                or not isinstance(credit['name'], str) or not _person_key(credit['name'])
                or literal['role_field'] not in {'author_type_name', 'web_author_type_overwrite'}
                or literal['locator'] != pointer + f'/book_authors/{index}'):
            return None
    writers = [credit['name'] for credit in credits if credit['role'] == 'author']
    if not writers or len({_person_key(name) for name in writers}) != len(writers):
        return None
    return isbn, writers, pointer


def _match(reference, parts):
    official = _official(reference)
    if official is None:
        return None
    isbn, writers, official_pointer = official
    source = parts['source']
    identity, ndc = source['source_id'], source['extra']['ndc_codes']
    if (not _NDL.fullmatch(identity) or source['source_url'] != 'https://ndlsearch.ndl.go.jp/books/' + identity
            or source.get('provider_id') not in {'iss-ndl-opac', 'iss-ndl-opac-national'}
            or source.get('record_kind') != 'edition' or source.get('language') not in {'ja', 'jpn'}
            or not _SHA.fullmatch(parts['source_raw_sha256'])
            or not ndc or any(not re.fullmatch(r'913(?:\.[0-9]+)?', value) for value in ndc)):
        return None
    publisher = _publisher(parts, '双葉社')
    if publisher is None:
        return None
    own = _parent(publisher['publisher_agent']['locator'])
    identifiers = [{'value': row['value'], 'projection_pointer': f'/source/identifiers/{index}',
                    'own_primary_resource_locator': own}
                   for index, row in enumerate(source['identifiers'])
                   if row['source'] == 'isbn' and normalize_isbn(row['value']) == isbn]
    if not identifiers or not parts['titles'] or any(not row['value'] or _parent(row['locator']) != own for row in parts['titles']):
        return None
    imprint = source.get('series')
    primary_imprint = unicodedata.normalize('NFKC', imprint or '').split(';', 1)[0].strip()
    imprint_forms = [value.strip() for value in primary_imprint.split(' = ')]
    if not imprint or _key(_LABEL) not in {_key(value) for value in imprint_forms}:
        return None
    title = _titles(parts, reference['title'], own) or _fallback_titles(parts, reference['title'], own)
    if title is None:
        return None
    authors = _authors(parts, writers, own)
    if not authors:
        return None
    locators = set(title['locators']) | {publisher['publisher_agent']['locator']}
    locators.update(value for row in publisher['distribution_agents'] for value in (row['locator'], row['description_locator']))
    locators.update(author['locator'] for author in authors if 'locator' in author)
    return {
        'rule_version': 'futaba-monster-ndl-contained-isbn-bibliography-v1', 'provider': 'futaba',
        'scope': 'edition_only', 'confirmed_scope': 'contained_bibliographic_edition_only',
        'same_isbn_bibliographic_correspondence_asserted': True, 'isbn13': isbn,
        'publisher_reference_id': reference['publisher_reference_id'], 'source_unit': reference['source_unit'],
        'official_source_url': reference['source_url'], 'official_title': reference['title'],
        'official_label': reference['label'], 'official_form': reference['form'],
        'official_publisher': None, 'official_lightnovel_tag': None,
        'official_credits': deepcopy(reference['credits']), 'official_credit_literals': deepcopy(reference['credit_literals']),
        'official_isbn_evidence': {'value': reference['isbn_source_literal'],
            'source_url': reference['source_evidence'][0]['source_url'],
            'raw_sha256': reference['source_evidence'][0]['raw_sha256'], 'locator': official_pointer + '/isbn_code'},
        'catalogue_owner': '双葉社', 'catalogue_owner_basis': 'authenticated_official_futaba_label_filtered_catalogue',
        'individual_publisher_agreement': 'unknown', 'official_publisher_asserted': False,
        'label_classification_context': deepcopy(_CLASSIFICATION_CONTEXT),
        'source_scope_membership': deepcopy(reference['source_scope_membership']),
        'publisher_source_evidence': deepcopy(reference['source_evidence']),
        'source_id': identity, 'source_url': source['source_url'], 'source_raw_sha256': parts['source_raw_sha256'],
        'own_primary_resource_locator': own, 'primary_isbn_evidence': identifiers,
        'title_match': title, 'matched_ndl_title': title['source_value'], 'authors': authors,
        'publisher_match': publisher, 'own_publisher': source['publisher'], 'own_imprint': imprint,
        'imprint_match': {'rule': 'exact_own_label_in_primary_imprint_with_existing_parsed_series_separator',
            'source_value': imprint, 'parsed_primary_imprint': primary_imprint, 'explicit_parallel_forms': imprint_forms,
            'own_primary_resource_locator': own, 'projection_pointer': '/source/series'},
        'language': source['language'], 'ndc_codes': deepcopy(ndc), 'primary_locators': sorted(locators),
        'primary_projection_pointers': ['/source/series', '/source/language', '/source/extra/ndc_codes'],
        'product_sku_identity_asserted': False, 'printing_identity_asserted': False,
        'same_edition_identity_asserted': False, 'paper_ebook_same_edition_asserted': False,
        'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
        'global_author_alias_asserted': False, 'global_author_identity_asserted': False,
    }


def match_futaba_reference(reference, primary_parts):
    """Return only a same-ISBN contained bibliographic correspondence proof."""
    try:
        return _match(reference, primary_parts)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
