"""Replay the bounded official Orange taxonomy as publisher listing groups.

Only pinned sitemap, transport receipts and bibliographic DOM fields are read.
Legacy ``series`` counts/credit unions never assert story identity or group
authorship. URL identifiers are opaque and never supply an ISBN or format.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _decode, _read, _relative,
)


_ORIGIN = 'https://orangebunko.shueisha.co.jp'
_SITEMAP = _ORIGIN + '/series-sitemap.xml'
_PARENT = re.compile(r'https://orangebunko\.shueisha\.co\.jp/book/series/([^/?#]+)/?')
_BOOK = re.compile(r'https://orangebunko\.shueisha\.co\.jp/book/([^/?#]+)')
_LOG_RIGHTS = 'reference/existence only; synopsis/images excluded from AI input and redistribution'
_FRAME_RIGHTS = 'reference/existence only; synopsis and images not emitted'
_LEDGER_RIGHTS = 'reference/existence only; publisher synopsis, prose and images excluded from AI/public exports'
_USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
          'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False}
_ROLES = {'著者': 'author', '著': 'author', '作者': 'author', '原作': 'original_author',
          '原案': 'original_concept', '装画': 'cover_illustrator', '解説': 'commentary_author',
          '脚本': 'screenplay_author', 'イラスト': 'illustrator'}
_GROUP_SELECTOR = 'section.content-wrap.book-wrap > div.content > div.row.col4'


def _load(root, ref):
    return json.loads(_read(_relative(root, ref['path']), ref['sha256'], MAX_METADATA_BYTES))


def _only(nodes, field):
    if len(nodes) != 1:
        raise ValueError('Orange bibliographic field cardinality differs: ' + field)
    return nodes[0]


def _children(node, tag, cls=None):
    return [c for c in node.parts if isinstance(c, _Node) and c.tag == tag
            and (cls is None or cls in c.attrs.get('class', '').split())]


def _literal(node):
    if not isinstance(node, _Node):
        return node
    if node.tag in {'script', 'style'}:
        return ''
    if node.tag == 'br':
        return ' '
    return ''.join(_literal(c) for c in node.parts)


def _normalized(parts):
    return ' '.join(' '.join(c.text() if isinstance(c, _Node) else c for c in parts).split())


def _source(root, receipt, url, kind):
    statuses = [receipt[k] for k in ('status', 'http_status') if k in receipt]
    if (receipt.get('source_url') != url or not statuses or any(type(s) is not int or s != 200 for s in statuses)
            or receipt.get('final_url', url) != url or receipt.get('location') or receipt.get('error')
            or receipt.get('size_cap_exceeded') or receipt.get('http_redirects_followed', 0) != 0
            or receipt.get('http_retry_count', 0) != 0 or receipt.get('rights') != _LOG_RIGHTS):
        raise ValueError('Orange transport or bounded metadata rights differ')
    allowed = {'html': {'text/html'}, 'xml': {'text/xml', 'application/xml'}, 'robots': {'text/plain'}}[kind]
    if (receipt.get('content_type') or '').split(';')[0].strip().lower() not in allowed:
        raise ValueError('Orange source content type differs')
    raw = _read(_relative(root, receipt['raw_path']), receipt['sha256'], MAX_SOURCE_BYTES)
    if receipt.get('byte_length') != len(raw):
        raise ValueError('Orange receipt size differs')
    return raw


def _evidence(root, claimed, receipt, selector):
    expected = {'source_url': receipt['source_url'], 'source_sha256': receipt['sha256'],
                'retrieved_at': receipt['retrieved_at'], 'selector': selector}
    if (any(claimed.get(k) != v for k, v in expected.items())
            or _relative(root, claimed['raw_path']) != _relative(root, receipt['raw_path'])):
        raise ValueError('Orange declared raw/selector evidence differs')


def _page(root, receipt, declared):
    url = receipt['source_url']
    raw = _source(root, receipt, url, 'html')
    tree = _DOM(_decode(raw, receipt['content_type'])).root
    title = _literal(_only(tree.nodes(tag='title'), 'native archive title')).strip()
    match = re.fullmatch(r'書籍 シリーズ別 (.+) - (集英社 オレンジ文庫)', title)
    category = 'シリーズ別'
    if not match:
        match = re.fullmatch(r'(.+) \| シリーズ \| (集英社オレンジ文庫)', title)
        category = 'シリーズ'
    if not match or not match[1].strip():
        raise ValueError('Orange native taxonomy title/site context missing')
    group_title, brand = match.groups()
    section = _only([n for n in tree.nodes(tag='section', cls='content-wrap')
                     if 'book-wrap' in n.attrs.get('class', '').split()], 'book archive section')
    content = _only(_children(section, 'div', 'content'), 'own archive content')
    row = _only([n for n in _children(content, 'div', 'row') if 'col4' in n.attrs.get('class', '').split()], 'own book list')
    articles = _children(row, 'article')
    all_books = [n for n in tree.nodes(tag='article', cls='book-box') if 'box' in n.attrs.get('class', '').split()]
    if not articles or articles != all_books:
        raise ValueError('Orange book cards lie outside the explicit own parent list')
    if any('book-box' not in a.attrs.get('class', '').split() or 'box' not in a.attrs.get('class', '').split() for a in articles):
        raise ValueError('Orange archive article scope differs')
    for anchor in tree.nodes(tag='a') + tree.nodes(tag='link'):
        target = urljoin(url, anchor.attrs.get('href', ''))
        if ('/book/series/' in target and '/page/' in target) or 'next' in anchor.attrs.get('rel', '').split():
            raise ValueError('Orange parent archive pagination is not closed')
    if len(articles) != len(declared['explicit_memberships']):
        raise ValueError('Orange complete parent member count differs')
    members, legacy_author_union, seen = [], [], set()
    for ordinal, (article, old) in enumerate(zip(articles, declared['explicit_memberships'], strict=True), 1):
        selector = _GROUP_SELECTOR + f' > article.book-box.box:nth-of-type({ordinal})'
        heading = _only(article.nodes(tag='h2'), 'complete child title')
        full_title = _literal(heading).strip()
        links = {a.attrs.get('href') for a in article.nodes(tag='a') if _BOOK.fullmatch(a.attrs.get('href', ''))}
        child_url = _only(list(links), 'own child URL')
        identity = _BOOK.fullmatch(child_url)[1]
        if not full_title or child_url in seen:
            raise ValueError('Orange empty title or duplicate child URL')
        seen.add(child_url)
        credits, literals, legacy = [], [], []
        for number, p in enumerate(article.nodes(tag='p', cls='book-info-name'), 1):
            role_nodes = _children(p, 'span')
            role = None
            name_parts = p.parts
            if role_nodes:
                first = role_nodes[0]
                position = p.parts.index(first)
                if _normalized(p.parts[:position]):
                    raise ValueError('Orange role/name field has unexpected prefix')
                role = first.text()
                name_parts = p.parts[position + 1:]
                if not role:
                    raise ValueError('Orange explicit role literal is empty')
            name = ''.join(_literal(c) for c in name_parts).strip()
            if not name:
                raise ValueError('Orange explicit credit name is empty')
            credits.append({'role': _ROLES.get(role, 'unknown'), 'name': name})
            literals.append({'role_literal': role, 'name': name,
                             'source_selector': selector + f' p.book-info-name[matching-index={number}]'})
            if role is not None:
                item = {'role': role, 'name': _normalized(name_parts)}
                legacy.append(item)
                if role in {'著者', '著', '作者', '原作', '原案'} and item not in legacy_author_union:
                    legacy_author_union.append(item)
        expected = {'source_url': child_url, 'title': heading.text(), 'credits': legacy,
                    'membership': 'explicit_on_publisher_series_page', 'isbn': None}
        if any(old.get(k) != v for k, v in expected.items()):
            raise ValueError('Orange declared member title/URL/credit differs from raw')
        _evidence(root, old['evidence'], receipt, f'article.book-box.box:nth-of-type({ordinal}) h2 / p.book-info-name')
        members.append({'publisher_reference_id': 'shueisha-orange-book:' + identity,
            'opaque_book_url_key': identity, 'source_unit': 'publisher_product_reference',
            'source_url': child_url, 'title': full_title, 'credits': credits, 'credit_literals': literals,
            'publisher': None, 'label': None, 'label_literals': [], 'form': None, 'isbn': None,
            'official_lightnovel_tag': None, 'media_references': None,
            'story_series_asserted': False, 'original_work_identity_asserted': False,
            'paper_electronic_same_edition_asserted': False, 'source_selector': selector,
            'source_evidence': [{'source_url': url, 'raw_sha256': receipt['sha256'],
                                 'locator': selector + ' h2 / p.book-info-name / a.thumb[href]'}]})
    expected = {'reference_id': 'publisher-series:' + hashlib.sha256(url.encode()).hexdigest()[:24],
        'kind': 'series', 'source_url': url, 'title': ' '.join(group_title.split()),
        'title_basis': 'official_series_taxonomy_html_title', 'publisher': '集英社',
        'label_scope': '集英社オレンジ文庫', 'index_id': 'shueisha-orange-series-sitemap-v1',
        'credits': legacy_author_union, 'author_credit': ' '.join(c['role']+'：'+c['name'] for c in legacy_author_union) or None,
        'identity_to_catalogue': 'unassessed', 'light_novel_status': 'unknown_boundary_women_general_literature_not_inferred',
        'pagination_urls': []}
    if any(declared.get(k) != v for k, v in expected.items()):
        raise ValueError('Orange declared group identity/title/credit/scope differs')
    _evidence(root, declared['source_evidence'], receipt, 'title; article.book-box.box h2 / p.book-info-name')
    return group_title, brand, category, members


def verify_orange_groups(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Return every native parent listing group, independent of NDL coverage."""
    path = Path(manifest_path).resolve()
    root = path.parent
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if (proof.get('schema_version') != 'shueisha-orange-publisher-groups-proof-v1'
                or proof.get('usage_scope') != _USAGE):
            raise ValueError('Orange proof schema or metadata-only usage scope differs')
        parents, children = proof['expected_groups'], proof['expected_members']
        if type(parents) is not int or not 1 <= parents <= 97 or type(children) is not int or not 1 <= children <= 455:
            raise ValueError('Orange bounded source counts differ')
        frame, handoff = _load(root, proof['source_manifest']), _load(root, proof['source_handoff'])
        expected = {'frame_id': 'shueisha-orange-series-sitemap-v1', 'publisher': '集英社',
            'label_scope': '集英社オレンジ文庫', 'frame_kind': 'official_series_taxonomy_sitemap', 'reported_total': None,
            'finite_sitemap_url_count': parents, 'processed': parents, 'series_rows': parents,
            'explicit_memberships': children, 'failures': [], 'known_pagination_holds': [],
            'rights': _FRAME_RIGHTS, 'unique_explicit_books': children}
        if (any(frame.get(k) != v for k, v in expected.items()) or frame.get('complete') is not True
                or frame.get('all_books_in_label_claim') is not False or len(handoff) != parents):
            raise ValueError('Orange fixed taxonomy frame or rights differ')
        ledger = _load(root, proof['rights_ledger'])
        if ledger.get('version') != 1 or ledger.get('rights') != _LEDGER_RIGHTS:
            raise ValueError('Orange existing rights ledger differs')
        entry = _only([e for e in ledger['frames'] if e.get('manifest') == proof['source_manifest']['path']], 'rights ledger frame')
        expected_entry = {'manifest_sha256': proof['source_manifest']['sha256'], 'handoff': proof['source_handoff']['path'],
            'handoff_sha256': proof['source_handoff']['sha256'], 'story_series': parents, 'explicit_memberships': children,
            'unique_explicit_books': children, 'finite_series_frame_complete': True}
        if any(entry.get(k) != v for k, v in expected_entry.items()):
            raise ValueError('Orange rights ledger/source pins differ')
        ref = proof['source_receipts']
        log = _read(_relative(root, ref['path']), ref['sha256'], MAX_METADATA_BYTES)
        logs = [json.loads(line) for line in log.splitlines() if line.strip()]
        receipts = {}
        for item in logs:
            receipts.setdefault(item['source_url'], []).append(item)
        def own(url):
            return _only(receipts.get(url, []), 'own successful receipt')
        robot = own(_ORIGIN + '/robots.txt')
        parser = RobotFileParser()
        parser.parse(_source(root, robot, robot['source_url'], 'robots').decode('utf-8').splitlines())
        sitemap_receipt = own(_SITEMAP)
        raw = _source(root, sitemap_receipt, _SITEMAP, 'xml')
        _evidence(root, frame['sitemap_evidence'], sitemap_receipt, 'urlset/url/loc')
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('Orange sitemap entity declarations are outside scope')
        xml = ET.fromstring(raw)
        namespace = '{http://www.sitemaps.org/schemas/sitemap/0.9}'
        if xml.tag != namespace + 'urlset':
            raise ValueError('Orange native sitemap type differs')
        urls = []
        for node in xml:
            if node.tag != namespace + 'url':
                raise ValueError('Orange sitemap row differs')
            loc = _only([n for n in node if n.tag == namespace + 'loc'], 'native parent sitemap loc')
            url = loc.text
            if not isinstance(url, str) or not _PARENT.fullmatch(url):
                raise ValueError('Orange native parent URL route differs')
            urls.append(url)
        if len(urls) != parents or len(set(urls)) != parents:
            raise ValueError('Orange finite native parent URL set differs')
        result, seen = [], []
        for url, declared in zip(urls, handoff, strict=True):
            if declared['source_url'] != url or not parser.can_fetch('recommend-book-research/1.0', url):
                raise ValueError('Orange sitemap/native group or robots scope differs')
            receipt = own(url)
            title, brand, category, members = _page(root, receipt, declared)
            if (declared['index_source_url'] != _SITEMAP or declared['index_source_sha256'] != sitemap_receipt['sha256']
                    or declared['index_retrieved_at'] != sitemap_receipt['retrieved_at']):
                raise ValueError('Orange native parent/index source binding differs')
            seen.extend(m['source_url'] for m in members)
            result.append({'kind': 'official_publisher_listing_group_reference', 'source_unit': 'publisher_work_group',
                'reference_id': 'orange-taxonomy-url:' + hashlib.sha256(url.encode()).hexdigest(),
                'reference_id_kind': 'local_hash_of_official_taxonomy_url', 'reference_id_is_official': False,
                'local_reference_key': {'provider': 'orange', 'source_scope': 'native_orange_series_taxonomy_urlset', 'official_parent_url': url},
                'independent_official_group_id': None, 'title': title, 'source_url': url,
                'authors': None, 'publisher': None, 'label': None, 'form': None, 'isbn': None,
                'official_lightnovel_tag': None, 'light_novel_status': 'unknown', 'story_series_asserted': False,
                'story_series_identity_asserted': False, 'original_work_identity_asserted': False,
                'independent_volume_numbers_inferred': False, 'paper_electronic_same_edition_asserted': False,
                'site_context': {'brand_title_literal': brand, 'brand_title_locator': 'title',
                    'source_category_literal': category, 'individual_book_label_asserted': False},
                'members': members, 'frame_member_count': len(members), 'parent_page_membership_complete': True,
                'global_membership_complete': None, 'scope': 'fixed_native_taxonomy_sitemap_and_saved_parent_listings_only',
                'source_evidence': [{'source_url': _SITEMAP, 'raw_sha256': sitemap_receipt['sha256'], 'locator': 'urlset/url/loc'},
                    {'source_url': url, 'raw_sha256': receipt['sha256'], 'locator': 'title; ' + _GROUP_SELECTOR}],
                'usage_permissions': dict(_USAGE)})
        if len(seen) != children or len(set(seen)) != frame['unique_explicit_books']:
            raise ValueError('Orange complete child reference or unique URL count differs')
        return result
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError, ET.ParseError) as exc:
        raise ValueError('Orange publisher group evidence cannot be reproduced') from exc
