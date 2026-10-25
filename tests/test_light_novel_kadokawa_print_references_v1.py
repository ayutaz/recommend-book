from copy import deepcopy
import hashlib
import json

import pytest

from recommend_book.light_novel_kadokawa_print_references_v1 import verify_kadokawa_print_references
from recommend_book.light_novel_kadokawa_references_v1 import verify_kadokawa_references
from recommend_book.light_novel_publisher_books_v1 import verify_kadokawa_books


IDS = ['kadokawa-product:123456789012', 'kadokawa-product:123456789013', 'kadokawa-product:123456789014']


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(e, name, value, *, coverage=False):
    p = (e['root'].parent/'coverage' if coverage else e['root'])/name
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return {'path': str(p), 'sha256': sha(p.read_bytes())}


def save(e):
    e['proof']['terminal'] = pin(e, 'terminal.json', e['terminal'])
    e['proof']['input_preparation'] = pin(e, 'input.json', e['input'], coverage=True)
    e['exact']['input_preparation'] = e['proof']['input_preparation']
    e['exact']['terminal'] = e['proof']['terminal']
    e['proof']['exact_source_id_set_pin'] = pin(e, 'exact.json', e['exact'])
    e['hold']['input_preparation'] = e['proof']['input_preparation']
    e['proof']['invalid_isbn_separate_hold_pin'] = pin(e, 'hold.json', e['hold'])
    e['handoff']['input_preparation'] = e['proof']['input_preparation']
    e['proof']['metadata_handoff'] = pin(e, 'handoff.json', e['handoff'])
    e['source_manifest']['parent_terminal_pin'] = e['proof']['terminal']
    e['proof']['source_manifest'] = pin(e, 'subset.json', e['source_manifest'])
    e['proof']['product_form_name_evidence_pin'] = pin(e, 'forms.json', e['forms'])
    e['sha'] = pin(e, 'print-scope.json', e['proof'])['sha256']


@pytest.fixture
def source(tmp_path):
    from test_light_novel_kadokawa_references_v1 import source as electronic_source
    root = tmp_path/'frame';root.mkdir()
    e = electronic_source.__wrapped__(root)
    for n in (1,2):
        p=root/f'page{n}.json';d=json.loads(p.read_bytes());d['product_form']['3']={'name':'文庫'}
        for item in d['result']['book']:
            item.update(product_form_id=3,isbn='978-4-04-429101-3')
            item['disp_author_name'] += '<li>カバーイラスト&nbsp;表紙　画家</li>'
        raw=json.dumps(d,ensure_ascii=False).encode();p.write_bytes(raw)
        for m in (e['source_manifest'],e['terminal']):m['pages'][n-1].update(sha256=sha(raw),byte_length=len(raw))
    oldpin=pin(e,'legacy-print.json',e['source_manifest'])
    legacy=verify_kadokawa_books(oldpin['path'],oldpin['sha256'])
    parent=pin(e,'parent-handoff.json',legacy)
    members=[]
    for ref,r in zip(IDS,legacy,strict=True):
        members.append({'publisher_reference_id':ref,'source_reference_id':r['reference_id'],
            **{k:deepcopy(r[k]) for k in ['publisher_book_id','official_item_code','source_url','title','isbn','label',
                'label_scope','official_label_id','official_label_site_id','product_form_id','credits','release_date','evidence']},
            'title_literal':r['title'],'title_source_field':'/result/book/{position}/title',
            'title_transformation':'none; saved official title literal retains volume and punctuation; no synthesized work title',
            'isbn_literal':r['isbn'],'normalized_isbn13_lookup_key':'9784044291013', 'rights_scope':'reference_only_bibliography',
            'individual_publisher':None,'catalogue_owner':'KADOKAWA',
            'publisher_field_basis':'catalogue_owner_context; legacy projected publisher was injected, not an individual source literal',
            'identity_assertions':{'work_identity':False,'edition_identity':False,'story_series_identity':False,
                'original_work_identity':False,'light_novel_confirmed':False}})
    e['proof'].update(schema_version='kadokawa-print-private-reference-preparation-scope-v1',members=members,
        selected_source_reference_ids=[m['source_reference_id'] for m in members],
        scope_predicates={'product_form_id':3,'isbn':'explicit non-null literal with valid ISBN-13 check digit; identity unasserted',
            'source_unit':'publisher_product_reference','actual_work_identity_asserted':False,'edition_identity_asserted':False,
            'story_series_identity_asserted':False,'original_work_identity_asserted':False,'light_novel_confirmed':False},
        batch_partition_metadata={'batch_number':1,'batch_total':1,'pool_count':3,'whole_raw_pages_assigned_to_one_batch':True})
    e['input']={'version':'kadokawa-valid-isbn2091-finite-lookup-preparation-and-search-key-diagnostic-v1',
        'lookup_targets':[{'source_reference_id':m['source_reference_id'],'publisher_book_id':m['publisher_book_id'],
            'isbn_literal':m['isbn_literal'],'normalized_isbn13_lookup_key':m['normalized_isbn13_lookup_key']} for m in members]}
    e['exact']={'schema_version':'kadokawa-print-valid-isbn2091-exact-source-id-set-v1',
        'full_metadata_handoff':parent,'count':3,'source_reference_ids':[m['source_reference_id'] for m in members],
        'publisher_book_ids':[m['publisher_book_id'] for m in members],'publisher_reference_ids':IDS.copy()}
    e['hold']={'schema_version':'kadokawa-print-invalid-isbn-separate-hold-v1','count':1,
        'members':[{'source_reference_id':'publisher-edition:kadokawa:999999999999','publisher_book_id':'kadokawa:999999999999',
                    'isbn_literal':'9784044291014'}]}
    e['handoff']={'schema_version':'kadokawa-print-private-reference-metadata-handoff-v1',
        'parent_metadata_handoff':parent,'count':3,'members':deepcopy(members)}
    e['forms']={'explicit_display_names':[{'product_form_id':3,'name':'文庫','json_pointer':'/product_form/3/name'}]}
    save(e)
    return e


