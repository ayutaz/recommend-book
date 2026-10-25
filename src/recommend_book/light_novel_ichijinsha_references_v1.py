"""Authenticate selected Iris cards without reading unrelated archive raw pages."""
from __future__ import annotations
from collections import deque
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin
from .light_novel_ichijinsha_books_v1 import ENTRY, FACET, LABELS, _Anchors, _blocks, _isbn13, _key, _text
from .light_novel_publisher_references_v1 import MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _decode, _read, _relative

SCHEMA = 'ichijinsha-iris-selected-references-v1'
_PRODUCT = re.compile(re.escape(ENTRY) + r'[^/?#]+/')
_ROLES = {'著': 'author', '著者': 'author', '絵': 'illustrator', 'イラスト': 'illustrator', '装画': 'illustrator'}


def _json(root, name, digest):
    return json.loads(_read(_relative(root, name), digest, MAX_METADATA_BYTES))


def _document(root, receipt):
    url = receipt['source_url']
    if (receipt.get('http_status') != 200 or receipt.get('final_url', url).rstrip('/') != url.rstrip('/')
            or not url.startswith(ENTRY) or receipt.get('location')
            or not receipt.get('content_type', '').lower().startswith('text/html')):
        raise ValueError('Iris selected source URL/status differs')
    raw = _read(_relative(root, receipt['raw_path']), receipt['sha256'], MAX_SOURCE_BYTES)
    if len(raw) != receipt['byte_length']:
        raise ValueError('Iris selected source byte length differs')
    return _decode(raw, receipt['content_type'])


def _literal_credits(document):
    result = []
    for block in _blocks(document, 'span', 'c-creators__creator'):
        match = re.fullmatch(r'([^：:]+)[：:]\s*(.+)', _text(block))
        if not match:
            raise ValueError('Iris card creator role is not explicit')
        literal, name = match[1].strip(), match[2].strip()
        result.append({'role': _ROLES.get(literal, 'unknown'), 'role_display': literal, 'name': name})
    return result


def _card(document, source_url):
    found = []
    for block in _blocks(document, 'article', 'c-title-card'):
        parser = _Anchors(); parser.feed(block)
        urls = {url for url in parser.hrefs if _PRODUCT.fullmatch(url)}
        if source_url not in urls:
            continue
        titles = list(_blocks(block, 'h1', 'c-title-card__title'))
        if len(urls) != 1 or len(titles) != 1:
            raise ValueError('Iris own card title/product is ambiguous')
        found.append({'title': _text(titles[0]), 'credits': _literal_credits(block)})
    if len(found) != 1:
        raise ValueError('Iris selected card missing or duplicated')
    return found[0]


def _route(pages, target):
    pending = deque([(ENTRY, [])]); seen = set()
    while pending:
        url, trail = pending.popleft()
        if url in seen or url not in pages:
            continue
        seen.add(url); trail = trail + [url]
        if url == target:
            return trail
        outgoing = set(pages[url].get('outgoing_urls', []))
        outgoing.add(pages[url]['source_snapshot'].get('final_url', url))
        pending.extend((next_url, trail) for next_url in sorted(outgoing))
    raise ValueError('Iris selected facet has no official archive route')


