"""Authenticate publisher grouping fields inside the fixed two-label M frame.

Groups are scoped source references, including singletons, not story-series or
original-work identities. Only one observed grouping search has exact-filter
closure evidence. No network, database, prose or image output is involved.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from .light_novel_futaba_references_v1 import (
    _FIRST, _json, _receipts, _row, _source, verify_futaba_mnovels_references,
)
from .light_novel_publisher_references_v1 import _relative


_JS = 'https://www.futabasha.co.jp/js/ContentDetail.558616518c9104f2.js'
_SCOPE = 'fixed_mnovels_and_mnovels_f_label_frames'
_PREFIXES = {
    'existing_books_heading': 'createElementVNode)("h3",null,"既刊",-1)',
    'existing_books_members': 'renderList)(o.groupingBooks,',
    'grouping_search_link': '6==o.groupingBooks.length?((0,n.openBlock)(),(0,n.createElementBlock)("div",u,[(0,n.createElementVNode)("a",{href:"/search?grouping=".concat(o.searchKeyword),class:"parts__more-btn"},t[0]||(t[0]=[(0,n.createElementVNode)("p",null,"もっと見る",-1)]),8,m)]))',
    'detail_grouping_to_existing_books': '"雑誌"!==this.basicInfo.publication_form_value&&null!==(s=this.basicInfo.grouping_books)&&void 0!==s&&s.length?((0,n.openBlock)(),(0,n.createBlock)(p,{key:2,groupingBooks:this.basicInfo.grouping_books,searchKeyword:this.basicInfo.grouping},null,8,["groupingBooks","searchKeyword"]))',
    'author_other_works_excludes_group': 'author_id:e,except_grouping:t.basicInfo.grouping',
}
_LABELS = {'mnovels': ('Ｍノベルス', 12), 'mnovels-f': ('Ｍノベルスｆ', 21)}


def _path(base, ref):
    value = ref['path']
    prefix = 'data/light-novels-v1/'
    if isinstance(value, str) and value.startswith(prefix):
        value = value[len(prefix):]
    return _relative(base, value)


def _load(base, ref):
    return _json(_path(base, ref), ref['sha256'])


def _same_pin(base, first, second):
    if _path(base, first) != _path(base, second) or first['sha256'] != second['sha256']:
        raise ValueError('Futaba workgroup evidence binding differs')


def _media(book):
    result = []
    for kind, key in ((1, 'web_rbooks_id'), (2, 'web_ebooks_id'), (3, 'web_abooks_id')):
        value = book.get(key)
        if value is not None and (type(value) not in (str, int)):
            raise ValueError('Futaba media source identifier literal malformed')
        if value:
            result.append({'media_type_source_literal': kind, 'media_id_source_literal': value,
                           'source_url': 'https://www.futabasha.co.jp/book/' + book['jdcn_code'] + '?type=' + str(kind)})
    return result


def _js_evidence(base, proof):
    ref = proof['detail_js_receipt']
    receipt_path = _path(base, ref)
    receipt = _load(base, ref)
    text = _source(receipt_path.parent, receipt, _JS, javascript=True)
    witnesses = _load(base, proof['detail_js_witnesses'])
    if witnesses.get('schema_version') != 'futaba-detail-grouping-route-witnesses-v1':
        raise ValueError('Futaba grouping JS witness schema differs')
    items = witnesses['witnesses']
    if len(items) != len(_PREFIXES) or {item['kind'] for item in items} != set(_PREFIXES):
        raise ValueError('Futaba grouping JS semantic witness set differs')
    for item in items:
        locator = item['locator']
        start, end = locator['start'], locator['end']
        if (item['source_url'] != _JS or item['raw_sha256'] != receipt['sha256']
                or locator.get('kind') != 'utf8_decoded_character_range'
                or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or text[start:end] != item['literal'] or not item['literal'].startswith(_PREFIXES[item['kind']])):
            raise ValueError('Futaba existing-books/grouping JS semantics do not replay')
    return {'source_url': _JS, 'raw_sha256': receipt['sha256'],
            'witnesses_sha256': proof['detail_js_witnesses']['sha256']}


def _field_groups(base, proof):
    ref = proof['mnovels_references']
    manifest_path = _path(base, ref)
    verified = verify_futaba_mnovels_references(manifest_path, ref['sha256'])
    if len(verified) != proof['expected_source_references']:
        raise ValueError('Futaba fixed M reference count differs')
    by_id = {row['publisher_reference_id']: row for row in verified}
    if len(by_id) != len(verified):
        raise ValueError('Futaba normalized product references repeat')
    m_proof = _load(base, ref)
    terminal_path = _path(manifest_path.parent, m_proof['terminal'])
    terminal = _json(terminal_path, m_proof['terminal']['sha256'])
    root = terminal_path.parent
    receipts = _receipts(root, terminal['receipt_log'])
    groups, seen, sources = {}, set(), {}
    for frame_ref in terminal['frames']:
        label, label_id = _LABELS[frame_ref['frame_id']]
        frame = _load(root, frame_ref['manifest'])
        search = 'https://www.futabasha.co.jp/search?label_name=' + quote(label)
        for page in frame['pagination_pages']:
            url = page['source_url']
            receipt = receipts[url]
            if page['receipt'] != receipt:
                raise ValueError('Futaba grouping page receipt binding differs')
            if url not in sources:
                sources[url] = _source(root, receipt, url)['books']['book_informations']
            for ordinal, book in enumerate(sources[url]):
                row = _row(book, receipt, ordinal, m_proof['observed_probe_contract']['path'],
                           label_literal=label, label_id=label_id, search_url=search)
                identity = row['publisher_reference_id']
                if identity in seen or by_id.get(identity) != row:
                    raise ValueError('Futaba grouping member differs from authenticated M product')
                seen.add(identity)
                grouping = book['grouping']
                if grouping is not None and not isinstance(grouping, str):
                    raise ValueError('Futaba grouping literal is not text or unknown')
                number = book.get('grouping_no')
                if number is not None and type(number) not in (str, int):
                    raise ValueError('Futaba grouping number source literal malformed')
                if grouping is None or not grouping.strip():
                    continue
                pointer = f'/books/book_informations/{ordinal}'
                member = {**deepcopy(row), 'grouping_source_literal': grouping,
                    'grouping_no_source_literal': number,
                    'grouping_no_semantics': 'unknown_not_an_independent_volume_number',
                    'media_source_literals': {key: book.get(key) for key in ('web_rbooks_id', 'web_ebooks_id', 'web_abooks_id')},
                    'media_references': _media(book), 'paper_electronic_same_edition_asserted': False,
                    'group_membership_evidence': {'source_url': url, 'raw_sha256': receipt['sha256'],
                        'locator': pointer + '/grouping', 'grouping_no_locator': pointer + '/grouping_no'}}
                groups.setdefault((frame_ref['frame_id'], label_id, grouping), []).append(member)
    if seen != set(by_id):
        raise ValueError('Futaba grouping extraction does not cover the fixed M frame')
    if (len(groups) != proof['expected_nonempty_groups']
            or sum(map(len, groups.values())) != proof['expected_grouped_references']):
        raise ValueError('Futaba nonempty grouping/member counts differ')
    old_path = _path(manifest_path.parent, m_proof['reused_monster_manifest'])
    old = _json(old_path, m_proof['reused_monster_manifest']['sha256'])
    next_ref = old['observed_route_proof']
    return groups, (_path(old_path.parent, next_ref), next_ref['sha256'])


def _search_query(url, grouping):
    parsed = urlsplit(url)
    expected = parse_qs(urlsplit(_FIRST).query, keep_blank_values=True)
    expected.update(label_name=[''], grouping=[grouping])
    if parsed.scheme != 'https' or parsed.netloc != 'book-api.futabasha.co.jp' or parsed.path != '/books' or parsed.fragment:
        raise ValueError('Futaba observed grouping endpoint differs')
    if parse_qs(parsed.query, keep_blank_values=True) != expected:
        raise ValueError('Futaba observed grouping query scope differs')


def _literal_credits(book):
    return [(a.get('web_author_type_overwrite') or a.get('author_type_name'), a['author_name'],
             'web_author_type_overwrite' if a.get('web_author_type_overwrite') else 'author_type_name')
            for a in book['book_authors']]


def _example(base, proof, groups, next_rule):
    example = _load(base, proof['exact_group_example'])
    if (example.get('schema_version') != 'futaba-single-publisher-workgroup-proof-v1'
            or example.get('source_unit') != 'publisher_work_group'):
        raise ValueError('Futaba single-group proof schema differs')
    _same_pin(base, example['observed_detail_js_witnesses'], proof['detail_js_witnesses'])
    next_ref = example['reused_official_next_rule']
    if (_path(base, next_ref), next_ref['sha256']) != next_rule:
        raise ValueError('Futaba grouping next-rule binding differs from authenticated M route')
    _load(base, next_ref)
    relation = _load(base, example['observed_book_page_relation'])
    _same_pin(base, relation['reused_js_witnesses'], proof['detail_js_witnesses'])
    _same_pin(base, relation['receipt'], proof['detail_receipt'])
    grouping = relation['grouping_title']
    parent = relation['parent_product']
    scope = next((key for key, value in _LABELS.items() if value[1] == parent['label_id']), None)
    group_key = (scope, parent['label_id'], grouping)
    if (relation.get('schema_version') != 'futaba-single-grouping-existing-books-projection-v1'
            or relation.get('source_unit') != 'publisher_work_group'
            or relation.get('independent_official_group_id') is not None
            or any(relation.get(k) is not False for k in ('story_series_asserted', 'original_work_identity_asserted', 'independent_volume_numbers_inferred'))
            or group_key not in groups):
        raise ValueError('Futaba typed publisher-group scope differs')
    ref = proof['detail_receipt']
    detail_path = _path(base, ref)
    receipt = _load(base, ref)
    parsed = urlsplit(receipt['source_url'])
    query = parse_qs(parsed.query, keep_blank_values=True)
    identifier = parent['opaque_jdcn_code']
    if (parsed.scheme != 'https' or parsed.netloc != 'book-api.futabasha.co.jp' or parsed.path != '/book_details'
            or parsed.fragment or query != {'media': ['1'], 'jdcn_code': [identifier], 'image_size': ['330 55 1000'], 'grouping_media': ['0']}):
        raise ValueError('Futaba observed own detail request differs')
    raw_parent = _source(detail_path.parent, receipt, receipt['source_url'])['book_details']['book_informations']
    if (raw_parent['jdcn_code'] != identifier or raw_parent['grouping'] != grouping
            or raw_parent['book_name'] != parent['title'] or raw_parent.get('isbn_code') != parent['isbn_source_literal']
            or raw_parent.get('label_name') != parent['label'] or raw_parent.get('label_id') != parent['label_id']):
        raise ValueError('Futaba typed parent/grouping fields do not replay')
    members = {m['publisher_reference_id']: m for m in groups[group_key]}
    own = members.get('futaba-product:' + identifier)
    if (own is None or own['title'] != raw_parent['book_name'] or own['source_url'] != parent['source_url']
            or _literal_credits(raw_parent) != [(c['role'], c['name'], c['role_field']) for c in own['credit_literals']]
            or _literal_credits(raw_parent) != [(c['role_literal'], c['name'], c['role_field']) for c in parent['credits']]):
        raise ValueError('Futaba parent is not an authenticated M group member')
    raw_children, declared = raw_parent['grouping_books'], relation['members']
    if (not isinstance(raw_children, list) or not 1 <= len(raw_children) <= 6
            or len(raw_children) != len(declared) or relation['member_count_observed'] != len(raw_children)):
        raise ValueError('Futaba explicit existing-books preview count differs')
    for ordinal, (child, typed) in enumerate(zip(raw_children, declared, strict=True)):
        identity = 'futaba-product:' + child['jdcn_code']
        media = _media(child)
        url = media[0]['source_url'] if media else 'https://www.futabasha.co.jp/book/' + child['jdcn_code'] + '?type=1'
        if (identity not in members or child['book_name'] != members[identity]['title']
                or child['book_name'] != typed['title'] or child['jdcn_code'] != typed['opaque_jdcn_code']
                or typed['source_url'] != url or typed['isbn'] is not None or typed['credits'] != []):
            raise ValueError('Futaba typed/nested existing-books member differs')
        evidence = typed['member_evidence']
        if (evidence['source_url'] != receipt['source_url'] or evidence['raw_sha256'] != receipt['sha256']
                or evidence['locator'] != '/book_details/book_informations/grouping_books/' + str(ordinal)):
            raise ValueError('Futaba existing-books child pointer differs')
    more = relation['more_link']
    if more['text'] != 'もっと見る' or more['href_literal'] != '/search?grouping=' + grouping:
        raise ValueError('Futaba observed existing-books grouping link differs')

    terminal = _load(base, example['terminal_frame'])
    dom = _load(base, example['observed_grouping_search_dom'])
    selected = _load(base, example['safe_projection'])
    total = terminal['reported_total']
    expected = {'schema_version': 'futaba-single-publisher-workgroup-terminal-frame-v1', 'source_unit': 'publisher_work_group',
                'grouping_title': grouping, 'independent_official_group_id': None,
                'status': 'complete_exact_observed_api_filter', 'first_page': 1, 'final_page': 1, 'per_page': 25}
    if any(terminal.get(k) != v for k, v in expected.items()) or type(total) is not int or not 1 <= total <= 25:
        raise ValueError('Futaba exact grouping-filter finite frame differs')
    url = terminal['first_api_url']
    _search_query(url, grouping)
    if (len(terminal['pagination_pages']) != 1 or len(example['source_receipts']) != 1):
        raise ValueError('Futaba fixed single-group terminal page range differs')
    page = terminal['pagination_pages'][0]
    ref = example['source_receipts'][0]
    _same_pin(base, page['receipt'], ref)
    receipt_path = _path(base, ref)
    query_receipt = _load(base, ref)
    result = _source(receipt_path.parent, query_receipt, url)['books']
    expected_result = {'total_counts': total, 'total_pages': 1, 'per_page': 25, 'current_page': 1}
    if (page.get('page') != 1 or page.get('source_url') != url
            or page.get('raw_sha256') != query_receipt['sha256'] or page.get('raw_path') != query_receipt['raw_path']
            or page.get('results') != expected_result or result['results'] != expected_result
            or page.get('has_more_by_official_rule') is not False or page.get('next_url') is not None
            or page.get('results_locator') != '/books/results' or page.get('items_locator') != '/books/book_informations'
            or page.get('count') != total or len(result['book_informations']) != total):
        raise ValueError('Futaba grouping-filter raw total/next/source checkpoint differs')
    listed, seen = [], set()
    for ordinal, book in enumerate(result['book_informations']):
        identity = 'futaba-product:' + book['jdcn_code']
        if book['grouping'] != grouping or identity not in members or identity in seen:
            raise ValueError('Futaba grouping-filter member set differs from fixed M group')
        known = members[identity]
        config = next((v for v in _LABELS.values() if v[1] == book.get('label_id')), None)
        if config is None:
            raise ValueError('Futaba example returned an unbound label scope')
        row = _row(book, query_receipt, ordinal, 'observed_grouping_search', label_literal=config[0], label_id=config[1], search_url=terminal['search_url'])
        keys = ('publisher_reference_id', 'title', 'source_url', 'isbn', 'isbn_source_literal', 'label', 'form', 'credits')
        if (any(row[k] != known[k] for k in keys) or _media(book) != known['media_references']
                or _literal_credits(book) != [(c['role'], c['name'], c['role_field']) for c in known['credit_literals']]
                or book.get('grouping_no') != known['grouping_no_source_literal']):
            raise ValueError('Futaba grouping-search bibliography differs from authenticated M member')
        seen.add(identity)
        listed.append(row)
    if seen != set(members) or len(selected['members']) != total:
        raise ValueError('Futaba exact-filter/fixed-frame member completeness differs')
    for row, typed in zip(listed, selected['members'], strict=True):
        if (typed['opaque_jdcn_code'] != row['publisher_reference_id'].removeprefix('futaba-product:')
                or typed['title'] != row['title'] or typed['isbn'] != row['isbn']):
            raise ValueError('Futaba typed grouping-search selected bibliography differs')
        for key in ('publisher_reference_id', 'source_unit', 'source_url', 'isbn_source_literal', 'publisher',
                    'credits', 'label', 'form', 'release_date_source_literal', 'start_date_source_literal',
                    'official_lightnovel_tag', 'story_series_asserted'):
            if typed[key] != row[key]:
                raise ValueError('Futaba typed grouping-search field differs')
        expected_literals = [{'role_literal': c['role'], 'name': c['name'], 'role_field': c['role_field'], 'locator': c['locator']}
                             for c in row['credit_literals']]
        if typed['credit_literals'] != expected_literals:
            raise ValueError('Futaba typed grouping-search credit evidence differs')
        if (typed['grouping_title_literal'] != grouping
                or typed['grouping_no_source_literal'] != members[row['publisher_reference_id']]['grouping_no_source_literal']
                or typed['media_source_literals'] != members[row['publisher_reference_id']]['media_source_literals']
                or any(typed.get(k, False) is not False for k in ('original_identity_asserted', 'paper_electronic_same_edition_asserted'))):
            raise ValueError('Futaba typed grouping-search literal or identity claim differs')
    search = urlsplit(terminal['search_url'])
    if (search.scheme != 'https' or search.netloc != 'www.futabasha.co.jp' or search.path != '/search'
            or search.fragment or parse_qs(search.query, keep_blank_values=True) != {'grouping': [grouping]}
            or dom.get('schema_version') != 'futaba-observed-grouping-search-dom-v1'
            or dom.get('page_url') != terminal['search_url'] or dom.get('observed_first_api_url') != url
            or dom.get('document_title') != '「' + grouping + '」一覧 | 双葉社'
            or dom.get('h3_product_card_count') != total or dom.get('own_book_link_count') != total
            or dom.get('more_control_count') != 0 or dom.get('raw_html_saved') is not False or dom.get('raw_html_sha256') is not None):
        raise ValueError('Futaba observed grouping-search DOM scope differs')
    for prefix, row in (('first', listed[0]), ('last', listed[-1])):
        if dom[prefix + '_title'] != row['title'] or dom[prefix + '_relative_href'] != row['source_url'].removeprefix('https://www.futabasha.co.jp'):
            raise ValueError('Futaba first/last observed grouping-search card differs')
    return group_key, {'source_url': terminal['search_url'], 'request_url': url, 'raw_sha256': query_receipt['sha256'],
                       'reported_total': total, 'total_pages': 1, 'filter_scope_only': True}


def verify_futaba_mnovels_workgroups(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Return all nonempty source-field groups; never a story-series catalogue."""
    path = Path(manifest_path).resolve()
    base = path.parent
    try:
        proof = _json(path, sha256)
        if proof.get('schema_version') != 'futaba-mnovels-workgroups-proof-v1':
            raise ValueError('Futaba M workgroup proof schema differs')
        if any(type(proof[k]) is not int or not 1 <= proof[k] <= 616 for k in
               ('expected_source_references', 'expected_nonempty_groups', 'expected_grouped_references', 'expected_unique_grouping_literals')):
            raise ValueError('Futaba M workgroup fixed counts invalid')
        semantics = _js_evidence(base, proof)
        groups, next_rule = _field_groups(base, proof)
        if len({key[2] for key in groups}) != proof['expected_unique_grouping_literals']:
            raise ValueError('Futaba unique grouping-literal count differs')
        exact, exact_evidence = _example(base, proof, groups, next_rule)
        result = []
        for group_key, members in groups.items():
            frame_id, label_id, literal = group_key
            natural_key = {'provider': 'futaba', 'source_scope': _SCOPE, 'source_field': 'grouping',
                           'source_snapshot_sha256': proof['mnovels_references']['sha256'],
                           'source_frame_id': frame_id, 'label_id_scope': label_id, 'literal': literal}
            digest = hashlib.sha256(json.dumps(natural_key, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            result.append({'kind': 'official_publisher_field_work_group_reference', 'source_unit': 'publisher_work_group',
                'reference_id': 'futaba-grouping-local:' + digest,
                'reference_id_kind': 'local_hash_of_publisher_source_field_key', 'reference_id_is_official': False,
                'local_reference_key': natural_key, 'independent_official_group_id': None,
                'title': literal, 'grouping_title': literal,
                'source_url': members[0]['source_scope_membership']['rendered_index_url'],
                'source_scope': {'kind': _SCOPE, 'label_ids': [label_id], 'source_frame_id': frame_id,
                                 'manifest_sha256': proof['mnovels_references']['sha256']},
                'authors': None, 'publisher': None, 'label': None, 'form': None, 'isbn': None,
                'official_lightnovel_tag': None, 'story_series_asserted': False,
                'story_series_identity_asserted': False, 'original_work_identity_asserted': False,
                'independent_volume_numbers_inferred': False, 'paper_electronic_same_edition_asserted': False,
                'members': members, 'frame_member_count': len(members), 'frame_membership_complete': True,
                'exact_grouping_filter_complete': True if group_key == exact else None,
                'exact_grouping_filter_evidence': deepcopy(exact_evidence) if group_key == exact else None,
                'global_membership_complete': None, 'grouping_search_url': exact_evidence['source_url'] if group_key == exact else None,
                'shared_field_semantics_evidence': deepcopy(semantics),
                'source_evidence': [{'source_url': m['group_membership_evidence']['source_url'],
                    'raw_sha256': m['group_membership_evidence']['raw_sha256'],
                    'locator': m['group_membership_evidence']['locator']} for m in members],
                'usage_permissions': {'commercial_reuse': 'unknown', 'prose_ai_permission': 'not_granted',
                                      'raw_redistribution_permission': 'not_granted'}})
        return result
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError, StopIteration) as exc:
        raise ValueError('Futaba workgroup evidence cannot be reproduced') from exc
