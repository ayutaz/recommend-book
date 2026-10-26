"""Replay the fixed 35-page Lanove product-reference frame from local raw.

Only bibliographic card fields are returned. The exported private roster is
never read as metadata authority; publisher prose, images, ISBN/volume guesses,
original-work identities and story-series identities are excluded.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urljoin

from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _decode, _read, _relative,
)


_START = 'https://www.kodansha.co.jp/comic/labels/lanove'
_LABEL = '講談社ラノベ文庫'
_PAGE = re.compile(re.escape(_START) + r'(?:\?page=([1-9][0-9]*))?')
_PRODUCT = re.compile(r'https://www\.kodansha\.co\.jp/comic/products/([0-9]+)')
_ROLE = re.compile(r'(?:^|，)\s*([^：，]+)：')
_ROLES = {'著': 'author', '著者': 'author', '作': 'author', 'イラスト': 'illustrator',
          '原作': 'original_author', '原案': 'original_concept', '監修': 'supervisor'}
_TOTAL, _LAST = 684, 35


def _json(path, expected):
    try:
        return json.loads(_read(path, expected, MAX_METADATA_BYTES))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Kodansha evidence JSON cannot be reproduced') from exc


def _literal(node):
    if node.tag in {'script', 'style'}:
        return ''
    return ''.join(_literal(p) if isinstance(p, _Node) else p for p in node.parts)


def _one(node, cls):
    nodes = node.nodes(cls=cls)
    if len(nodes) != 1:
        raise ValueError('Kodansha bibliographic field cardinality differs: ' + cls)
    return nodes[0]


def _source(root, receipt, expected_url):
    statuses = [receipt[key] for key in ('status', 'http_status') if key in receipt]
    if (receipt.get('source_url') != expected_url or not statuses or any(v != 200 for v in statuses)
            or receipt.get('final_url', expected_url) != expected_url or receipt.get('location')
            or receipt.get('error') or receipt.get('size_cap_exceeded')
            or receipt.get('http_redirects_followed', 0) != 0 or receipt.get('http_retry_count', 0) != 0):
        raise ValueError('Kodansha receipt status, URL or access scope differs')
    content_type = receipt.get('content_type') or ''
    if not content_type.lower().startswith('text/html'):
        raise ValueError('Kodansha source is not HTML')
    raw = _read(_relative(root, receipt.get('raw_path')), receipt.get('sha256'), MAX_SOURCE_BYTES)
    if receipt.get('byte_length') != len(raw):
        raise ValueError('Kodansha receipt declared source size differs')
    return _DOM(_decode(raw, content_type)).root


def _credits(value):
    if not value:
        return []
    matches = list(_ROLE.finditer(value))
    if not matches:
        return [{'role': 'unknown', 'name': value}]
    if value[:matches[0].start()].strip():
        raise ValueError('Kodansha mixed marked/unmarked creator field unresolved')
    result = []
    for index, match in enumerate(matches):
        name = value[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(value)].strip()
        if not name:
            raise ValueError('Kodansha explicit creator name missing')
        result.append({'role': _ROLES.get(match[1].strip(), 'unknown'), 'name': name})
    return result


def _page(tree, receipt, number):
    totals = {int(n.text()[:-1]) for n in tree.nodes(tag='strong') if re.fullmatch(r'[0-9]+件', n.text())}
    if totals != {_TOTAL}:
        raise ValueError('Kodansha explicit total changed or ambiguous')
    headings = [n.text() for tag in ('h1', 'h2') for n in tree.nodes(tag=tag)]
    if _LABEL + '作品一覧' not in headings:
        raise ValueError('Kodansha single-label listing heading differs')
    cards = tree.nodes(tag='a', cls='app-product-list-item')
    if len(cards) != (20 if number < _LAST else 4):
        raise ValueError('Kodansha fixed page card count differs')
    rows = []
    for ordinal, card in enumerate(cards, 1):
        url = card.attrs.get('href', '')
        identity = _PRODUCT.fullmatch(url)
        title = _literal(_one(card, 'app-product-list-item-detail-name')).strip()
        creators = _one(card, 'app-product-list-item-detail-authors').text()
        label = _one(card, 'app-product-list-item-detail-label').text()
        if not identity or not title or label != _LABEL:
            raise ValueError('Kodansha opaque product URL, complete title or card label differs')
        locator = (f'(a.app-product-list-item)[{ordinal}] .app-product-list-item-detail-name / '
                   '.app-product-list-item-detail-authors / .app-product-list-item-detail-label')
        rows.append({'publisher_reference_id': 'kodansha-product:' + identity[1],
            'source_unit': 'publisher_product_reference', 'source_url': url, 'title': title,
            'isbn': None, 'credits': _credits(creators), 'publisher': None, 'label': label,
            'label_literals': [label], 'form': None, 'official_lightnovel_tag': True,
            'story_series_asserted': False, 'metadata_conflict': False, 'bibliographic_variants': [],
            'source_evidence': [{'source_url': receipt['source_url'], 'raw_sha256': receipt['sha256'], 'locator': locator}]})
    links = {urljoin(receipt['source_url'], a.attrs.get('href', '')) for a in tree.nodes(tag='a')}
    links = sorted(u for u in links if _PAGE.fullmatch(u))
    numbers = {int(_PAGE.fullmatch(u)[1] or '1') for u in links}
    if any(n > _LAST for n in numbers) or number == 1 and _LAST not in numbers:
        raise ValueError('Kodansha explicit final-page bound differs')
    nexts = [u for u in links if int(_PAGE.fullmatch(u)[1] or '1') == number + 1]
    if number < _LAST and len(nexts) != 1 or number == _LAST and nexts:
        raise ValueError('Kodansha explicit next or terminal differs')
    return rows, links, nexts[0] if nexts else None


def verify_kodansha_references(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Return 684 product references reproduced from pinned receipts and raw.

    This single-label snapshot is intentionally fixed at 35 pages. A changed
    total or boundary requires a new reviewed frame, not guessed pagination.
    """
    path = Path(manifest_path).resolve()
    try:
        manifest = _json(path, sha256)
        counts = {'fixed_publisher_reported_total': _TOTAL, 'fixed_last_linked_page': _LAST,
                  'pages_acquired': _LAST, 'product_reference_occurrences': _TOTAL, 'unique_product_ids': _TOTAL}
        if (manifest.get('version') != 1 or manifest.get('frame_id') != 'kodansha-lanove-product-index-v1'
                or manifest.get('label') != _LABEL or manifest.get('source_url') != _START
                or manifest.get('unit') != 'publisher_product_reference_not_story_series_original_work_or_edition'
                or manifest.get('index_pages_complete') is not True
                or manifest.get('terminal_no_next_observed') is not True or manifest.get('holds')
                or any(manifest.get(key) != value for key, value in counts.items())):
            raise ValueError('Kodansha finite one-label frame schema or terminal differs')
        pages = _json(_relative(path.parent, manifest['page_receipts_path']), manifest['page_receipts_sha256'])
        if not isinstance(pages, list) or len(pages) != _LAST:
            raise ValueError('Kodansha receipt page count differs')
        rows, seen = [], set()
        for number, page in enumerate(pages, 1):
            url = _START if number == 1 else _START + f'?page={number}'
            if page['page'] != number:
                raise ValueError('Kodansha receipt page order differs')
            receipt = page['source_receipt']
            tree = _source(path.parent, receipt, url)
            parsed, links, next_url = _page(tree, receipt, number)
            if (page['cards'] != len(parsed) or page['linked_pagination_urls'] != links
                    or page['explicit_next_url'] != next_url):
                raise ValueError('Kodansha pagination checkpoint differs from raw')
            for row in parsed:
                identity = row['publisher_reference_id']
                if identity in seen:
                    raise ValueError('Kodansha duplicate opaque product ID')
                seen.add(identity)
            rows.extend(parsed)
        if len(rows) != _TOTAL or len(seen) != _TOTAL:
            raise ValueError('Kodansha unique product-reference count differs')
        return rows
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Kodansha source evidence schema cannot be reproduced') from exc
