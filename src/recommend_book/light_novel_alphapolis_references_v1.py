"""Replay the pinned Alpha/Regina commercial-book index, not novel identities.

Only the 67 manifest-listed raw pages are read. Exported rosters and publisher
prose/images are not used as metadata. General Alpha novels and Regina cards
retain unknown individual light-novel, ISBN, publisher, form and volume facts.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from .light_novel_publisher_references_v1 import MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _read, _relative


_SPECS = (
    ('alphapolis', 'アルファポリス', 'https://www.alphapolis.co.jp/book/alphapolis/tankou/100', 1753, 1752, 44),
    ('regina', 'レジーナブックス', 'https://www.alphapolis.co.jp/book/regina/tankou', 886, 886, 23),
)
_DETAIL = re.compile(r'https://www\.alphapolis\.co\.jp/book/detail/([0-9]+)/([0-9]+)')
_DUPLICATE = 'alphapolis-commercial-book:1048860/14281'
_ROLES = {'著': 'author', '著者': 'author', '編': 'editor', 'イラスト': 'illustrator', '原作': 'original_story_credit'}


def _literal(node):
    if node.tag in {'script', 'style'}:
        return ''
    if node.tag == 'br':
        return '\n'
    return ''.join(_literal(part) if isinstance(part, _Node) else part for part in node.parts)


def _one(nodes, name):
    if len(nodes) != 1:
        raise ValueError('Alpha own bibliographic field cardinality differs: ' + name)
    return nodes[0]


def _url(entry, number):
    return entry if number == 1 else entry + f'?limit=40&page={number}'


def _source(root, receipt, url):
    if (receipt.get('source_url') != url or receipt.get('http_status') != 200
            or receipt.get('final_url', url) != url or receipt.get('location') or receipt.get('error')
            or receipt.get('hold') or receipt.get('size_cap_exceeded')
            or receipt.get('redirects', 0) != 0 or receipt.get('retries', 0) != 0):
        raise ValueError('Alpha raw receipt URL or successful source scope differs')
    raw = _read(_relative(root, receipt.get('raw_path')), receipt.get('raw_sha256'), MAX_SOURCE_BYTES)
    return _DOM(raw.decode('utf-8')).root


def _page(tree, receipt, brand, label, entry, total, last, number):
    pagers = tree.nodes(cls='paginator')
    totals = []
    for pager in pagers:
        match = re.match(r'^([0-9,]+)\s*件\b', pager.text())
        if match:
            totals.append(int(match[1].replace(',', '')))
    if not totals or set(totals) != {total}:
        raise ValueError('Alpha explicit card total differs')
    container = _one(tree.nodes(cls='book-blocks'), 'book-blocks')
    cards = container.nodes(cls='book')
    if len(cards) != min(40, total - (number - 1) * 40):
        raise ValueError('Alpha own page card count differs')
    rows = []
    for ordinal, card in enumerate(cards, 1):
        if not {'book', 'section', brand}.issubset(card.attrs.get('class', '').split()):
            raise ValueError('Alpha card brand scope differs')
        link = _one(_one(card.nodes(tag='h2', cls='title'), 'title').nodes(tag='a'), 'title link')
        url = urljoin(receipt['source_url'], link.attrs.get('href', ''))
        identity = _DETAIL.fullmatch(url)
        title = _literal(link).strip()
        if not identity or not title:
            raise ValueError('Alpha own opaque reference or complete title missing')
        subtitle_nodes = card.nodes(tag='h3', cls='sub-title')
        subtitle = _literal(_one(subtitle_nodes, 'subtitle')).strip() if subtitle_nodes else ''
        labels = [node.text() for node in card.nodes(cls='label-tag')]
        if labels != [label]:
            raise ValueError('Alpha own card label differs from bounded brand')
        credits = []
        authors = _one(card.nodes(cls='authors'), 'authors')
        for credit in authors.nodes(cls='author'):
            anchor = _one(credit.nodes(tag='a'), 'author name')
            name, full = _literal(anchor).strip(), credit.text()
            if not name or not full.startswith(anchor.text()):
                raise ValueError('Alpha creator name/role boundary differs')
            suffix = full[len(anchor.text()):].strip()
            if not suffix.startswith('/'):
                raise ValueError('Alpha explicit creator role delimiter absent')
            role = suffix[1:].strip()
            credits.append({'name': name, 'role_display': role, 'role': _ROLES.get(role, 'unknown')})
        locator = f'.book-blocks .book:nth-of-type({ordinal}) h2.title/a; h3.sub-title; .label-tag; .authors .author'
        complete = title + ('\n' + subtitle if subtitle else '')
        rows.append({'publisher_reference_id': 'alphapolis-commercial-book:' + '/'.join(identity.groups()),
            'source_unit': 'publisher_product_reference', 'source_url': url, 'opaque_url_ids': list(identity.groups()),
            'title': complete, 'title_literal': title, 'subtitle_literal': subtitle, 'complete_display_title': complete,
            'credits': credits, 'label': label, 'label_literals': [label],
            'publisher': None, 'isbn': None, 'form': None, 'volume': None, 'official_lightnovel_tag': None,
            'story_series_asserted': False, 'metadata_conflict': False, 'bibliographic_variants': [],
            'edition_identity_asserted': False, 'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
            'source_scope_membership': {'source_unit': 'publisher_commercial_book_index_membership',
                'brand': brand, 'entry_url': entry,
                'index_scope': 'alphapolis_novel_tankou_category_100' if brand == 'alphapolis' else 'regina_tankou',
                'membership_basis': 'explicit_book_card_and_own_label_tag_in_fixed_index',
                'label_context_literal': label, 'individual_lightnovel_classification': 'unknown',
                'full_historical_catalogue_complete': False},
            'occurrence_count': 1,
            'source_evidence': [{'source_url': receipt['source_url'], 'raw_sha256': receipt['raw_sha256'], 'locator': locator}]})
    nexts, higher, linked = set(), set(), set()
    for pager in pagers:
        for anchor in pager.nodes(tag='a'):
            href = urljoin(receipt['source_url'], anchor.attrs.get('href', ''))
            parsed = urlsplit(href)
            if parsed.scheme != 'https' or parsed.netloc != 'www.alphapolis.co.jp' or parsed.path != urlsplit(entry).path or parsed.fragment:
                continue
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (set(query) != {'limit', 'page'} or query['limit'] != ['40'] or len(query['page']) != 1
                    or not re.fullmatch(r'[1-9][0-9]*', query['page'][0])):
                continue
            target = int(query['page'][0])
            linked.add(target)
            if target > last:
                raise ValueError('Alpha pagination escapes explicit finite bound')
            if target > number:
                higher.add(target)
            if target == number + 1:
                nexts.add(href)
    if (number == 1 and last not in linked or number < last and nexts != {_url(entry, number + 1)}
            or number == last and higher):
        raise ValueError('Alpha explicit next or terminal differs')
    return rows


def verify_alphapolis_references(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Return 2638 sparse references from 2639 raw cards in two fixed frames.

    Only the known equal-metadata duplicate is folded, retaining both raw
    locators. Any metadata conflict or changed boundary requires a new frame.
    """
    path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        expected = {'schema_version': 'alphapolis-two-commercial-novel-bookframes-v1',
            'source_frame_status': 'two_declared_card_frames_terminal_with_one_duplicate_reference_hold',
            'declared_card_count': 2639, 'unique_book_references': 2638, 'same_metadata_duplicate_occurrences': 1,
            'novel_or_light_novel_positive_admissions': 0, 'original_work_or_story_series_identities_asserted': 0}
        if any(manifest.get(k) != v for k, v in expected.items()) or len(manifest['frames']) != 2:
            raise ValueError('Alpha fixed two-brand terminal frame differs')
        output, all_ids = [], set()
        for frame, spec in zip(manifest['frames'], _SPECS):
            brand, label, entry, total, unique, last = spec
            expected_frame = {'brand': brand, 'label': label, 'entry_url': entry, 'declared_card_total': total,
                'observed_cards': total, 'unique_book_references': unique, 'pages': last,
                'one_to_one_unique_total_verified': brand == 'regina', 'unresolved_metadata_conflicts': 0,
                'unknown_author_reference_count': 3 if brand == 'alphapolis' else 0,
                'duplicates': [_DUPLICATE] if brand == 'alphapolis' else [], 'isbn_known_count': 0, 'volume_known_count': 0}
            if (any(frame.get(k) != v for k, v in expected_frame.items())
                    or frame.get('next_terminal_observed') is not True or frame.get('card_frame_terminal') is not True
                    or len(frame['source_pages']) != last):
                raise ValueError('Alpha finite brand frame or sparse metadata checkpoint differs')
            indexed, duplicate_ids, occurrences = {}, [], 0
            for number, receipt in enumerate(frame['source_pages'], 1):
                url = _url(entry, number)
                rows = _page(_source(path.parent, receipt, url), receipt, brand, label, entry, total, last, number)
                occurrences += len(rows)
                for row in rows:
                    identity = row['publisher_reference_id']
                    if identity in indexed:
                        old = indexed[identity]
                        ignore = {'source_evidence', 'occurrence_count'}
                        if {k: v for k, v in old.items() if k not in ignore} != {k: v for k, v in row.items() if k not in ignore}:
                            raise ValueError('Alpha duplicate opaque reference has conflicting metadata')
                        old['source_evidence'].extend(row['source_evidence'])
                        old['occurrence_count'] += 1
                        duplicate_ids.append(identity)
                    else:
                        indexed[identity] = row
            unknown_authors = sum(not any(c['role'] == 'author' for c in row['credits']) for row in indexed.values())
            if (occurrences != total or len(indexed) != unique or duplicate_ids != expected_frame['duplicates']
                    or unknown_authors != expected_frame['unknown_author_reference_count'] or all_ids.intersection(indexed)):
                raise ValueError('Alpha raw unique/duplicate/unknown-author totals differ')
            all_ids.update(indexed)
            output.extend(indexed.values())
        if len(output) != 2638:
            raise ValueError('Alpha combined unique reference count differs')
        return output
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Alpha source evidence schema cannot be reproduced') from exc
