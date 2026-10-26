from copy import deepcopy
import hashlib
import json

import pytest

from recommend_book.light_novel_kadokawa_references_v1 import verify_kadokawa_references
from recommend_book.light_novel_publisher_books_v1 import verify_kadokawa_books


ENDPOINT = 'https://www.kadokawa.co.jp/product/search/'
RIGHTS = {'purpose': 'discovery and bibliographic identity reference only', 'synopsis_ai_input': False,
          'image_reuse': False, 'redistribution_of_raw': False, 'additional_permissions': 'unknown'}
USAGE = {'purpose': 'private_bibliographic_discovery', 'private_use_only': True, 'loopback_ui_only': True,
         'public_display': 'unknown', 'commercial': 'unknown', 'prose_AI': False, 'images': False,
         'raw_redistribution': False, 'upstream_permission_grant_asserted': False}
IDS = ['kadokawa-product:123456789012', 'kadokawa-product:123456789013', 'kadokawa-product:123456789014']


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(e, name, value):
    p = e['root']/name;p.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return {'path': str(p), 'sha256': sha(p.read_bytes())}


def save(e):
    for key in ['metadata_handoff', 'terminal', 'usage_boundary_pin', 'robots_policy_pin',
                'product_form_name_evidence_pin', 'target_id_check']:
        e['proof'][key] = pin(e, key+'.json', e[key])
    e['source_manifest']['parent_terminal_pin'] = deepcopy(e['proof']['terminal'])
    e['proof']['source_manifest'] = pin(e, 'source_manifest.json', e['source_manifest'])
    e['sha'] = pin(e, 'scope.json', e['proof'])['sha256']


@pytest.fixture
def source(tmp_path):
    e = {'root': tmp_path}
    receipts = []
    for page, codes in [(1, ['123456789012', '123456789013']), (2, ['123456789014'])]:
        items = [{'itemCode': c, 'title': '完全題　'+c[-1]+'【電子限定】', 'isbn': None, 'label_id': 330,
            'label_site_id': '1043', 'product_form_id': 13, 'release_date': '2020-01-01',
            'disp_author_name': '<li>著者&nbsp;架空　著者</li><li>イラスト&nbsp;絵の人</li><li>役割未記載</li>',
            'catchphrase': 'PRIVATE_PROSE', 'bookCoverUrl': 'PRIVATE_IMAGE'} for c in codes]
        payload = {'count_raw': {'book': 40}, 'result': {'book': items}, 'product_form': {'13': {'name': '電子専売'}}}
        raw = json.dumps(payload, ensure_ascii=False).encode();f=tmp_path/f'page{page}.json';f.write_bytes(raw)
        receipts.append({'source_url': ENDPOINT, 'final_url': ENDPOINT, 'http_status': 200, 'http_method': 'POST',
            'request_data': {'pageno': str(page), 'pageno_book': str(page), 'pageno_media': str(page),
                             'size': '20', 'label': '330', 'releaseDate': '1'},
            'raw_path': f.name, 'sha256': sha(raw), 'byte_length': len(raw), 'content_type': 'application/json',
            'retrieved_at': '2026-10-25T00:00:00+00:00', 'location': None, 'rights': deepcopy(RIGHTS)})
    e['source_manifest'] = {'schema_version': 'kadokawa-publisher-books-v1', 'rights_scope': 'reference_only_bibliography',
        'index_complete': False, 'national_or_historical_coverage_proved': False, 'pages': deepcopy(receipts)}
    # Legacy output is the comparison projection, never the public reference.
    p=pin(e, 'legacy.json', e['source_manifest'])
    e['metadata_handoff'] = verify_kadokawa_books(p['path'], p['sha256'])
    e['terminal'] = {'schema_version': 'kadokawa-publisher-books-v1', 'rights_scope': 'reference_only_bibliography',
        'index_complete': True, 'national_or_historical_coverage_proved': False, 'pages': deepcopy(receipts),
        'closure': {'330': {'reported_total': 40, 'unique_editions': 40, 'retrieved_pages': 2, 'expected_pages': 2,
            'source_frame_complete': True, 'total_stable': True, 'historical_entire_label_complete': False}}}
    robot = b'User-agent: *\nCrawl-delay: 20\nDisallow:\n';(tmp_path/'robots.txt').write_bytes(robot)
    e['robots_policy_pin'] = {'www.kadokawa.co.jp': {'user_agent': 'recommend-book-bibliography-research/1.0',
        'applied_group': ['*'], 'crawl_delay_seconds': 20, 'request_rate': None, 'effective_interval_seconds': 20,
        'source_url': 'https://www.kadokawa.co.jp/robots.txt', 'raw_path': 'robots.txt', 'sha256': sha(robot)}}
    e['usage_boundary_pin'] = {'publisher_prose_classification_input_admitted': False,
        'publisher_prose_public_display_admitted': False, 'publisher_image_reuse_admitted': False,
        'raw_evidence': 'Private local only; not redistribution permission'}
    e['product_form_name_evidence_pin'] = {'explicit_display_names': [
        {'product_form_id': 13, 'name': '電子専売', 'json_pointer': '/product_form/13/name'}]}
    e['target_id_check'] = {'DB_writes': 0}
    keys = ['publisher_book_id', 'official_item_code', 'title', 'source_url', 'label', 'official_label_id',
            'product_form_id', 'isbn', 'credits', 'evidence']
    members = [dict(publisher_reference_id=IDS[i], **{k: deepcopy(r[k]) for k in keys}) for i,r in enumerate(e['metadata_handoff'])]
    e['proof'] = {'schema_version': 'kadokawa-electronic-publisher-reference-scope-v1',
        'rights_scope': 'reference_only_bibliography', 'reference_scope': 'private_bibliographic_discovery',
        'usage': deepcopy(USAGE), 'rights_conditions': deepcopy(RIGHTS), 'selected_reference_ids': IDS.copy(),
        'selected_publisher_book_ids': [r['publisher_book_id'] for r in members], 'candidate_count': 3,
        'raw_pages_in_scope': 2, 'source_page_count': 2, 'terminal_source_items': 40, 'members': members,
        'scope_predicates': {'product_form_id': 13, 'isbn': None, 'source_unit': 'publisher_product_reference',
            'actual_work_identity_asserted': False, 'edition_identity_asserted': False,
            'story_series_identity_asserted': False, 'original_work_identity_asserted': False},
        'usage_boundary_explanation': {'existing_rights_scope_is_upstream_grant': False,
            'external_LLM_or_model_use': False, 'raw_redistribution_permission': False}}
    save(e)
    return e


