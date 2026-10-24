"""Reference-only publisher series assertions matched to NDL bibliography.

No publisher prose, cover, or illustration is returned to the public catalogue
or to a model. Publisher cards establish the series unit; displayable metadata
is independently reproduced from NDL-created bibliographic records.
"""
from __future__ import annotations

import hashlib
import gzip
import html
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlparse, urljoin


INDEXES = {
    'hj-bunko-series': {'publisher': 'ホビージャパン', 'label': 'HJ文庫',
                        'host': 'firecross.jp', 'path': '/hjbunko/series', 'route': r'https://firecross\.jp/hjbunko/series/[0-9]+'},
    'hj-novels-series': {'publisher': 'ホビージャパン', 'label': 'HJノベルス',
                         'host': 'firecross.jp', 'path': '/hjnovels/series', 'route': r'https://firecross\.jp/hjnovels/series/[0-9]+'},
    'arianrose-series': {'publisher': 'フロンティアワークス', 'label': 'アリアンローズ',
                        'host': 'arianrose.jp', 'path': '/series/', 'route': r'https://arianrose\.jp/novel/\?series_id=[0-9]+'},
    'ga-ebook-series': {'publisher': 'SBクリエイティブ', 'label': 'GA文庫 / GAノベル / ツギクルブックス',
                        'host': 'ebook.sbcr.jp', 'path': '/lightnovel/series', 'route': r'https://ebook\.sbcr\.jp/lightnovel/series/id/[0-9]+'},
}


def person_key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold()
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def _resolved_path(path, cache):
    key = ('resolved_path', str(path))
    if key not in cache:
        cache[key] = Path(path).resolve()
    return cache[key]


