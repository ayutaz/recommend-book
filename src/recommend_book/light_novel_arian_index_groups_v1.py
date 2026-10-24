"""Authenticate exact saved Arian index references, without inventing children.

The selected article's h3, native series URL and index-credit display are the
only assertions. A complete listing card never establishes product membership,
author identity, a light novel, an edition, or original story-series identity.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _decode, _read, _relative,
)


_PAGE = 'https://arianrose.jp/series/'
_INDEX = 'arianrose-series'
_PARENT = re.compile(r'https://arianrose\.jp/novel/\?series_id=[0-9]+')
_FRAME_IDS = {_INDEX, 'hj-bunko-series', 'hj-novels-series', 'ga-ebook-series'}
_RIGHTS = 'Reference-only publisher page; no synopsis, image or AI-input reuse inferred'
_USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
          'prose_ai_permission': False, 'images_permission': False,
          'raw_redistribution_permission': False}
_ROBOTS = {'status': 'not_saved_in_existing_source_evidence', 'permission_inferred': False}
_SCOPE = 'selected_native_index_cards_only'
_ROLE = re.compile(r'(キャラクター原案|キャラクターデザイン|イラスト|原作|原案|監修|翻訳|著者|著|編)[：:]')


def _one(items, field):
    if len(items) != 1:
        raise ValueError('Arian own index card field absent or ambiguous: ' + field)
    return items[0]


def _load(root, pin):
    return json.loads(_read(_relative(root, pin['path']), pin['sha256'], MAX_METADATA_BYTES))


def _ids(value):
    if (not isinstance(value, list) or any(not isinstance(v, str)
            or not re.fullmatch(r'publisher-series:[0-9a-f]{24}', v) for v in value)
            or len(value) != len(set(value))):
        raise ValueError('Arian selected/covered reference IDs differ')
    return set(value)


def _text(node):
    if any(n.tag in {'img', 'input', 'textarea', 'select', 'button'} or 'hidden' in n.attrs
           or n.attrs.get('aria-hidden') == 'true' for n in [node, *node.nodes()]):
        raise ValueError('Arian index text includes hidden/form/image content')
    value = node.text()
    if not value or len(value) > 4096:
        raise ValueError('Arian complete index text is absent or oversized')
    return value


def _source(root, receipt):
    if (receipt.get('source_url') != _PAGE or receipt.get('final_url') != _PAGE
            or receipt.get('http_status') != 200 or receipt.get('rights') != _RIGHTS
            or any(receipt.get(k) for k in ('location', 'error', 'size_cap_exceeded',
                   'redirects_followed', 'http_redirects_followed', 'retry_count', 'http_retry_count'))
            or not re.match(r'^text/html(?:\s*;|$)', receipt.get('content_type', ''), re.I)
            or type(receipt.get('byte_length')) is not int
            or not 0 < receipt['byte_length'] <= MAX_SOURCE_BYTES):
        raise ValueError('Arian own index receipt URL/status/rights/size differs')
    data = _read(_relative(root, receipt['raw_path']), receipt['sha256'], MAX_SOURCE_BYTES)
    if len(data) != receipt['byte_length']:
        raise ValueError('Arian index declared byte length differs')
    return _DOM(_decode(data, receipt['content_type'])).root


def _credit_literals(display):
    """Keep literal roles and names in this index card, without author mapping."""
    if display is None:
        return []
    markers = list(_ROLE.finditer(display))
    if not markers:
        return [{'role_literal': None, 'name': display, 'role_scope': 'publisher_index_card'}]
    result = []
    prefix = display[:markers[0].start()].strip()
    if prefix:
        result.append({'role_literal': None, 'name': prefix, 'role_scope': 'publisher_index_card'})
    for number, marker in enumerate(markers):
        end = markers[number+1].start() if number+1 < len(markers) else len(display)
        name = display[marker.end():end].strip()
        if not name:
            raise ValueError('Arian explicit index role has no literal name/display')
        result.append({'role_literal': marker[1], 'name': name, 'role_scope': 'publisher_index_card'})
    return result


def _cards(tree, selected_urls):
    found = {}
    for article in tree.nodes(tag='article'):
        urls = {a.attrs.get('href') for a in article.nodes(tag='a')
                if _PARENT.fullmatch(a.attrs.get('href', ''))}
        if not urls & selected_urls:
            continue
        url = _one(list(urls), 'own native group URL')
        if url in found or article.nodes(tag='article'):
            raise ValueError('Arian native card is duplicate or nested')
        title = _text(_one(article.nodes(tag='h3'), 'complete h3 title'))
        displays = article.nodes(tag='p', cls='author')
        display = _text(_one(displays, 'index credit display')) if displays else None
        found[url] = {'title': title, 'credit_display': display, 'credits': _credit_literals(display)}
    if set(found) != selected_urls:
        raise ValueError('Arian exact selected own article set differs')
    return found


def verify_arian_index_groups(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Replay one pinned index and the exact selected49 native references only."""
    path = Path(manifest_path).resolve()
    root = path.parent
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if (proof.get('schema_version') != 'arian-selected-index-groups-proof-v1'
                or proof.get('usage_scope') != _USAGE or proof.get('robots_evidence') != _ROBOTS
                or proof.get('source_rights_literal') != _RIGHTS):
            raise ValueError('Arian proof schema/rights/missing robots context differs')
        source_root = _relative(root, proof['source_root'])
        counts = proof['expected_counts']
        caps = {'index_groups': 114, 'selected_groups': 49, 'selected_index_pages': 1, 'index_pages': 1}
        if (set(counts) != set(caps)
                or any(type(counts[k]) is not int or not 1 <= counts[k] <= cap for k, cap in caps.items())):
            raise ValueError('Arian bounded selected index counts differ')
        selected, covered = [_ids(proof[k]) for k in ('selected_reference_ids', 'covered_reference_ids')]
        if len(selected) != counts['selected_groups'] or selected & covered or len(covered) > 917:
            raise ValueError('Arian selected and previously covered scopes differ')
        report = _load(root, proof['coverage_report'])
        reported = [x for x in report['rows'] if x.get('index_id') == _INDEX]
        by_id = {x['reference_id']: x for x in reported}
        if (len(reported) != counts['index_groups'] or len(by_id) != len(reported)
                or {x['reference_id'] for x in reported if x['status'] == 'uncovered'} != selected
                or any(x['status'] not in {'covered', 'uncovered'} for x in reported)
                or {x['reference_id'] for x in reported if x['status'] == 'covered'} - covered
                or _ids(report['covered_reference_ids']) != covered
                or not selected <= _ids(report['uncovered_reference_ids'])
                or report['provider_counts'][_INDEX] != {'source_references': counts['index_groups'],
                    'covered': counts['index_groups']-counts['selected_groups'],
                    'uncovered': counts['selected_groups'], 'conflicts': 0}):
            raise ValueError('Arian selection differs from pinned uncovered source partition')
        candidates = _load(root, proof['source_candidates'])
        own_candidates = [x for x in candidates['groups'] if x.get('provider_profile_candidate') == 'arianrose-index-only']
        candidate_by_id = {x['reference_id']: x for x in own_candidates}
        if (candidates.get('schema_version') != 'hjn39-arian49-native-index-only-candidates-v1'
                or _ids(candidates['selected_by_provider'][_INDEX]) != selected
                or len(candidate_by_id) != len(own_candidates) or set(candidate_by_id) != selected):
            raise ValueError('Arian exact candidate source scope differs')
        frames = _load(root, proof['source_frames'])
        if not isinstance(frames, list) or len(frames) != 4 or {x['id'] for x in frames} != _FRAME_IDS:
            raise ValueError('Arian original four-frame source metadata differs')
        frame = _one([x for x in frames if x['id'] == _INDEX], 'own source frame')
        if (frame.get('url') != _PAGE or frame.get('kind') != 'series_index'
                or frame.get('index_pages_complete') is not True
                or frame.get('completion_reason') != 'all_linked_pages_exhausted'
                or frame.get('historical_entire_label_complete') is not False
                or frame.get('reference_series_count') != counts['index_groups']
                or len(frame['pages']) != counts['index_pages']):
            raise ValueError('Arian original one-page index source scope differs')
        receipt = _one(frame['pages'], 'single index receipt')
        roster = [x for x in _load(root, proof['source_roster']) if x.get('index_id') == _INDEX]
        roster_by_id = {x['reference_id']: x for x in roster}
        if (len(roster) != counts['index_groups'] or len(roster_by_id) != len(roster)
                or set(roster_by_id) != set(by_id)):
            raise ValueError('Arian original roster count/identity differs')
        for reference in proof['selected_reference_ids']:
            row = roster_by_id[reference]
            url = row['source_url']
            if (not isinstance(url, str) or not _PARENT.fullmatch(url)
                    or reference != 'publisher-series:'+hashlib.sha256(url.encode()).hexdigest()[:24]
                    or row.get('kind') != 'series' or not row.get('title')
                    or any(by_id[reference].get(k) != row.get(k) for k in
                        ('reference_id', 'source_url', 'index_id', 'index_source_url', 'index_source_sha256'))
                    or any(candidate_by_id[reference].get(k) != row.get(k) for k in ('reference_id', 'source_url', 'title'))
                    or row.get('index_source_url') != _PAGE or row.get('index_source_sha256') != receipt['sha256']
                    or row.get('index_retrieved_at') != receipt['retrieved_at']):
                raise ValueError('Arian selected native URL/title/index receipt binding differs')
        cards = _cards(_source(source_root, receipt), {roster_by_id[r]['source_url'] for r in selected})
        result = []
        for reference in proof['selected_reference_ids']:
            row = roster_by_id[reference]
            url, card = row['source_url'], cards[row['source_url']]
            if card['title'] != row['title'] or card['credit_display'] != row.get('author_credit'):
                raise ValueError('Arian complete own h3 title/index credit display differs from original projection')
            key = {'provider': 'arian-rose-index-only', 'source_index': _PAGE,
                   'source_scope': _SCOPE, 'official_group_url': url}
            serialized = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            locator = 'article:has(a[href="'+url+'"])'
            result.append({'schema_version': 'light-novel-arian-index-source-group-v1',
                'kind': 'official_publisher_native_index_group_reference', 'source_unit': 'publisher_work_group',
                'reference_id': 'arian-index-group-local:'+hashlib.sha256(serialized.encode()).hexdigest(),
                'reference_id_kind': 'local_hash_of_official_index_group_URL_context', 'reference_id_is_official': False,
                'local_reference_key': key, 'legacy_source_reference_id': reference,
                'independent_official_group_id': None, 'title': card['title'], 'source_url': url,
                'authors': None, 'publisher': None, 'label': None, 'form': None, 'isbn': None,
                'official_lightnovel_tag': None, 'media_references': None, 'light_novel_status': 'unknown',
                'index_credit_literals': card['credits'], 'members': [], 'frame_member_count': 0,
                'known_verified_member_reference_count': 0, 'total_product_count': None,
                'native_index_card_complete': True, 'index_frame_pages_complete': True,
                'frame_membership_complete': None, 'parent_page_membership_complete': None,
                'global_membership_complete': None, 'story_series_asserted': False,
                'story_series_identity_asserted': False, 'original_work_identity_asserted': False,
                'edition_identity_asserted': False, 'singleton_asserted': False,
                'independent_volume_numbers_inferred': False, 'paper_electronic_same_edition_asserted': False,
                'scope': _SCOPE, 'source_evidence': [{'source_url': _PAGE, 'raw_sha256': receipt['sha256'],
                    'locator': locator+' h3 / a[href]'}, {'source_url': _PAGE,
                    'raw_sha256': receipt['sha256'], 'locator': locator+' p.author'}],
                'robots_evidence': dict(_ROBOTS), 'usage_permissions': dict(_USAGE)})
        return result
    except (KeyError, TypeError, AttributeError, IndexError, UnicodeDecodeError, json.JSONDecodeError, OSError, EOFError) as exc:
        raise ValueError('Arian selected native index evidence cannot be reproduced') from exc
