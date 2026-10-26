"""Reproduce pinned DX catalogue book metadata without admitting publisher prose.

These are books, never inferred story-series or original works. Downstream
matching decides whether an NDL-created record and the explicit imprint qualify.
"""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import urljoin


_INDEX = re.compile(r'https://dash\.shueisha\.co\.jp/library/index/(?:[1-9][0-9]*/)?')
_BOOK = re.compile(r'https://dash\.shueisha\.co\.jp/bookDetail/index/([0-9Xx-]+)')


def _text(value: str) -> str:
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', value)).split())


def _isbn(value: str) -> str | None:
    digits = value.replace('-', '')
    if not re.fullmatch(r'97[89][0-9]{10}', digits):
        return None
    if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits)) % 10:
        return None
    return digits


def _local_file(root: Path, relative: str) -> Path:
    # Portable separators preserve Windows acquisition receipts on Linux CI.
    relative = relative.replace('\\', '/')
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise ValueError('publisher raw path escapes manifest directory')
    return path


def _parse(source: dict, document: str) -> tuple[list[dict], list[str], int]:
    source_url = source['source_url']
    total = re.search(r'検索結果[：:]\s*([\d,]+)件', document)
    if not total:
        raise ValueError('publisher reported count missing')
    rows = []
    blocks = re.findall(r'<section class="result_box">(.*?)</section>', document, re.S)
    if not blocks:
        raise ValueError('publisher book card scope missing')
    for index, block in enumerate(blocks, 1):
        title = re.search(r'<h4><a href="([^"]+)">(.*?)</a></h4>', block, re.S)
        if not title:
            raise ValueError('publisher book title missing')
        url = urljoin(source_url, title[1])
        book_id = _BOOK.fullmatch(url)
        if not book_id:
            raise ValueError('unexpected publisher book URL')
        credits = []
        for credit in re.findall(r'<span class="boldText">(.*?)</span>', block, re.S):
            value = _text(credit)
            if '：' in value:
                role, name = value.split('：', 1)
                credits.append({'role': role, 'name': name})
        label = re.search(r'<span class="boldText">レーベル</span>[：:]\s*([^<\r\n]+)', block)
        date = re.search(r'<span class="boldText">発売日</span>[：:]\s*([^<\r\n]+)', block)
        if not label or not _text(title[2]):
            raise ValueError('publisher bibliographic fields missing')
        rows.append({
            'kind': 'book', 'source_url': url, 'publisher_book_id': book_id[1],
            'isbn_as_publisher_url': book_id[1], 'isbn': _isbn(book_id[1]),
            'title': _text(title[2]), 'publisher': '集英社', 'label': _text(label[1]),
            'credits': credits, 'published_on': _text(date[1]) if date else None,
            'story_series_identity': 'unassessed_no_title_stripping',
            'source_evidence': {
                'source_url': source_url, 'source_sha256': source['sha256'],
                'raw_path': source['raw_path'], 'retrieved_at': source['retrieved_at'],
                'selector': f'section.result_box:nth-of-type({index}) h4 / span.boldText',
            },
        })
    next_pages = []
    for href, value in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', document, re.S):
        if _text(value) == '次の20件':
            next_pages.append(urljoin(source_url, html.unescape(href)))
    return rows, list(dict.fromkeys(next_pages)), int(total[1].replace(',', ''))


def verify_shueisha_books(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Verify a complete finite frame and return metadata parsed from pinned raw.

    The exported roster is intentionally ignored: title, role, label and ISBN
    must be reproducible from each source. A one-card overlap between published
    result pages is deduplicated only when the bibliographic fields agree.
    """
    path = Path(manifest_path).resolve()
    manifest_bytes = path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != sha256:
        raise ValueError('publisher manifest hash mismatch')
    manifest = json.loads(manifest_bytes)
    if manifest.get('frame_id') != 'shueisha-dash-library-all-v1' or manifest.get('complete') is not True:
        raise ValueError('publisher finite frame incomplete')
    pages = manifest.get('visited_pages', [])
    sources = manifest.get('sources', [])
    if not 1 <= len(pages) <= 100 or len(pages) != len(set(pages)) or len(sources) != len(pages):
        raise ValueError('publisher pagination scope mismatch')
    if pages[0] != 'https://dash.shueisha.co.jp/library/index/':
        raise ValueError('publisher pagination start mismatch')
    by_url = {}
    for position, (page, source) in enumerate(zip(pages, sources, strict=True)):
        if not _INDEX.fullmatch(page) or source.get('source_url') != page or source.get('status') != 200:
            raise ValueError('publisher source scope mismatch')
        raw_path = _local_file(path.parent, source['raw_path'])
        raw = raw_path.read_bytes()
        if len(raw) > 5_000_000 or hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise ValueError('publisher source hash mismatch')
        rows, next_pages, total = _parse(source, raw.decode('utf-8-sig'))
        expected_next = [pages[position + 1]] if position + 1 < len(pages) else []
        if next_pages != expected_next:
            raise ValueError('publisher pagination not reproduced')
        if total != manifest.get('publisher_reported_books'):
            raise ValueError('publisher count changed between pages')
        for row in rows:
            old = by_url.get(row['source_url'])
            if old is not None:
                fields = ('title', 'isbn', 'credits', 'label', 'published_on')
                if any(old[field] != row[field] for field in fields):
                    raise ValueError('publisher overlapping card metadata conflict')
            else:
                by_url[row['source_url']] = row
    if len(by_url) != manifest.get('publisher_reported_books'):
        raise ValueError('publisher unique book count mismatch')
    return list(by_url.values())
