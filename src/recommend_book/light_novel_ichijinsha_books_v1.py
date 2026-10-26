"""Replay publisher Iris book cards; no story-series identity is inferred.

This module authenticates reference-only publisher evidence. A caller must
independently match NDL-created edition metadata before admitting any book.
"""
from __future__ import annotations

import gzip
import hashlib
import html
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urljoin

ENTRY = 'https://www.ichijinsha.co.jp/iris/title/'
FACET = re.compile(re.escape(ENTRY) + r'(\d{4})/(iris|irisneo)/?(?:page/\d+/)?')
LABELS = {'iris': '一迅社文庫アイリス', 'irisneo': 'アイリスNEO'}


def _artifact(root, relative_path, sha256):
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Iris evidence path escapes source root')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError('Iris evidence hash mismatch')
    return raw


def _text(value):
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', value)).split())


def _key(value):
    return ''.join(unicodedata.normalize('NFKC', value).split())


def _decode(raw, content_type):
    if raw.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(5_000_001)
    if len(raw) > 5_000_000:
        raise ValueError('Iris source exceeds bounded size')
    charset = re.search(r'charset\s*=\s*["\']?([A-Za-z0-9_-]+)', content_type, re.I)
    encoding = charset[1] if charset else 'utf-8-sig'
    if encoding.lower() in {'shift_jis', 'shift-jis', 'sjis', 'windows-31j'}:
        encoding = 'cp932'
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise ValueError('Iris source charset cannot be reproduced') from exc


def _document(receipt, root):
    url = receipt.get('source_url', '')
    final = receipt.get('final_url', url)
    if (receipt.get('http_status') != 200 or not url.startswith(ENTRY)
            or final.rstrip('/') != url.rstrip('/')):
        raise ValueError('Iris receipt URL/status differs')
    document = _decode(_artifact(root, receipt['raw_path'], receipt['sha256']), receipt.get('content_type', ''))
    document = re.sub(r'<!--.*?-->', '', document, flags=re.S)
    return re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', document, flags=re.S | re.I)


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []
        self.alt = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'a' and values.get('href'):
            self.hrefs.append(values['href'])
        if tag == 'img' and values.get('alt'):
            self.alt.append(values['alt'])


def _blocks(document, tag, class_name):
    for match in re.finditer(r'<' + tag + r'\b([^>]*)>(.*?)</' + tag + r'>', document, re.S | re.I):
        classes = re.search(r'\bclass\s*=\s*(["\'])(.*?)\1', match[1], re.S | re.I)
        if classes and class_name in classes[2].split():
            yield match[2]


def _credits(document):
    result = []
    roles = {'著': 'author', '著者': 'author', '絵': 'illustrator', 'イラスト': 'illustrator', '装画': 'illustrator'}
    for block in _blocks(document, 'span', 'c-creators__creator'):
        value = _text(block)
        match = re.fullmatch(r'([^：:]+)[：:]\s*(.+)', value)
        if match and match[1].strip() in roles:
            result.append({'role': roles[match[1].strip()], 'name': match[2].strip()})
    return result


def _cards(document):
    result = {}
    for article in _blocks(document, 'article', 'c-title-card'):
        titles = list(_blocks(article, 'h1', 'c-title-card__title'))
        anchors = _Anchors()
        anchors.feed(article)
        urls = {url for url in anchors.hrefs if re.fullmatch(re.escape(ENTRY) + r'[^/?#]+/', url)}
        if len(titles) == 1 and len(urls) == 1:
            url = urls.pop()
            result[url] = {'title': _text(titles[0]), 'credits': _credits(article)}
    return result


def _isbn13(value):
    value = re.sub(r'[\s-]', '', value)
    if not re.fullmatch(r'97[89]\d{10}', value):
        raise ValueError('Iris ISBN is not an ISBN13')
    total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(value))
    if total % 10:
        raise ValueError('Iris ISBN check digit differs')
    return value