def verify(e, ids=None, cache=None):
    return verify_kadokawa_references(e['root']/'scope.json', e['sha'], reference_ids=ids or [IDS[0]], cache=cache)


def test_explicit_subset_reads_only_its_page_and_operation_cache_reuses_index_and_pages(source, monkeypatch):
    import recommend_book.light_novel_publisher_books_v1 as legacy
    import recommend_book.light_novel_kadokawa_references_v1 as module
    original, calls = legacy._checked_bytes, []
    original_scope, scope_calls = module._scope, []
    def counted(path, expected):
        calls.append(path.name)
        return original(path, expected)
    monkeypatch.setattr(legacy, '_checked_bytes', counted)
    def counted_scope(*args):
        scope_calls.append(1)
        return original_scope(*args)
    monkeypatch.setattr(module, '_scope', counted_scope)
    cache = {}
    a, = verify(source, cache=cache)
    assert calls == ['page1.json']
    b, = verify(source, [IDS[1]], cache)
    assert calls == ['page1.json']
    c, = verify(source, [IDS[2]], cache)
    assert calls == ['page1.json', 'page2.json']
    assert scope_calls == [1]
    assert [r['publisher_reference_id'] for r in [a,b,c]] == IDS
    assert a['title'] == '完全題　2【電子限定】' and a['official_product_id'] == '123456789012'
    assert a['source_unit'] == 'publisher_product_reference' and a['reference_scope'] == 'private_bibliographic_discovery'
    assert a['form'] == '電子専売' and a['product_form_id'] == 13 and a['isbn'] is None
    assert a['credits'][0] == {'role': 'author', 'name': '架空　著者'}
    assert a['credit_literals'][0]['role_literal'] == '著者' and a['credit_literals'][0]['locator']
    assert a['credits'][-1] == {'role': 'unknown', 'name': '役割未記載'}
    assert a['credit_literals'][-1]['role_literal'] is None
    assert a['usage_permissions'] == {'purpose': 'private_bibliographic_discovery', 'public_display': 'unknown',
        'commercial_reuse': 'unknown', 'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False}
    assert all(a[k] is None for k in ['publisher', 'official_lightnovel_tag', 'original_language'])
    assert all(a[k] is False for k in ['actual_work_identity_asserted', 'edition_identity_asserted',
        'story_series_asserted', 'original_work_identity_asserted', 'light_novel_confirmed'])
    assert all(x not in json.dumps(a) for x in ['PRIVATE_PROSE', 'PRIVATE_IMAGE'])
    a['title'] = 'caller mutation'
    assert verify(source, cache=cache)[0]['title'] != a['title']


@pytest.mark.parametrize('ids', [[], [IDS[0], IDS[0]], ['kadokawa-product:999999999999'], ['9781234567897']])
def test_selection_is_mandatory_nonempty_unique_and_bounded(source, ids):
    with pytest.raises((ValueError, TypeError)):
        verify_kadokawa_references(source['root']/'scope.json', source['sha'], reference_ids=ids)


@pytest.mark.parametrize('kind', ['usage', 'rights', 'metadata_title', 'metadata_role', 'selector', 'scope',
                                 'terminal_receipt', 'raw_hash', 'form', 'isbn', 'same_id_changed_title'])
def test_scope_conditions_projection_and_selected_source_changes_fail_closed(source, kind):
    if kind == 'usage': source['proof']['usage']['public_display'] = True
    elif kind == 'rights': source['usage_boundary_pin']['publisher_prose_public_display_admitted'] = True
    elif kind == 'metadata_title': source['proof']['members'][0]['title'] = '偽題'
    elif kind == 'metadata_role': source['proof']['members'][0]['credits'][0]['role'] = '原作'
    elif kind == 'selector': source['proof']['members'][0]['evidence']['json_pointer'] = '/result/book/19'
    elif kind == 'scope': source['proof']['reference_scope'] = 'public_service'
    elif kind == 'terminal_receipt': source['terminal']['pages'][0]['request_data']['label'] = '55'
    elif kind == 'raw_hash': (source['root']/'page1.json').write_bytes(b'{}')
    else:
        p=source['root']/'page1.json';payload=json.loads(p.read_bytes());item=payload['result']['book'][0]
        if kind == 'form': item['product_form_id'] = 3
        elif kind == 'isbn': item['isbn'] = '9781234567897'
        else: item['title'] = '同IDの別題'
        raw=json.dumps(payload,ensure_ascii=False).encode();p.write_bytes(raw)
        for manifest in [source['source_manifest'],source['terminal']]:
            manifest['pages'][0].update(sha256=sha(raw),byte_length=len(raw))
        for r in source['metadata_handoff']+source['proof']['members']:
            if r['evidence']['request_data']['pageno_book']=='1':r['evidence']['source_sha256']=sha(raw)
    save(source)
    with pytest.raises(ValueError): verify(source)


def test_unselected_page_is_not_opened_and_legacy_output_keeps_its_contract(source):
    p=source['root']/'page2.json';p.write_bytes(b'changed unselected page')
    assert verify(source)[0]['publisher_reference_id'] == IDS[0]
    with pytest.raises(ValueError): verify(source,[IDS[2]])
    # Legacy rows still describe the old matching API, not direct display.
    page=source['source_manifest']['pages'][0]
    f=pin(source,'legacy-one.json',{'schema_version':'kadokawa-publisher-books-v1',
        'rights_scope':'reference_only_bibliography','pages':[page]})
    rows=verify_kadokawa_books(f['path'],f['sha256'])
    assert rows[0]['kind']=='edition' and rows[0]['publisher']=='KADOKAWA'
    assert rows[0]['evidence']['raw_path']==str((source['root']/'page1.json').resolve())


def test_legacy_complete_projection_golden_is_unchanged(tmp_path):
    from test_light_novel_publisher_books_v1 import publisher_books
    path, expected, _, _ = publisher_books.__wrapped__(tmp_path)
    rows = verify_kadokawa_books(path, expected)
    for row in rows:
        row['evidence']['raw_path'] = '<RAW_PATH>'
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    # Captured before factoring the legacy in-memory replay helper.
    assert sha(canonical) == 'a2944edf2824b7c77999fbfe8af379aec72426ddf3438d3bc6d3469b74d3fc8e'
