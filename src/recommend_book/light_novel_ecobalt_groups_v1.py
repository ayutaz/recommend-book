"""Replay e-Cobalt's saved author-index heading intervals as listing groups.

Names remain author-index displays, not individually asserted author roles.
Native product keys are opaque. No story, novel, edition or imprint identity
is inferred, including for headings also cited by the separate Cobalt8 source.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _decode, _read, _relative,
)


_PAGE = 'https://ebooks.shueisha.co.jp/cobalt/'
_ROBOTS = 'https://ebooks.shueisha.co.jp/robots.txt'
_BOOK = re.compile(r'https://ebooks\.shueisha\.co\.jp/detail\.php\?book=([A-Za-z0-9]+)')
_FRAME_ID = 'shueisha-ebooks-cobalt-author-list-v1'
_CAPTION = '著者50音別書籍リスト'
_CAPTION_LOCATOR = 'h2 img[alt="著者50音別書籍リスト"]'
_PAGE_TITLE = '集英社eコバルト文庫 | e!集英社 | 集英社の電子書籍のプロモーションHPです'
_LOG_RIGHTS = 'reference/existence only; synopsis/images excluded from AI input and redistribution'
_FRAME_RIGHTS = 'Reference/existence only; tooltip synopsis and images excluded from output'
_LEDGER_RIGHTS = 'reference/existence only; publisher synopsis, prose and images excluded from AI/public exports'
_USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
          'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False}
_CORRECTION = {'legacy_selector': 'div.list dl dd; dd span em',
               'verified_selector': '#leftBlock > div.serchKana > div.line > dl[id] > dd',
               'legacy_selector_is_authoritative': False}
_HOLDS = ['All series references are local snapshot assertions, not invented official series IDs',
          'Publisher index grouping candidates require confirmation before catalogue identity or volume linking',
          'Current e-distribution list includes neighboring historical labels; per-work LN/imprint not inferred',
          'ISBN absent from index: do not infer ISBN from ebook product IDs']


def _only(nodes, field):
    if len(nodes) != 1:
        raise ValueError('eCobalt field cardinality differs: ' + field)
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


def _load(root, ref):
    return json.loads(_read(_relative(root, ref['path']), ref['sha256'], MAX_METADATA_BYTES))


def _evidence(receipt, selector):
    return {'source_url': receipt['source_url'], 'source_sha256': receipt['sha256'],
            'raw_path': receipt['raw_path'], 'retrieved_at': receipt['retrieved_at'], 'selector': selector}


def _source(root, receipt, url, media_type):
    statuses = [receipt[k] for k in ('status', 'http_status') if k in receipt]
    if (receipt.get('source_url') != url or not statuses or any(type(s) is not int or s != 200 for s in statuses)
            or receipt.get('final_url', url) != url or receipt.get('location') or receipt.get('error')
            or receipt.get('size_cap_exceeded') or receipt.get('http_redirects_followed', 0) != 0
            or receipt.get('http_retry_count', 0) != 0 or receipt.get('rights') != _LOG_RIGHTS
            or (receipt.get('content_type') or '').split(';')[0].strip().lower() != media_type
            or not isinstance(receipt.get('retrieved_at'), str) or not receipt['retrieved_at']):
        raise ValueError('eCobalt transport or bounded metadata rights differ')
    raw = _read(_relative(root, receipt['raw_path']), receipt['sha256'], MAX_SOURCE_BYTES)
    if receipt.get('byte_length') != len(raw):
        raise ValueError('eCobalt receipt size differs')
    return raw


def _parse(raw, receipt):
    tree = _DOM(_decode(raw, receipt['content_type'])).root
    if _literal(_only(tree.nodes(tag='title'), 'page title')).strip() != _PAGE_TITLE:
        raise ValueError('eCobalt site context differs')
    captions = [img for h in tree.nodes(tag='h2') for img in h.nodes(tag='img') if img.attrs.get('alt') == _CAPTION]
    _only(captions, 'global author-index caption')
    body = _only([n for n in tree.nodes(tag='body') if n.attrs.get('id') == 'kobalt'], 'own body')
    wrap = _only([n for n in _children(body, 'div') if n.attrs.get('id') == 'wrap'], 'own wrap')
    contents = _only([n for n in _children(wrap, 'div') if n.attrs.get('id') == 'contentsBlock'], 'own contents')
    left = _only([n for n in _children(contents, 'div') if n.attrs.get('id') == 'leftBlock'], 'own left list')
    kanas = _children(left, 'div', 'serchKana')
    dls = [dl for kana in kanas for line in _children(kana, 'div', 'line') for dl in _children(line, 'dl')]
    native_dls = [n for n in tree.nodes(tag='dl') if n.attrs.get('id', '').startswith('word_')]
    if not dls or dls != native_dls:
        raise ValueError('eCobalt kana DL scope differs from corrected DOM selector')
    groups, old_groups, books, ungrouped, seen_ids, seen_keys, seen_dls = [], [], [], [], set(), set(), set()
    for dl in dls:
        dlid = dl.attrs.get('id', '')
        if not re.fullmatch(r'word_[^\s#\[\]().>:"\']+', dlid) or dlid in seen_dls:
            raise ValueError('eCobalt DL identifier is absent or ambiguous')
        seen_dls.add(dlid)
        dds = _children(dl, 'dd')
        if not dds or dds != dl.nodes(tag='dd'):
            raise ValueError('eCobalt DD interval structure differs')
        active = old_active = None
        def close(selector, reason):
            if active is not None:
                active['group_end'] = {'selector': selector, 'reason': reason}
        for number, dd in enumerate(dds, 1):
            selector = f'dl#{dlid} dd:nth-of-type({number})'
            ems = dd.nodes(tag='em')
            if ems:
                close(selector, 'next_explicit_heading')
                b = _only(_children(dd, 'b'), 'heading name display')
                span = _only(_children(dd, 'span'), 'heading span')
                em = _only(_children(span, 'em'), 'own heading')
                if ems != [em] or dd.nodes(tag='a') or dd.attrs.get('id') or em.attrs.get('id'):
                    raise ValueError('eCobalt heading is not the native unlinked display field')
                heading, display = _literal(em).strip(), _literal(b).strip()
                match = re.fullmatch(r'＜(.+)シリーズ＞', heading)
                if not match or not display:
                    raise ValueError('eCobalt complete explicit heading/display missing')
                title = match[1]
                loc = selector+' span em'
                old_heading, old_display = em.text(), b.text()
                old_title = re.fullmatch(r'＜(.+)シリーズ＞', old_heading)
                if not old_title:
                    raise ValueError('eCobalt legacy heading cannot be reproduced')
                old_active = {'reference_id': 'publisher-series-heading:'+hashlib.sha256((_PAGE+'\n'+receipt['sha256']+'\n'+loc).encode()).hexdigest()[:24],
                    'kind': 'publisher_series_group', 'is_story_series': 'unknown_requires_group_review_including_anthologies',
                    'source_url': _PAGE, 'publisher_series_id': None, 'official_series_url': None, 'title': old_title[1],
                    'publisher_series_heading': old_heading, 'title_basis': 'explicit_official_em_series_heading_not_volume_stripping',
                    'publisher': '集英社', 'label_scope': '集英社eコバルト文庫 catalogue (includes Super Fantasy reissues; item imprint unverified)',
                    'index_id': _FRAME_ID, 'author_credit': '著者：'+old_display,
                    'credits': [{'role': '著者', 'name': old_display, 'role_basis': 'official 著者50音別書籍リスト author b field'}],
                    'identity_to_catalogue': 'unassessed', 'light_novel_status': 'unknown_per_work_imprint_not_inferred',
                    'index_source_url': _PAGE, 'index_source_sha256': receipt['sha256'], 'index_retrieved_at': receipt['retrieved_at'],
                    'source_evidence': _evidence(receipt, loc), 'explicit_memberships': [], 'publisher_group_locator': loc,
                    'local_assertion_id_not_official_series_id': True}
                old_groups.append(old_active)
                key = {'provider': 'ecobalt', 'source_page': _PAGE, 'source_scope': 'author_index_heading_interval',
                       'dl_id': dlid, 'heading_literal': heading, 'heading_author_display': display}
                serialized = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                if serialized in seen_keys:
                    raise ValueError('eCobalt scoped heading key is ambiguous')
                seen_keys.add(serialized)
                active = {'kind': 'official_publisher_listing_group_reference', 'source_unit': 'publisher_work_group',
                    'reference_id': 'ecobalt-heading-local:'+hashlib.sha256(serialized.encode()).hexdigest(),
                    'reference_id_kind': 'local_hash_of_scoped_heading_fields', 'reference_id_is_official': False,
                    'local_reference_key': key, 'independent_official_group_id': None, 'title': title,
                    'heading_literal': heading, 'heading_author_display': display, 'heading_author_display_locator': selector+' > b',
                    'role_context': {'literal': _CAPTION, 'locator': _CAPTION_LOCATOR, 'scope': 'author_index_display_only'},
                    'start_selector': selector, 'source_url': _PAGE, 'authors': None, 'publisher': None, 'label': None,
                    'form': None, 'isbn': None, 'official_lightnovel_tag': None, 'light_novel_status': 'unknown',
                    'story_series_asserted': False, 'story_series_identity_asserted': False, 'original_work_identity_asserted': False,
                    'independent_volume_numbers_inferred': False, 'paper_electronic_same_edition_asserted': False,
                    'site_context': {'title_literal': _PAGE_TITLE, 'title_locator': 'title', 'individual_book_label_asserted': False},
                    'members': [], 'parent_page_membership_complete': True, 'global_membership_complete': None,
                    'scope': 'saved_author_index_contiguous_heading_and_same_display_interval_only',
                    'source_evidence': [{'source_url': _PAGE, 'raw_sha256': receipt['sha256'],
                                         'locator': loc+'; '+selector+' > b; '+_CAPTION_LOCATOR}],
                    'usage_permissions': dict(_USAGE)}
                groups.append(active)
                continue
            anchor = _only(_children(dd, 'a'), 'own product link')
            if dd.nodes(tag='a') != [anchor]:
                raise ValueError('eCobalt product link structure differs')
            b, span = _only(_children(anchor, 'b'), 'product name display'), _only(_children(anchor, 'span'), 'complete product title')
            url = urljoin(_PAGE, anchor.attrs.get('href', ''))
            match = _BOOK.fullmatch(url)
            display, title = _literal(b).strip(), _literal(span).strip()
            if not match or not display or not title or match[1] in seen_ids:
                raise ValueError('eCobalt opaque product URL/title is absent or duplicate')
            seen_ids.add(match[1])
            old_display = b.text()
            belongs = active is not None and old_display == old_active['credits'][0]['name']
            if not belongs:
                close(selector, 'author_display_changed')
                active = old_active = None
            book = {'kind': 'book', 'source_url': url, 'publisher_book_id': match[1], 'title': span.text(), 'isbn': None,
                'publisher': '集英社', 'label_scope': '集英社eコバルト文庫 catalogue', 'credits': [{'role': '著者', 'name': old_display}],
                'source_evidence': _evidence(receipt, selector+' a > b / span'),
                'membership_basis': 'adjacent_after_explicit_series_heading_within_same_dl_and_author' if belongs else None,
                'membership_status': 'publisher_index_grouping_candidate_no_invented_series_url' if belongs else 'ungrouped'}
            books.append(book)
            if belongs:
                old_active['explicit_memberships'].append(book)
                active['members'].append({'publisher_reference_id': 'shueisha-ebook:'+match[1], 'opaque_book_url_key': match[1],
                    'source_unit': 'publisher_product_reference', 'source_url': url, 'title': title,
                    'author_display': display, 'credits': [{'role': 'unknown', 'name': display}],
                    'credit_literals': [{'name': display, 'entry_role_literal': None, 'role_context_literal': _CAPTION,
                        'role_scope': 'author_index_display_only', 'role_context_locator': _CAPTION_LOCATOR,
                        'source_selector': selector+' a > b'}],
                    'publisher': None, 'label': None, 'label_literals': [], 'form': None, 'isbn': None,
                    'official_lightnovel_tag': None, 'media_references': None, 'story_series_asserted': False,
                    'original_work_identity_asserted': False, 'paper_electronic_same_edition_asserted': False,
                    'source_selector': selector, 'source_evidence': [{'source_url': _PAGE,
                        'raw_sha256': receipt['sha256'], 'locator': selector+' a[href] > b / span'}]})
            else:
                ungrouped.append(book)
        close('dl#'+dlid, 'dl_end')
    if any(not g['members'] or 'group_end' not in g for g in groups):
        raise ValueError('eCobalt heading interval is empty or unclosed')
    return groups, old_groups, books, ungrouped


def verify_ecobalt_groups(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Return all heading groups; authenticate the separate ungrouped partition."""
    path = Path(manifest_path).resolve()
    root = path.parent
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if (proof.get('schema_version') != 'shueisha-ecobalt-publisher-groups-proof-v1'
                or proof.get('usage_scope') != _USAGE or proof.get('selector_correction') != _CORRECTION):
            raise ValueError('eCobalt proof/rights/corrected DOM contract differs')
        counts = proof['expected_counts']
        if (set(counts) != {'groups', 'products', 'members', 'ungrouped'}
                or any(type(counts[k]) is not int or not 0 <= counts[k] <= cap
                       for k, cap in [('groups', 211), ('products', 1811), ('members', 1487), ('ungrouped', 324)])
                or not counts['groups'] or counts['products'] != counts['members']+counts['ungrouped']):
            raise ValueError('eCobalt finite frame bounds differ')
        frame = _load(root, proof['source_manifest'])
        handoff, declared_books, declared_ungrouped = [_load(root, proof[k]) for k in ('source_handoff', 'source_books', 'source_ungrouped')]
        ledger = _load(root, proof['rights_ledger'])
        if ledger.get('version') != 1 or ledger.get('rights') != _LEDGER_RIGHTS:
            raise ValueError('eCobalt bounded rights ledger differs')
        entry = _only([e for e in ledger['frames'] if e.get('manifest') == proof['source_manifest']['path']], 'rights frame')
        expected_entry = {'manifest': proof['source_manifest']['path'], 'manifest_sha256': proof['source_manifest']['sha256'],
            'handoff': proof['source_handoff']['path'], 'handoff_sha256': proof['source_handoff']['sha256'],
            'publisher_series_groups': counts['groups'], 'is_story_series': 'unknown_requires_group_review',
            'books': counts['products'], 'grouped_membership_candidates': counts['members'], 'ungrouped_books': counts['ungrouped'],
            'finite_edistribution_frame_complete': True, 'official_series_id': None}
        if entry != expected_entry:
            raise ValueError('eCobalt rights frame/pins differ')
        logref = proof['source_receipts']
        logs = [json.loads(line) for line in _read(_relative(root, logref['path']), logref['sha256'], MAX_METADATA_BYTES).splitlines() if line.strip()]
        receipt = _only([r for r in logs if r.get('source_url') == _PAGE], 'own page receipt')
        robot = _only([r for r in logs if r.get('source_url') == _ROBOTS], 'own robots receipt')
        parser = RobotFileParser()
        parser.parse(_source(root, robot, _ROBOTS, 'text/plain').decode('utf-8').splitlines())
        if not parser.can_fetch('recommend-book-research/1.0', _PAGE):
            raise ValueError('eCobalt saved robots disallow the listing')
        raw = _source(root, receipt, _PAGE, 'text/html')
        expected_frame = {'frame_id': _FRAME_ID, 'kind': 'finite_official_author_book_list_with_explicit_series_headings',
            'source_evidence': _evidence(receipt, _CORRECTION['legacy_selector']), 'raw_sha256_verified': True,
            'complete_within_single_page': True, 'publisher_reported_total': None,
            'series_heading_count': counts['groups'], 'parsed_series_rows': counts['groups'], 'books': counts['products'],
            'unique_book_urls': counts['products'], 'book_memberships_under_explicit_heading': counts['members'],
            'ungrouped_books': counts['ungrouped'], 'whole_historical_cobalt_complete': False,
            'official_series_ids_available': False, 'holds': _HOLDS, 'rights': _FRAME_RIGHTS}
        if frame != expected_frame:
            raise ValueError('eCobalt source frame/selector/finite scope differs')
        groups, old_groups, books, ungrouped = _parse(raw, receipt)
        actual = {'groups': len(groups), 'products': len(books), 'members': sum(len(g['members']) for g in groups), 'ungrouped': len(ungrouped)}
        if (actual != counts or old_groups != handoff or books != declared_books or ungrouped != declared_ungrouped):
            raise ValueError('eCobalt raw heading/product/role/selector/partition projection differs')
        for g in groups:
            g['frame_member_count'] = len(g['members'])
            g['frame_partition'] = {'product_references': counts['products'], 'grouped_members': counts['members'], 'ungrouped_products': counts['ungrouped']}
            g['ungrouped_projection_pin'] = dict(proof['source_ungrouped'])
        return groups
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('eCobalt listing evidence cannot be reproduced') from exc
