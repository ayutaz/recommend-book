"""Replay finite publisher references, without asserting novels, editions or series.

Only local, hash-pinned evidence is read. Output contains bibliographic literals,
not descriptions, image URLs or publisher prose. Network and database access are
deliberately absent. Unknown roles and product identities are preserved.
"""
from __future__ import annotations

import gzip
import hashlib
import html
from html.parser import HTMLParser
import io
import json
from pathlib import Path, PureWindowsPath
import re
from urllib.parse import parse_qs, urljoin, urlsplit

MAX_SOURCE_BYTES = 5_000_000
MAX_METADATA_BYTES = 64_000_000
_EARTH_PAGE = re.compile(r'https://earthstar-novel\.com/book/(?:page/([1-9][0-9]*)/)?')
_EARTH_WORK = re.compile(r'https://earthstar-novel\.com/book/detail/([A-Za-z0-9_-]+)/')
_GAGAGA_ITEM = re.compile(r'https://gagagabunko\.jp/lineup/(?:[0-9]{6}|hard|mook)\.html#[A-Za-z0-9_.-]+')
_GAGAGA_PAGES = ('https://gagagabunko.jp/lineup/index.html',
                  'https://gagagabunko.jp/lineup/indexOld.html',
                  'https://gagagabunko.jp/release/index.html',
                  'https://gagagabunko.jp/lineup/index_cover.html')
_ROLE = re.compile(r'(キャラクター原案|アニメーション製作|アニメーション制作|イラスト|原案|原作|監修|著|編|漫画|編集|文|作|写真|撮影)[／：:]')
_TO_SCOPE = {'sp[lightnovel]': ['1'], 'status[available]': ['1'], 'status[preorder]': ['1']}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('invalid expected hash')
    return value


def _read(path, expected, limit):
    _sha(expected)
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('artifact exceeds bounded size')
    if _digest(data) != expected:
        raise ValueError('artifact hash mismatch')
    return data


def _relative(root, value):
    if not isinstance(value, str) or not value or PureWindowsPath(value).drive:
        raise ValueError('artifact path must be relative')
    value = value.replace('\\', '/')
    if value.startswith('/') or '..' in value.split('/') or '\x00' in value:
        raise ValueError('artifact path escapes evidence root')
    target = (root / value).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('artifact path escapes evidence root')
    return target


def _json(root, name, expected):
    try:
        return json.loads(_read(_relative(root, name), expected, MAX_METADATA_BYTES))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('metadata JSON cannot be reproduced') from exc


