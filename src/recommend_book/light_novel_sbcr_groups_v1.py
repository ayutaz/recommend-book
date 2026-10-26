"""Replay selected saved SB parent booklists as local publisher groups.

The input pins the earlier four-frame discovery and an explicit uncovered
partition. Only SB's one index and the selected parent pages are replayed.
Names previously obtained from representative products are deliberately unused.
No network, database, model, prose, images or edition inference is involved.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from .light_novel_ndl_series_v1 import _cards, _sbcr_books, _snapshot_text
from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _read, _relative,
)


_PAGE = 'https://ebook.sbcr.jp/lightnovel/series'
_PARENT = re.compile(r'https://ebook\.sbcr\.jp/lightnovel/series/id/[0-9]+')
_BOOK = re.compile(r'https://ebook\.sbcr\.jp/product/([A-Za-z0-9]+)')
_INDEX = 'ga-ebook-series'
_FRAME_IDS = {'hj-bunko-series', 'hj-novels-series', 'arianrose-series', _INDEX}
_RIGHTS = 'Reference-only publisher page; no synopsis, image or AI-input reuse inferred'
_USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
          'prose_ai_permission': False, 'images_permission': False,
          'raw_redistribution_permission': False}
_ROBOTS = {'status': 'not_saved_in_existing_source_evidence', 'permission_inferred': False}
_OPEN = '<!-- ↓↓↓↓ partial.comic.bookloop ↓↓↓↓ -->'
_CLOSE = '<!-- ↑↑↑↑ partial.comic.bookloop↑↑↑↑ -->'
_SCOPE = 'selected_saved_parent_bookloops'
_BOOKLIST_CONTRACT = {'scope': 'partial.comic.bookloop',
    'cards': 'div.bookarea > div.book_line > div.book > dl',
    'complete_title': 'dd.title > a[href]', 'cover_alt_is_title': False,
    'unscoped_links_are_members': False, 'conflicting_explicit_titles': 'reject',
    'legacy_handoff': 'exact_existing_helper_projection_only',
    'canonical_title_agreement_with_legacy_required': False,
    'canonical_url_set_and_count_agreement': True}


def _one(items, field):
    if len(items) != 1:
        raise ValueError('SB field cardinality differs: ' + field)
    return items[0]


def _children(node, tag, cls=None):
    return [n for n in node.parts if isinstance(n, _Node) and n.tag == tag
            and (cls is None or cls in n.attrs.get('class', '').split())]


def _load(root, pin):
    return json.loads(_read(_relative(root, pin['path']), pin['sha256'], MAX_METADATA_BYTES))


def _old_id(url):
    return 'publisher-series:' + hashlib.sha256(url.encode()).hexdigest()[:24]


def _ids(value):
    if (not isinstance(value, list) or len(value) != len(set(value))
            or any(not isinstance(v, str) or not re.fullmatch(r'publisher-series:[0-9a-f]{24}', v) for v in value)):
        raise ValueError('SB selected/covered/held reference IDs differ')
    return set(value)


def _source(root, receipt, url, cache):
    if (receipt.get('source_url') != url or receipt.get('final_url') != url
            or receipt.get('http_status') != 200 or receipt.get('rights') != _RIGHTS
            or not re.match(r'^text/html(?:\s*;|$)', receipt.get('content_type', ''), re.I)
            or type(receipt.get('byte_length')) is not int
            or not 0 < receipt['byte_length'] <= MAX_SOURCE_BYTES):
        raise ValueError('SB own source receipt/rights/status/length differs')
    path = _relative(root, receipt['raw_path'])

    def artifact(target, sha, _cache):
        if target != path:
            raise ValueError('SB raw path differs from pinned own receipt')
        if (path, sha) not in cache:
            data = _read(path, sha, MAX_SOURCE_BYTES)
            if len(data) != receipt['byte_length']:
                raise ValueError('SB declared source byte length differs')
            cache[(path, sha)] = data
        return cache[(path, sha)]

    return _snapshot_text(receipt, root, artifact, cache, expected_url=url)


class _Boundary(HTMLParser):
    def __init__(self):
        super().__init__()
        self.markers = []

    def handle_comment(self, data):
        marker = '<!--' + data + '-->'
        if marker in {_OPEN, _CLOSE}:
            self.markers.append(marker)


def _members(document, url, receipt, declared):
    boundaries = _Boundary()
    boundaries.feed(document)
    if (document.count(_OPEN) != 1 or document.count(_CLOSE) != 1
            or boundaries.markers != [_OPEN, _CLOSE]
            or document.index(_OPEN) >= document.index(_CLOSE)):
        raise ValueError('SB explicit own bookloop is absent, repeated or unclosed')
    block = document.split(_OPEN, 1)[1].split(_CLOSE, 1)[0]
    tree = _DOM(block).root
    if any('next' in n.attrs.get('rel', '').split() for n in tree.nodes(tag='a') + tree.nodes(tag='link')):
        raise ValueError('SB own bookloop has an unresolved next page')
    area = _one(tree.nodes(tag='div', cls='bookarea'), 'own bookarea')
    lines = _children(area, 'div', 'book_line')
    cards = [card for line in lines for card in _children(line, 'div', 'book')]
    if not cards or cards != area.nodes(tag='div', cls='book'):
        raise ValueError('SB explicit own book card boundary differs')
    unique, books = {}, []
    for number, card in enumerate(cards, 1):
        dl = _one(_children(card, 'dl'), 'own book card dl')
        field = _one(_children(dl, 'dd', 'title'), 'explicit complete title field')
        anchor = _one(_children(field, 'a'), 'explicit complete title link')
        if field.nodes(tag='a') != [anchor] or anchor.nodes(tag='img'):
            raise ValueError('SB complete title field contains non-title link/image')
        href, title = anchor.attrs.get('href', ''), anchor.text()
        if not re.fullmatch(r'/product/[A-Za-z0-9]+', href):
            raise ValueError('SB own child URL is outside the fixed product route')
        if not title:
            raise ValueError('SB complete child title is absent')
        if href in unique:
            if unique[href]['title'] != title:
                raise ValueError('SB duplicate child URL has conflicting explicit complete title fields')
            continue
        item = {'book_url': 'https://ebook.sbcr.jp' + href, 'title': title}
        unique[href] = {**item, 'card_ordinal': number}
        books.append(item)
    # Reproduce the pre-existing projection as evidence of what was saved.
    # Its all-link/cover fallback is not the authority for the safe full title.
    legacy_books = _sbcr_books(document, url)
    if legacy_books != declared['explicit_book_memberships']:
        raise ValueError('SB declared legacy child projection differs from raw')
    if (len(books) != len(legacy_books)
            or {b['book_url'] for b in books} != {b['book_url'] for b in legacy_books}):
        raise ValueError('SB explicit title fields and legacy own child URL set/count differ')
    count = declared['publisher_reported_edition_count']
    if type(count) is not int or count != len(books):
        raise ValueError('SB own index count and closed parent bookloop differ')
    result = []
    for number, book in enumerate(books, 1):
        match = _BOOK.fullmatch(book['book_url'])
        if not match:
            raise ValueError('SB child opaque product URL differs')
        ordinal = unique['/product/' + match[1]]['card_ordinal']
        locator = ('partial.comic.bookloop div.bookarea > div.book_line > '
                   f'div.book[matching-index={ordinal}] > dl > dd.title > a[href="/product/{match[1]}"]')
        result.append({'publisher_reference_id': 'sbcr-product:' + match[1],
            'opaque_book_url_key': match[1], 'source_unit': 'publisher_product_reference',
            'source_url': book['book_url'], 'title': book['title'], 'credits': [], 'credit_literals': [],
            'publisher': None, 'label': None, 'label_literals': [], 'form': None, 'isbn': None,
            'official_lightnovel_tag': None, 'media_references': None,
            'story_series_asserted': False, 'original_work_identity_asserted': False,
            'paper_electronic_same_edition_asserted': False,
            'membership_basis': 'explicit_link_in_saved_own_parent_bookloop',
            'title_basis': 'explicit_own_book_card_dd_title_anchor_text',
            'legacy_projection_is_canonical_title': False,
            'source_selector': locator, 'source_evidence': [{'source_url': url,
                'raw_sha256': receipt['sha256'], 'locator': locator}]})
    return result


def verify_sbcr_groups(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Authenticate one SB index plus the exact selected complete parent pages.

    Missing historic robots evidence is explicit, not interpreted as permission.
    The returned group is a local listing assertion, never a story-series ID.
    """
    path = Path(manifest_path).resolve()
    root = path.parent
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if (proof.get('schema_version') != 'sbcr-selected-publisher-groups-proof-v2'
                or proof.get('usage_scope') != _USAGE or proof.get('robots_evidence') != _ROBOTS
                or proof.get('source_rights_literal') != _RIGHTS
                or proof.get('booklist_contract') != _BOOKLIST_CONTRACT):
            raise ValueError('SB proof schema/rights/missing robots context differs')
        source_root = _relative(root, proof['source_root'])
        counts = proof['expected_counts']
        caps = {'index_groups': 723, 'selected_groups': 215, 'selected_members': 1029,
                'unique_selected_members': 1029}
        if (set(counts) != set(caps) or any(type(counts[k]) is not int or not 1 <= counts[k] <= v for k, v in caps.items())
                or counts['unique_selected_members'] > counts['selected_members']):
            raise ValueError('SB selected finite source bounds differ')
        selected, covered, held = [_ids(proof[k]) for k in ('selected_reference_ids', 'covered_reference_ids', 'held_reference_ids')]
        if (not selected or selected & covered or selected & held or covered & held
                or len(selected) != counts['selected_groups'] or len(held) > 8 or len(covered) > 917):
            raise ValueError('SB selected/covered/incomplete partition differs')
        report = _load(root, proof['coverage_report'])
        if (report.get('next_single_provider') != _INDEX
                or _ids(report['covered_reference_ids']) != covered
                or _ids(report['next_provider_complete_membership_reference_ids']) != selected
                or _ids(report['next_provider_incomplete_membership_reference_ids']) != held
                or _ids(report['next_provider_uncovered_reference_ids']) != selected | held
                or report['next_provider_complete_group_child_references'] != counts['selected_members']
                or report['next_provider_complete_group_unique_child_urls'] != counts['unique_selected_members']
                or report['next_provider_cross_group_child_title_conflicts'] != []):
            raise ValueError('SB selected scope differs from pinned coverage partition')
        frames = _load(root, proof['source_frames'])
        if len(frames) != 4 or {f['id'] for f in frames} != _FRAME_IDS:
            raise ValueError('SB original four-frame metadata differs')
        frame = _one([f for f in frames if f['id'] == _INDEX], 'SB source frame')
        if (frame.get('url') != _PAGE or frame.get('kind') != 'series_index'
                or frame.get('index_pages_complete') is not True
                or frame.get('completion_reason') != 'all_linked_pages_exhausted'
                or frame.get('historical_entire_label_complete') is not False
                or frame.get('reference_series_count') != counts['index_groups']):
            raise ValueError('SB finite index scope differs')
        index_receipt = _one(frame['pages'], 'single SB index page')
        cache = {}
        index_cards = _cards(_INDEX, _source(source_root, index_receipt, _PAGE, cache))
        by_url = {}
        for card in index_cards:
            if not card['title'] or card['source_url'] in by_url:
                raise ValueError('SB index parent URL/title is empty or duplicate')
            by_url[card['source_url']] = card
        if len(by_url) != counts['index_groups']:
            raise ValueError('SB original finite index count differs')
        handoff = _load(root, proof['source_handoff'])
        manifest = _load(root, proof['source_handoff_manifest'])
        if (manifest.get('schema_version') != 'sbcr-publisher-series-handoff-v3'
                or manifest.get('snapshot_sha256') != proof['source_handoff']['sha256']
                or manifest.get('rows') != len(handoff) or len(handoff) != len(by_url)
                or manifest.get('national_coverage_proved') is not False
                or manifest.get('membership_index_complete') != sum(r.get('membership_index_complete') is True for r in handoff)):
            raise ValueError('SB v3 handoff snapshot pin/counts differ')
        declarations = {}
        for row in handoff:
            url = row['source_url']
            if not isinstance(url, str) or not _PARENT.fullmatch(url) or row['reference_id'] != _old_id(url):
                raise ValueError('SB declared parent URL/reference binding differs')
            if row['reference_id'] in declarations:
                raise ValueError('SB duplicate declared parent reference')
            declarations[row['reference_id']] = row
        if {_old_id(u) for u in by_url} != set(declarations) or not (selected | held) <= set(declarations):
            raise ValueError('SB handoff/index/selected parent set differs')
        if any(declarations[r].get('membership_index_complete') is not False for r in held):
            raise ValueError('SB held incomplete parent scope differs')
        roster = _load(root, proof['source_roster'])
        roster_rows = [r for r in roster if r.get('index_id') == _INDEX]
        roster_map = {r['reference_id']: r for r in roster_rows}
        if len(roster_map) != len(roster_rows) or set(roster_map) != set(declarations):
            raise ValueError('SB original roster/index parent set differs')
        result, seen_children = [], {}
        for reference in proof['selected_reference_ids']:
            row = declarations[reference]
            url = row['source_url']
            expected = {'reference_id': reference, 'source_url': url, 'title': by_url[url]['title'],
                        'index_id': _INDEX, 'index_source_url': _PAGE,
                        'index_source_sha256': index_receipt['sha256'], 'index_retrieved_at': index_receipt['retrieved_at']}
            if (any(row.get(k) != v or roster_map[reference].get(k) != v for k, v in expected.items())
                    or row.get('kind') != 'series' or row.get('membership_index_complete') is not True
                    or row.get('membership_hold_reason') is not None
                    or row.get('individual_novel_form_unproved') is not True
                    or row.get('synopsis_reuse') != 'not_granted_or_used'
                    or row.get('publisher_reported_edition_count') != by_url[url]['publisher_reported_edition_count']):
                raise ValueError('SB selected group metadata/index/scope differs')
            receipt = row['series_source_snapshot']
            try:
                members = _members(_source(source_root, receipt, url, cache), url, receipt, row)
            except ValueError as exc:
                raise ValueError(f'SB parent reference={reference} source_url={url} '
                                 f'raw_sha256={receipt.get("sha256")} '
                                 f'locator=partial.comic.bookloop/dd.title/a: {exc}') from exc
            for member in members:
                child, title = member['source_url'], member['title']
                if child in seen_children and seen_children[child] != title:
                    raise ValueError('SB selected parents conflict on child complete title')
                seen_children[child] = title
            key = {'provider': 'sbcr', 'source_page': _PAGE, 'source_scope': _SCOPE, 'official_parent_url': url}
            serialized = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            result.append({'kind': 'official_publisher_listing_group_reference', 'source_unit': 'publisher_work_group',
                'reference_id': 'sbcr-group-local:' + hashlib.sha256(serialized.encode()).hexdigest(),
                'reference_id_kind': 'local_hash_of_official_parent_context', 'reference_id_is_official': False,
                'local_reference_key': key, 'independent_official_group_id': None,
                'legacy_source_reference_id': reference, 'title': row['title'], 'source_url': url,
                'authors': None, 'publisher': None, 'label': None, 'form': None, 'isbn': None,
                'official_lightnovel_tag': None, 'light_novel_status': 'unknown',
                'story_series_asserted': False, 'story_series_identity_asserted': False,
                'original_work_identity_asserted': False, 'independent_volume_numbers_inferred': False,
                'paper_electronic_same_edition_asserted': False, 'members': members,
                'frame_member_count': len(members), 'parent_page_membership_complete': True,
                'legacy_projection_reproduced': True, 'legacy_projection_is_canonical_title': False,
                'global_membership_complete': None, 'scope': _SCOPE,
                'source_evidence': [{'source_url': _PAGE, 'raw_sha256': index_receipt['sha256'],
                                     'locator': 'index a[href resolving to "' + url + '"] (series title/count)'},
                    {'source_url': url, 'raw_sha256': receipt['sha256'],
                     'locator': 'partial.comic.bookloop div.bookarea > div.book_line > div.book > dl > dd.title > a[href]'}],
                'robots_evidence': dict(_ROBOTS), 'usage_permissions': dict(_USAGE)})
        if (sum(len(g['members']) for g in result) != counts['selected_members']
                or len(seen_children) != counts['unique_selected_members']):
            raise ValueError('SB selected complete parent/member totals differ')
        return result
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError, OSError, EOFError) as exc:
        raise ValueError('SB selected publisher group evidence cannot be reproduced') from exc
