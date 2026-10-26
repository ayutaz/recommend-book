"""Hash-pinned Hifumi book details and pure, ISBN-scoped NDL confirmation.

Only explicit bibliographic fields are returned. Catalogue ownership is not an
assertion of an individual book's publisher, physical printing or retail SKU.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import unicodedata

from .light_novel_bibliographic_match_v1 import _authors, _publisher
from .light_novel_earthstar_match_v1 import _fallback_titles
from .light_novel_hifumi_references_v1 import (
    _MIXED, _PRODUCT, _container, _json, _nav, _only, _source,
)
from .light_novel_publisher_references_v1 import MAX_METADATA_BYTES, _Node, _read, _relative
from .light_novel_reference_match_v1 import _key, _parent, _titles
from .light_novel_explicit_series_match_v1 import _person_key
from .normalization import normalize_isbn


_PAIRS = {'サーガフォレスト': 'Saga Forest', 'メルティアブックス': 'Meltia Books'}
_SHA = re.compile(r'[0-9a-f]{64}')
_NDL = re.compile(r'R100000002-[A-Za-z0-9._-]+')


def _direct(node, tag=None, cls=None):
    return [part for part in node.parts if isinstance(part, _Node)
            and (tag is None or part.tag == tag)
            and (cls is None or cls in part.attrs.get('class', '').split())]


def _detail(root, pin, listed):
    url = pin['source_url']
    identity = _PRODUCT.fullmatch(url)
    if identity is None or url not in listed:
        raise ValueError('Hifumi detail URL is not an observed own LN book')
    tree, _, _ = _source(root, pin, url)
    canonical = [node.attrs.get('href') for node in tree.nodes(tag='link')
                 if 'canonical' in node.attrs.get('rel', '').split()]
    if canonical != [url]:
        raise ValueError('Hifumi canonical detail identity differs')
    page = _only(tree.nodes(cls='hifumi-book-page-root'), 'own book page')
    if page.tag != 'main':
        raise ValueError('Hifumi book page scope differs')
    book = _only(page.nodes(cls='hifumi-book-main-container'), 'own book body')
    right = _only(book.nodes(cls='detail-right-side'), 'own detail metadata')
    heading = _only(_direct(right, tag='h1', cls='hifumi-title-v2'), 'complete own title')
    title = heading.text()
    label_row = _only(_direct(right, cls='label-badge-row-v2'), 'own label row')
    label = _only(_direct(label_row, tag='span', cls='label-badge'), 'own explicit label').text()
    if not title or label not in _PAIRS or label != listed[url]:
        raise ValueError('Hifumi own label differs from observed LN listing')
    credits = []
    credit_box = _only(_direct(right, cls='author-credits-v2'), 'own explicit credits')
    for row in _direct(credit_box, tag='p', cls='credit-item'):
        role = _only(_direct(row, tag='span', cls='role-label'), 'creator role').text()
        name = _only(_direct(row, tag='span', cls='name-label'), 'creator name').text()
        if not role or not name:
            raise ValueError('Hifumi empty own creator field')
        credits.append({'role': {'著者': 'author', '著': 'author', 'イラスト': 'illustrator'}
                        .get(''.join(role.split()), 'unknown'), 'name': name})
    specifications = _only(book.nodes(cls='spec-content-v2'), 'own book specifications')
    isbn_rows = [row for row in _direct(specifications, tag='p')
                 if any(child.text() == 'ISBN' for child in _direct(row, tag='span'))]
    isbn_text = _only(isbn_rows, 'explicit ISBN').text()
    match = re.fullmatch(r'ISBN\s*([0-9-]+)', isbn_text)
    isbn = normalize_isbn(match[1]) if match else None
    if not isbn or len(isbn) != 13:
        raise ValueError('Hifumi explicit valid ISBN absent')
    return {'publisher_book_id': 'hifumi:' + identity[1], 'source_unit': 'publisher_book_detail',
        'source_url': url, 'title': title, 'isbn': isbn, 'credits': credits,
        'label': label, 'label_literals': [label], 'catalogue_owner': '一二三書房',
        'individual_publisher': None, 'publisher': None, 'official_lightnovel_tag': True,
        'story_series_asserted': False, 'metadata_conflict': False, 'bibliographic_variants': [],
        'source_evidence': [{'source_url': url, 'raw_sha256': pin['raw_sha256'],
            'locator': 'main.hifumi-book-page-root .hifumi-book-main-container; '
                '.detail-right-side > h1.hifumi-title-v2; .label-badge-row-v2 > span.label-badge; '
                '.author-credits-v2 > p.credit-item > .role-label/.name-label; '
                '.spec-content-v2 > p[explicit ISBN]'}]}


def verify_hifumi_product_details(manifest_path, sha256):
    """Replay raw-pinned details within the finite 548-reference LN frame."""
    path = Path(manifest_path).resolve()
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if proof.get('schema_version') != 'hifumi-product-details-proof-v1':
            raise ValueError('Hifumi detail proof schema differs')
        pins = proof['details']
        if not isinstance(pins, list) or not 1 <= len(pins) <= 548:
            raise ValueError('Hifumi detail frame outside fixed bounded scope')
        urls = [row['source_url'] for row in pins]
        if len(set(urls)) != len(urls) or any(not _PRODUCT.fullmatch(url) for url in urls):
            raise ValueError('Hifumi duplicate/outside detail identity')
        base_pin = proof['base_frame']
        base_path = _relative(path.parent, base_pin['manifest_path'])
        base = _json(path.parent, base_pin['manifest_path'], base_pin['manifest_sha256'])
        if base.get('schema_version') != 'hifumi-three-label-bibliographic-proof-v1':
            raise ValueError('Hifumi base frame schema differs')
        mixed_pin = base['mixed_index']
        mixed, _, _ = _source(base_path.parent, mixed_pin, _MIXED)
        _nav(mixed)
        listed = {}
        for card in _container(mixed).nodes(cls='custom-item-row'):
            body = _only(card.nodes(cls='custom-item-body'), 'own index card')
            headings = body.nodes(tag='h2')
            matching = [a for h in headings for a in h.nodes(tag='a') if a.attrs.get('href') in urls]
            if not matching:
                continue
            anchor = _only(matching, 'observed detail link')
            url = anchor.attrs['href']
            label = _only(body.nodes(cls='label-badge'), 'observed own card label').text()
            if url in listed or label not in _PAIRS:
                raise ValueError('Hifumi duplicate or unsupported book label')
            listed[url] = label
        if set(listed) != set(urls):
            raise ValueError('Hifumi detail is not contained in saved LN frame')
        evidence = {'source_url': _MIXED, 'raw_sha256': mixed_pin['raw_sha256'],
            'locator': 'li#menu-item-21093 > a[ライトノベル]; explicit brand nav; '
                '#lineup-container .custom-item-row .custom-item-body > h2 > a[exact detail URL]; .label-badge'}
        return [{**row, 'source_evidence': row['source_evidence'] + [deepcopy(evidence)]}
                for row in (_detail(path.parent, pin, listed) for pin in pins)]
    except (KeyError, TypeError, IndexError, AttributeError, UnicodeDecodeError, OSError) as error:
        raise ValueError('Hifumi detail bibliographic evidence cannot be reproduced') from error


def _match(detail, parts):
    url = detail['source_url']
    identity = _PRODUCT.fullmatch(url)
    label = detail['label']
    if (not identity or detail['publisher_book_id'] != 'hifumi:' + identity[1]
            or detail['source_unit'] != 'publisher_book_detail' or not detail['title']
            or detail['catalogue_owner'] != '一二三書房' or detail['individual_publisher'] is not None
            or detail['publisher'] is not None or label not in _PAIRS or detail['label_literals'] != [label]
            or detail['official_lightnovel_tag'] is not True or detail['story_series_asserted'] is not False
            or detail['metadata_conflict'] is not False or detail['bibliographic_variants'] != []):
        return None
    isbn = normalize_isbn(detail['isbn'])
    evidence = detail['source_evidence']
    if (not isbn or len(isbn) != 13 or not isinstance(evidence, list)
            or {row['source_url'] for row in evidence} != {url, _MIXED}
            or any(not _SHA.fullmatch(row['raw_sha256']) or not row['locator'] for row in evidence)):
        return None
    source = parts['source']
    source_id = source['source_id']
    ndc = source['extra']['ndc_codes']
    if (not _NDL.fullmatch(source_id) or source['source_url'] != 'https://ndlsearch.ndl.go.jp/books/' + source_id
            or source['provider_id'] not in {'iss-ndl-opac', 'iss-ndl-opac-national'}
            or source['record_kind'] != 'edition' or source['language'] not in {'ja', 'jpn'}
            or not _SHA.fullmatch(parts['source_raw_sha256']) or not ndc
            or any(not re.fullmatch(r'913(?:\.[0-9]+)?', code) for code in ndc)
            or {normalize_isbn(row['value']) for row in source['identifiers'] if row['source'] == 'isbn'} != {isbn}):
        return None
    publisher = _publisher(parts, '一二三書房')
    if publisher is None:
        return None
    own = _parent(publisher['publisher_agent']['locator'])
    title_path = re.compile(re.escape(own) + r'/\{http://purl\.org/dc/(?:terms/|elements/1\.1/)\}title\[[1-9][0-9]*\]')
    if not parts['titles'] or any(not row['value'] or not title_path.fullmatch(row['locator']) for row in parts['titles']):
        return None
    imprint = source.get('series')
    forms = unicodedata.normalize('NFKC', imprint or '').split(';', 1)[0].strip().split(' = ')
    if not imprint or _key(_PAIRS[label]) not in {_key(form.strip()) for form in forms}:
        return None
    title = _titles(parts, detail['title'], own) or _fallback_titles(parts, detail['title'], own)
    if title is None:
        return None
    names = [row['name'] for row in detail['credits'] if row['role'] == 'author']
    if not names or any(not _person_key(name) for name in names) or len({_person_key(n) for n in names}) != len(names):
        return None
    authors = _authors(parts, names, own)
    if not authors:
        return None
    return {'rule_version': 'publisher-ndl-isbn-bibliographic-confirmation-hifumi-v1',
        'scope': 'edition_only', 'confirmed_scope': 'contained_bibliographic_edition_only',
        'same_isbn_bibliographic_correspondence_asserted': True,
        'isbn13': isbn, 'publisher_book_id': detail['publisher_book_id'],
        'official_source_url': url, 'official_title': detail['title'], 'official_label': label,
        'official_lightnovel_tag': True, 'catalogue_owner': '一二三書房',
        'individual_publisher': None, 'individual_publisher_agreement': 'unknown',
        'official_publisher_asserted': False, 'source_id': source_id, 'source_url': source['source_url'],
        'source_raw_sha256': parts['source_raw_sha256'], 'title_match': title,
        'matched_ndl_title': title['source_value'], 'authors': authors, 'publisher_match': publisher,
        'imprint_match': {'rule': 'same_explicit_isbn_hifumi_japanese_label_english_imprint_pair',
            'scope': 'this_isbn_bibliography_only', 'isbn13': isbn, 'official_label': label,
            'own_imprint': imprint, 'expected_english_literal': _PAIRS[label],
            'own_primary_resource_locator': own, 'projection_pointer': '/source/series'},
        'language': source['language'], 'ndc_codes': deepcopy(ndc),
        'publisher_source_evidence': deepcopy(evidence), 'sources': [source['source_url'], url],
        'same_printing_identity_asserted': False, 'product_sku_identity_asserted': False,
        'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
        'global_imprint_alias_asserted': False, 'global_author_alias_asserted': False,
        'global_author_identity_asserted': False}


def match_hifumi_product_detail(detail, primary_parts):
    """Match authenticated own primary parts, preserving unknown author authority.

    The caller authenticates NDL raw/typed identity and supplies these direct own
    fields; this pure function does no I/O and creates no global imprint alias.
    """
    try:
        return _match(detail, primary_parts)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
