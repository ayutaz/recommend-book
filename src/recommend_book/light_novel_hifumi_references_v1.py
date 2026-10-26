"""Replay three finite Hifumi label listings as minimum book references.

The closed unit is the explicit embedded listing, not a historical catalogue,
edition, original work or story series. URL digits supply opaque source IDs,
never ISBNs or volume numbers. Only hash-pinned local artifacts are read.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _decode, _read, _relative,
)

_LABELS = {
    'sagaforest': ('サーガフォレスト', 'https://hifumi.co.jp/saga_forest/'),
    'meltia': ('メルティアブックス', 'https://hifumi.co.jp/meltia/'),
    'bravenovel': ('ブレイブ文庫', 'https://hifumi.co.jp/bravenovel/'),
}
_MIXED = 'https://hifumi.co.jp/lineup_all/'
_PRODUCT = re.compile(r'https://hifumi\.co\.jp/lineup/([A-Za-z0-9_-]+)/')
_FRAME_UNIT = 'explicit_book_cards_in_one_current_official_label_listing'


def _json(root, name, digest):
    return json.loads(_read(_relative(root, name), digest, MAX_METADATA_BYTES))


def _only(values, field):
    if len(values) != 1:
        raise ValueError('Hifumi field cardinality differs: ' + field)
    return values[0]


def _literal(node):
    if node.tag in ('script', 'style'):
        return ''
    if node.tag == 'br':
        return '\n'
    return ''.join(_literal(part) if isinstance(part, _Node) else part for part in node.parts)


def _source(root, pin, expected_url):
    if pin['source_url'] != expected_url:
        raise ValueError('Hifumi source outside fixed own URL')
    receipt = _json(root, pin['receipt_path'], pin['receipt_sha256'])
    if (receipt['source_url'] != expected_url or receipt.get('final_url') != expected_url
            or receipt.get('http_status') != 200 or receipt.get('location') or receipt.get('hold')
            or not receipt.get('content_type', '').lower().startswith('text/html')
            or receipt['sha256'] != pin['raw_sha256']):
        raise ValueError('Hifumi HTTP receipt scope/status differs')
    path = _relative(root, pin['raw_path'])
    raw = _read(path, pin['raw_sha256'], MAX_SOURCE_BYTES)
    if len(raw) != receipt['byte_length']:
        raise ValueError('Hifumi declared source size differs')
    return _DOM(_decode(raw, receipt['content_type'])).root, receipt, path


def _nav(tree):
    parent = _only([n for n in tree.nodes(tag='li') if n.attrs.get('id') == 'menu-item-21093'], 'LN nav')
    direct = [n for n in parent.parts if isinstance(n, _Node) and n.tag == 'a']
    if _only(direct, 'LN nav label').text() != 'ライトノベル':
        raise ValueError('Hifumi parent category is not explicitly light novels')
    comic = _only([n for n in tree.nodes(tag='li') if n.attrs.get('id') == 'menu-item-21097'], 'comic nav')
    direct = [n for n in comic.parts if isinstance(n, _Node) and n.tag == 'a']
    if _only(direct, 'comic nav label').text() != 'コミック':
        raise ValueError('Hifumi comic category is not independently explicit')
    for label, url in _LABELS.values():
        _only([a for a in parent.nodes(tag='a') if a.text() == label
               and urljoin(_MIXED, a.attrs.get('href', '')) == url], 'own LN brand nav')


def _container(tree):
    return _only([n for n in tree.nodes() if n.attrs.get('id') == 'lineup-container'], 'own book listing')


def _card(node, number, label, source_url, digest):
    body = _only(node.nodes(cls='custom-item-body'), 'own card body')
    heading = _only(body.nodes(tag='h2'), 'complete book title heading')
    link = _only(heading.nodes(tag='a'), 'complete book title anchor')
    url = urljoin(source_url, link.attrs.get('href', ''))
    identity = _PRODUCT.fullmatch(url)
    title = _literal(link).strip()
    if not identity or not title:
        raise ValueError('Hifumi own product-reference URL/title differs')
    labels = [n.text() for n in body.nodes(cls='label-badge')]
    if labels and labels != [label]:
        raise ValueError('Hifumi card label differs from selected own brand')
    credits = []
    for row in body.nodes(cls='author-tag-item'):
        role = _literal(_only(row.nodes(cls='author-role'), 'explicit creator role')).strip()
        name = _literal(_only(row.nodes(cls='author-name'), 'explicit creator name')).strip()
        if not role or not name:
            raise ValueError('Hifumi empty explicit creator field')
        credits.append({'role': {'著者': 'author', '著': 'author', 'イラスト': 'illustrator'}.get(''.join(role.split()), 'unknown'),
                        'role_display': role, 'name': name})
    dates = [_literal(n).strip() for n in body.nodes(cls='ymd')]
    return {'identity': identity[1], 'source_url': url, 'title': title, 'credits': credits,
            'publication_date_literals': dates,
            'evidence': {'source_url': source_url, 'raw_sha256': digest,
                'locator': f'#lineup-container .custom-item-row:nth-of-type({number}) .custom-item-body h2 > a; .author-role/.author-name'}}


def _signature(row):
    return {key: row[key] for key in ('source_url', 'title', 'credits', 'publication_date_literals')}


def _index(rows):
    result = {}
    for row in rows:
        if row['identity'] in result:
            raise ValueError('Hifumi duplicate/conflicting product reference')
        result[row['identity']] = row
    return result


def _no_pagination(tree, url):
    for anchor in tree.nodes(tag='a'):
        text = anchor.text()
        href = urljoin(url, anchor.attrs.get('href', ''))
        if ('next' in anchor.attrs.get('rel', '').split()
                or re.fullmatch(r'次へ|次のページ|次の[0-9]+件', text)
                or (re.fullmatch(r'[0-9]+', text) and urlsplit(url).path in href)):
            raise ValueError('Hifumi current snapshot declares an unclosed next/page route')


def _brand(root, pin, mixed_rows, nav_evidence):
    label, url = _LABELS[pin['id']]
    if pin['label'] != label:
        raise ValueError('Hifumi fixed brand label differs')
    manifest_path = _relative(root, pin['manifest_path'])
    manifest = _json(root, pin['manifest_path'], pin['manifest_sha256'])
    if (manifest['source_url'] != url or manifest['label'] != label or manifest['frame_unit'] != _FRAME_UNIT
            or manifest.get('official_declared_total') is not None
            or manifest.get('publisher_historical_catalogue_complete') is not False
            or manifest.get('finite_embedded_listing_complete') is not True
            or manifest.get('holds') != [] or manifest.get('duplicates') != []
            or manifest.get('saved_mixed_reference_set_matches') is not True
            or manifest.get('next_hrefs') != [] or manifest.get('numbered_pagination_hrefs') != []):
        raise ValueError('Hifumi finite embedded-listing scope differs')
    tree, receipt, raw_path = _source(root, pin, url)
    saved_receipt = manifest['raw_source']
    # Original receipts can use paths relative to a nested brand directory.
    # Only the separately pinned, boundary-checked raw_path above is opened.
    if ((manifest_path.parent / saved_receipt['raw_path']).resolve() != raw_path
            or any(saved_receipt.get(k) != receipt.get(k) for k in (
                'source_url', 'final_url', 'http_status', 'content_type', 'sha256', 'byte_length'))):
        raise ValueError('Hifumi saved frame receipt/raw binding differs')
    headings = [h.text() for n in tree.nodes(cls='label_title') for h in n.nodes(tag='h2')]
    if headings != [label]:
        raise ValueError('Hifumi own listing heading differs from label')
    _no_pagination(tree, url)
    cards = _container(tree).nodes(cls='custom-item-row')
    if not cards or len(cards) > 5000:
        raise ValueError('Hifumi embedded listing size outside finite bound')
    rows = [_card(node, number, label, url, pin['raw_sha256']) for number, node in enumerate(cards, 1)]
    current = _index(rows)
    old = _index(mixed_rows[label])
    if current.keys() != old.keys() or any(_signature(row) != _signature(old[key]) for key, row in current.items()):
        raise ValueError('Hifumi label and saved mixed metadata/reference sets differ')
    for field in ('observed_embedded_book_cards', 'parsed_references', 'unique_book_references', 'saved_mixed_label_cards'):
        if type(manifest.get(field)) is not int or manifest[field] != len(rows):
            raise ValueError('Hifumi saved embedded-card count differs')
    roster = _json(manifest_path.parent, manifest['roster']['path'], manifest['roster']['sha256'])
    if len(roster) != len(rows):
        raise ValueError('Hifumi saved roster cardinality differs')
    for declared, actual in zip(roster, rows):
        if (declared.get('publisher_reference_id') != 'hifumi-book-reference:' + actual['identity']
                or declared.get('label') != label or declared.get('isbn') is not None or declared.get('volume') is not None
                or any(declared.get(k) != actual[k] for k in ('source_url', 'title', 'publication_date_literals'))
                or [{k: c[k] for k in ('role', 'role_display', 'name')} for c in declared['credits']] != actual['credits']):
            raise ValueError('Hifumi saved roster metadata is not reproduced by raw')
    return [{'publisher_reference_id': 'hifumi-product:' + row['identity'], 'source_unit': 'publisher_product_reference',
        'source_url': row['source_url'], 'title': row['title'], 'isbn': None,
        'credits': [{k: c[k] for k in ('role', 'name')} for c in row['credits']],
        'publisher': None, 'label': label, 'label_literals': [label], 'form': None,
        'official_lightnovel_tag': True, 'story_series_asserted': False,
        'metadata_conflict': False, 'bibliographic_variants': [],
        'source_evidence': [row['evidence'], old[row['identity']]['evidence'], nav_evidence]}
        for row in rows]


def verify_hifumi_references(manifest_path, sha256):
    """Return bounded explicit LN-labelled book references without identity inference."""
    path = Path(manifest_path).resolve()
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if (proof.get('schema_version') != 'hifumi-three-label-bibliographic-proof-v1'
                or proof.get('historical_catalogue_complete') is not False
                or proof.get('identity_assertions') != {'edition': False, 'original_work': False, 'story_series': False}):
            raise ValueError('Hifumi proof finite scope differs')
        brands = proof['brands']
        if len(brands) != 3 or {row['id'] for row in brands} != set(_LABELS):
            raise ValueError('Hifumi fixed three-brand scope differs')
        mixed_pin = proof['mixed_index']
        mixed, _, _ = _source(path.parent, mixed_pin, _MIXED)
        _nav(mixed)
        mixed_rows = {label: [] for label, _ in _LABELS.values()}
        for number, node in enumerate(_container(mixed).nodes(cls='custom-item-row'), 1):
            labels = [n.text() for n in node.nodes(cls='label-badge')]
            if len(labels) == 1 and labels[0] in mixed_rows:
                mixed_rows[labels[0]].append(_card(node, number, labels[0], _MIXED, mixed_pin['raw_sha256']))
        nav_evidence = {'source_url': _MIXED, 'raw_sha256': mixed_pin['raw_sha256'],
            'locator': 'li#menu-item-21093 > a[ライトノベル]; descendant explicit brand link; separate li#menu-item-21097[コミック]'}
        rows = [row for pin in brands for row in _brand(path.parent, pin, mixed_rows, nav_evidence)]
        if (type(proof.get('expected_book_references')) is not int or len(rows) != proof['expected_book_references']
                or len({row['publisher_reference_id'] for row in rows}) != len(rows)):
            raise ValueError('Hifumi finite reference count/uniqueness differs')
        return rows
    except (KeyError, TypeError, IndexError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('Hifumi local bibliographic evidence cannot be reproduced') from error