def _decode(data, content_type):
    if data.startswith(b'\x1f\x8b'):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(MAX_SOURCE_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise ValueError('source gzip is invalid') from exc
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError('source exceeds bounded decompressed size')
    declared = re.search(r'charset\s*=\s*["\']?([A-Za-z0-9_-]+)', content_type, re.I)
    if declared is None:
        declared = re.search(r'charset\s*=\s*["\']?([A-Za-z0-9_-]+)', data[:8000].decode('ascii', errors='ignore'), re.I)
    encoding = declared[1] if declared else 'utf-8-sig'
    if encoding.lower() in {'shift_jis', 'shift-jis', 'sjis', 'windows-31j'}:
        encoding = 'cp932'
    try:
        text = data.decode(encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise ValueError('source charset cannot be reproduced') from exc
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    return re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>', '', text, flags=re.S | re.I)


def _to_page(url, *, pagination=False):
    if not isinstance(url, str):
        raise ValueError('TO URL absent')
    parts = urlsplit(url)
    if (parts.scheme not in ({'https', 'http'} if pagination else {'https'})
            or parts.netloc != 'www.tobooks.jp' or parts.path != '/contents' or parts.fragment):
        raise ValueError('TO source URL is outside listing route')
    query = parse_qs(parts.query, keep_blank_values=True)
    page = query.pop('page', ['1'])
    if query != _TO_SCOPE or len(page) != 1 or not re.fullmatch(r'[1-9][0-9]*', page[0]):
        raise ValueError('TO URL query scope differs')
    return int(page[0])


def _source(root, receipt, provider):
    url = receipt.get('source_url', '')
    valid = bool(_EARTH_PAGE.fullmatch(url)) if provider == 'earthstar' else url in _GAGAGA_PAGES if provider == 'gagaga' else _to_page(url) <= 100
    if not valid:
        raise ValueError('publisher source URL outside declared route')
    statuses = [receipt[k] for k in ('http_status', 'status') if k in receipt]
    if not statuses or any(status != 200 for status in statuses):
        raise ValueError('publisher receipt status is not 200')
    if receipt.get('final_url', url) != url or receipt.get('location'):
        raise ValueError('publisher final URL differs from source URL')
    content_type = receipt.get('content_type') or ''
    if not content_type.lower().startswith('text/html'):
        raise ValueError('publisher source is not HTML')
    raw_root = root
    if receipt.get('raw_path_relative_to'):
        if receipt['raw_path_relative_to'] != 'data/light-novels-v1' or root.parent.name != 'light-novels-v1':
            raise ValueError('unknown receipt evidence root')
        raw_root = root.parent
    data = _read(_relative(raw_root, receipt.get('raw_path')), receipt.get('sha256'), MAX_SOURCE_BYTES)
    if receipt.get('byte_length') != len(data):
        raise ValueError('publisher receipt declared size differs')
    return _decode(data, content_type)


class _Node:
    def __init__(self, tag='', attrs=None):
        self.tag, self.attrs, self.parts = tag, attrs or {}, []

    def nodes(self, tag=None, cls=None):
        result = []
        for child in self.parts:
            if not isinstance(child, _Node) or child.tag in {'script', 'style'}:
                continue
            if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get('class', '').split()):
                result.append(child)
            result.extend(child.nodes(tag, cls))
        return result

    def text(self):
        if self.tag in {'script', 'style'}:
            return ''
        return ' '.join(' '.join(p.text() if isinstance(p, _Node) else p for p in self.parts).split())


class _DOM(HTMLParser):
    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].parts.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        self.stack[-1].parts.append(value)


def _one(node, cls, *, required=False):
    values = node.nodes(cls=cls)
    if len(values) > 1 or (required and len(values) != 1):
        raise ValueError('bibliographic field cardinality differs: ' + cls)
    return values[0].text() if values else None


def _isbn(value):
    if not value:
        return None
    value = re.sub(r'[-\s]', '', value)
    if not re.fullmatch(r'97[89][0-9]{10}', value) or sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(value)) % 10:
        raise ValueError('publisher ISBN13 is invalid')
    return value


def _evidence(receipt, locator):
    return {'source_url': receipt['source_url'], 'raw_sha256': receipt['sha256'], 'locator': locator}


def _row(provider, identity, unit, url, title, isbn, credits, labels, form, receipt, locator, *, lightnovel=None):
    if not title or any(not c['name'] for c in credits):
        raise ValueError('bibliographic title or creator name empty')
    return {'publisher_reference_id': identity, 'source_unit': unit, 'source_url': url, 'title': title,
            'isbn': isbn, 'credits': credits,
            'publisher': {'earthstar': 'アース・スター エンターテイメント', 'tobooks': 'TOブックス', 'gagaga': '小学館'}[provider],
            'label': labels[0] if len(labels) == 1 else None, 'label_literals': labels, 'form': form,
            'official_lightnovel_tag': lightnovel, 'story_series_asserted': False,
            'metadata_conflict': False, 'bibliographic_variants': [],
            'source_evidence': [_evidence(receipt, locator)]}


