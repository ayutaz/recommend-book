"""Selected KADOKAWA product references for private bibliography discovery.

This purpose is private local storage and loopback discovery only. Neither the
local usage audit nor robots grants public/commercial display, prose/model,
image or raw redistribution permission. The old NDL matching API is unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.robotparser import RobotFileParser

from .light_novel_publisher_books_v1 import (
    KADOKAWA_LABELS, _ENDPOINT, _kadokawa_page_key, _replay_kadokawa_book_pages,
)
from .light_novel_publisher_references_v1 import MAX_METADATA_BYTES, _DOM, _Node, _read


_ID = re.compile(r'kadokawa-product:([0-9]{12})')
_RIGHTS = {'purpose': 'discovery and bibliographic identity reference only', 'synopsis_ai_input': False,
           'image_reuse': False, 'redistribution_of_raw': False, 'additional_permissions': 'unknown'}
_USAGE = {'purpose': 'private_bibliographic_discovery', 'private_use_only': True, 'loopback_ui_only': True,
          'public_display': 'unknown', 'commercial': 'unknown', 'prose_AI': False, 'images': False,
          'raw_redistribution': False, 'upstream_permission_grant_asserted': False}
_PERMISSIONS = {'purpose': 'private_bibliographic_discovery', 'public_display': 'unknown', 'commercial_reuse': 'unknown',
                'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False}
_ROLES = {'著者': 'author', '著': 'author', '作者': 'author', '原作': 'original_author', '原案': 'original_concept',
          'イラスト': 'illustrator', 'イラストレーター': 'illustrator', '口絵・本文イラスト': 'illustrator',
          'キャラクター原案': 'character_designer', 'キャラクター原案・漫画': 'unknown', '訳者': 'translator', '編者': 'editor'}
_MEMBER_KEYS = ('publisher_book_id', 'official_item_code', 'title', 'source_url', 'label', 'official_label_id',
                'product_form_id', 'isbn', 'credits', 'evidence')


def _path(root, value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('KAD evidence path missing')
    value = value.replace('\\', '/')
    p = Path(value)
    # Existing pinned files use both absolute local paths and paths relative
    # to data/light-novels-v1. Every resolved target must stay in this frame.
    if not p.is_absolute():
        p = (root.parent if p.parts[0] == root.name else root)/p
    p = p.resolve()
    if not p.is_relative_to(root):
        raise ValueError('KAD evidence path escapes source frame')
    return p


def _load(root, pin):
    return json.loads(_read(_path(root, pin['path']), pin['sha256'], MAX_METADATA_BYTES))


def _receipt(root, row):
    key = _kadokawa_page_key(row)
    if (row.get('rights') != _RIGHTS or row.get('location') or row.get('error')
            or row.get('http_redirects_followed', 0) != 0 or row.get('http_retry_count', 0) != 0
            or row.get('size_cap_exceeded') or row.get('content_type', '').split(';')[0].strip().lower() != 'application/json'
            or type(row.get('byte_length')) is not int or not 0 < row['byte_length'] <= 10_000_000
            or not isinstance(row.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', row['sha256'])):
        raise ValueError('KAD transport/private bibliography boundary differs')
    _path(root, row['raw_path'])
    return key


def _same_evidence(root, evidence, receipt):
    pointer = evidence.get('json_pointer', '')
    if (not re.fullmatch(r'/result/book/(?:[0-9]|1[0-9])', pointer)
            or evidence.get('source_url') != receipt['source_url'] or evidence.get('http_method') != 'POST'
            or evidence.get('request_data') != receipt['request_data'] or evidence.get('source_sha256') != receipt['sha256']
            or evidence.get('retrieved_at') != receipt.get('retrieved_at')
            or _path(root, evidence['raw_path']) != _path(root, receipt['raw_path'])):
        raise ValueError('KAD metadata own page/pointer differs')


class _VerifiedScope:
    def __init__(self, root, proof, source, index):
        self.root, self.proof, self.source, self.index = root, proof, source, index
        self.pages = {}


def _scope(path, expected):
    root = path.parent
    proof = json.loads(_read(path, expected, MAX_METADATA_BYTES))
    if (proof.get('schema_version') != 'kadokawa-electronic-publisher-reference-scope-v1'
            or proof.get('rights_scope') != 'reference_only_bibliography'
            or proof.get('reference_scope') != 'private_bibliographic_discovery'
            or proof.get('usage') != _USAGE or proof.get('rights_conditions') != _RIGHTS):
        raise ValueError('KAD private discovery scope/usage differs')
    predicates = {'product_form_id': 13, 'isbn': None, 'source_unit': 'publisher_product_reference',
        'actual_work_identity_asserted': False, 'edition_identity_asserted': False,
        'story_series_identity_asserted': False, 'original_work_identity_asserted': False}
    explanation = proof['usage_boundary_explanation']
    if (proof.get('scope_predicates') != predicates or any(explanation.get(k) is not False for k in (
            'existing_rights_scope_is_upstream_grant', 'external_LLM_or_model_use', 'raw_redistribution_permission'))):
        raise ValueError('KAD purpose does not grant upstream permissions')
    source, terminal = _load(root, proof['source_manifest']), _load(root, proof['terminal'])
    if (source.get('schema_version') != 'kadokawa-publisher-books-v1' or source.get('rights_scope') != 'reference_only_bibliography'
            or source.get('index_complete') is not False or source.get('parent_terminal_pin') != proof['terminal']
            or terminal.get('schema_version') != 'kadokawa-publisher-books-v1'
            or terminal.get('rights_scope') != 'reference_only_bibliography' or terminal.get('index_complete') is not True
            or len(terminal['pages']) != proof['source_page_count'] or not 1 <= len(terminal['pages']) <= 5000
            or sum(c['unique_editions'] for c in terminal['closure'].values()) != proof['terminal_source_items']):
        raise ValueError('KAD pinned partial/terminal metadata differs')
    terminal_pages = {}
    for r in terminal['pages']:
        key = _kadokawa_page_key(r)
        if key in terminal_pages:
            raise ValueError('KAD duplicate terminal page')
        terminal_pages[key] = r
    pages = {}
    if not 1 <= len(source['pages']) == proof['raw_pages_in_scope'] <= 200:
        raise ValueError('KAD bounded source page subset differs')
    for r in source['pages']:
        key = _receipt(root, r)
        if key in pages or terminal_pages.get(key) != r:
            raise ValueError('KAD selected page is not the pinned terminal page')
        pages[key] = r
    boundary = _load(root, proof['usage_boundary_pin'])
    if (any(boundary.get(k) is not False for k in ('publisher_prose_classification_input_admitted',
            'publisher_prose_public_display_admitted', 'publisher_image_reuse_admitted'))
            or boundary.get('raw_evidence') != 'Private local only; not redistribution permission'):
        raise ValueError('KAD local prose/image boundary differs')
    policy = _load(root, proof['robots_policy_pin'])['www.kadokawa.co.jp']
    if policy['source_url'] != 'https://www.kadokawa.co.jp/robots.txt':
        raise ValueError('KAD robots source differs')
    robots = RobotFileParser()
    robots.parse(_read(_path(root, policy['raw_path']), policy['sha256'], 10_000_000).decode('utf-8').splitlines())
    delay = robots.crawl_delay(policy['user_agent'])
    rate = robots.request_rate(policy['user_agent'])
    declared_rate = {'requests': rate.requests, 'seconds': rate.seconds} if rate else None
    if (not robots.can_fetch(policy['user_agent'], _ENDPOINT) or delay != policy['crawl_delay_seconds']
            or policy.get('request_rate') != declared_rate
            or policy['effective_interval_seconds'] < max(5, delay or 0, rate.seconds/rate.requests if rate else 0)):
        raise ValueError('KAD saved robots scope/interval differs')
    form = _load(root, proof['product_form_name_evidence_pin'])
    if [x for x in form['explicit_display_names'] if x.get('product_form_id') == 13] != [
            {'product_form_id': 13, 'name': '電子専売', 'json_pointer': '/product_form/13/name'}]:
        raise ValueError('KAD explicit form dictionary witness differs')
    _load(root, proof['target_id_check'])  # Pin only; never infer DB identity or absence.
    handoff = _load(root, proof['metadata_handoff'])
    members, ids = proof['members'], proof['selected_reference_ids']
    if (type(proof['candidate_count']) is not int or not 1 <= len(members) == proof['candidate_count'] <= 200
            or len(ids) != len(members) or len(set(ids)) != len(ids)
            or [m['publisher_reference_id'] for m in members] != ids
            or proof['selected_publisher_book_ids'] != [m['publisher_book_id'] for m in members]):
        raise ValueError('KAD bounded candidate metadata differs')
    index = {}
    for m in members:
        identity = _ID.fullmatch(m['publisher_reference_id'])
        matches = [r for r in handoff if r.get('publisher_book_id') == m['publisher_book_id']]
        if not identity or len(matches) != 1:
            raise ValueError('KAD product metadata ID is ambiguous')
        original = matches[0]
        if (m != {'publisher_reference_id': m['publisher_reference_id'], **{k: original[k] for k in _MEMBER_KEYS}}
                or m['official_item_code'] != identity[1] or m['publisher_book_id'] != 'kadokawa:'+identity[1]
                or m['source_url'] != 'https://www.kadokawa.co.jp/product/'+identity[1]+'/'
                or not isinstance(m['title'], str) or not m['title'].strip()
                or type(m['product_form_id']) is not int or m['product_form_id'] != 13 or m['isbn'] is not None
                or KADOKAWA_LABELS.get(str(m['official_label_id'])) != m['label']):
            raise ValueError('KAD metadata title/role/form/ISBN differs')
        query = m['evidence']['request_data']
        key = str(query['label']), str(query['pageno_book'])
        if key not in pages:
            raise ValueError('KAD candidate page is outside selected frame')
        _same_evidence(root, m['evidence'], pages[key])
        index[m['publisher_reference_id']] = (original, pages[key], key)
    return _VerifiedScope(root, proof, source, index)


def _literal(node):
    if not isinstance(node, _Node):
        return node
    if node.tag in {'script', 'style'}:
        return ''
    return ' ' if node.tag == 'br' else ''.join(_literal(c) for c in node.parts)


def _credits(fragment, pointer):
    if fragment is None:
        return [], []
    if not isinstance(fragment, str):
        raise ValueError('KAD credit field type differs')
    credits, literals = [], []
    for number, li in enumerate(_DOM(fragment).root.nodes(tag='li'), 1):
        display = _literal(li).strip()
        if not display:
            raise ValueError('KAD empty credit display')
        fields = display.split(None, 1)
        role = fields[0] if len(fields) == 2 and fields[0] in _ROLES else None
        name = fields[1].strip() if role is not None else display
        credits.append({'role': _ROLES.get(role, 'unknown'), 'name': name})
        literals.append({'role_literal': role, 'name': name, 'locator': pointer+f'/disp_author_name/li[{number}]'})
    return credits, literals


def _page(scope, receipt, key):
    cache_key = key, receipt['sha256']
    if cache_key not in scope.pages:
        decoded = {}
        manifest = {'schema_version': 'kadokawa-publisher-books-v1', 'rights_scope': 'reference_only_bibliography', 'pages': [receipt]}
        rows = _replay_kadokawa_book_pages(manifest, scope.root, decoded_pages=decoded)
        payload, length = decoded[key]
        if length != receipt['byte_length']:
            raise ValueError('KAD raw receipt size differs')
        raw_items = {}
        for item in payload['result']['book']:
            code = str(item['itemCode'])
            if code in raw_items:
                raise ValueError('KAD duplicate product in selected raw page')
            raw_items[code] = {'credit': item.get('disp_author_name'), 'isbn_present': 'isbn' in item}
        # Retain bibliographic fields only in the operation cache.
        scope.pages[cache_key] = (rows, raw_items)
    return scope.pages[cache_key]


def _row(scope, reference):
    expected, receipt, key = scope.index[reference]
    rows, raw_items = _page(scope, receipt, key)
    matches = [r for r in rows if r['publisher_book_id'] == expected['publisher_book_id']]
    if len(matches) != 1:
        raise ValueError('KAD selected product absent or ambiguous in raw')
    row = matches[0]
    for k, v in expected.items():
        if k == 'evidence':
            _same_evidence(scope.root, row[k], receipt)
            if row[k]['json_pointer'] != v['json_pointer']:
                raise ValueError('KAD selected own item pointer changed')
        elif row.get(k) != v:
            raise ValueError('KAD selected product metadata changed: '+k)
    code = row['official_item_code']
    if (row['product_form_id'] != 13 or row['isbn'] is not None or row['official_product_form_name'] != '電子専売'
            or not raw_items[code]['isbn_present']):
        raise ValueError('KAD selected electronic/null source predicate differs')
    pointer = row['evidence']['json_pointer']
    credits, literals = _credits(raw_items[code]['credit'], pointer)
    local = {'provider': 'kadokawa', 'source_scope': 'form13_isbn_null_product_reference',
        'publisher_reference_id': reference, 'official_product_url': row['source_url'], 'product_form_id': 13, 'label': row['label']}
    return {'kind': 'official_publisher_product_reference', 'source_unit': 'publisher_product_reference',
        'publisher_reference_id': reference, 'publisher_book_id': row['publisher_book_id'], 'official_product_id': code,
        'official_item_code': code, 'reference_id': 'kadokawa-product-local:'+hashlib.sha256(json.dumps(local,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'reference_id_kind': 'local_hash_of_scoped_product_reference_fields', 'reference_id_is_official': False,
        'local_reference_key': local, 'reference_scope': 'private_bibliographic_discovery',
        'title': row['title'], 'source_url': row['source_url'], 'label': row['label'], 'official_label_id': row['official_label_id'],
        'form': '電子専売', 'product_form_id': 13, 'isbn': None, 'publisher': None, 'individual_publisher': None,
        'credits': credits, 'credit_literals': literals, 'official_lightnovel_tag': None, 'original_language': None,
        'light_novel_status': 'unknown', 'light_novel_confirmed': False, 'media_references': None,
        'actual_work_identity_asserted': False, 'edition_identity_asserted': False, 'story_series_asserted': False,
        'story_series_identity_asserted': False, 'original_work_identity_asserted': False,
        'same_edition_identity_asserted': False, 'product_sku_identity_asserted': False,
        'independent_volume_numbers_inferred': False, 'paper_electronic_same_edition_asserted': False,
        'global_author_identity_asserted': False, 'global_author_alias_asserted': False,
        'usage_permissions': dict(_PERMISSIONS), 'evidence': deepcopy(row['evidence']),
        'source_evidence': [{'source_url': _ENDPOINT, 'raw_sha256': receipt['sha256'], 'locator': pointer,
                             'http_method': 'POST', 'request_data': deepcopy(receipt['request_data'])}]}


def verify_kadokawa_references(manifest_path: str | Path, sha256: str, *, reference_ids, cache=None) -> list[dict]:
    """Authenticate an explicit arbitrary subset; read each needed page once.

    A caller may share a dict only within one operation. Cached authenticated
    metadata/page facts are keyed by source path and proof SHA. Returned rows
    are copies. No unspecified page, DB, network, image or prose is exported.
    """
    if (not isinstance(reference_ids, (list, tuple, set, frozenset)) or not reference_ids
            or any(not isinstance(i,str) or not _ID.fullmatch(i) for i in reference_ids)
            or len(reference_ids) != len(set(reference_ids)) or len(reference_ids) > 200):
        raise ValueError('KAD explicit nonempty unique reference IDs required')
    if cache is not None and not isinstance(cache, dict):
        raise ValueError('KAD operation cache must be a dict')
    cache = {} if cache is None else cache
    path = Path(manifest_path).resolve()
    key = ('kadokawa-private-product-references-v1', str(path), sha256)
    try:
        scope = cache.get(key)
        if not isinstance(scope, _VerifiedScope):
            scope = _scope(path, sha256)
            cache[key] = scope
        if any(i not in scope.index for i in reference_ids):
            raise ValueError('KAD selected product outside pinned candidate scope')
        return deepcopy([_row(scope, i) for i in sorted(reference_ids)])
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError, IndexError) as exc:
        raise ValueError('KAD private product reference cannot be reproduced') from exc
