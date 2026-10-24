"""Replay eight official Cobalt series references and bounded ebook groups.

This verifies publisher references, not original-work or edition identity. The
other e-Cobalt headings are outside this verifier's scope. A page brand never
supplies an individual product's publisher or imprint.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urljoin

from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _decode, _read, _relative,
)


_IDS = frozenset(('52807', '52812', '47089', '52670', '52520', '52973', '69979', '52881'))
_SOURCES = {
    'cobalt_anniversary': 'https://orangebunko.shueisha.co.jp/feature/cobalt50th_specialbooks',
    'ebook_author_list': 'https://ebooks.shueisha.co.jp/cobalt/',
}
_SERIES = re.compile(r'https://www\.shueisha\.co\.jp/books/search/search\.html\?seriesid=([0-9]+)')
_BOOK = re.compile(r'https://ebooks\.shueisha\.co\.jp/detail\.php\?book=([A-Za-z0-9]+)')
_START = re.compile(r'dl#([^\s#]+) dd:nth-of-type\(([1-9][0-9]*)\) span em')
_ROLE_SELECTOR = 'h2 img[alt="著者50音別書籍リスト"]'
_INCLUSION = 'explicit_series_heading_followed_by_book_dd_links_inside_same_official_author_list_group'


def _children(node, tag):
    return [p for p in node.parts if isinstance(p, _Node) and p.tag == tag]


def _literal(node):
    """Preserve full title typography, including internal ideographic spaces."""
    if node.tag in {'script', 'style'}:
        return ''
    return ''.join(_literal(p) if isinstance(p, _Node) else p for p in node.parts)


def _only(nodes, field):
    if len(nodes) != 1:
        raise ValueError('Cobalt field cardinality differs: ' + field)
    return nodes[0]


def _source(root, evidence, source_key):
    if evidence.get('source_url') != _SOURCES[source_key]:
        raise ValueError('Cobalt source URL outside fixed scope')
    data = _read(_relative(root, evidence.get('raw_path')), evidence.get('source_sha256'), MAX_SOURCE_BYTES)
    return _DOM(_decode(data, '')).root


def _anniversary(tree):
    listing = _only(tree.nodes(tag='ul', cls='anthology-list'), 'anniversary list')
    items = _children(listing, 'li')
    if len(items) != 8:
        raise ValueError('Cobalt fixed reference count differs')
    rows = {}
    for ordinal, item in enumerate(items, 1):
        heading = _only(item.nodes(tag='p', cls='anthology-title'), 'anniversary heading')
        span = _only(heading.nodes(tag='span'), 'anniversary series literal')
        match = re.fullmatch(r'●『(.+)』シリーズ', span.text())
        if not match:
            raise ValueError('Cobalt explicit series heading missing')
        before = []
        for part in heading.parts:
            if part is span:
                break
            before.append(_literal(part) if isinstance(part, _Node) else part)
        author = ' '.join(''.join(before).split())
        links = []
        for anchor in item.nodes(tag='a'):
            url = urljoin(_SOURCES['cobalt_anniversary'], anchor.attrs.get('href', ''))
            identity = _SERIES.fullmatch(url)
            if identity:
                if anchor.text() != 'シリーズ本編購入はこちら':
                    raise ValueError('Cobalt independent series link role differs')
                links.append((identity[1], url))
        identity, url = _only(links, 'independent official series URL')
        if identity not in _IDS or identity in rows or not author:
            raise ValueError('Cobalt official series ID outside fixed scope')
        rows[identity] = {'title': match[1], 'author': author, 'url': url, 'ordinal': ordinal}
    if set(rows) != _IDS:
        raise ValueError('Cobalt official series ID set differs')
    return rows


def _ebook_group(tree, declared, title, author):
    pointer = declared.get('selector', '')
    start = _START.fullmatch(pointer)
    if not start:
        raise ValueError('Cobalt group start selector differs')
    dl_id, position = start[1], int(start[2])
    dl = _only([n for n in tree.nodes(tag='dl') if n.attrs.get('id') == dl_id], 'ebook kana list')
    children = _children(dl, 'dd')
    if position > len(children):
        raise ValueError('Cobalt group start outside kana list')
    heading = children[position - 1]
    if heading.attrs.get('id') or heading.nodes(tag='a'):
        raise ValueError('unexpected ebook heading identity')
    name = _only(_children(heading, 'b'), 'ebook heading author').text()
    em = _only(heading.nodes(tag='em'), 'ebook series heading')
    if name != author or em.text() != f'＜{title}シリーズ＞':
        raise ValueError('Cobalt ebook heading title or author differs')
    books = []
    boundary = None
    for number, child in enumerate(children[position:], position + 1):
        locator = f'dl#{dl_id} dd:nth-of-type({number})'
        if child.nodes(tag='em'):
            boundary = {'selector': locator, 'reason': 'next explicit publisher series heading'}
            break
        anchor = _only(_children(child, 'a'), 'ebook member link')
        book_author = _only(_children(anchor, 'b'), 'ebook member author').text()
        if book_author != author:
            boundary = {'selector': locator, 'reason': 'next explicit author field value'}
            break
        span = _only(_children(anchor, 'span'), 'ebook complete book title')
        url = urljoin(_SOURCES['ebook_author_list'], anchor.attrs.get('href', ''))
        identity = _BOOK.fullmatch(url)
        book_title = _literal(span).strip()
        if not identity or not book_title:
            raise ValueError('Cobalt member URL or complete title differs')
        books.append({'title': book_title, 'source_url': url, 'publisher_book_id': identity[1],
            'author_display_text': _literal(_only(_children(anchor, 'b'), 'ebook author')).strip(),
            'isbn': None, 'source_selector': locator + ' a > b / span'})
    if boundary is None:
        boundary = {'selector': f'dl#{dl_id}', 'reason': 'end of explicit kana dl'}
    expected = {'source_key': 'ebook_author_list', 'selector': pointer,
        'heading_text': title + 'シリーズ', 'independent_group_id': None, 'group_end': boundary,
        'book_inclusion_basis': _INCLUSION, 'book_count': len(books)}
    if not books or declared != expected:
        raise ValueError('Cobalt bounded group projection differs')
    return books, expected, f'dl#{dl_id} dd:nth-of-type({position}) > b'


def verify_cobalt_series(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Verify a pinned eight-reference projection using exactly its two raw files."""
    path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Cobalt manifest JSON cannot be reproduced') from exc
    if (manifest.get('version') != 1 or manifest.get('frame_id') != 'cobalt-official-series-projection-v1'
            or manifest.get('finite_candidate_refs') != 8 or manifest.get('established_refs') != 8
            or manifest.get('official_id_scope_complete') is not True or manifest.get('holds')):
        raise ValueError('Cobalt finite projection scope differs')
    sources = manifest.get('sources', {})
    if set(sources) != set(_SOURCES):
        raise ValueError('Cobalt two-source scope differs')
    anniversary = _source(path.parent, sources['cobalt_anniversary'], 'cobalt_anniversary')
    ebook = _source(path.parent, sources['ebook_author_list'], 'ebook_author_list')
    observed = _anniversary(anniversary)
    role_labels = [img for h in ebook.nodes(tag='h2') for img in h.nodes(tag='img')
                   if img.attrs.get('alt') == '著者50音別書籍リスト']
    if len(role_labels) != 1:
        raise ValueError('Cobalt explicit author role label missing or ambiguous')
    projection = manifest.get('series', [])
    if len(projection) != 8 or {r.get('official_series_id') for r in projection} != _IDS:
        raise ValueError('Cobalt declared official series ID scope differs')
    result = []
    for declared in projection:
        identity = declared['official_series_id']
        raw = observed[identity]
        if (declared.get('kind') != 'official_series_reference'
                or declared.get('official_series_url') != raw['url'] or declared.get('series_title') != raw['title']):
            raise ValueError('Cobalt official series projection differs')
        independent = {'source_key': 'cobalt_anniversary',
            'selector': f'ul.anthology-list li:nth-child({raw["ordinal"]}) p.anthology-title; a[href="{raw["url"]}"]',
            'label_text': 'シリーズ本編購入はこちら'}
        if declared.get('independent_series_evidence') != independent:
            raise ValueError('Cobalt official series evidence locator differs')
        books, group, author_field = _ebook_group(ebook, declared.get('ebook_group_evidence', {}), raw['title'], raw['author'])
        author = {'role': '著者', 'display_text': raw['author'], 'role_source': 'ebook_author_list',
            'role_label_selector': _ROLE_SELECTOR, 'author_field_selector': author_field}
        if declared.get('series_author') != author or declared.get('books') != books:
            raise ValueError('Cobalt author role or complete member projection differs')
        result.append({'kind': 'official_series_reference', 'official_series_id': identity,
            'official_series_url': raw['url'], 'series_title': raw['title'], 'series_author': author,
            'publisher': None, 'label': None, 'independent_series_evidence': independent,
            'ebook_group_evidence': group, 'books': books,
            'source_evidence': [
                {'source_url': sources[key]['source_url'], 'raw_sha256': sources[key]['source_sha256'],
                 'locator': independent['selector'] if key == 'cobalt_anniversary' else group['selector']}
                for key in _SOURCES],
            'catalogue_identity': 'unassessed_no_automatic_original_or_edition_identity'})
    books = [b for row in result for b in row['books']]
    if (len(books) != manifest.get('book_link_references')
            or len({b['source_url'] for b in books}) != manifest.get('unique_book_urls')):
        raise ValueError('Cobalt complete member counts differ')
    return result