def verify(e, ids=None, cache=None):
    return verify_kadokawa_print_references(e['root']/'print-scope.json',e['sha'],reference_ids=ids or [IDS[0]],cache=cache)


def test_print_contract_preserves_literal_isbn_and_scope_and_only_reads_selected_pages_once(source,monkeypatch):
    import recommend_book.light_novel_publisher_books_v1 as legacy
    import recommend_book.light_novel_kadokawa_print_references_v1 as module
    original,calls,scopes=legacy._checked_bytes,[],[]
    original_scope=module._scope
    def counted(path,expected):calls.append(path.name);return original(path,expected)
    def counted_scope(*args):scopes.append(1);return original_scope(*args)
    monkeypatch.setattr(legacy,'_checked_bytes',counted);monkeypatch.setattr(module,'_scope',counted_scope)
    cache={};row,=verify(source,cache=cache)
    assert row['form']=='文庫' and row['product_form_id']==3 and row['isbn']==row['isbn_literal']=='978-4-04-429101-3'
    assert row['normalized_isbn13_lookup_key']=='9784044291013'
    assert row['local_reference_key']['source_scope']=='form3_valid_isbn_product_reference'
    assert row['catalogue_owner']=='KADOKAWA' and row['publisher'] is None and row['individual_publisher'] is None
    assert row['credit_literals'][0]['role_literal']=='著者' and row['credits'][0]['role']=='author'
    assert row['credit_literals'][-1]['role_literal']=='カバーイラスト' and row['credit_literals'][-1]['name']=='表紙　画家'
    verify(source,[IDS[1]],cache);verify(source,[IDS[2]],cache)
    assert calls==['page1.json','page2.json'] and scopes==[1]
    row['credits'][0]['name']='mutated'
    assert verify(source,cache=cache)[0]['credits'][0]['name']!='mutated'
    assert row['reference_scope']=='private_bibliographic_discovery' and row['usage_permissions']['public_display']=='unknown'
    assert all(row[k] is False for k in ['edition_identity_asserted','product_sku_identity_asserted',
        'story_series_identity_asserted','original_work_identity_asserted','light_novel_confirmed','global_author_identity_asserted'])
    assert row['official_lightnovel_tag'] is None and 'authors' not in row
    assert 'PRIVATE_PROSE' not in json.dumps(row) and 'PRIVATE_IMAGE' not in json.dumps(row)


@pytest.mark.parametrize('ids',[[],[IDS[0],IDS[0]],['kadokawa-product:999999999999'],IDS*67])
def test_selection_is_explicit_unique_nonempty_and_at_most_200(source,ids):
    with pytest.raises(ValueError):verify_kadokawa_print_references(source['root']/'print-scope.json',source['sha'],reference_ids=ids)