def _earth(document, receipt):
    tree = _DOM(document).root
    # The brand site contains multiple imprints. Its page title does not label
    # an individual work card; this card schema provides no explicit imprint.
    labels = []
    rows = []
    for ordinal, card in enumerate(tree.nodes(cls='c-card06'), 1):
        urls = {urljoin(receipt['source_url'], a.attrs.get('href', '')) for a in card.nodes(tag='a')
                if _EARTH_WORK.fullmatch(urljoin(receipt['source_url'], a.attrs.get('href', '')))}
        if len(urls) != 1:
            raise ValueError('EarthStar work reference URL ambiguous')
        url = urls.pop()
        title = _one(card, 'c-card06__head', required=True)
        credits = []
        for item in card.nodes(cls='c-card06__info-item'):
            role, name = _one(item, 'c-card06__info-head'), _one(item, 'c-card06__info-txt')
            if role and name:
                credits.append({'role': {'著者：': 'author', 'イラスト：': 'illustrator'}.get(role, 'unknown'), 'name': name})
        markers = [n.text() for n in card.nodes(cls='c-card06__label-item')]
        forms = [v for v in markers if v in {'小説', 'ノベル', '漫画', 'コミック'}]
        rows.append(_row('earthstar', 'earthstar-work:' + _EARTH_WORK.fullmatch(url)[1], 'publisher_work_reference',
                         url, title, None, credits, labels, forms[0] if len(forms) == 1 else None, receipt,
                         f'.c-card06[{ordinal}]: title/explicit credits/markers'))
    nexts, lasts = set(), set()
    for anchor in tree.nodes(tag='a'):
        url = urljoin(receipt['source_url'], anchor.attrs.get('href', ''))
        if _EARTH_PAGE.fullmatch(url):
            if anchor.text() == '次のページ':
                nexts.add(url)
            if anchor.text() == '最後':
                lasts.add(url)
    if not rows or len(nexts) > 1:
        raise ValueError('EarthStar page empty or next link ambiguous')
    return rows, sorted(nexts), sorted(lasts)


def _to(document, receipt):
    tree = _DOM(document).root
    if not any(n.attrs.get('action') == 'https://www.tobooks.jp/contents' and n.attrs.get('method', '').lower() == 'get' for n in tree.nodes(tag='form')):
        raise ValueError('TO official HTTPS listing form missing')
    lists = [n for n in tree.nodes(tag='ul') if n.attrs.get('id') == 'search_ul']
    if len(lists) != 1:
        raise ValueError('TO product list boundary ambiguous')
    rows = []
    for ordinal, card in enumerate(lists[0].nodes(cls='search_ul_a'), 1):
        url = urljoin(receipt['source_url'], card.attrs.get('href', ''))
        match = re.fullmatch(r'https://www\.tobooks\.jp/contents/([0-9]+)', url)
        if not match:
            raise ValueError('TO product URL invalid')
        title = _one(card, 'search_ul_ttl', required=True)
        tags = [p.text() for group in card.nodes(cls='search_ul_tags') for p in group.nodes(tag='p')]
        credits = [{'role': 'unknown', 'name': n.text()} for group in card.nodes(cls='search_ul_author') for n in group.nodes(tag='p') if n.text()]
        fields = {}
        for group in card.nodes(cls='search_ul_data'):
            for paragraph in group.nodes(tag='p'):
                labels = paragraph.nodes(tag='b')
                if len(labels) == 1:
                    key = labels[0].text()
                    if key in fields:
                        raise ValueError('TO product bibliographic field duplicated')
                    fields[key] = paragraph.text()[len(key):].strip()
        labels = [v for v in tags if 'ノベルス' in v or '文庫' in v]
        rows.append(_row('tobooks', 'tobooks-product:' + match[1], 'publisher_product_reference', url, title,
                         _isbn(fields.get('ISBN：')), credits, labels, fields.get('判型：'), receipt,
                         f'ul#search_ul .search_ul_a[{ordinal}]: title/names/ISBN/tags/format', lightnovel='ライトノベル' in tags))
    pagers = tree.nodes(cls='pagination-container')
    links = [n for pager in pagers for n in pager.nodes(tag='a')]
    nexts = {n.attrs['href'] for n in links if 'next' in n.attrs.get('rel', '').split() or n.attrs.get('aria-label') == '次のページ'}
    numbers = [int(n.text()) for n in links if n.text().isdigit()]
    current = [int(n.text()) for pager in pagers for n in pager.nodes() if n.attrs.get('aria-current') == 'page' and n.text().isdigit()]
    counts = []
    for node in tree.nodes(tag='div'):
        if node.attrs.get('id') == 'search_sort':
            counts.extend(int(match[1]) for n in node.nodes(tag='p') if (match := re.fullmatch(r'検索結果\s*([0-9]+)\s*件', n.text())))
    if not rows or len(nexts) > 1 or len(counts) != 1:
        raise ValueError('TO page cards, pagination or total ambiguous')
    for url in nexts:
        _to_page(url, pagination=True)
    return rows, sorted(nexts), max(numbers + current, default=None), current, counts[0]


def _signature(row):
    return {k: row[k] for k in ('publisher_reference_id', 'source_unit', 'source_url', 'title', 'isbn', 'credits',
                               'label_literals', 'form', 'official_lightnovel_tag')}


