"""Replay EarthStar's explicit parent/book-list groups as publisher references.

The module name does not assert story-series identity. Index and parent sources
are pinned independently; children are links in the parent, never fetched. A
brand, ABJ mark or copyright notice supplies no imprint or reuse permission.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _decode, _read, _relative,
    verify_publisher_references,
)

_PARENT = re.compile(r'https://earthstar-novel\.com/book/detail/([A-Za-z0-9_-]+)/')
_CHILD = re.compile(r'https://earthstar-novel\.com/vol/detail/([A-Za-z0-9_-]+)/')
_RIGHTS = {'bibliographic_reference_only': True, 'publisher_prose_ai_input': False,
           'images_fetched_or_reused': False, 'raw_public_redistribution': False,
           'separate_content_licence': 'not_granted_or_inferred'}


def _json(root, name, expected):
    try:
        return json.loads(_read(_relative(root, name), expected, MAX_METADATA_BYTES))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('EarthStar JSON cannot be reproduced') from exc


def _one(values, field):
    if len(values) != 1:
        raise ValueError('EarthStar field cardinality differs: ' + field)
    return values[0]


def _children(node, *, tag=None, cls=None):
    return [p for p in node.parts if isinstance(p, _Node)
            and (tag is None or p.tag == tag) and (cls is None or cls in p.attrs.get('class', '').split())]


def _literal(node):
    if node.tag in {'script', 'style'}:
        return ''
    if node.tag == 'br':
        return '\n'
    return ''.join(_literal(p) if isinstance(p, _Node) else p for p in node.parts)


def _normalized_credits(values):
    return [{'role': c['role'], 'name': c['name']} for c in values]


def _source(base, receipt, expected_url):
    if receipt.get('source_url') != expected_url or not _PARENT.fullmatch(expected_url):
        raise ValueError('EarthStar parent source URL is outside fixed own scope')
    statuses = [receipt[key] for key in ('http_status', 'status') if key in receipt]
    if not statuses or any(v != 200 for v in statuses):
        raise ValueError('EarthStar parent receipt status differs')
    if receipt.get('final_url', expected_url) != expected_url or receipt.get('location'):
        raise ValueError('EarthStar parent final URL differs')
    if receipt.get('raw_path_relative_to') != 'data/light-novels-v1':
        raise ValueError('EarthStar parent evidence root differs')
    content_type = receipt.get('content_type') or ''
    if not content_type.lower().startswith('text/html'):
        raise ValueError('EarthStar parent source type is not HTML')
    raw = _read(_relative(base, receipt.get('raw_path')), receipt.get('sha256'), MAX_SOURCE_BYTES)
    if receipt.get('byte_length') != len(raw):
        raise ValueError('EarthStar declared source size differs')
    return _DOM(_decode(raw, content_type)).root


def _book(card, number, parent_id, parent_url, receipt):
    anchor = _one(card.nodes(tag='a', cls='c-card01__main'), 'own child main anchor')
    url = anchor.attrs.get('href', '')
    identity = _CHILD.fullmatch(url)
    if not identity:
        raise ValueError('EarthStar child reference URL differs')
    heading = _one(anchor.nodes(cls='c-card01__head'), 'complete child title')
    title, title_display = _literal(heading).strip(), heading.text()
    if not title or not title_display:
        raise ValueError('EarthStar complete child title is empty')
    info = _one(card.nodes(cls='c-card01__info'), 'own child credits')
    credits = []
    for item in info.nodes(cls='c-card01__info-item'):
        role_node = _one(item.nodes(cls='c-card01__info-head'), 'explicit role label')
        name_node = _one(item.nodes(cls='c-card01__info-txt'), 'explicit creator name')
        role, name = role_node.text(), name_node.text()
        if not role or not name:
            raise ValueError('EarthStar explicit role or creator name empty')
        credits.append({'role_display': role, 'role': {'著者：': 'author', 'イラスト：': 'illustrator'}.get(role, 'unknown'), 'name': name})
    if not any(c['role'] == 'author' for c in credits):
        raise ValueError('EarthStar explicit child author role missing')
    locator = f'.c-book-archive01[1] .c-card01[{number}] title/explicit credits/a[href]'
    stored = {'publisher_child_reference_id': 'earthstar-volume-reference:' + identity[1],
        'source_url': url, 'title_literal': title_display, 'credits': credits,
        'isbn': None, 'label': None, 'source_novel_or_comic_form': 'unknown',
        'publisher_parent_reference_id': parent_id, 'parent_work_reference_url': parent_url,
        'publisher_group_membership_explicit': True, 'story_series_asserted': False,
        'source_locator': locator, 'child_detail_fetched': False}
    output = {'official_child_reference_id': identity[1], 'source_url': url, 'title': title,
        'credits': _normalized_credits(credits), 'explicit_role_credit_literals': credits,
        'publisher': None, 'label': None, 'form': None, 'isbn': None,
        'source_selector': locator, 'publisher_group_membership_explicit': True,
        'story_series_asserted': False, 'edition_identity': 'unassessed',
        'source_evidence': {'source_url': parent_url, 'raw_sha256': receipt['sha256'], 'locator': locator}}
    return stored, output


def _group(tree, index, receipt):
    parent_id, parent_url = index['publisher_reference_id'], index['source_url']
    identity = _PARENT.fullmatch(parent_url)
    if not identity or parent_id != 'earthstar-work:' + identity[1]:
        raise ValueError('EarthStar opaque parent identity differs from index')
    canonical = [n.attrs.get('href') for n in tree.nodes(tag='link') if n.attrs.get('rel') == 'canonical']
    if canonical != [parent_url]:
        raise ValueError('EarthStar canonical parent identity missing or different')
    heading = _one(tree.nodes(tag='h1', cls='c-book-intro01__head'), 'own parent h1')
    title, display = _literal(heading).strip(), heading.text()
    if not title or not display:
        raise ValueError('EarthStar parent h1 empty')
    # A direct named book-list section contains this group, not sidebars, related
    # products, preview labels or other nested sections on the same HTML page.
    section = _one(tree.nodes(tag='section', cls='c-book-archive01'), 'own book-list section')
    inner = _one(_children(section, cls='c-book-archive01__inner'), 'own book-list inner')
    label = _one(_children(inner, tag='h2', cls='c-head01'), 'explicit book-list heading')
    if label.text() != '書籍一覧':
        raise ValueError('EarthStar explicit book-list heading differs')
    listing = _one(_children(inner, cls='c-card01-list'), 'direct own book-list boundary')
    cards = _children(listing, cls='c-card01')
    if not cards or len(cards) > 1000:
        raise ValueError('EarthStar child group size differs')
    stored_books, books, seen = [], [], set()
    for number, card in enumerate(cards, 1):
        stored, book = _book(card, number, parent_id, parent_url, receipt)
        if book['source_url'] in seen:
            raise ValueError('EarthStar duplicate/conflicting child reference')
        seen.add(book['source_url'])
        stored_books.append(stored)
        books.append(book)
    authors = [c for c in index['credits'] if c['role'] == 'author']
    if not authors:
        raise ValueError('EarthStar explicit index parent author role missing')
    result = {'kind': 'official_work_group_reference', 'reference_id': parent_id,
        'official_work_reference_id': identity[1], 'source_url': parent_url, 'title': title,
        'index_title': index['title'], 'index_author_credits': authors,
        'index_author_inclusion_basis': 'explicit_author_role_in_own_official_index_card_not_inferred_from_children',
        'publisher': None, 'label': None, 'form': None, 'isbn': None,
        'books': books, 'book_group_evidence': {'heading_literal': '書籍一覧',
            'group_selector': 'section.c-book-archive01 > .c-book-archive01__inner > .c-card01-list',
            'book_count': len(books), 'membership_basis': 'explicit_child_cards_inside_this_parent_book_list'},
        'story_series_asserted': False, 'catalogue_identity': 'unassessed_no_automatic_original_or_edition_identity',
        'rights': dict(_RIGHTS), 'source_evidence': [*index['source_evidence'],
            {'source_url': parent_url, 'raw_sha256': receipt['sha256'],
             'locator': 'h1.c-book-intro01__head; section.c-book-archive01 own book-list'}]}
    return display, stored_books, result


def _check_projection(value, index, receipt, display, books):
    expected = {'publisher_parent_reference_id': index['publisher_reference_id'], 'source_unit': 'publisher_work_reference',
        'source_url': index['source_url'], 'parent_h1_title_literal': display,
        'parent_title_differs_from_index_literal': display != index['title'], 'direct_parent_author_credits': None,
        'index_parent_title_literal': index['title'], 'source_snapshot': receipt,
        'isbn': None, 'label': None, 'source_novel_or_comic_form': 'unknown', 'story_series_asserted': False,
        'explicit_child_references': books, 'child_reference_count': len(books),
        'duplicate_child_reference_ids': [], 'conflicting_child_reference_ids': [], 'holds': []}
    if any(value.get(key) != wanted for key, wanted in expected.items()):
        raise ValueError('EarthStar parent/member projection differs from original raw')
    if _normalized_credits(value.get('index_parent_explicit_credits', [])) != index['credits']:
        raise ValueError('EarthStar index author projection differs')
    evidence = value.get('index_source_snapshot', {})
    if not any(e['source_url'] == evidence.get('source_url') and e['raw_sha256'] == evidence.get('sha256') for e in index['source_evidence']):
        raise ValueError('EarthStar index source projection binding differs')


def verify_earthstar_series(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Replay explicit publisher work groups, never original story-series identity.

    ``manifest_path`` is a private proof in ``earthstar-sourceframes-v1``; it pins
    the existing index manifest and parent terminal manifest separately. Every
    receipt, individual projection and aggregate projection is checked against
    raw-derived fields. No current directory, cache, network or DB is consulted.
    """
    path = Path(manifest_path).resolve()
    root, base = path.parent, path.parent.parent
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if (root.name != 'earthstar-sourceframes-v1' or base.name != 'light-novels-v1'
                or proof.get('schema_version') != 'earthstar-parent-group-proof-v1'):
            raise ValueError('EarthStar pinned manifest location or schema differs')
        parent_count, child_count = proof.get('expected_parent_references'), proof.get('expected_child_references')
        if (type(parent_count) is not int or not 1 <= parent_count <= 192
                or type(child_count) is not int or not 1 <= child_count <= 20000):
            raise ValueError('EarthStar finite proof counts differ')
        index_path = _relative(root, proof['index_manifest_path'])
        index_manifest = _json(root, proof['index_manifest_path'], proof['index_manifest_sha256'])
        index = verify_publisher_references('earthstar', index_path, proof['index_manifest_sha256'])
        if len(index) != parent_count:
            raise ValueError('EarthStar complete index identity count differs')
        terminal = _json(root, proof['terminal_manifest_path'], proof['terminal_manifest_sha256'])
        if (terminal.get('schema_version') != 'earthstar-parent-details-progress-v1'
                or terminal.get('status') != 'terminal_all_fixed_parents_attempted'
                or terminal.get('finite_parent_requests_complete') is not True
                or terminal.get('all_parent_projections_complete') is not True
                or terminal.get('failure_or_holds') != [] or terminal.get('story_series_asserted') is not False
                or terminal.get('raw_child_or_preview_or_image_or_store_requests') != 0
                or terminal.get('conditions') != _RIGHTS):
            raise ValueError('EarthStar parent terminal scope or reference-only conditions differ')
        for key in ('fixed_parent_count', 'attempted_parents', 'successful_parent_projections', 'projected_parent_count'):
            if terminal.get(key) != parent_count:
                raise ValueError('EarthStar terminal parent count differs')
        declared_roster = str(terminal.get('roster_path', '')).replace('\\', '/')
        index_roster = _relative(index_path.parent, index_manifest['roster_path'])
        wanted_roster = 'data/light-novels-v1/' + index_roster.relative_to(base).as_posix()
        if terminal.get('roster_sha256') != index_manifest['roster_sha256'] or declared_roster != wanted_roster:
            raise ValueError('EarthStar terminal/index fixed roster binding differs')
        projections = _json(base, terminal['projection_path'], terminal['projection_sha256'])
        parents = terminal.get('parents', [])
        if len(projections) != parent_count or len(parents) != parent_count:
            raise ValueError('EarthStar complete parent projection set differs')
        result, seen = [], set()
        for parent, declared, index_row in zip(parents, projections, index):
            if (parent.get('parent_reference_id') != index_row['publisher_reference_id']
                    or parent.get('source_url') != index_row['source_url'] or parent.get('holds') != []
                    or index_row['source_url'] in seen):
                raise ValueError('EarthStar complete parent/index identity binding differs')
            seen.add(index_row['source_url'])
            receipt = _json(base, parent['receipt_path'], parent['receipt_sha256'])
            individual = _json(base, parent['projection_path'], parent['projection_sha256'])
            if individual != declared:
                raise ValueError('EarthStar individual/aggregate projection differs')
            tree = _source(base, receipt, index_row['source_url'])
            display, books, group = _group(tree, index_row, receipt)
            _check_projection(declared, index_row, receipt, display, books)
            if parent.get('child_reference_count') != len(books):
                raise ValueError('EarthStar terminal group count differs')
            result.append(group)
        books = [book for group in result for book in group['books']]
        if (len(books) != child_count or terminal.get('child_reference_occurrences') != len(books)
                or terminal.get('unique_child_reference_urls') != len({b['source_url'] for b in books})):
            raise ValueError('EarthStar complete child URL/reference count differs')
        return result
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('EarthStar evidence schema cannot be reproduced') from exc