@pytest.mark.parametrize('kind',['schema','usage','metadata_isbn','lookup_key','role','pointer','terminal','path','form_witness','exact_set'])
def test_pin_metadata_scope_and_conditions_tampering_rejects(source,kind):
    if kind=='schema':source['proof']['schema_version']='kadokawa-electronic-publisher-reference-scope-v1'
    elif kind=='usage':source['proof']['usage']['commercial']=True
    elif kind=='metadata_isbn':source['proof']['members'][0]['isbn_literal']='9784044291014'
    elif kind=='lookup_key':source['proof']['members'][0]['normalized_isbn13_lookup_key']='9784044291014'
    elif kind=='role':source['proof']['members'][0]['credits'][0]['role']='原作'
    elif kind=='pointer':source['proof']['members'][0]['evidence']['json_pointer']='/result/book/19'
    elif kind=='terminal':source['terminal']['pages'][0]['request_data']['label']='55'
    elif kind=='path':source['source_manifest']['pages'][0]['raw_path']='../outside.json'
    elif kind=='form_witness':source['forms']['explicit_display_names'][0]['name']='電子専売'
    else:source['exact']['publisher_reference_ids'][0]='kadokawa-product:999999999999'
    save(source)
    with pytest.raises(ValueError):verify(source)


@pytest.mark.parametrize('kind',['same_id_retitle','isbn','isbn_absent','form','raw_hash'])
def test_selected_raw_facts_must_match_metadata_and_have_explicit_valid_isbn(source,kind):
    p=source['root']/'page1.json'
    if kind=='raw_hash':p.write_bytes(b'{}')
    else:
        d=json.loads(p.read_bytes());item=d['result']['book'][0]
        if kind=='same_id_retitle':item['title']='別題'
        elif kind=='isbn':item['isbn']='9784044291014'
        elif kind=='isbn_absent':del item['isbn']
        else:item['product_form_id']=13
        raw=json.dumps(d,ensure_ascii=False).encode();p.write_bytes(raw)
        for m in (source['source_manifest'],source['terminal']):m['pages'][0].update(sha256=sha(raw),byte_length=len(raw))
        for m in source['proof']['members']+source['handoff']['members']:
            if m['evidence']['request_data']['pageno_book']=='1':m['evidence']['source_sha256']=sha(raw)
    save(source)
    with pytest.raises(ValueError):verify(source)


def test_print_input_is_not_admitted_by_electronic_api_and_unselected_raw_stays_unread(source):
    with pytest.raises(ValueError):verify_kadokawa_references(source['root']/'print-scope.json',source['sha'],reference_ids=[IDS[0]])
    (source['root']/'page2.json').write_bytes(b'changed unselected')
    assert verify(source)[0]['publisher_reference_id']==IDS[0]
    with pytest.raises(ValueError):verify(source,[IDS[2]])


def test_future_pool_names_and_existing_ndl_hints_do_not_change_source_admission(source):
    source['exact']['schema_version']='kadokawa-print-valid-isbn-exact-source-id-set-v1'
    source['input']['version']='kadokawa-valid-isbn-finite-lookup-preparation-and-search-key-diagnostic-v1'
    for target in source['input']['lookup_targets']:
        target['same_isbn_candidates']=['saved-record-already-present']
    source['proof']['batch_partition_metadata'].update(batch_number=2,batch_total=3)
    save(source)
    assert verify(source)[0]['publisher_reference_id']==IDS[0]


def test_valid_isbn10_literal_is_not_replaced_by_normalized_lookup_key(source):
    source['proof']['scope_predicates']['isbn']='explicit non-null literal with valid ISBN-10/13 check digit; identity unasserted'
    p=source['root']/'page1.json';d=json.loads(p.read_bytes())
    d['result']['book'][0]['isbn']='4-04-429101-2'
    raw=json.dumps(d,ensure_ascii=False).encode();p.write_bytes(raw)
    for manifest in (source['source_manifest'],source['terminal']):
        manifest['pages'][0].update(sha256=sha(raw),byte_length=len(raw))
    for member in source['proof']['members']+source['handoff']['members']:
        if member['evidence']['request_data']['pageno_book']=='1':
            member['evidence']['source_sha256']=sha(raw)
        if member['publisher_reference_id']==IDS[0]:
            member.update(isbn='4-04-429101-2',isbn_literal='4-04-429101-2')
    source['input']['lookup_targets'][0]['isbn_literal']='4-04-429101-2'
    save(source)
    row,=verify(source)
    assert row['isbn']==row['isbn_literal']=='4-04-429101-2'
    assert row['normalized_isbn13_lookup_key']=='9784044291013'
    assert row['isbn_source_evidence']['literal']=='4-04-429101-2'


def test_shared_metadata_cache_still_checks_each_declared_pin_size(source):
    cache={};verify(source,cache=cache)
    source['proof']['product_form_name_evidence_pin']['byte_length']=1
    source['sha']=pin(source,'print-scope.json',source['proof'])['sha256']
    with pytest.raises(ValueError):verify(source,cache=cache)