def _stored(provider, value):
    if provider == 'earthstar':
        return {'publisher_reference_id': value['publisher_work_reference_id'], 'source_unit': value['kind'],
                'source_url': value['source_url'], 'title': value['title'], 'isbn': value.get('isbn'),
                'credits': [{'role': c['role'], 'name': c['name']} for c in value['credits']],
                'label_literals': [value['label_display']] if value.get('label_display') else [],
                'form': value.get('novel_or_comic_form') if value.get('novel_or_comic_form') != 'unknown' else None,
                'official_lightnovel_tag': None}
    return {'publisher_reference_id': value['publisher_product_reference_id'], 'source_unit': value['kind'],
            'source_url': value['source_url'], 'title': value['title'], 'isbn': value.get('isbn'),
            'credits': [{'role': c['role'], 'name': c['name']} for c in value['credits']],
            'label_literals': value.get('label_display', []), 'form': value.get('format_display'),
            'official_lightnovel_tag': value.get('official_genre_lightnovel')}


def _unique(rows):
    result = {}
    for row in rows:
        identity = row['publisher_reference_id']
        if identity in result:
            if _signature(row) != _signature(result[identity]):
                raise ValueError('publisher reference duplicate conflict')
            result[identity]['source_evidence'].extend(row['source_evidence'])
        else:
            result[identity] = {**row, 'source_evidence': list(row['source_evidence'])}
    return list(result.values())


def _count(manifest, key, actual):
    if manifest.get(key) != actual:
        raise ValueError('publisher manifest count differs: ' + key)


def _paginated(provider, manifest, root):
    schemas = {'earthstar': 'earthstar-publisher-work-index-v1', 'tobooks': 'tobooks-lightnovel-product-frame-v2'}
    units = {'earthstar': 'publisher_work_reference_not_edition_or_story_series', 'tobooks': 'publisher_product_reference_not_story_series'}
    if manifest.get('schema_version') != schemas[provider] or manifest.get('unit') != units[provider] or manifest.get('index_pages_complete') is not True:
        raise ValueError('publisher finite frame schema or terminal state differs')
    data = _read(_relative(root, manifest['pages_checkpoint_path']), manifest['pages_checkpoint_sha256'], MAX_METADATA_BYTES)
    try:
        pages = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('publisher pages JSON cannot be reproduced') from exc
    limit = 20 if provider == 'earthstar' else 100
    if not pages or len(pages) > limit:
        raise ValueError('publisher finite page bound differs')
    _count(manifest, 'retrieved_pages', len(pages))
    _count(manifest, 'expected_linked_pages' if provider == 'earthstar' else 'expected_linked_pages_at_entry', len(pages))
    rows, seen, expected = [], set(), manifest['source_url']
    totals = []
    for number, page in enumerate(pages, 1):
        receipt = page['source_snapshot']
        if receipt['source_url'] != expected or expected in seen:
            raise ValueError('publisher pagination URL chain differs or loops')
        seen.add(expected)
        document = _source(root, receipt, provider)
        if provider == 'earthstar':
            if expected != ('https://earthstar-novel.com/book/' if number == 1 else f'https://earthstar-novel.com/book/page/{number}/'):
                raise ValueError('EarthStar page route order differs')
            current, nexts, lasts = _earth(document, receipt)
            _unique(current)  # Detect conflicts before considering stored projection.
            if page.get('next_urls') != nexts or page.get('last_urls') != lasts:
                raise ValueError('EarthStar saved pagination projection differs')
            if [_stored(provider, r) for r in page.get('cards', [])] != [_signature(r) for r in current]:
                raise ValueError('EarthStar saved card projection differs from raw')
            for last in lasts:
                if int(_EARTH_PAGE.fullmatch(last)[1] or 1) != len(pages):
                    raise ValueError('EarthStar last-page witness differs')
            expected = nexts[0] if nexts else None
        else:
            if _to_page(expected) != number:
                raise ValueError('TO page route order differs')
            current, nexts, linked_max, current_page, total = _to(document, receipt)
            _unique(current)
            if (page.get('product_cards') != len(current) or page.get('next_literal_hrefs') != nexts
                    or page.get('linked_max_page') != linked_max or page.get('current_page') != current_page
                    or current_page != [number] or linked_max != len(pages)):
                raise ValueError('TO saved pagination/card projection differs')
            totals.append(total)
            expected = 'https://' + nexts[0].split('://', 1)[1] if nexts else None
        if (number < len(pages)) != bool(expected):
            raise ValueError('publisher pagination terminal differs')
        rows.extend(current)
    unique = _unique(rows)
    _count(manifest, 'observed_card_occurrences', len(rows))
    _count(manifest, 'unique_work_references' if provider == 'earthstar' else 'unique_product_references', len(unique))
    if provider == 'tobooks':
        if len(set(totals)) != 1 or totals[0] != len(unique):
            raise ValueError('TO publisher total differs from unique product references')
        _count(manifest, 'publisher_reported_total_items', totals[0])
    roster = _json(root, manifest['roster_path'], manifest['roster_sha256'])
    if [_stored(provider, value) for value in roster] != [_signature(value) for value in unique]:
        raise ValueError('publisher pinned roster projection differs from raw')
    return unique


