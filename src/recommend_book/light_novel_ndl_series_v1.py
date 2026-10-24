"""Reference-only publisher series assertions matched to NDL bibliography.

No publisher prose, cover, or illustration is returned to the public catalogue
or to a model. Publisher cards establish the series unit; displayable metadata
is independently reproduced from NDL-created bibliographic records.
"""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlparse


INDEXES = {
    'hj-bunko-series': {'publisher': 'ホビージャパン', 'label': 'HJ文庫',
                        'host': 'firecross.jp', 'path': '/hjbunko/series', 'route': r'https://firecross\.jp/hjbunko/series/[0-9]+'},
    'hj-novels-series': {'publisher': 'ホビージャパン', 'label': 'HJノベルス',
                         'host': 'firecross.jp', 'path': '/hjnovels/series', 'route': r'https://firecross\.jp/hjnovels/series/[0-9]+'},
    'arianrose-series': {'publisher': 'フロンティアワークス', 'label': 'アリアンローズ',
                        'host': 'arianrose.jp', 'path': '/series/', 'route': r'https://arianrose\.jp/novel/\?series_id=[0-9]+'},
}


def person_key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold()
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def _text(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', value))).strip()


def _decode(raw, content_type):
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
    return rows


def publisher_assertions(proof, artifact, cache):
    """Reproduce official card IDs/authors from pinned source receipts and HTML."""
    key = ('publisher_assertions', str(Path(proof['frames_path']).resolve()), proof['frames_sha256'], str(Path(proof['source_root']).resolve()))
    if key in cache:
        return cache[key]
    frames = json.loads(artifact(proof['frames_path'], proof['frames_sha256'], cache))
    root = Path(proof['source_root']).resolve()
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
    cache[key] = result
    return result


def match_bibliography(assertion, source, normalize_search, *, allow_unnumbered=False):
    """Six mandatory checks; neither a title nor an ISBN proves a series."""
    if source['provider_id'] != 'iss-ndl-opac' or source['record_kind'] != 'edition' or source['language'] not in {'ja', 'jpn'}:
        return None
    titles = [t['value'] for t in source['titles'] if normalize_search(t['value']) == normalize_search(assertion['title'])]
    if not titles or not assertion['authors']:
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
    if normalize_search(assertion['label']) not in {normalize_search(v) for v in imprint.split(' = ')}:
        return None
    if normalize_search(assertion['publisher']) not in {normalize_search(v) for v in (source.get('publisher') or '').split(';')}:
        return None
    authors = []
    for name in assertion['authors']:
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
    return {'title': titles[0], 'authors': authors, 'volume': source['volume'],
            'volume_number': int(number[1]) if number else None,
            'ndc_codes': source['extra']['ndc_codes'], 'imprint': imprint,
            'publisher': source['publisher'], 'reference_id': assertion['reference_id']}