def _text(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', value))).strip()


def _decode(raw, content_type):
    if raw.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
            raw = compressed.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError('publisher decompressed source exceeds bounded size')
    charset = re.search(r'charset\s*=\s*["\']?([A-Za-z0-9_-]+)', content_type, re.I)
    if not charset:
        charset = re.search(r'charset\s*=\s*["\']?([A-Za-z0-9_-]+)', raw[:8000].decode('ascii', errors='ignore'), re.I)
    encoding = charset[1] if charset else 'utf-8-sig'
    if encoding.lower() in {'shift_jis', 'shift-jis', 'sjis', 'windows-31j'}:
        encoding = 'cp932'
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise ValueError('publisher index charset cannot be reproduced') from exc


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.current = {'href': attrs.get('href', ''), 'text': [], 'images': []}
        if self.current is not None and tag == 'img' and attrs.get('alt'):
            self.current['images'].append(attrs['alt'])

    def handle_data(self, data):
        if self.current is not None:
            self.current['text'].append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.current['text'] = ' '.join(' '.join(self.current['text']).split())
            self.links.append(self.current)
            self.current = None


def _sbcr_books(document, source_url):
    match = re.search(r'<!-- ↓↓↓↓ partial\.comic\.bookloop ↓↓↓↓ -->(.*?)<!-- ↑↑↑↑ partial\.comic\.bookloop↑↑↑↑ -->', document, re.S)
    if not match:
        return []
    parser = _Links()
    parser.feed(match[1])
    result = {}
    for link in parser.links:
        if re.fullmatch(r'/product/[A-Za-z0-9]+', link['href']):
            title = link['text'] or next(iter(link['images']), None)
            if title:
                url = urljoin(source_url, link['href'])
                result[url] = {'book_url': url, 'title': title}
    return list(result.values())


def _sbcr_fields(document):
    fields = {}
    block = re.search(r'<ul class="author clear">(.*?)</ul>', document, re.S)
    if block:
        fields['著者表示'] = [_text(v) for v in re.findall(r'<li class="mgnBottom10">(.*?)</li>', block[1], re.S)]
        label = re.search(r'<li>レーベル：(.*?)</li>', block[1], re.S)
        if label:
            fields['レーベル'] = _text(label[1])
    publication = re.search(r'出版日：(\d{4}/\d{2}/\d{2})', document)
    if publication:
        fields['出版日'] = publication[1]
    for key, value in re.findall(r'<(?:dt|th)\b[^>]*>(.*?)</(?:dt|th)>\s*<(?:dd|td)\b[^>]*>(.*?)</(?:dd|td)>', document, re.S):
        key = _text(key)
        if any(word in key for word in ['著者', 'レーベル', 'ISBN', '出版社', '発売日', 'ページ数', 'シリーズ']):
            fields[key] = _text(value)
    return fields


def _snapshot_text(receipt, root, artifact, cache, expected_url=None):
    if receipt.get('http_status') != 200 or expected_url is not None and receipt.get('source_url') != expected_url:
        raise ValueError('publisher detail receipt URL/status differs')
    path = (root / receipt['raw_path']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('publisher raw path escapes source root')
    return _decode(artifact(path, receipt['sha256'], cache), receipt.get('content_type', ''))


def _jsonld(document):
    result = []
    for block in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', document, re.S | re.I):
        value = json.loads(block)
        values = value if isinstance(value, list) else [value]
        for row in values:
            if isinstance(row, dict):
                result.extend(row.get('@graph', [row]))
    return result


def _isbn(value):
    return re.sub(r'[^0-9Xx]', '', str(value or '')).upper()


def _verify_hj_alias(proof, assertions, root, artifact, cache):
    """Prove an imprint alias by explicit same-edition ISBNs in two series."""
    import xml.etree.ElementTree as ET
    from .ingestion.core import RawArtifact
    from .ingestion.ndl import NDLAdapter, SRU
    witnesses = json.loads(artifact(proof['label_aliases_path'], proof['label_aliases_sha256'], cache))
    confirmed = set()
    for witness in witnesses:
        reference = witness['publisher_reference']['reference_id']
        assertion = assertions.get(reference)
        if not assertion or assertion['index_id'] != 'hj-novels-series':
            raise ValueError('HJ alias witness series absent from official index')
        series_text = _snapshot_text(witness['series_source_snapshot'], root, artifact, cache, assertion['source_url'])
        series = [row for row in _jsonld(series_text) if row.get('@type') == 'BookSeries' and row.get('url') == assertion['source_url']]
        if len(series) != 1 or series[0].get('name') != assertion['title']:
            raise ValueError('HJ BookSeries identity does not reproduce official index')
        members = {part.get('url'): part for part in series[0].get('hasPart', []) if part.get('@type') == 'Book'}
        raw = artifact(witness['ndl_raw_path'], witness['ndl_raw_sha256'], cache)
        xml = ET.fromstring(raw)
        nodes = xml.findall(f'.//{{{SRU}}}recordData') or [xml]
        adapter = NDLAdapter(None, approved_providers={'iss-ndl-opac'})
        source = None
        for index, node in enumerate(nodes, 1):
            if len(node) == 0 and node.text:
                node = ET.fromstring(node.text)
            identities = {v.split('#', 1)[0].rsplit('/', 1)[-1] for child in node.iter()
                          for v in child.attrib.values() if v.startswith('https://ndlsearch.ndl.go.jp/books/')}
            if witness['ndl_record_id'] in identities:
                source = adapter._normalize(node, RawArtifact(witness['ndl_raw_sha256'], witness['ndl_raw_path'], len(raw),
                    '2026-10-24T00:00:00Z', witness['ndl_source_url']), 'iss-ndl-opac', locator=f'records[{index}]/recordData').model_dump(mode='json')
                break
        if (not source or source['source_url'] != witness['ndl_source_url'] or source['series'] != witness['ndl_imprint']
                or unicodedata.normalize('NFKC', source['series']).split(';')[0].strip() != 'HJ NOVELS'):
            raise ValueError('HJ alias NDL edition/imprint not reproduced')
        # NDL's RDF title punctuation can explicitly separate a parallel title
        # or subtitle. It is never arbitrary prefix matching.
        forms = [{**title, 'value': re.split(r' (?:=|:) ', title['value'], maxsplit=1)[0].strip()}
                 for title in source['titles']]
        checked = {**source, 'titles': [*source['titles'], *forms]}
        if not match_bibliography({**assertion, 'label_aliases': ['HJ NOVELS']}, checked,
                                  lambda value: ''.join(unicodedata.normalize('NFKC', value).casefold().split()), allow_unnumbered=True):
            raise ValueError('HJ alias NDL author/publisher/title/novel checks failed')
        ndl_isbns = {_isbn(row['value']) for row in source['identifiers'] if row['source'] == 'isbn'}
        exact = False
        for check in witness['publisher_product_checks']:
            receipt = check['source_snapshot']
            url = receipt['source_url']
            if url not in members or not re.fullmatch(r'https://firecross\.jp/hjnovels/product/[0-9]+', url):
                raise ValueError('HJ alias product is not an explicit series member')
            text = _snapshot_text(receipt, root, artifact, cache, url)
            books = [row for row in _jsonld(text) if row.get('@type') == 'Book' and row.get('url') == url]
            for book in books:
                if book.get('isPartOf', {}).get('url') == assertion['source_url']:
                    exact |= _isbn(book.get('isbn')) in ndl_isbns and _isbn(members[url].get('isbn')) == _isbn(book.get('isbn'))
        if not exact:
            raise ValueError('HJ label alias lacks exact edition ISBN membership proof')
        confirmed.add(reference)
    if len(confirmed) < 2:
        raise ValueError('HJ label alias requires two independent series witnesses')
    for assertion in assertions.values():
        if assertion['index_id'] == 'hj-novels-series':
            assertion['label_aliases'] = ['HJ NOVELS']


def _cards(index_id, document):
    rows = []
    if index_id.startswith('hj-'):
        for block in re.split(r'<li class="seriesList_item">', document)[1:]:
            link = re.search(r'<a class="seriesList_itemTitle[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            authors = []
            author_block = re.search(r'<ul class="book-author-list">(.*?)</ul>', block, re.S)
            if author_block:
                for credit in re.findall(r'<li class="book-author-item">(.*?)</li>', author_block[1], re.S):
                    role = re.search(r'<span class="book-author-type">(.*?)</span>', credit, re.S)
                    name = re.search(r'class="book-author-name">(.*?)</a>', credit, re.S)
                    if role and name and _text(role[1]) in {'著', '著者', '原作'}:
                        authors.append(_text(name[1]))
            if link:
                rows.append({'source_url': html.unescape(link[1]), 'title': _text(link[2]), 'authors': authors})
    elif index_id == 'arianrose-series':
        for article in re.findall(r'<article\b[^>]*>(.*?)</article>', document, re.S):
            title = re.search(r'<h3\b[^>]*>(.*?)</h3>', article, re.S)
            link = re.search(r'href=["\'](https://arianrose.jp/novel/\?series_id=\d+)["\']', article)
            author = re.search(r'<p class="author">(.*?)</p>', article, re.S)
            authors = []
            if author:
                credit = _text(author[1])
                matched = re.search(r'(?:著|著者|原作)[:：]\s*(.*?)(?=\s+(?:イラスト|絵|漫画|キャラクター原案)[:：]|$)', credit)
                if matched and matched[1].strip():
                    authors = [matched[1].strip()]
            if title and link:
                rows.append({'source_url': link[1], 'title': _text(title[1]), 'authors': authors})
    elif index_id == 'ga-ebook-series':
        parser = _Links()
        parser.feed(document)
        for link in parser.links:
            url = urljoin('https://ebook.sbcr.jp', link['href'])
            if re.fullmatch(INDEXES[index_id]['route'], url):
                match = re.fullmatch(r'「(.*)」シリーズ\s*\((\d+)\)', link['text'])
                if match:
                    rows.append({'source_url': url, 'title': match[1], 'authors': [],
                                 'publisher_reported_edition_count': int(match[2])})
    return rows


def publisher_assertions(proof, artifact, cache):
    """Reproduce official card IDs/authors from pinned source receipts and HTML."""
    key = ('publisher_assertions', str(_resolved_path(proof['frames_path'], cache)), proof['frames_sha256'],
           str(_resolved_path(proof['source_root'], cache)), proof.get('sbcr_series_sha256'), proof.get('label_aliases_sha256'))
    if key in cache:
        return cache[key]
    frames = json.loads(artifact(proof['frames_path'], proof['frames_sha256'], cache))
    root = _resolved_path(proof['source_root'], cache)
    result = {}
    for frame in frames:
        spec = INDEXES.get(frame.get('id'))
        if spec is None:
            continue
        if frame.get('publisher') != spec['publisher'] or frame.get('label') != spec['label']:
            raise ValueError('official publisher/index label differs')
        for receipt in frame.get('pages', []):
            url = urlparse(receipt['source_url'])
            if url.scheme != 'https' or url.netloc != spec['host'] or not url.path.startswith(spec['path']):
                raise ValueError('publisher index origin/path differs')
            if not receipt.get('raw_path') or receipt.get('http_status') != 200:
                continue
            raw_path = (root / receipt['raw_path']).resolve()
            if not raw_path.is_relative_to(root):
                raise ValueError('publisher raw path escapes source root')
            document = _decode(artifact(raw_path, receipt['sha256'], cache), receipt.get('content_type', ''))
            for row in _cards(frame['id'], document):
                if not re.fullmatch(spec['route'], row['source_url']):
                    continue
                reference_id = 'publisher-series:' + hashlib.sha256(row['source_url'].encode()).hexdigest()[:24]
                current = {**row, 'reference_id': reference_id, 'publisher': spec['publisher'], 'label': spec['label'],
                           'index_id': frame['id'], 'index_source_url': receipt['source_url'], 'raw_sha256': receipt['sha256']}
                previous = result.get(reference_id)
                if previous and (previous['title'], previous['authors']) != (current['title'], current['authors']):
                    raise ValueError('publisher series assertion conflicts across index pages')
                result[reference_id] = current
    if proof.get('sbcr_series_path'):
        supplemental = json.loads(artifact(proof['sbcr_series_path'], proof['sbcr_series_sha256'], cache))
        for row in supplemental:
            assertion = result.get(row['reference_id'])
            if assertion is None or assertion['index_id'] != 'ga-ebook-series':
                raise ValueError('SB series absent from pinned official index')
            if row['source_url'] != assertion['source_url'] or row['title'] != assertion['title']:
                raise ValueError('SB series ID/title differs from pinned official index')
            series_text = _snapshot_text(row['series_source_snapshot'], root, artifact, cache, assertion['source_url'])
            books = _sbcr_books(series_text, assertion['source_url'])
            if books != row['explicit_book_memberships']:
                raise ValueError('SB explicit book membership does not reproduce source block')
            by_url = {book['book_url']: book for book in books}
            displays, labels = [], set()
            for product in row['representative_products']:
                if product['book_url'] not in by_url or product['title'] != by_url[product['book_url']]['title']:
                    raise ValueError('SB representative book is not an explicit member')
                text = _snapshot_text(product['source_snapshot'], root, artifact, cache, product['book_url'])
                fields = _sbcr_fields(text)
                if fields != product['fields']:
                    raise ValueError('SB representative bibliographic fields not reproduced')
                if fields.get('レーベル') in {'GA文庫', 'GAノベル', 'ツギクルブックス'}:
                    labels.add(fields['レーベル'])
                    displays.extend(name.strip() for group in fields.get('著者表示', []) for name in group.split(' / ') if name.strip())
            if len(labels) == 1 and displays:
                assertion.update(label=next(iter(labels)), creator_display_names=sorted(set(displays)),
                                 explicit_book_memberships=books, creator_role_basis='ndl_author_role_intersection')
    if proof.get('label_aliases_path'):
        _verify_hj_alias(proof, result, root, artifact, cache)
    cache[key] = result
    return result


def match_bibliography(assertion, source, normalize_search, *, allow_unnumbered=False):
    """Six mandatory checks; neither a title nor an ISBN proves a series."""
    if source['provider_id'] not in {'iss-ndl-opac', 'iss-ndl-opac-national'} or source['record_kind'] != 'edition' or source['language'] not in {'ja', 'jpn'}:
        return None
    candidates = [{**t, 'kind': 'reproduced_rdf_title', 'locator': 'recordData/title'} for t in source['titles']]
    candidates += source.get('_cataloguing_main_titles', [])
    titles = [t for t in candidates if normalize_search(t['value']) == normalize_search(assertion['title'])]
    if not titles or not (assertion['authors'] or assertion.get('creator_display_names')):
        return None
    if not any(v.startswith('913') for v in source.get('extra', {}).get('ndc_codes', [])):
        return None
    volume = unicodedata.normalize('NFKC', source.get('volume') or '').strip()
    number = re.match(r'^(?:第)?([0-9]+)(?:巻)?(?:$|\s|[(:])', volume)
    if volume and (not number or not 1 <= int(number[1]) <= 10000):
        return None
    if not volume and not allow_unnumbered:
        return None
    imprint = unicodedata.normalize('NFKC', source.get('series') or '').split(';')[0].strip()
    if not {normalize_search(v) for v in [assertion['label'], *assertion.get('label_aliases', [])]} & {normalize_search(v) for v in imprint.split(' = ')}:
        return None
    if normalize_search(assertion['publisher']) not in {normalize_search(v) for v in (source.get('publisher') or '').split(';')}:
        return None
    authors = []
    names = assertion['authors']
    if not names:
        # The shop display mixes authors and illustrators. Only NDL's explicit
        # author role plus an authority ID can select an actual author.
        displays = {person_key(name) for name in assertion.get('creator_display_names', [])}
        names = list(dict.fromkeys(c['name'] for c in source['contributors']
                                  if c['role'] == 'author' and person_key(c['name']) in displays))
    for name in names:
        matches = [c for c in source['contributors'] if c['role'] == 'author' and person_key(c['name']) == person_key(name)]
        confirmed = []
        for author in matches:
            ids = [i['value'] for i in author['identifiers'] if i['source'] == 'ndl-authority' and
                   re.fullmatch(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/[0-9]{6,12}', i['value'])]
            if ids:
                confirmed.append({'name': author['name'], 'authority_ids': sorted(set(ids))})
        if not confirmed:
            return None
        authors.extend(confirmed)
    if not authors:
        return None
    return {'title': titles[0]['value'], 'title_origin': {k: titles[0][k] for k in ('kind', 'locator')},
            'authors': authors, 'volume': source['volume'],
            'volume_number': int(number[1]) if number else None,
            'ndc_codes': source['extra']['ndc_codes'], 'imprint': imprint,
            'publisher': source['publisher'], 'reference_id': assertion['reference_id']}