def _text(markup):
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', markup)).split())


def _gagaga_credits(value):
    matches = list(_ROLE.finditer(value))
    return [{'role': match[1], 'name': value[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(value)].strip()}
            for index, match in enumerate(matches)]


def _gagaga_archive(document, receipt):
    rows = []
    for ordinal, match in enumerate(re.finditer(r'<li\b[^>]*>(.*?)</li>', document, re.S | re.I), 1):
        block = match[1]
        anchor = re.search(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', block, re.S | re.I)
        if not anchor:
            continue
        url = urljoin(receipt['source_url'], html.unescape(anchor[1]))
        if not _GAGAGA_ITEM.fullmatch(url):
            continue
        paragraph = re.search(r'<p\b[^>]*>(.*?)</p>', anchor[2], re.S | re.I)
        if paragraph:
            value = _text(paragraph[1])
            role = _ROLE.search(value)
            if not role:
                raise ValueError('Gagaga title/credit boundary unknown')
            title, credits = value[:role.start()].strip(), _gagaga_credits(value[role.start():])
        else:
            title, credits = _text(anchor[2]), _gagaga_credits(_text(block[anchor.end():]))
        rows.append(_row('gagaga', 'publisher-lineup:' + _digest(url.encode())[:24], 'publisher_lineup_item_reference',
                         url, title, None, credits, [], None, receipt, f'li[{ordinal}] a/p and explicit credit roles'))
    return rows


def _gagaga_release(document, receipt):
    rows = []
    for section in re.finditer(r'<section\b[^>]*id=["\']([^"\']+)["\'][^>]*>(.*?)</section>', document, re.S | re.I):
        identity, block = section.groups()
        titleblock = re.search(r'<div\b[^>]*id=["\']title["\'][^>]*>(.*?)</div>', block, re.S | re.I)
        if not titleblock:
            continue
        bib = titleblock[1]
        heading = re.search(r'<h3\b[^>]*>(.*?)</h3>', bib, re.S | re.I)
        if not heading:
            raise ValueError('Gagaga release title missing')
        labels, form = [], None

        def title_span(match):
            nonlocal form
            value = _text(match[1])
            if value in {'＜ガガガブックスf＞', '＜ガガガブックス＞'}:
                labels.append(value.strip('＜＞'))
                return ''
            if value == '＜画集＞':
                form = '画集'
                return ''
            return '' if '賞受賞作' in value else match[0]

        title = _text(re.sub(r'<span\b[^>]*>(.*?)</span>', title_span, heading[1], flags=re.S | re.I))
        matches = re.findall(r'ISBN\s*([0-9-]+)', bib)
        if len(matches) > 1:
            raise ValueError('Gagaga release ISBN ambiguous')
        isbn = _isbn(matches[0]) if matches else None
        creditspans = [_text(m[1]) for m in re.finditer(r'<span\b[^>]*class=["\']textsize14["\'][^>]*>(.*?)</span>', bib, re.S | re.I) if _ROLE.search(_text(m[1]))]
        url = receipt['source_url'] + '#' + identity
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', identity):
            raise ValueError('Gagaga release item locator invalid')
        rows.append(_row('gagaga', 'publisher-lineup-snapshot:' + _digest((url + '\n' + receipt['sha256']).encode())[:24],
                         'publisher_lineup_item_reference', url, title, isbn, _gagaga_credits(' '.join(creditspans)),
                         labels, form, receipt, f'section#{identity} div#title h3/span.textsize14/ISBN'))
    return rows


def _gagaga(manifest, root):
    if (manifest.get('version') != 1 or manifest.get('frame_id') != 'gagaga-official-lineup-indexes-v1'
            or manifest.get('unit') != 'publisher_lineup_item_reference_not_story_series_or_edition'
            or manifest.get('finite_index_complete') is not True):
        raise ValueError('Gagaga finite frame schema or terminal differs')
    receipts = manifest.get('source_pages', [])
    if [r.get('source_url') for r in receipts] != list(_GAGAGA_PAGES):
        raise ValueError('Gagaga source page URL set differs')
    documents = [_source(root, r, 'gagaga') for r in receipts]
    current = _gagaga_archive(documents[0], receipts[0])
    old = _gagaga_archive(documents[1], receipts[1])
    release = _gagaga_release(documents[2], receipts[2])
    rows = current + old + release
    old_urls = {row['source_url'] for row in old}
    cover_links = {}
    for anchor in _DOM(documents[3]).root.nodes(tag='a'):
        url = urljoin(receipts[3]['source_url'], anchor.attrs.get('href', ''))
        if _GAGAGA_ITEM.fullmatch(url):
            cover_links.setdefault(url, []).extend(n.attrs['alt'] for n in anchor.nodes(tag='img') if n.attrs.get('alt'))
    for url in sorted(set(cover_links) - old_urls):
        alts = cover_links[url]
        if len(alts) != 1:
            raise ValueError('Gagaga cover-only title literal ambiguous')
        rows.append(_row('gagaga', 'publisher-lineup:' + _digest(url.encode())[:24], 'publisher_lineup_item_reference',
                         url, _text(alts[0]), None, [], [], None, receipts[3], 'official cover-index link img@alt bibliographic literal'))
    unique = {}
    for row in rows:
        prior = unique.get(row['source_url'])
        if prior is None:
            unique[row['source_url']] = row
            continue
        prior['source_evidence'].extend(row['source_evidence'])
        if any(prior[k] != row[k] for k in ('title', 'isbn', 'credits', 'label_literals', 'form')):
            prior['metadata_conflict'] = True
            prior['bibliographic_variants'].append({k: row[k] for k in ('title', 'isbn', 'credits', 'label_literals', 'form', 'source_evidence')})
    _count(manifest, 'unique_item_references', len(unique))
    _count(manifest, 'all_rows_before_url_dedup', len(rows))
    _count(manifest, 'current_release_items', len(release))
    _count(manifest, 'current_release_isbn13', sum(bool(row['isbn']) for row in release))
    _count(manifest, 'unique_url_metadata_variants', sum(row['metadata_conflict'] for row in unique.values()))
    return list(unique.values())


def verify_publisher_references(provider, manifest_path, sha256):
    """Return minimum bibliographic references from hash-pinned local evidence.

    Providers are ``earthstar``, ``tobooks``, ``gagaga``, ``kodansha``,
    ``hifumi``, ``overlap`` and ``futaba``. No result asserts
    edition identity or story-series identity. Gagaga's unpinned private roster
    is never read; its manifest and raw sources are the entire replay contract.
    """
    if provider == 'kodansha':
        from .light_novel_kodansha_references_v1 import verify_kodansha_references
        return verify_kodansha_references(manifest_path, sha256)
    if provider == 'hifumi':
        from .light_novel_hifumi_references_v1 import verify_hifumi_references
        return verify_hifumi_references(manifest_path, sha256)
    if provider == 'overlap':
        from .light_novel_overlap_references_v1 import verify_overlap_references
        return verify_overlap_references(manifest_path, sha256)
    if provider == 'futaba':
        from .light_novel_futaba_references_v1 import verify_futaba_references
        return verify_futaba_references(manifest_path, sha256)
    if provider not in {'earthstar', 'tobooks', 'gagaga'}:
        raise ValueError('unsupported publisher reference provider')
    path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if not isinstance(manifest, dict):
            raise ValueError('publisher manifest must be an object')
        return _gagaga(manifest, path.parent) if provider == 'gagaga' else _paginated(provider, manifest, path.parent)
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('publisher reference evidence schema cannot be reproduced') from exc