def verify_ichijinsha_books(manifest_path, sha256):
    """Return hash-verified edition assertions, retaining unknown ISBN as None.

    No DB writes or network reads occur. The manifest and every referenced raw
    artifact are revalidated on each call, including the actual archive links.
    """
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = json.loads(_artifact(root, manifest_path.name, sha256))
    if manifest.get('schema_version') != 'ichijinsha-iris-book-proof-v1':
        raise ValueError('Iris proof schema differs')
    roster = json.loads(_artifact(root, manifest['roster_path'], manifest['roster_sha256']))
    pages = [json.loads(line) for line in _artifact(root, manifest['pages_path'], manifest['pages_sha256']).decode('utf-8').splitlines() if line.strip()]
    documents = {}
    receipts = {}
    outgoing = {}
    cards = {}
    brands = {}
    for page in pages:
        receipt = page['source_snapshot']
        url = receipt['source_url']
        if url != ENTRY and not FACET.fullmatch(url) and not re.fullmatch(re.escape(ENTRY) + r'page/\d+/', url):
            raise ValueError('Iris archive route differs')
        document = _document(receipt, root)
        documents[url] = document
        receipts[url] = receipt
        parser = _Anchors()
        parser.feed(document)
        brands[url] = {_key(alt) for alt in parser.alt}
        outgoing[url] = {urljoin(receipt.get('final_url', url), href).split('#')[0] for href in parser.hrefs}
        outgoing[url].add(receipt.get('final_url', url))
        cards[url] = _cards(document)
    reachable = set()
    pending = [ENTRY]
    while pending:
        url = pending.pop()
        if url in reachable or url not in documents:
            continue
        reachable.add(url)
        pending.extend(outgoing[url] - reachable)
    details = {}
    if manifest.get('details_path'):
        for row in json.loads(_artifact(root, manifest['details_path'], manifest['details_sha256'])):
            receipt = row['source_snapshot']
            if receipt['source_url'] != row['source_url'] or row['source_url'] in details:
                raise ValueError('Iris detail identity differs')
            details[row['source_url']] = _document(receipt, root)
    result = []
    seen = set()
    for row in roster:
        url = row['source_url']
        expected_id = 'publisher-book:' + hashlib.sha256(url.encode()).hexdigest()[:24]
        if row['reference_id'] != expected_id or expected_id in seen:
            raise ValueError('Iris book identity differs')
        seen.add(expected_id)
        labels = set()
        reproduced = None
        for proof in row['label_year_proofs']:
            receipt = proof['source_snapshot']
            source_url = receipt['source_url']
            if source_url not in reachable:
                raise ValueError('Iris facet is not reachable from official archive: ' + source_url)
            if receipt != receipts[source_url]:
                raise ValueError('Iris proof receipt differs')
            facet = FACET.fullmatch(source_url)
            if not facet or proof['year'] != int(facet[1]) or proof['label'] != LABELS[facet[2]]:
                raise ValueError('Iris label/year proof differs')
            if _key(proof['label']) not in brands[source_url]:
                raise ValueError('Iris brand witness missing')
            reproduced = cards[source_url].get(url)
            claimed_credits = []
            for credit in row['credits']:
                match = re.fullmatch(r'([^：:]+)[：:]\s*(.+)', credit)
                role = {'著': 'author', '著者': 'author', '絵': 'illustrator', 'イラスト': 'illustrator', '装画': 'illustrator'}.get(match[1].strip()) if match else None
                if role:
                    claimed_credits.append({'role': role, 'name': match[2].strip()})
            if not reproduced or reproduced['title'] != row['title'] or reproduced['credits'] != claimed_credits:
                raise ValueError('Iris book card does not reproduce assertion')
            labels.add(proof['label'])
        if len(labels) != 1 or reproduced is None:
            raise ValueError('Iris book has ambiguous or missing label proof')
        isbn = None
        if url in details:
            document = details[url]
            titles = list(_blocks(document, 'h1', 'c-article-header__title'))
            # Creator cards below the related-books heading describe other works.
            primary = re.split(r'<article\b[^>]*class=["\']c-title-card["\']', document, maxsplit=1)[0]
            if len(titles) != 1 or _key(_text(titles[0])) != _key(reproduced['title']) or _credits(primary) != reproduced['credits']:
                raise ValueError('Iris detail title/creator differs from listed book')
            values = re.findall(r'<th\b[^>]*>\s*ISBN\s*</th>\s*<td\b[^>]*>(.*?)</td>', primary, re.S | re.I)
            if len(values) == 1:
                isbn = _isbn13(_text(values[0]))
            elif values:
                raise ValueError('Iris detail ISBN is ambiguous')
        result.append({'publisher_book_id': expected_id, 'source_url': url,
                       'title': reproduced['title'], 'isbn': isbn, 'credits': reproduced['credits'],
                       'publisher': '一迅社', 'label': labels.pop()})
    return result
