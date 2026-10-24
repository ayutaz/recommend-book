"""Authenticate selected cards in two fixed HJ native indexes, never child lists.

Only the selected index snapshots are opened. A complete index card is not a
complete product membership, an edition, a light novel or a story-series ID.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _decode, _read, _relative,
)


_PAGE = 'https://firecross.jp/hjbunko/series'
_INDEX = 'hj-bunko-series'
_PARENT = re.compile(r'https://firecross\.jp/hjbunko/series/[0-9]+')
_FRAME_IDS = {_INDEX, 'hj-novels-series', 'arianrose-series', 'ga-ebook-series'}
_RIGHTS = 'Reference-only publisher page; no synopsis, image or AI-input reuse inferred'
_USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
          'prose_ai_permission': False, 'images_permission': False,
          'raw_redistribution_permission': False}
_ROBOTS = {'status': 'not_saved_in_existing_source_evidence', 'permission_inferred': False}
_SCOPE = 'selected_native_index_cards_only'
_PROFILES = {
    'hj': {'page': _PAGE, 'index': _INDEX, 'provider': 'hj-bunko-index-only',
        'proof_schema': 'hj-selected-index-groups-proof-v1',
        'group_schema': 'light-novel-hj-index-source-group-v1',
        'reference_prefix': 'hj-index-group-local:',
        'caps': {'index_groups': 418, 'selected_groups': 137, 'selected_index_pages': 17, 'index_pages': 18}},
    'hjnovels': {'page': 'https://firecross.jp/hjnovels/series', 'index': 'hj-novels-series',
        'provider': 'hj-novels-index-only', 'proof_schema': 'hjnovels-selected-index-groups-proof-v1',
        'group_schema': 'light-novel-hjnovels-index-source-group-v1',
        'reference_prefix': 'hjnovels-index-group-local:',
        'caps': {'index_groups': 110, 'selected_groups': 39, 'selected_index_pages': 5, 'index_pages': 5}},
}
_CREDIT_CONTRACT = {'role_boundary': 'at_most_one_direct_span.book-author-type_per_own_credit_item',
    'name_boundary': 'one_or_more_direct_a.book-author-name_in_same_own_credit_item',
    'legacy_projection': 'first_direct_name_for_each_explicit_role_item',
    'safe_projection': 'all_direct_name_literals_at_index_scope',
    'descendant_or_cross_item_name_fallback': False}


def _one(items, field):
    if len(items) != 1:
        raise ValueError('HJ native own-card field absent or ambiguous: ' + field)
    return items[0]


def _load(root, pin):
    return json.loads(_read(_relative(root, pin['path']), pin['sha256'], MAX_METADATA_BYTES))


def _old_id(url):
    return 'publisher-series:' + hashlib.sha256(url.encode()).hexdigest()[:24]


def _ids(value):
    if (not isinstance(value, list) or any(not isinstance(v, str)
            or not re.fullmatch(r'publisher-series:[0-9a-f]{24}', v) for v in value)
            or len(value) != len(set(value))):
        raise ValueError('HJ selected/covered reference identity differs')
    return set(value)


def _source(root, receipt, url):
    if (receipt.get('source_url') != url or receipt.get('final_url') != url
            or receipt.get('http_status') != 200 or receipt.get('rights') != _RIGHTS
            or any(receipt.get(k) for k in ('location', 'error', 'size_cap_exceeded',
                   'redirects_followed', 'http_redirects_followed', 'retry_count', 'http_retry_count'))
            or not re.match(r'^text/html(?:\s*;|$)', receipt.get('content_type', ''), re.I)
            or type(receipt.get('byte_length')) is not int
            or not 0 < receipt['byte_length'] <= MAX_SOURCE_BYTES):
        raise ValueError('HJ selected receipt URL/status/rights/size differs')
    data = _read(_relative(root, receipt['raw_path']), receipt['sha256'], MAX_SOURCE_BYTES)
    if len(data) != receipt['byte_length']:
        raise ValueError('HJ selected source declared byte length differs')
    return _DOM(_decode(data, receipt['content_type'])).root


def _text(node, field):
    nodes = [node, *node.nodes()]
    if any(n.tag in {'img', 'input', 'textarea', 'select', 'button'} or 'hidden' in n.attrs
           or n.attrs.get('aria-hidden') == 'true' for n in nodes):
        raise ValueError('HJ explicit text field contains hidden/form/image content: ' + field)
    value = node.text()
    if not value or len(value) > 4096:
        raise ValueError('HJ explicit complete text absent or oversized: ' + field)
    return value


def _credits(card):
    lists = card.nodes(tag='ul', cls='book-author-list')
    if not lists:
        return [], []
    listing = _one(lists, 'index credit list')
    entries = [n for n in listing.parts if isinstance(n, _Node)
               and n.tag == 'li' and 'book-author-item' in n.attrs.get('class', '').split()]
    if entries != listing.nodes(tag='li', cls='book-author-item'):
        raise ValueError('HJ own index credit boundary differs')
    result, legacy = [], []
    for entry in entries:
        roles = [n for n in entry.parts if isinstance(n, _Node)
                 and n.tag == 'span' and 'book-author-type' in n.attrs.get('class', '').split()]
        names = [n for n in entry.parts if isinstance(n, _Node)
                 and n.tag == 'a' and 'book-author-name' in n.attrs.get('class', '').split()]
        if (len(roles) > 1 or roles != entry.nodes(cls='book-author-type')
                or not names or names != entry.nodes(cls='book-author-name')):
            raise ValueError('HJ own index role/name boundary is absent, nested or ambiguous')
        role = _text(roles[0], 'credit role') if roles else None
        literals = [_text(name, 'credit name') for name in names]
        # The old collector's re.search kept only the first name per role item.
        # Reproduce that projection independently, without losing other names
        # explicitly under the same single-role, own-item boundary.
        if role is not None:
            legacy.append({'role': role, 'name': literals[0]})
        result.extend({'role_literal': role, 'name': name, 'role_scope': 'publisher_index_card'} for name in literals)
    return result, legacy


def _failure(index_url, receipt, own_url, error, locator='li.seriesList_item'):
    return ValueError('HJ source_index_url=' + index_url + ' raw_sha256=' + str(receipt.get('sha256'))
        + ' own_card_url=' + own_url + ' legacy_reference_id=' + _old_id(own_url)
        + ' locator=' + locator + ': ' + str(error))


def _cards(tree, wanted, all_selected, index_url, receipt, parent_pattern):
    found = {}
    for card in tree.nodes(tag='li', cls='seriesList_item'):
        anchors = card.nodes(tag='a', cls='seriesList_itemTitle')
        matches = [a for a in anchors if a.attrs.get('href') in all_selected]
        if not matches:
            continue
        own_url = matches[0].attrs['href']
        try:
            anchor = _one(anchors, 'complete index title link')
            url = anchor.attrs.get('href')
            if not parent_pattern.fullmatch(url) or url not in wanted or url in found:
                raise ValueError('HJ duplicate or out-of-scope selected own-card URL')
            credits, legacy = _credits(card)
            found[url] = {'title': _text(anchor, 'complete index title'), 'credits': credits, 'legacy_credits': legacy}
        except ValueError as exc:
            locator = 'li.seriesList_item:has(a.seriesList_itemTitle[href="' + own_url + '"])'
            raise _failure(index_url, receipt, own_url, exc, locator) from exc
    if set(found) != wanted:
        own_url = sorted(wanted - set(found))[0]
        raise _failure(index_url, receipt, own_url,
            'HJ selected own cards do not match exact declared index page', 'expected_missing_own_card')
    return found


def _verify_index_groups(manifest_path, sha256, profile):
    spec = _PROFILES[profile]
    page, index = spec['page'], spec['index']
    parent_pattern = re.compile(re.escape(page) + r'/[0-9]+')
    path = Path(manifest_path).resolve()
    root = path.parent
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if (proof.get('schema_version') != spec['proof_schema']
                or proof.get('artifact_version') != 2 or proof.get('index_credit_contract') != _CREDIT_CONTRACT
                or proof.get('usage_scope') != _USAGE or proof.get('robots_evidence') != _ROBOTS
                or proof.get('source_rights_literal') != _RIGHTS):
            raise ValueError('HJ index-only proof schema/rights/robots context differs')
        source_root = _relative(root, proof['source_root'])
        counts = proof['expected_counts']
        caps = spec['caps']
        if (set(counts) != set(caps) or any(type(counts[k]) is not int or not 1 <= counts[k] <= cap for k, cap in caps.items())):
            raise ValueError('HJ selected finite source bounds differ')
        selected, covered = [_ids(proof[k]) for k in ('selected_reference_ids', 'covered_reference_ids')]
        if len(selected) != counts['selected_groups'] or selected & covered or len(covered) > 917:
            raise ValueError('HJ selected/covered partition differs')
        report = _load(root, proof['coverage_report'])
        report_rows = [r for r in report['rows'] if r.get('index_id') == index]
        partition = report['provider_counts'][index]
        selected_rows = [r for r in report_rows if r['status'] == 'uncovered']
        report_by_id = {r['reference_id']: r for r in selected_rows}
        if (len(report_rows) != counts['index_groups']
                or len({r['reference_id'] for r in report_rows}) != len(report_rows)
                or len(report_by_id) != len(selected_rows)
                or set(report_by_id) != selected or _ids(report['covered_reference_ids']) != covered
                or not selected <= _ids(report['uncovered_reference_ids'])
                or any(r['status'] not in {'covered', 'uncovered'} for r in report_rows)
                or {r['reference_id'] for r in report_rows if r['status'] == 'covered'} - covered
                or partition != {'source_references': counts['index_groups'],
                    'covered': counts['index_groups'] - counts['selected_groups'],
                    'uncovered': counts['selected_groups'], 'conflicts': 0}):
            raise ValueError('HJ selection differs from pinned uncovered coverage partition')
        frames = _load(root, proof['source_frames'])
        if not isinstance(frames, list) or len(frames) != 4 or {f['id'] for f in frames} != _FRAME_IDS:
            raise ValueError('HJ original four-frame metadata differs')
        frame = _one([f for f in frames if f['id'] == index], 'HJ source frame')
        if (frame.get('url') != page or frame.get('kind') != 'series_index'
                or frame.get('index_pages_complete') is not True
                or frame.get('completion_reason') != 'all_linked_pages_exhausted'
                or frame.get('historical_entire_label_complete') is not False
                or frame.get('reference_series_count') != counts['index_groups']
                or len(frame['pages']) != counts['index_pages']):
            raise ValueError('HJ original finite index frame differs')
        page_by_url = {}
        for number, receipt in enumerate(frame['pages'], 1):
            url = page if number == 1 else page + '?sort=1&page=' + str(number)
            if receipt.get('source_url') != url:
                raise ValueError('HJ fixed index page route/order differs')
            page_by_url[url] = receipt
        roster = _load(root, proof['source_roster'])
        rows = [r for r in roster if r.get('index_id') == index]
        roster_by_id = {r['reference_id']: r for r in rows}
        if (len(rows) != counts['index_groups'] or len(roster_by_id) != len(rows)
                or set(roster_by_id) != {r['reference_id'] for r in report_rows}):
            raise ValueError('HJ original index roster count/uniqueness differs')
        wanted_by_page = {}
        for reference in proof['selected_reference_ids']:
            row, declared = roster_by_id[reference], report_by_id[reference]
            url = row['source_url']
            if (not isinstance(url, str) or not parent_pattern.fullmatch(url) or reference != _old_id(url)
                    or row.get('kind') != 'series' or not row.get('title')
                    or any(declared.get(k) != row.get(k) for k in
                           ('reference_id', 'source_url', 'index_id', 'index_source_url', 'index_source_sha256'))):
                raise ValueError('HJ selected native URL/reference/roster scope differs')
            receipt = page_by_url[row['index_source_url']]
            if (receipt['sha256'] != row['index_source_sha256']
                    or receipt['retrieved_at'] != row['index_retrieved_at']):
                raise ValueError('HJ selected index receipt/roster snapshot differs')
            wanted_by_page.setdefault(row['index_source_url'], set()).add(url)
        if len(wanted_by_page) != counts['selected_index_pages']:
            raise ValueError('HJ exact selected index-page set/count differs')
        authenticated = {}
        all_selected_urls = {roster_by_id[reference]['source_url'] for reference in selected}
        for url, wanted in wanted_by_page.items():
            receipt = page_by_url[url]
            try:
                tree = _source(source_root, receipt, url)
            except (ValueError, OSError) as exc:
                raise _failure(url, receipt, sorted(wanted)[0], exc,
                    'declared_expected_own_card_context/source_authentication') from exc
            cards = _cards(tree, wanted, all_selected_urls, url, receipt, parent_pattern)
            if set(authenticated) & set(cards):
                raise ValueError('HJ selected native card repeated across index pages')
            authenticated.update(cards)
        result = []
        for reference in proof['selected_reference_ids']:
            row = roster_by_id[reference]
            url, page = row['source_url'], row['index_source_url']
            card = authenticated[url]
            legacy_credits = card['legacy_credits']
            reconstructed = ' '.join(c['role'] + '：' + c['name'] for c in legacy_credits)
            if (card['title'] != row['title'] or legacy_credits != row.get('credits', [])
                    or reconstructed != row.get('author_credit', '')):
                raise _failure(page, page_by_url[page], url,
                    'HJ selected complete title/explicit role-name projection differs from raw own card',
                    'li.seriesList_item/seriesList_itemTitle/book-author-item/legacy_first_name_projection')
            key = {'provider': spec['provider'], 'source_index': spec['page'],
                   'source_scope': _SCOPE, 'official_group_url': url}
            serialized = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            locator = 'li.seriesList_item:has(a.seriesList_itemTitle[href="' + url + '"])'
            result.append({'schema_version': spec['group_schema'],
                'kind': 'official_publisher_native_index_group_reference', 'source_unit': 'publisher_work_group',
                'reference_id': spec['reference_prefix'] + hashlib.sha256(serialized.encode()).hexdigest(),
                'reference_id_kind': 'local_hash_of_official_index_group_URL_context', 'reference_id_is_official': False,
                'local_reference_key': key, 'legacy_source_reference_id': reference,
                'independent_official_group_id': None, 'title': card['title'], 'source_url': url,
                'authors': None, 'publisher': None, 'label': None, 'form': None, 'isbn': None,
                'official_lightnovel_tag': None, 'media_references': None, 'light_novel_status': 'unknown',
                'index_credit_literals': card['credits'], 'legacy_index_credit_projection': legacy_credits,
                'legacy_projection_reproduced': True, 'members': [], 'frame_member_count': 0,
                'known_verified_member_reference_count': 0, 'total_product_count': None,
                'native_index_card_complete': True, 'index_frame_pages_complete': True,
                'frame_membership_complete': None, 'parent_page_membership_complete': None,
                'global_membership_complete': None, 'story_series_asserted': False,
                'story_series_identity_asserted': False, 'original_work_identity_asserted': False,
                'edition_identity_asserted': False, 'singleton_asserted': False,
                'independent_volume_numbers_inferred': False, 'paper_electronic_same_edition_asserted': False,
                'scope': _SCOPE, 'source_evidence': [{'source_url': page,
                    'raw_sha256': page_by_url[page]['sha256'], 'locator': locator + ' a.seriesList_itemTitle'},
                    {'source_url': page, 'raw_sha256': page_by_url[page]['sha256'],
                     'locator': locator + ' ul.book-author-list > li.book-author-item (.book-author-type / .book-author-name)'}],
                'robots_evidence': dict(_ROBOTS), 'usage_permissions': dict(_USAGE)})
        return result
    except (KeyError, TypeError, AttributeError, IndexError, UnicodeDecodeError, json.JSONDecodeError, OSError, EOFError) as exc:
        raise ValueError('HJ selected native index evidence cannot be reproduced') from exc


def verify_hj_index_groups(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Replay exact HJ文庫 cards in at most17 pinned index pages.

    Empty members mean zero asserted child references, not zero actual books.
    Historical robots/usage evidence remains unknown, never inferred permission.
    """
    return _verify_index_groups(manifest_path, sha256, 'hj')


def verify_hjnovels_index_groups(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Replay exact HJノベルス cards in at most5 pinned index pages."""
    return _verify_index_groups(manifest_path, sha256, 'hjnovels')
