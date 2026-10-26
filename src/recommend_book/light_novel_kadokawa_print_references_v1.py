"""Selected form-3/form-1 KADOKAWA references for private discovery.

ISBNs are publisher metadata literals, never an edition or original-work
identity. This adapter has its own print contract and never routes print input
through the electronic admission contract. No upstream permissions are added.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.robotparser import RobotFileParser

from .light_novel_kadokawa_references_v1 import (
    _ID, _PERMISSIONS, _RIGHTS, _USAGE, _VerifiedScope, _credits,
    _page, _path, _receipt, _same_evidence,
)
from .light_novel_publisher_books_v1 import KADOKAWA_LABELS, _ENDPOINT, _kadokawa_page_key
from .light_novel_publisher_references_v1 import MAX_METADATA_BYTES, _read
from .normalization import normalize_isbn


_SCHEMA = 'kadokawa-print-private-reference-preparation-scope-v1'
_SOURCE_SCOPE = 'form3_valid_isbn_product_reference'
_ISBN_PREDICATE = 'explicit non-null literal with valid ISBN-13 check digit; identity unasserted'
_ISBN_GENERAL_PREDICATE = 'explicit non-null literal with valid ISBN-10/13 check digit; identity unasserted'
_FACTS = ('publisher_book_id', 'official_item_code', 'source_url', 'title', 'isbn', 'label', 'label_scope',
          'official_label_id', 'official_label_site_id', 'product_form_id', 'credits', 'release_date')
_PRINT_ROLES = {'カバーイラスト': 'cover_illustrator', '口絵イラスト': 'illustrator', '本文イラスト': 'illustrator'}


class _Pinned:
    def __init__(self, value, byte_length):
        self.value, self.byte_length = value, byte_length


@dataclass(frozen=True)
class _FormContract:
    name: str
    form_id: int
    literal: str
    preparation_prefix: str

    @property
    def source_scope(self):
        return f'form{self.form_id}_valid_isbn_product_reference'


_PRINT = _FormContract('print',3,'文庫','kadokawa-valid-isbn')
_TRADE = _FormContract('trade',1,'単行本','kadokawa-trade-valid-isbn')


class _PhysicalScope(_VerifiedScope):
    def __init__(self,root,proof,source,index,contract):
        super().__init__(root,proof,source,index)
        self.contract = contract


class _PrintScope(_PhysicalScope):
    pass


class _TradeScope(_PhysicalScope):
    pass


def _pin_path(root, pin, *, coverage=False):
    if coverage:
        path = _path(root.parent, pin['path'])
        if not path.is_relative_to((root.parent/'coverage').resolve()):
            raise ValueError('KAD print preparation metadata is outside coverage')
        return path
    return _path(root, pin['path'])


def _load(root, pin, cache, *, coverage=False, parse=True):
    path = _pin_path(root, pin, coverage=coverage)
    key = ('kadokawa-print-pinned-metadata-v1', str(path), pin['sha256'], parse)
    item = cache.get(key)
    if not isinstance(item, _Pinned):
        raw = _read(path, pin['sha256'], MAX_METADATA_BYTES)
        item = _Pinned(json.loads(raw) if parse else None,len(raw))
        cache[key] = item
    if 'byte_length' in pin and pin['byte_length'] != item.byte_length:
        raise ValueError('KAD print metadata pin size differs')
    return item.value


def _same_pin(root, left, right, *, coverage=False):
    return left['sha256'] == right['sha256'] and _pin_path(root,left,coverage=coverage) == _pin_path(root,right,coverage=coverage)


def _isbn(value):
    if not isinstance(value, str):
        return None
    compact = re.sub(r'[\s\-‐‑‒–—]', '', unicodedata.normalize('NFKC',value))
    if not re.fullmatch(r'(?:97[89][0-9]{10}|[0-9]{9}[0-9Xx])', compact):
        return None
    return normalize_isbn(value)


def _conditions(root, proof, cache, contract=_PRINT):
    if (proof.get('rights_scope') != 'reference_only_bibliography'
            or proof.get('reference_scope') != 'private_bibliographic_discovery'
            or proof.get('usage') != _USAGE or proof.get('rights_conditions') != _RIGHTS):
        raise ValueError('KAD print private discovery scope/usage differs')
    explanation = proof['usage_boundary_explanation']
    if any(explanation.get(k) is not False for k in ('existing_rights_scope_is_upstream_grant',
            'external_LLM_or_model_use','raw_redistribution_permission')):
        raise ValueError('KAD print local scope is not upstream permission')
    audit = _load(root,proof['usage_boundary_pin'],cache)
    if (any(audit.get(k) is not False for k in ('publisher_prose_classification_input_admitted',
            'publisher_prose_public_display_admitted','publisher_image_reuse_admitted'))
            or audit.get('raw_evidence') != 'Private local only; not redistribution permission'):
        raise ValueError('KAD print prose/image boundary differs')
    policy = _load(root,proof['robots_policy_pin'],cache)['www.kadokawa.co.jp']
    if policy['source_url'] != 'https://www.kadokawa.co.jp/robots.txt':
        raise ValueError('KAD print robots source differs')
    key = ('kadokawa-print-robots-v1',str(_path(root,policy['raw_path'])),policy['sha256'],policy['user_agent'])
    robots = cache.get(key)
    if not isinstance(robots,RobotFileParser):
        robots = RobotFileParser()
        robots.parse(_read(_path(root,policy['raw_path']),policy['sha256'],10_000_000).decode('utf-8').splitlines())
        cache[key] = robots
    delay,rate = robots.crawl_delay(policy['user_agent']),robots.request_rate(policy['user_agent'])
    declared_rate = {'requests':rate.requests,'seconds':rate.seconds} if rate else None
    if (not robots.can_fetch(policy['user_agent'],_ENDPOINT) or policy['crawl_delay_seconds'] != delay
            or policy.get('request_rate') != declared_rate
            or policy['effective_interval_seconds'] < max(5,delay or 0,rate.seconds/rate.requests if rate else 0)):
        raise ValueError('KAD print saved robots scope/interval differs')
    form = _load(root,proof['product_form_name_evidence_pin'],cache)
    if contract == _PRINT:
        if [x for x in form['explicit_display_names'] if x.get('product_form_id') == 3] != [
                {'product_form_id':3,'name':'文庫','json_pointer':'/product_form/3/name'}]:
            raise ValueError('KAD print explicit form dictionary differs')
    elif form['definitions']['1'] != {'definition':{'name':'単行本'},'json_pointer':'/product_form/1/name'}:
        raise ValueError('KAD trade explicit form dictionary differs')
    return form


def _scope(path, expected, cache, *, contract=_PRINT):
    root = path.parent
    proof = json.loads(_read(path,expected,MAX_METADATA_BYTES))
    if proof.get('schema_version') != f'kadokawa-{contract.name}-private-reference-preparation-scope-v1':
        raise ValueError('KAD print source schema differs')
    form = _conditions(root,proof,cache,contract)
    isbn_predicate = proof.get('scope_predicates',{}).get('isbn')
    predicates = {'product_form_id':contract.form_id,'isbn':isbn_predicate,'source_unit':'publisher_product_reference',
        'actual_work_identity_asserted':False,'edition_identity_asserted':False,
        'story_series_identity_asserted':False,'original_work_identity_asserted':False,'light_novel_confirmed':False}
    if isbn_predicate not in {_ISBN_PREDICATE,_ISBN_GENERAL_PREDICATE} or proof.get('scope_predicates') != predicates:
        raise ValueError('KAD print source predicates differ')
    source,terminal = [_load(root,proof[k],cache) for k in ('source_manifest','terminal')]
    if (source.get('schema_version') != 'kadokawa-publisher-books-v1' or source.get('rights_scope') != 'reference_only_bibliography'
            or source.get('index_complete') is not False or source.get('national_or_historical_coverage_proved') is not False
            or not _same_pin(root,source['parent_terminal_pin'],proof['terminal'])
            or terminal.get('schema_version') != 'kadokawa-publisher-books-v1' or terminal.get('rights_scope') != 'reference_only_bibliography'
            or terminal.get('index_complete') is not True or len(terminal['pages']) != proof['source_page_count']
            or not 1 <= len(terminal['pages']) <= 5000
            or sum(c['unique_editions'] for c in terminal['closure'].values()) != proof['terminal_source_items']):
        raise ValueError('KAD print partial/terminal metadata differs')
    terminal_pages = {}
    for r in terminal['pages']:
        key = _kadokawa_page_key(r)
        if key in terminal_pages:
            raise ValueError('KAD print duplicate terminal page')
        terminal_pages[key] = r
    if contract == _TRADE:
        query = form['definition_request_data']
        witness = terminal_pages.get((str(query.get('label')),str(query.get('pageno_book'))))
        if (witness is None or form['definition_source_url'] != _ENDPOINT
                or query != witness['request_data'] or form['definition_raw_sha256'] != witness['sha256']
                or _path(root,form['definition_raw_path']) != _path(root,witness['raw_path'])):
            raise ValueError('KAD trade dictionary is outside pinned source terminal')
    if not 1 <= len(source['pages']) == proof['raw_pages_in_scope'] <= 200:
        raise ValueError('KAD print source page subset is not bounded')
    pages = {}
    for r in source['pages']:
        key = _receipt(root,r)
        if key in pages or terminal_pages.get(key) != r:
            raise ValueError('KAD print selected receipt is outside pinned terminal')
        pages[key] = r
    handoff,exact,hold = [_load(root,proof[k],cache) for k in ('metadata_handoff','exact_source_id_set_pin','invalid_isbn_separate_hold_pin')]
    preparation = _load(root,proof['input_preparation'],cache,coverage=True)
    if (handoff.get('schema_version') != f'kadokawa-{contract.name}-private-reference-metadata-handoff-v1'
            or not re.fullmatch(rf'kadokawa-{contract.name}-valid-isbn(?:[1-9][0-9]*)?-exact-source-id-set-v1',exact.get('schema_version',''))
            or hold.get('schema_version') != f'kadokawa-{contract.name}-invalid-isbn-separate-hold-v1'
            or not re.fullmatch(rf'{contract.preparation_prefix}(?:[1-9][0-9]*)?-finite-lookup-preparation-and-search-key-diagnostic-v1',preparation.get('version',''))
            or not _same_pin(root,handoff['input_preparation'],proof['input_preparation'],coverage=True)
            or not _same_pin(root,exact['input_preparation'],proof['input_preparation'],coverage=True)
            or not _same_pin(root,hold['input_preparation'],proof['input_preparation'],coverage=True)
            or not _same_pin(root,exact['terminal'],proof['terminal'])
            or not _same_pin(root,handoff['parent_metadata_handoff'],exact['full_metadata_handoff'])):
        raise ValueError('KAD print metadata lineage differs')
    # Authenticate the parent metadata bytes, not all source pages or products.
    _load(root,exact['full_metadata_handoff'],cache,parse=False)
    pool = exact['publisher_reference_ids']
    if (type(exact['count']) is not int or not 1 <= len(pool) == exact['count'] <= 50_000
            or len(set(pool)) != len(pool) or any(not isinstance(i,str) or not _ID.fullmatch(i) for i in pool)
            or exact['publisher_book_ids'] != ['kadokawa:'+_ID.fullmatch(i)[1] for i in pool]
            or exact['source_reference_ids'] != ['publisher-edition:kadokawa:'+_ID.fullmatch(i)[1] for i in pool]):
        raise ValueError('KAD print exact source ID set differs')
    # Selection provenance only: saved NDL lookup results never prove absence,
    # bibliographic correspondence, or identity in this source-only adapter.
    inputs = {}
    for item in preparation['lookup_targets']:
        identity = item['source_reference_id']
        if identity in inputs:
            raise ValueError('KAD print duplicate preparation source ID')
        inputs[identity] = item
    if set(inputs) != set(exact['source_reference_ids']) or len(hold['members']) != hold['count']:
        raise ValueError('KAD print preparation/hold universe differs')
    held = {r['source_reference_id'] for r in hold['members']}
    if held & set(inputs) or any(_isbn(r.get('isbn_literal')) for r in hold['members']):
        raise ValueError('KAD print invalid ISBN hold conflicts with valid pool')
    members,ids = proof['members'],proof['selected_reference_ids']
    partition = proof['batch_partition_metadata']
    if (type(proof['candidate_count']) is not int or not 1 <= len(members) == proof['candidate_count'] <= 200
            or handoff['count'] != len(members) or handoff['members'] != members
            or ids != [m['publisher_reference_id'] for m in members] or len(set(ids)) != len(ids)
            or not set(ids) <= set(pool)
            or proof['selected_source_reference_ids'] != [m['source_reference_id'] for m in members]
            or proof['selected_publisher_book_ids'] != [m['publisher_book_id'] for m in members]
            or partition['pool_count'] != len(pool) or partition['whole_raw_pages_assigned_to_one_batch'] is not True
            or not 1 <= partition['batch_number'] <= partition['batch_total']):
        raise ValueError('KAD print batch metadata count/partition differs')
    index = {}
    for m in members:
        code = _ID.fullmatch(m['publisher_reference_id'])[1]
        normalized = _isbn(m['isbn_literal'])
        input_item = inputs[m['source_reference_id']]
        if (m['source_reference_id'] != 'publisher-edition:kadokawa:'+code or m['publisher_book_id'] != 'kadokawa:'+code
                or m['official_item_code'] != code or m['source_url'] != 'https://www.kadokawa.co.jp/product/'+code+'/'
                or not isinstance(m['title'],str) or not m['title'].strip() or m['title_literal'] != m['title']
                or m['title_source_field'] != '/result/book/{position}/title'
                or m['title_transformation'] != 'none; saved official title literal retains volume and punctuation; no synthesized work title'
                or type(m['product_form_id']) is not int or m['product_form_id'] != contract.form_id
                or normalized is None or m['isbn'] != m['isbn_literal'] or m['normalized_isbn13_lookup_key'] != normalized
                or (isbn_predicate == _ISBN_PREDICATE and len(re.sub(r'[\s\-‐‑‒–—]','',unicodedata.normalize('NFKC',m['isbn_literal']))) != 13)
                or input_item['publisher_book_id'] != m['publisher_book_id'] or input_item['isbn_literal'] != m['isbn_literal']
                or input_item['normalized_isbn13_lookup_key'] != normalized
                or KADOKAWA_LABELS.get(str(m['official_label_id'])) != m['label'] or m['label_scope'] != m['label']
                or m['rights_scope'] != 'reference_only_bibliography' or m['individual_publisher'] is not None
                or m['catalogue_owner'] != 'KADOKAWA'
                or m['identity_assertions'] != {'work_identity':False,'edition_identity':False,'story_series_identity':False,
                    'original_work_identity':False,'light_novel_confirmed':False}):
            raise ValueError('KAD print explicit metadata/title/ISBN/role scope differs')
        query = m['evidence']['request_data']
        key = str(query['label']),str(query['pageno_book'])
        if key not in pages:
            raise ValueError('KAD print member page outside source subset')
        _same_evidence(root,m['evidence'],pages[key])
        index[m['publisher_reference_id']] = (m,pages[key],key)
    state = _PrintScope if contract == _PRINT else _TradeScope
    return state(root,proof,source,index,contract)


def _print_credits(fragment,pointer):
    credits,literals = _credits(fragment,pointer)
    for credit,literal in zip(credits,literals,strict=True):
        if literal['role_literal'] is None:
            fields = literal['name'].split(None,1)
            if len(fields) == 2 and fields[0] in _PRINT_ROLES:
                role,name = fields[0],fields[1].strip()
                credit.update(role=_PRINT_ROLES[role],name=name)
                literal.update(role_literal=role,name=name)
    return credits,literals


def _row(scope,reference):
    contract = scope.contract
    member,receipt,key = scope.index[reference]
    rows,raw_items = _page(scope,receipt,key)
    matches = [r for r in rows if r['publisher_book_id'] == member['publisher_book_id']]
    if len(matches) != 1:
        raise ValueError('KAD print selected product is absent or ambiguous')
    row = matches[0]
    if any(row.get(k) != member[k] for k in _FACTS) or row['reference_id'] != member['source_reference_id']:
        raise ValueError('KAD print selected raw metadata changed')
    _same_evidence(scope.root,row['evidence'],receipt)
    code,pointer = row['official_item_code'],row['evidence']['json_pointer']
    if (pointer != member['evidence']['json_pointer'] or row['official_product_form_name'] != contract.literal
            or not raw_items[code]['isbn_present'] or _isbn(row['isbn']) != member['normalized_isbn13_lookup_key']):
        raise ValueError('KAD print own pointer/form/explicit ISBN differs')
    credits,literals = _print_credits(raw_items[code]['credit'],pointer)
    local = {'provider':'kadokawa','source_scope':contract.source_scope,'publisher_reference_id':reference,
        'official_product_url':row['source_url'],'product_form_id':contract.form_id,'label':row['label']}
    return {'kind':'official_publisher_product_reference','source_unit':'publisher_product_reference',
        'provider':'kadokawa','publisher_reference_id':reference,'publisher_book_id':row['publisher_book_id'],
        'official_product_id':code,'official_item_code':code,'source_reference_id':member['source_reference_id'],
        'reference_id':'kadokawa-product-local:'+hashlib.sha256(json.dumps(local,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'reference_id_kind':'local_hash_of_scoped_product_reference_fields','reference_id_is_official':False,
        'local_reference_key':local,'reference_scope':'private_bibliographic_discovery','source_scope':contract.source_scope,
        'title':row['title'],'source_url':row['source_url'],'label':row['label'],'official_label_id':row['official_label_id'],
        'form':contract.literal,'product_form_id':contract.form_id,'isbn':row['isbn'],'isbn_literal':row['isbn'],
        'normalized_isbn13_lookup_key':member['normalized_isbn13_lookup_key'],
        'isbn_lookup_key_basis':'explicit_source_literal_only_not_identity',
        'catalogue_owner':'KADOKAWA','publisher_field_basis':'catalogue_owner_context',
        'publisher':None,'individual_publisher':None,'credits':credits,'credit_literals':literals,
        'official_lightnovel_tag':None,'original_language':None,'light_novel_status':'unknown','light_novel_confirmed':False,
        'media_references':None,'actual_work_identity_asserted':False,'edition_identity_asserted':False,
        'story_series_asserted':False,'story_series_identity_asserted':False,'original_work_identity_asserted':False,
        'same_edition_identity_asserted':False,'product_sku_identity_asserted':False,'independent_volume_numbers_inferred':False,
        'paper_electronic_same_edition_asserted':False,'global_author_identity_asserted':False,'global_author_alias_asserted':False,
        'usage_permissions':dict(_PERMISSIONS),'evidence':deepcopy(row['evidence']),
        'source_evidence':[{'source_url':_ENDPOINT,'raw_sha256':receipt['sha256'],'locator':pointer,
            'http_method':'POST','request_data':deepcopy(receipt['request_data'])}],
        'isbn_source_evidence':{'source_url':_ENDPOINT,'raw_sha256':receipt['sha256'],'locator':pointer+'/isbn','literal':row['isbn']}}


def verify_kadokawa_print_references(manifest_path: str | Path, sha256: str, *, reference_ids, cache=None) -> list[dict]:
    """Replay an explicit print-ID subset, with private operation-only caching.

    The input is one bounded print preparation scope, not the outer inventory
    or an electronic scope. ISBN lookup metadata is not a correspondence proof.
    """
    return _verify(manifest_path,sha256,reference_ids=reference_ids,cache=cache,contract=_PRINT)


def verify_kadokawa_trade_references(manifest_path: str | Path, sha256: str, *, reference_ids, cache=None) -> list[dict]:
    """Replay explicit form-1 references; 単行本 asserts no binding or size.

    ISBN literals and normalized lookup keys are separate metadata facts.
    Private discovery conveys no work, edition, story or permission identity.
    """
    return _verify(manifest_path,sha256,reference_ids=reference_ids,cache=cache,contract=_TRADE)


def _verify(manifest_path, sha256, *, reference_ids, cache, contract):
    if (not isinstance(reference_ids,(list,tuple,set,frozenset)) or not reference_ids
            or any(not isinstance(i,str) or not _ID.fullmatch(i) for i in reference_ids)
            or len(reference_ids) != len(set(reference_ids)) or len(reference_ids) > 200):
        raise ValueError('KAD print explicit unique nonempty IDs required, at most 200')
    if cache is not None and not isinstance(cache,dict):
        raise ValueError('KAD print operation cache must be a dict')
    cache = {} if cache is None else cache
    path = Path(manifest_path).resolve()
    key = (f'kadokawa-{contract.name}-private-product-references-v1',str(path),sha256)
    try:
        scope = cache.get(key)
        state = _PrintScope if contract == _PRINT else _TradeScope
        if not isinstance(scope,state):
            scope = _scope(path,sha256,cache) if contract == _PRINT else _scope(path,sha256,cache,contract=contract)
            cache[key] = scope
        if any(i not in scope.index for i in reference_ids):
            raise ValueError('KAD print selected ID outside pinned batch')
        return deepcopy([_row(scope,i) for i in sorted(reference_ids)])
    except (KeyError,TypeError,AttributeError,UnicodeDecodeError,json.JSONDecodeError,IndexError) as exc:
        raise ValueError('KAD print private reference cannot be reproduced') from exc
