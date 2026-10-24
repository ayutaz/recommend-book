"""Authenticate the acquired 600 references of a 627-result GC/GCN frame.

The explicit next page failed with HTTP 500. This verifier neither pretends the
frame is complete nor assigns an individual GC/GCN brand or genre from its site.
Only local pinned artifacts and bibliographic card literals are projected.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _decode, _read, _relative,
)

_START = 'https://gcnovels.jp/search?keyword=&title=&author=&isbn=&search_series=&amount=120&order=release_date_desc'
_ROLES = {'小説':'author', '著':'author', '著者':'author', 'イラスト':'illustrator',
          '原作':'original_author', '漫画':'manga_artist', '監修':'supervisor', '編':'editor'}
_SCOPE = {'index_pages_complete':False, 'pages_verified':5, 'publisher_reported_total':627,
          'references_verified':600, 'unacquired_references':27, 'unacquired_http_status':500}


def _json(root, name, digest):
    return json.loads(_read(_relative(root, name), digest, MAX_METADATA_BYTES))


def _literal(node):
    if node.tag in {'script', 'style', 'input', 'img'}:
        return ''
    if node.tag == 'br':
        return '\n'
    return ''.join(_literal(p) if isinstance(p, _Node) else p for p in node.parts)


def _only(nodes, field):
    if len(nodes) != 1:
        raise ValueError('GC card or page field ambiguous: ' + field)
    return nodes[0]


def _route(url):
    p = urlsplit(url)
    query = parse_qs(p.query, keep_blank_values=True)
    page = query.pop('page', ['1'])
    for name in ('keyword', 'title', 'author', 'isbn', 'search_series'):
        if query.pop(name, ['']) != ['']:
            raise ValueError('GC search filter is not empty')
    if (p.scheme != 'https' or p.netloc != 'gcnovels.jp' or p.path != '/search' or p.fragment
            or query != {'amount':['120'], 'order':['release_date_desc']}
            or len(page) != 1 or not re.fullmatch(r'[1-6]', page[0])):
        raise ValueError('GC source outside fixed shared search frame')
    return int(page[0])


def _source(root, receipt, url):
    statuses = [receipt[k] for k in ('status', 'http_status') if k in receipt]
    if (receipt.get('source_url') != url or not statuses or any(v != 200 for v in statuses)
            or receipt.get('final_url', url) != url or receipt.get('location') or receipt.get('error')
            or receipt.get('redirects_followed', False) or receipt.get('size_cap_exceeded')
            or receipt.get('http_redirects_followed', 0) or receipt.get('http_retry_count', 0)):
        raise ValueError('GC acquired receipt URL, status or access scope differs')
    kind = receipt.get('content_type') or ''
    if not kind.lower().startswith('text/html'):
        raise ValueError('GC acquired source is not HTML')
    name = receipt.get('raw_path')
    normalized = name.replace('\\', '/') if isinstance(name, str) else ''
    if normalized.startswith('gc-sourceframes-v1/'):
        if root.name != 'gc-sourceframes-v1':
            raise ValueError('GC raw frame root differs')
        raw_root = root.parent
    else:
        raw_root = root
    data = _read(_relative(raw_root, name), receipt.get('sha256'), MAX_SOURCE_BYTES)
    if receipt.get('byte_length') != len(data):
        raise ValueError('GC acquired receipt declared size differs')
    return _DOM(_decode(data, kind)).root


def _card(node, receipt, ordinal):
    head = _only(node.nodes(tag='h3'), 'complete title')
    link = _only(head.nodes(tag='a'), 'title link')
    title = _literal(link).strip()
    url = urljoin(receipt['source_url'], link.attrs.get('href', ''))
    identity = re.fullmatch(r'https://gcnovels\.jp/book/([0-9]+)', url)
    if not title or not identity:
        raise ValueError('GC opaque own product URL or complete title differs')
    author_list = _only(node.nodes(cls='author'), 'creator list')
    credits = []
    for li in (n for n in author_list.parts if isinstance(n, _Node) and n.tag == 'li'):
        creator = _only(li.nodes(tag='a'), 'creator name')
        name = _literal(creator).strip()
        marker = ''.join(_literal(p) if isinstance(p, _Node) and p.tag != 'a'
                         else p if isinstance(p, str) else '' for p in li.parts).strip()
        if not name:
            raise ValueError('GC creator name is empty')
        credits.append({'role':_ROLES.get(marker, 'unknown'), 'role_literal':marker, 'name':name})
    return {'publisher_reference_id':'gc-product:' + identity[1], 'official_product_id':identity[1],
        'source_unit':'publisher_product_reference', 'title':title, 'source_url':url,
        'credits':credits, 'publisher':None, 'isbn':None, 'label':None, 'label_literals':[],
        'form':None, 'volume':None, 'official_lightnovel_tag':None, 'story_series_asserted':False,
        'metadata_conflict':False, 'bibliographic_variants':[],
        'source_evidence':[{'source_url':receipt['source_url'], 'raw_sha256':receipt['sha256'],
            'locator':f'ul.book-list > li.book-list-item-root[{ordinal}] h3>a / ul.author>li'}],
        'source_frame_scope':dict(_SCOPE)}


def _page(tree, receipt, page):
    total = _only(tree.nodes(cls='paging'), 'publisher total window').text()
    if total != f'{(page - 1) * 120 + 1}~{page * 120}件（全627件）':
        raise ValueError('GC fixed total or current result window differs')
    listing = _only(tree.nodes(cls='book-list'), 'own book list')
    cards = [n for n in listing.parts if isinstance(n, _Node)
             and 'book-list-item-root' in n.attrs.get('class', '').split()]
    if len(cards) != 120:
        raise ValueError('GC acquired page card count differs')
    rows = [_card(n, receipt, i) for i, n in enumerate(cards, 1)]
    pagination = _only(tree.nodes(cls='book-pagination'), 'pagination')
    current = _only(pagination.nodes(cls='active'), 'current page')
    if current.text() != str(page) or current.nodes(tag='a'):
        raise ValueError('GC current page witness differs')
    numbers = sorted({_route(urljoin(receipt['source_url'], a.attrs.get('href', '')))
                      for a in pagination.nodes(tag='a')})
    nexts = [urljoin(receipt['source_url'], a.attrs.get('href', ''))
             for a in pagination.nodes(tag='a') if a.attrs.get('rel') == 'next']
    if len(nexts) != 1 or _route(nexts[0]) != page + 1:
        raise ValueError('GC explicit acquired-to-missing next chain differs')
    return rows, nexts[0], total, numbers


def _failure(receipt, expected_url):
    if (receipt.get('source_url') != expected_url or receipt.get('final_url', expected_url) != expected_url
            or receipt.get('http_status') != 500 or receipt.get('error') != 'HTTPError'
            or receipt.get('location') or receipt.get('redirects_followed', False)
            or receipt.get('raw_path') is not None or receipt.get('sha256') is not None
            or _route(expected_url) != 6):
        raise ValueError('GC missing final page is not the pinned HTTP500 hold')


def verify_gc_references(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Return only the acquired 5-page/600-reference partial source scope.

    Raw cards supply title and marked creator roles. A pinned private roster is
    checked against that replay; it cannot supply title, author or brand facts.
    The sixth-page HTTP500 remains a hold, not an empty or complete terminal.
    """
    path = Path(manifest_path).resolve()
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        expected = {'version':1, 'status':'partial_source_http500_hold_no_retry',
            'site_brand_scope':['GCノベルズ', 'GCN文庫'], 'per_product_brand_assignment':'unknown',
            'publisher_reported_total':627, 'finite_pages_from_total':6, 'pages_acquired':5,
            'reference_occurrences_acquired':600, 'unique_product_references_acquired':600,
            'duplicate_ids':[], 'conflicting_ids':[], 'unacquired_final_page':6,
            'unacquired_expected_references':27, 'failed_url_resend_count':0,
            'alternate_route_workaround_requests':0}
        if (any(proof.get(k) != v for k, v in expected.items())
                or proof.get('index_pages_complete') is not False):
            raise ValueError('GC partial proof scope or missing-page hold differs')
        stop = _json(path.parent, proof['stopped_frame_path'], proof['stopped_frame_sha256'])
        expected_stop = {'version':1, 'frame_id':'gc-common-search-product-index-v1',
            'status':'stopped_source_hold_no_retry', 'source_url':_START, 'fixed_reported_total':627,
            'fixed_amount':120, 'fixed_pages':6, 'processed_pages':5, 'observed_references':600, 'stopped_page':6}
        if (any(stop.get(k) != v for k, v in expected_stop.items())
                or stop.get('index_pages_complete') is not False or len(stop['pages']) != 5):
            raise ValueError('GC stopped frame does not attest the fixed acquired scope')
        rows, seen_ids, seen_urls, next_url = [], set(), set(), _START
        for number, page in enumerate(stop['pages'], 1):
            if page['page'] != number or _route(next_url) != number:
                raise ValueError('GC acquired page chain order differs')
            receipt = page['source_receipt']
            parsed, next_url, total, numbers = _page(_source(path.parent, receipt, next_url), receipt, number)
            if (page['card_count'] != 120 or page['next_url'] != next_url
                    or page['publisher_total_window'] != total or page['linked_page_numbers'] != numbers):
                raise ValueError('GC acquired checkpoint is not reproduced by raw')
            for row in parsed:
                identity, url = row['publisher_reference_id'], row['source_url']
                if identity in seen_ids or url in seen_urls:
                    raise ValueError('GC duplicate or conflicting opaque product reference')
                seen_ids.add(identity); seen_urls.add(url)
            rows.extend(parsed)
        _failure(stop['receipt'], next_url)
        _failure(proof['failed_receipt'], next_url)
        if any(stop['receipt'].get(k) != proof['failed_receipt'].get(k)
               for k in ('source_url', 'final_url', 'http_status', 'error', 'raw_path', 'sha256')):
            raise ValueError('GC partial and stopped failure receipts differ')
        roster = _json(path.parent, proof['partial_roster_path'], proof['partial_roster_sha256'])
        if not isinstance(roster, list) or len(roster) != 600 or len(rows) != 600:
            raise ValueError('GC acquired roster count differs')
        fields = ('publisher_reference_id', 'official_product_id', 'source_unit', 'source_url', 'title',
            'credits', 'publisher', 'isbn', 'label', 'label_literals', 'form', 'volume',
            'official_lightnovel_tag', 'story_series_asserted', 'metadata_conflict', 'bibliographic_variants')
        for declared, actual in zip(roster, rows):
            if any(declared.get(k) != actual[k] for k in fields):
                raise ValueError('GC roster bibliographic metadata differs from raw replay')
        return rows
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('GC partial evidence schema cannot be reproduced') from exc
