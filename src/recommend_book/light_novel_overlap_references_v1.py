"""Replay a fixed BNK index as product references, without genre/series guesses.

Only local pinned receipts and card literals are authoritative. Exported rosters,
prose, hidden values and image metadata are not projection inputs.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _decode, _read, _relative,
)

_START = 'https://over-lap.co.jp/Form/Product/ProductList.aspx?cat=BNK'
_LABEL = 'オーバーラップ文庫'
_SCOPE = {key: [''] for key in ('pid', 'vid', 'swrd', 'cicon', 'min', 'max', 'dosp',
    'sellfrom_y', 'sellfrom_m', 'sellto_y', 'sellto_m', 'hasq')}
_SCOPE.update(img=['1'], sort=['11'], udns=['0'])
_LAST, _TOTAL = 34, 1019


def _literal(node):
    if node.tag in {'script', 'style', 'input', 'img'}:
        return ''
    if node.tag == 'br':
        return '\n'
    return ''.join(_literal(p) if isinstance(p, _Node) else p for p in node.parts)


def _route(url):
    p = urlsplit(url)
    q = parse_qs(p.query, keep_blank_values=True)
    number = q.pop('pno', [])
    if (p.scheme != 'https' or p.netloc != 'over-lap.co.jp' or p.fragment
            or not re.fullmatch(r'/[^/]+/category/0/BNK/', p.path) or q != _SCOPE
            or len(number) != 1 or not re.fullmatch(r'[1-9][0-9]*', number[0])
            or not 1 <= int(number[0]) <= _LAST):
        raise ValueError('Overlap pagination leaves fixed BNK filter or bound')
    return int(number[0]), p.path


def _source(root, receipt, url):
    statuses = [receipt[k] for k in ('status', 'http_status') if k in receipt]
    if (receipt.get('source_url') != url or not statuses or any(v != 200 for v in statuses)
            or receipt.get('final_url', url) != url or receipt.get('location') or receipt.get('error')
            or receipt.get('redirects_followed', False) or receipt.get('size_cap_exceeded')
            or receipt.get('http_redirects_followed', 0) or receipt.get('http_retry_count', 0)):
        raise ValueError('Overlap receipt status, URL or access scope differs')
    content_type = receipt.get('content_type') or ''
    if not content_type.lower().startswith('text/html'):
        raise ValueError('Overlap source is not HTML')
    # Production receipts explicitly include their frame directory as the first
    # component. Do not search arbitrary ancestors or permit path traversal.
    raw_name = receipt.get('raw_path')
    normalized = raw_name.replace('\\', '/') if isinstance(raw_name, str) else ''
    if normalized.startswith('overlap-sourceframes-v1/'):
        if root.name != 'overlap-sourceframes-v1':
            raise ValueError('Overlap receipt root differs')
        raw_root = root.parent
    else:
        raw_root = root
    data = _read(_relative(raw_root, raw_name), receipt.get('sha256'), MAX_SOURCE_BYTES)
    if receipt.get('byte_length') != len(data):
        raise ValueError('Overlap source declared size differs')
    return _DOM(_decode(data, content_type)).root


def _page(tree, receipt, page, route_path):
    if [n.text() for n in tree.nodes(tag='title')] != [_LABEL + '｜OVERLAP']:
        raise ValueError('Overlap one-label page title differs')
    lists = tree.nodes(cls='product-cards')
    if len(lists) != 1:
        raise ValueError('Overlap own card list boundary ambiguous')
    cards = [n for n in lists[0].parts if isinstance(n, _Node)
             and 'product-card' in n.attrs.get('class', '').split()]
    if len(cards) != (30 if page < _LAST else 29):
        raise ValueError('Overlap fixed page card count differs')
    rows = []
    for ordinal, card in enumerate(cards, 1):
        anchors = card.nodes(tag='a')
        heads, labels = card.nodes(cls='product-card__title'), card.nodes(cls='bnk__badge')
        if len(anchors) != 1 or len(heads) != 1 or len(labels) != 1:
            raise ValueError('Overlap bibliographic card field ambiguous')
        url = urljoin(receipt['source_url'], anchors[0].attrs.get('href', ''))
        parsed = urlsplit(url)
        identity = re.fullmatch(r'/[^/]+/product/0/([A-Za-z0-9_-]+)/', parsed.path)
        title, label = _literal(heads[0]).strip(), labels[0].text()
        if (parsed.scheme != 'https' or parsed.netloc != 'over-lap.co.jp' or parsed.fragment
                or not identity or parse_qs(parsed.query, keep_blank_values=True) != {'cat':['BNK'], 'swrd':['']}
                or not title or label != _LABEL):
            raise ValueError('Overlap opaque product URL, complete title or explicit card label differs')
        rows.append({'publisher_reference_id':'overlap-product:' + identity[1],
            'source_unit':'publisher_product_reference', 'source_url':url, 'title':title,
            'publisher':None, 'isbn':None, 'form':None, 'credits':[], 'label':label,
            'label_literals':[label], 'official_lightnovel_tag':None,
            'story_series_asserted':False, 'metadata_conflict':False, 'bibliographic_variants':[],
            'source_evidence':[{'source_url':receipt['source_url'], 'raw_sha256':receipt['sha256'],
                'locator':f'.product-cards > .product-card[{ordinal}] a / .product-card__title / .bnk__badge'}]})
    next_nodes = tree.nodes(tag='li', cls='next_on')
    nexts = {urljoin(receipt['source_url'], a.attrs.get('href', ''))
             for li in next_nodes for a in li.nodes(tag='a')}
    links = {urljoin(receipt['source_url'], a.attrs.get('href', ''))
             for a in tree.nodes(tag='a') if 'pno=' in a.attrs.get('href', '')}
    numbers = []
    for link in links:
        number, path = _route(link)
        if route_path is not None and path != route_path:
            raise ValueError('Overlap pagination route changed')
        numbers.append(number)
    if max(numbers + [page]) != _LAST:
        raise ValueError('Overlap last linked page differs')
    if page < _LAST:
        if len(next_nodes) != 1 or len(nexts) != 1 or len(next_nodes[0].nodes(tag='a')) != 1:
            raise ValueError('Overlap explicit next ambiguous or absent')
        next_url = next(iter(nexts))
        number, path = _route(next_url)
        if number != page + 1 or route_path is not None and path != route_path:
            raise ValueError('Overlap sequential next changed')
        route_path = path
    else:
        currents = tree.nodes(tag='li', cls='on')
        ends = tree.nodes(tag='li', cls='next_off')
        if (next_nodes or nexts or len(currents) != 1 or currents[0].text() != str(_LAST)
                or currents[0].nodes(tag='a') or len(ends) != 1 or ends[0].nodes(tag='a')):
            raise ValueError('Overlap terminal current page or next_off differs')
        next_url = None
    return rows, next_url, route_path


def verify_overlap_references(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Return 1,019 card references from the fixed 34-page source snapshot.

    A Bunko category or brand is not itself explicit novel-genre evidence.
    ISBNs and authors are unknown; numerical opaque IDs are never ISBN guesses.
    This frame does not establish a national or historical denominator.
    """
    path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        expected = {'schema_version':'overlap-bunko-product-frame-v1', 'status':'terminal_fixed34_pages',
            'label':_LABEL, 'source_url':_START, 'declared_finite_pages':_LAST,
            'source_unit':'publisher_product_reference_not_story_series_or_edition',
            'processed_pages':_LAST, 'index_pages_complete':True, 'observed_card_occurrences':_TOTAL,
            'unique_product_references':_TOTAL, 'duplicate_reference_ids':[], 'conflicting_reference_ids':[]}
        if any(manifest.get(k) != v for k, v in expected.items()):
            raise ValueError('Overlap finite frame terminal or scope differs')
        pages = manifest['pages']
        if not isinstance(pages, list) or len(pages) != _LAST:
            raise ValueError('Overlap fixed receipt count differs')
        rows, seen_ids, seen_urls, next_url, route_path = [], set(), set(), _START, None
        for number, page in enumerate(pages, 1):
            if page['page'] != number:
                raise ValueError('Overlap receipt page order differs')
            receipt = page['source_snapshot']
            if number > 1:
                observed, observed_path = _route(next_url)
                if observed != number or observed_path != route_path:
                    raise ValueError('Overlap source chain differs')
            tree = _source(path.parent, receipt, next_url)
            parsed, next_url, route_path = _page(tree, receipt, number, route_path)
            if (page['card_count'] != len(parsed) or page['next_explicit_urls'] != ([next_url] if next_url else [])
                    or page['last_numbered_page'] != _LAST):
                raise ValueError('Overlap page checkpoint differs from raw')
            for row in parsed:
                identity, url = row['publisher_reference_id'], row['source_url']
                if identity in seen_ids or url in seen_urls:
                    raise ValueError('Overlap duplicate opaque ID or product URL')
                seen_ids.add(identity); seen_urls.add(url)
            rows.extend(parsed)
        if len(rows) != _TOTAL or next_url is not None:
            raise ValueError('Overlap total or terminal differs')
        return rows
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Overlap evidence schema cannot be reproduced') from exc
