"""Replay held SB index references without asserting incomplete child lists.

Only the one saved index is opened. Existing complete-parent assertions stay
separate. An index name, including a wrapped or magazine-word display, proves
neither a story identity nor a novel, publication type, edition or membership.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin

from .light_novel_ndl_series_v1 import _cards
from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _decode, _read, _relative,
)


_PAGE = 'https://ebook.sbcr.jp/lightnovel/series'
_INDEX = 'ga-ebook-series'
_PARENT = re.compile(r'https://ebook\.sbcr\.jp/lightnovel/series/id/[0-9]+')
_FRAME_IDS = {_INDEX, 'hj-bunko-series', 'hj-novels-series', 'arianrose-series'}
_RIGHTS = 'Reference-only publisher page; no synopsis, image or AI-input reuse inferred'
_USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
          'prose_ai_permission': False, 'images_permission': False,
          'raw_redistribution_permission': False}
_ROBOTS = {'status': 'not_saved_in_existing_source_evidence', 'permission_inferred': False}
_SCOPE = 'selected_native_index_cards_only'
_ADOPTION_CAPS = {'SB215': 215, 'HJ137': 137, 'HJN39': 39, 'Arian49': 49}


def _one(items, field):
    if len(items) != 1:
        raise ValueError('SB held index field absent or ambiguous: '+field)
    return items[0]


def _load(root, pin):
    return json.loads(_read(_relative(root, pin['path']), pin['sha256'], MAX_METADATA_BYTES))


def _ids(value):
    if (not isinstance(value, list) or any(not isinstance(v, str)
            or not re.fullmatch(r'publisher-series:[0-9a-f]{24}', v) for v in value)
            or len(value) != len(set(value))):
        raise ValueError('SB held/protected reference IDs differ')
    return set(value)


def _cautions(title):
    result = []
    if title.startswith('（シリーズ：'):
        result.append('wrapped_index_display_title')
    if 'マガジン' in title:
        result.append('publication_type_unconfirmed')
    return result


def _source(root, receipt):
    if (receipt.get('source_url') != _PAGE or receipt.get('final_url') != _PAGE
            or receipt.get('http_status') != 200 or receipt.get('rights') != _RIGHTS
            or any(receipt.get(k) for k in ('location', 'error', 'size_cap_exceeded',
                   'redirects_followed', 'http_redirects_followed', 'retry_count', 'http_retry_count'))
            or not re.match(r'^text/html(?:\s*;|$)', receipt.get('content_type', ''), re.I)
            or type(receipt.get('byte_length')) is not int
            or not 0 < receipt['byte_length'] <= MAX_SOURCE_BYTES):
        raise ValueError('SB held index receipt URL/status/rights/size differs')
    raw = _read(_relative(root, receipt['raw_path']), receipt['sha256'], MAX_SOURCE_BYTES)
    if len(raw) != receipt['byte_length']:
        raise ValueError('SB held index declared byte length differs')
    return _decode(raw, receipt['content_type'])


def _selected_cards(document, selected_urls, expected_total):
    cards = _cards(_INDEX, document)
    by_url = {x['source_url']: x for x in cards}
    if len(cards) != expected_total or len(by_url) != len(cards):
        raise ValueError('SB original index count/URL uniqueness differs')
    tree = _DOM(document).root
    found = {}
    for anchor in tree.nodes(tag='a'):
        url = urljoin(_PAGE, anchor.attrs.get('href', ''))
        if url not in selected_urls or not re.fullmatch(r'「(.*)」シリーズ\s*\(([0-9]+)\)', anchor.text()):
            continue
        if (url in found or any(n.tag in {'img', 'input', 'textarea', 'select', 'button'}
                or 'hidden' in n.attrs or n.attrs.get('aria-hidden') == 'true'
                for n in [anchor, *anchor.nodes()])):
            raise ValueError('SB selected index link is hidden, ambiguous or contains non-title fields')
        literal = re.fullmatch(r'「(.*)」シリーズ\s*\(([0-9]+)\)', anchor.text())
        title = literal[1]
        if not title or len(title) > 4096 or by_url[url]['title'] != title:
            raise ValueError('SB selected complete index title differs')
        found[url] = title
    if set(found) != selected_urls:
        raise ValueError('SB exact held own index URL/title set differs')
    return found


def verify_sbcr_index_groups(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Authenticate exact held8 index cards; never open parent or child pages."""
    path = Path(manifest_path).resolve()
    root = path.parent
    try:
        proof = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if (proof.get('schema_version') != 'sbcr-held-index-groups-proof-v1'
                or proof.get('usage_scope') != _USAGE or proof.get('robots_evidence') != _ROBOTS
                or proof.get('source_rights_literal') != _RIGHTS):
            raise ValueError('SB held index proof schema/usage/robots context differs')
        counts = proof['expected_counts']
        caps = {'index_groups': 723, 'selected_groups': 8, 'selected_index_pages': 1,
                'index_pages': 1, 'protected_references': 1357}
        if (set(counts) != set(caps)
                or any(type(counts[k]) is not int or not 1 <= counts[k] <= cap for k, cap in caps.items())):
            raise ValueError('SB held finite index bounds differ')
        selected = _ids(proof['selected_reference_ids'])
        candidate = _load(root, proof['source_candidates'])
        delta, parent_proof = [_load(root, proof[k]) for k in ('coverage_report', 'parent_source_proof')]
        if (candidate.get('schema_version') != 'sbcr-held8-native-index-only-candidates-v1'
                or parent_proof.get('schema_version') != 'sbcr-selected-publisher-groups-proof-v2'
                or len(selected) != counts['selected_groups']
                or _ids(candidate['selected_reference_ids']) != selected
                or _ids(parent_proof['held_reference_ids']) != selected
                or _ids(delta['next_provider_incomplete_membership_reference_ids']) != selected):
            raise ValueError('SB exact incomplete-membership index selection differs')
        protected = _ids(delta['covered_reference_ids'])
        if len(protected) > 917 or _ids(parent_proof['covered_reference_ids']) != protected:
            raise ValueError('SB old covered source partition differs')
        if (set(proof['completed_adoptions']) != set(_ADOPTION_CAPS)
                or candidate['completed_adoption_pins'] != proof['completed_adoptions']):
            raise ValueError('SB completed adoption scope/pins differ')
        for key, cap in _ADOPTION_CAPS.items():
            adoption = _load(root, proof['completed_adoptions'][key])
            references = _ids(adoption['selected_legacy_reference_ids'])
            if (adoption.get('status') != 'completed' or not 1 <= len(references) <= cap
                    or references & protected or references & selected
                    or (key == 'SB215' and references != _ids(parent_proof['selected_reference_ids']))):
                raise ValueError('SB held selection overlaps or differs from completed source claims')
            protected |= references
        if protected & selected or len(protected) != counts['protected_references']:
            raise ValueError('SB held selection/protected1357 exact partition differs')
        roster = _load(root, proof['source_roster'])
        roster_by_id = {x['reference_id']: x for x in roster}
        if len(roster_by_id) != len(roster) or set(roster_by_id) != protected | selected:
            raise ValueError('SB held and protected scopes do not partition the original roster')
        handoff = _load(root, proof['source_handoff'])
        handoff_by_id = {x['reference_id']: x for x in handoff}
        manifest = _load(root, proof['source_handoff_manifest'])
        if (manifest.get('schema_version') != 'sbcr-publisher-series-handoff-v3'
                or manifest.get('snapshot_sha256') != proof['source_handoff']['sha256']
                or manifest.get('rows') != len(handoff) or len(handoff) != counts['index_groups']
                or len(handoff_by_id) != len(handoff) or manifest.get('national_coverage_proved') is not False):
            raise ValueError('SB unchanged parent handoff snapshot pin/counts differ')
        frames = _load(root, proof['source_frames'])
        if not isinstance(frames, list) or len(frames) != 4 or {x['id'] for x in frames} != _FRAME_IDS:
            raise ValueError('SB original four-frame metadata differs')
        frame = _one([x for x in frames if x['id'] == _INDEX], 'own frame')
        if (frame.get('url') != _PAGE or frame.get('kind') != 'series_index'
                or frame.get('index_pages_complete') is not True
                or frame.get('completion_reason') != 'all_linked_pages_exhausted'
                or frame.get('historical_entire_label_complete') is not False
                or frame.get('reference_series_count') != counts['index_groups']
                or len(frame['pages']) != counts['index_pages']):
            raise ValueError('SB original one-page index scope differs')
        receipt = _one(frame['pages'], 'single saved index')
        candidates = {x['legacy_source_reference_id']: x for x in candidate['groups']}
        if len(candidates) != len(candidate['groups']) or set(candidates) != selected:
            raise ValueError('SB exact held candidate reference set differs')
        for reference in proof['selected_reference_ids']:
            row, old, proposed = roster_by_id[reference], handoff_by_id[reference], candidates[reference]
            url = row['source_url']
            if (not isinstance(url, str) or not _PARENT.fullmatch(url)
                    or reference != 'publisher-series:'+hashlib.sha256(url.encode()).hexdigest()[:24]
                    or row.get('index_id') != _INDEX or row.get('kind') != 'series' or not row.get('title')
                    or any(old.get(k) != row.get(k) for k in ('source_url', 'title', 'index_id',
                        'index_source_url', 'index_source_sha256', 'index_retrieved_at'))
                    or any(proposed.get(k) != row.get(k) for k in ('source_url', 'title'))
                    or old.get('membership_index_complete') is not False
                    or old.get('membership_hold_reason') != 'source_count_or_pagination_unresolved'
                    or row.get('index_source_url') != _PAGE or row.get('index_source_sha256') != receipt['sha256']
                    or row.get('index_retrieved_at') != receipt['retrieved_at']):
                raise ValueError('SB held complete title/own URL/index snapshot binding differs')
        document = _source(_relative(root, proof['source_root']), receipt)
        cards = _selected_cards(document, {roster_by_id[r]['source_url'] for r in selected}, counts['index_groups'])
        result = []
        for reference in proof['selected_reference_ids']:
            row = roster_by_id[reference]
            url, title = row['source_url'], cards[row['source_url']]
            if title != row['title']:
                raise ValueError('SB complete unchanged index literal title differs from raw own link')
            key = {'provider': 'sbcr-index-only', 'source_index': _PAGE,
                   'source_scope': _SCOPE, 'official_group_url': url}
            serialized = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            locator = 'a[href resolving to "'+url+'"] (literal series index title/count)'
            result.append({'schema_version': 'light-novel-sbcr-index-source-group-v1',
                'kind': 'official_publisher_native_index_group_reference', 'source_unit': 'publisher_work_group',
                'reference_id': 'sbcr-index-group-local:'+hashlib.sha256(serialized.encode()).hexdigest(),
                'reference_id_kind': 'local_hash_of_official_index_group_URL_context', 'reference_id_is_official': False,
                'local_reference_key': key, 'legacy_source_reference_id': reference,
                'independent_official_group_id': None, 'title': title, 'source_url': url,
                'authors': None, 'publisher': None, 'label': None, 'form': None, 'isbn': None,
                'official_lightnovel_tag': None, 'media_references': None, 'light_novel_status': 'unknown',
                'index_credit_literals': [], 'index_name_cautions': _cautions(title),
                'members': [], 'frame_member_count': 0, 'known_verified_member_reference_count': 0,
                'total_product_count': None, 'native_index_card_complete': True, 'index_frame_pages_complete': True,
                'frame_membership_complete': None, 'parent_page_membership_complete': None,
                'global_membership_complete': None, 'story_series_asserted': False,
                'story_series_identity_asserted': False, 'original_work_identity_asserted': False,
                'edition_identity_asserted': False, 'singleton_asserted': False,
                'independent_volume_numbers_inferred': False, 'paper_electronic_same_edition_asserted': False,
                'scope': _SCOPE, 'source_evidence': [{'source_url': _PAGE,
                    'raw_sha256': receipt['sha256'], 'locator': locator}],
                'robots_evidence': dict(_ROBOTS), 'usage_permissions': dict(_USAGE)})
        return result
    except (KeyError, TypeError, AttributeError, IndexError, UnicodeDecodeError, json.JSONDecodeError, OSError, EOFError) as exc:
        raise ValueError('SB held index-only source evidence cannot be reproduced') from exc