def verify_ichijinsha_references(manifest_path, sha256):
    path = Path(manifest_path).resolve()
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if proof.get('schema_version') != SCHEMA:
            raise ValueError('Iris selected reference schema differs')
        ids = proof['reference_ids']
        if (not isinstance(ids, list) or not ids or len(ids) > 1000
                or any(not isinstance(i, str) or not re.fullmatch(r'publisher-book:[0-9a-f]{24}', i) for i in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('Iris selected reference IDs differ')
        core_path = _relative(path.parent, proof['core_manifest_path']); root = core_path.parent
        core = _json(path.parent, proof['core_manifest_path'], proof['core_manifest_sha256'])
        if core.get('schema_version') != 'ichijinsha-iris-book-proof-v1':
            raise ValueError('Iris original core proof differs')
        roster = _json(root, core['roster_path'], core['roster_sha256'])
        metadata = _read(_relative(root, core['pages_path']), core['pages_sha256'], MAX_METADATA_BYTES)
        pages = {}
        for line in metadata.decode('utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line); url = row['source_snapshot']['source_url']
            if url in pages or (url != ENTRY and not FACET.fullmatch(url)
                    and not re.fullmatch(re.escape(ENTRY) + r'page/[0-9]+/', url)):
                raise ValueError('Iris original archive page identity differs')
            pages[url] = row
        selected_rows = [row for row in roster if row['reference_id'] in ids]
        selected = {row['reference_id']: row for row in selected_rows}
        if len(selected) != len(ids) or len(selected_rows) != len(ids):
            raise ValueError('Iris selection absent or ambiguous in original roster')
        details = {}
        if core.get('details_path'):
            for row in _json(root, core['details_path'], core['details_sha256']):
                if row['source_url'] in details:
                    raise ValueError('Iris detail identity duplicated')
                details[row['source_url']] = row
        documents = {}
        def document(url):
            if url not in documents:
                documents[url] = _document(root, pages[url]['source_snapshot'])
            return documents[url]
        result = []
        for identity in ids:
            row = selected[identity]; url = row['source_url']
            if not _PRODUCT.fullmatch(url) or identity != 'publisher-book:' + hashlib.sha256(url.encode()).hexdigest()[:24]:
                raise ValueError('Iris selected product URL identity differs')
            labels = set(); reproduced = None; evidence = []
            for claim in row['label_year_proofs']:
                receipt = claim['source_snapshot']; facet_url = receipt['source_url']; facet = FACET.fullmatch(facet_url)
                if (not facet or claim['year'] != int(facet[1]) or claim['label'] != LABELS[facet[2]]
                        or receipt != pages[facet_url]['source_snapshot']):
                    raise ValueError('Iris selected label/year receipt differs')
                route = _route(pages, facet_url)
                for index, route_url in enumerate(route):
                    parser = _Anchors(); parser.feed(document(route_url))
                    base = pages[route_url]['source_snapshot'].get('final_url', route_url)
                    links = {urljoin(base, h).split('#')[0] for h in parser.hrefs} | {base}
                    if index + 1 < len(route) and route[index + 1] not in links:
                        raise ValueError('Iris selected archive edge missing in raw')
                parser = _Anchors(); parser.feed(document(facet_url))
                if _key(claim['label']) not in {_key(value) for value in parser.alt}:
                    raise ValueError('Iris selected label brand witness missing')
                card = _card(document(facet_url), url); claimed = []
                for value in row['credits']:
                    match = re.fullmatch(r'([^：:]+)[：:]\s*(.+)', value)
                    if not match:
                        raise ValueError('Iris saved creator role missing')
                    literal = match[1].strip()
                    claimed.append({'role': _ROLES.get(literal, 'unknown'), 'role_display': literal, 'name': match[2].strip()})
                if card != {'title': row['title'], 'credits': claimed} or reproduced is not None and card != reproduced:
                    raise ValueError('Iris selected title/creator differs from raw')
                reproduced = card; labels.add(claim['label'])
                for route_url in route:
                    pin = pages[route_url]['source_snapshot']
                    item = {'source_url': route_url, 'raw_sha256': pin['sha256'], 'locator': 'official archive navigation and selected article.c-title-card h1/creator spans'}
                    if item not in evidence:
                        evidence.append(item)
            if len(labels) != 1 or reproduced is None:
                raise ValueError('Iris selected label is ambiguous')
            isbn = None
            if url in details:
                detail = details[url]; pin = detail['source_snapshot']
                if pin['source_url'] != url:
                    raise ValueError('Iris own detail URL differs')
                raw = _document(root, pin)
                titles = list(_blocks(raw, 'h1', 'c-article-header__title'))
                primary = re.split(r'<article\b[^>]*class=["\']c-title-card["\']', raw, maxsplit=1)[0]
                if len(titles) != 1 or _key(_text(titles[0])) != _key(row['title']) or _literal_credits(primary) != reproduced['credits']:
                    raise ValueError('Iris selected detail title/creator differs')
                fields = re.findall(r'<th\b[^>]*>\s*ISBN\s*</th>\s*<td\b[^>]*>(.*?)</td>', primary, re.S | re.I)
                if len(fields) > 1:
                    raise ValueError('Iris selected ISBN ambiguous')
                isbn = _isbn13(_text(fields[0])) if fields else None
                evidence.append({'source_url': url, 'raw_sha256': pin['sha256'], 'locator': 'own article header, creator spans and explicit ISBN table field'})
            label = labels.pop()
            result.append({'publisher_reference_id': identity, 'source_unit': 'publisher_product_reference',
                'source_url': url, 'title': row['title'], 'isbn': isbn, 'credits': reproduced['credits'],
                'publisher': '一迅社', 'label': label, 'label_literals': [label], 'form': None,
                'official_lightnovel_tag': None, 'story_series_asserted': False,
                'metadata_conflict': False, 'bibliographic_variants': [], 'source_evidence': evidence})
        return result
    except (KeyError, TypeError, IndexError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Iris selected bibliographic evidence cannot be reproduced') from exc
