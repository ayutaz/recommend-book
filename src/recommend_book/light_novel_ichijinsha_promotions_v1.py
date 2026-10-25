"""Bounded Iris promotion references; novel and comic forms remain unknown."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin
from .light_novel_ichijinsha_books_v1 import _isbn13, _text
from .light_novel_publisher_references_v1 import MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _decode, _read, _relative

SCHEMA = 'ichijinsha-iris-selected-promotion-references-v1'
ENTRY = 'https://www.ichijinsha.co.jp/iris/specials/'
_PROMOTION = re.compile(r'https://www\.ichijinsha\.co\.jp/iris/[A-Za-z0-9_-]+/?')
_LOCATOR = re.compile(r'book_info\[([1-9][0-9]*)\]/book_header\+book_data')


def _json(root, name, digest):
    return json.loads(_read(_relative(root, name), digest, MAX_METADATA_BYTES))


def _source(root, pin, url):
    if (pin['source_url'] != url or pin.get('http_status') != 200
            or pin.get('final_url', url).rstrip('/') != url.rstrip('/')
            or pin.get('location') or pin.get('hold')
            or not pin.get('content_type', '').lower().startswith('text/html')):
        raise ValueError('Iris promotion receipt URL/status differs')
    raw = _read(_relative(root, pin['raw_path']), pin['sha256'], MAX_SOURCE_BYTES)
    if len(raw) != pin['byte_length']:
        raise ValueError('Iris promotion source size differs')
    return _decode(raw, pin['content_type'])


def _book(document, ordinal):
    # Match the saved roster's own bibliography boundaries, never store links,
    # synopsis, images or page-level series names.
    parts = re.split(r'<div\s+class="book_info">', document)[1:]
    if ordinal > len(parts):
        raise ValueError('Iris selected promotion book ordinal is absent')
    part = parts[ordinal - 1]
    headers = re.findall(r'<div class="book_header">(.*?)</div>\s*</div>', part, re.S)
    data = re.findall(r'<div class="book_data">(.*?)</div>', part, re.S)
    if len(headers) != 1 or len(data) != 1:
        raise ValueError('Iris promotion own header/data is ambiguous')
    titles = re.findall(r'<h3\b[^>]*>(.*?)</h3>', headers[0], re.S)
    isbns = re.findall(r'ISBN\s*(97[89][0-9-]{10,16})', data[0])
    creator = re.search(r'<div>(.*?)</div>', headers[0] + '</div>', re.S)
    if len(titles) != 1 or len(isbns) != 1:
        raise ValueError('Iris promotion own title/explicit ISBN is ambiguous')
    title = _text(titles[0]); literal = _text(creator[1]) if creator else None
    if not title or literal == '':
        raise ValueError('Iris promotion title/creator literal is empty')
    return {'title': title, 'isbn': _isbn13(isbns[0]), 'creator_display': literal}


def verify_ichijinsha_promotions(manifest_path, sha256):
    path = Path(manifest_path).resolve(); root = path.parent
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if proof.get('schema_version') != SCHEMA:
            raise ValueError('Iris selected promotion proof schema differs')
        ids = proof['reference_ids']
        if (not isinstance(ids, list) or not ids or len(ids) > 1000
                or any(not isinstance(i, str) or not re.fullmatch(r'publisher-promotion-book:[0-9a-f]{24}', i) for i in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('Iris selected promotion IDs differ')
        roster = _json(root, proof['roster_path'], proof['roster_sha256'])
        metadata = _read(_relative(root, proof['sourceframes_path']), proof['sourceframes_sha256'], MAX_METADATA_BYTES)
        frames = {}
        for line in metadata.decode('utf-8').splitlines():
            if not line.strip():
                continue
            frame = json.loads(line); url = frame['source_url']
            if not _PROMOTION.fullmatch(url) or url.rstrip('/') == ENTRY.rstrip('/') or url in frames:
                raise ValueError('Iris promotion sourceframe URL is ambiguous')
            frames[url] = frame
        selected = [row for row in roster if row['reference_id'] in ids]
        indexed = {row['reference_id']: row for row in selected}
        if len(selected) != len(ids) or len(indexed) != len(ids):
            raise ValueError('Iris selected promotion is absent or duplicated')
        rootpin = None; documents = {}; result = []; rootlinks = None
        for identity in ids:
            row = indexed[identity]; url = row['source_url']; frame = frames[url]
            if (row.get('kind') != 'publisher_promotion_listed_book'
                    or row.get('novel_form') != 'unknown_may_include_comic_adaptations'
                    or row.get('story_series_identity') != 'unassessed'
                    or frame.get('series_assertion_admitted') is not False
                    or row['source_snapshot'] != frame['source_snapshot']):
                raise ValueError('Iris promotion reference-only scope differs')
            pin = frame['index_source_snapshot']
            if rootpin is None:
                rootpin = pin
                tree = _DOM(_source(root, pin, ENTRY)).root
                rootlinks = {urljoin(pin.get('final_url', ENTRY), a.attrs.get('href', '')).split('#')[0]
                    for a in tree.nodes(tag='a') if any(n.attrs.get('alt') for n in a.nodes(tag='img'))}
            elif pin != rootpin:
                raise ValueError('Iris promotion index receipt differs between selected frames')
            if url not in rootlinks:
                raise ValueError('Iris promotion is not linked by an official image banner')
            locator = _LOCATOR.fullmatch(row['locator'])
            if not locator or identity != 'publisher-promotion-book:' + hashlib.sha256((url + '#' + locator[1]).encode()).hexdigest()[:24]:
                raise ValueError('Iris promotion own ordinal identity differs')
            if url not in documents:
                documents[url] = _source(root, frame['source_snapshot'], url)
            actual = _book(documents[url], int(locator[1]))
            if actual != {k: row[k] for k in ('title', 'isbn', 'creator_display')}:
                raise ValueError('Iris promotion saved bibliographic fields differ from raw')
            # This is a whole display literal, not a resolved person's name.
            credits = ([{'role': 'unknown', 'name': actual['creator_display']}] if actual['creator_display'] else [])
            result.append({'publisher_reference_id': identity, 'source_unit': 'publisher_product_reference',
                'source_url': url, 'title': actual['title'], 'isbn': actual['isbn'], 'credits': credits,
                'publisher': None, 'label': None, 'label_literals': [], 'form': None,
                'official_lightnovel_tag': None, 'story_series_asserted': False,
                'metadata_conflict': False, 'bibliographic_variants': [],
                'source_evidence': [{'source_url': ENTRY, 'raw_sha256': rootpin['sha256'],
                    'locator': 'official specials image-banner anchor to selected promotion'},
                    {'source_url': url, 'raw_sha256': frame['source_snapshot']['sha256'], 'locator': row['locator']}]})
        return result
    except (KeyError, TypeError, IndexError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Iris selected promotion bibliographic evidence cannot be reproduced') from exc
