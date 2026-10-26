import copy
import hashlib
import json
from urllib.parse import quote

import pytest

import recommend_book.light_novel_futaba_workgroups_v1 as module
from recommend_book.light_novel_futaba_references_v1 import _FIRST, _row


JS_URL = 'https://www.futabasha.co.jp/js/ContentDetail.558616518c9104f2.js'
WITNESSES = {
    'existing_books_heading': 'createElementVNode)("h3",null,"既刊",-1)',
    'existing_books_members': 'renderList)(o.groupingBooks,',
    'grouping_search_link': '6==o.groupingBooks.length?((0,n.openBlock)(),(0,n.createElementBlock)("div",u,[(0,n.createElementVNode)("a",{href:"/search?grouping=".concat(o.searchKeyword),class:"parts__more-btn"},t[0]||(t[0]=[(0,n.createElementVNode)("p",null,"もっと見る",-1)]),8,m)]))',
    'detail_grouping_to_existing_books': '"雑誌"!==this.basicInfo.publication_form_value&&null!==(s=this.basicInfo.grouping_books)&&void 0!==s&&s.length?((0,n.openBlock)(),(0,n.createBlock)(p,{key:2,groupingBooks:this.basicInfo.grouping_books,searchKeyword:this.basicInfo.grouping},null,8,["groupingBooks","searchKeyword"]))',
    'author_other_works_excludes_group': 'author_id:e,except_grouping:t.basicInfo.grouping',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def put(env, name, value):
    path = env['root'] / name
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return {'path': name, 'sha256': sha(path.read_bytes())}


def raw_source(env, name, url, value, javascript=False):
    raw = value.encode() if javascript else json.dumps(value, ensure_ascii=False).encode()
    (env['root'] / name).write_bytes(raw)
    return {'source_url': url, 'status': 200, 'final_url': url, 'location': None,
            'content_type': 'application/javascript' if javascript else 'application/json',
            'raw_path': name, 'sha256': sha(raw), 'byte_length': len(raw),
            'http_redirects_followed': 0, 'http_retry_count': 0}


def save(env):
    root = env['root']
    text = ';'.join(WITNESSES.values())
    if env.get('bad_js'):
        text = text.replace('既刊', '別資料')
    receipt = raw_source(env, 'detail.js', JS_URL, text, True)
    env['proof']['detail_js_receipt'] = put(env, 'js-receipt.json', receipt)
    witnesses = []
    for kind, literal in WITNESSES.items():
        if env.get('bad_js'):
            literal = literal.replace('既刊', '別資料')
        start = text.index(literal)
        witnesses.append({'kind': kind, 'literal': literal, 'source_url': JS_URL,
            'raw_sha256': receipt['sha256'], 'locator': {'kind': 'utf8_decoded_character_range', 'start': start, 'end': start + len(literal)}})
    wp = put(env, 'witnesses.json', {'schema_version': 'futaba-detail-grouping-route-witnesses-v1', 'witnesses': witnesses})
    env['proof']['detail_js_witnesses'] = wp
    env['relation']['reused_js_witnesses'] = wp
    env['relation']['receipt'] = put(env, 'detail-receipt.json', env['detail_receipt'])
    env['proof']['detail_receipt'] = env['relation']['receipt']
    ep = env['example']
    ep['observed_detail_js_witnesses'] = wp
    ep['observed_book_page_relation'] = put(env, 'relation.json', env['relation'])
    ep['observed_grouping_search_dom'] = put(env, 'dom.json', env['dom'])
    ep['safe_projection'] = put(env, 'selected.json', env['selected'])
    ep['source_receipts'] = [put(env, 'query-receipt.json', env['query_receipt'])]
    ep['reused_official_next_rule'] = put(env, 'next-rule.json', {'next_rule': 'page + 1 only while total_pages > page'})
    env['terminal']['pagination_pages'][0]['receipt'] = ep['source_receipts'][0]
    ep['terminal_frame'] = put(env, 'query-terminal.json', env['terminal'])
    env['proof']['exact_group_example'] = put(env, 'example.json', ep)
    frames = []
    receipts = []
    for i, page in enumerate(env['pages']):
        receipts.append(page['receipt'])
        frames.append({'frame_id': ['mnovels', 'mnovels-f'][i], 'manifest': put(env, f'frame{i}.json', {'pagination_pages': [page]})})
    log = root / 'm-receipts.jsonl'
    log.write_text(''.join(json.dumps(r) + '\n' for r in receipts), encoding='utf-8')
    terminal = put(env, 'm-terminal.json', {'frames': frames, 'receipt_log': {'path': log.name, 'sha256': sha(log.read_bytes())}})
    old = put(env, 'old-manifest.json', {'observed_route_proof': ep['reused_official_next_rule']})
    env['proof']['mnovels_references'] = put(env, 'm-proof.json', {'terminal': terminal, 'observed_probe_contract': {'path': 'observed-m-route.json'}, 'reused_monster_manifest': old})
    env['manifest_sha'] = put(env, 'proof.json', env['proof'])['sha256']


@pytest.fixture
def source(tmp_path, monkeypatch):
    env = {'root': tmp_path, 'proof': {'schema_version': 'futaba-mnovels-workgroups-proof-v1',
            'expected_source_references': 5, 'expected_nonempty_groups': 4, 'expected_grouped_references': 5,
            'expected_unique_grouping_literals': 3}, 'pages': []}
    rows, books = [], []
    for i, grouping in enumerate(['架空グループ', '架空グループ', '架空グループ ', '単独の題【外伝】', '単独の題【外伝】']):
        books.append({'jdcn_code': f'opaque-{i}', 'book_name': f'完全題 {i} 巻３【特装版】',
            'label_id': 12 if i < 2 or i == 4 else 21, 'label_name': 'Ｍノベルス' if i < 2 or i == 4 else 'Ｍノベルスｆ',
            'isbn_code': '9781234567897' if i != 3 else None, 'publication_form_value': '単行本',
            'release_dt': '2020-01-01', 'start_dt': '2020-01-01', 'grouping': grouping, 'grouping_no': 999-i,
            'web_rbooks_id': i+10 if i != 1 else None, 'web_ebooks_id': i+20, 'web_abooks_id': None,
            'book_authors': [{'author_name': f'著者{i}', 'author_type_name': None if i == 3 else '著', 'web_author_type_overwrite': None},
                             {'author_name': '絵師', 'author_type_name': '著', 'web_author_type_overwrite': 'イラスト'}],
            'introductions_400': 'PRIVATE_PROSE', 'book_images': [{'url': 'https://example.invalid/image.jpg'}]})
    for i, label in enumerate(['Ｍノベルス', 'Ｍノベルスｆ']):
        from recommend_book.light_novel_futaba_references_v1 import _LABEL
        url = _FIRST.replace(quote(_LABEL), quote(label))
        frame_books = [b for b in books if b['label_id'] == (12 if i == 0 else 21)]
        receipt = raw_source(env, f'm-page{i}.json', url, {'books': {'book_informations': frame_books}})
        env['pages'].append({'page': 1, 'source_url': url, 'receipt': receipt})
        for j, book in enumerate(frame_books):
            rows.append(_row(book, receipt, j, 'observed-m-route.json', label_literal=label, label_id=12 if i == 0 else 21,
                search_url='https://www.futabasha.co.jp/search?label_name='+quote(label)))
    env['rows'] = rows
    env['dependency_calls'] = []
    def authenticated_m_dependency(path, digest):
        env['dependency_calls'].append((path, digest))
        return copy.deepcopy(env['rows'])
    monkeypatch.setattr(module, 'verify_futaba_mnovels_references', authenticated_m_dependency)
    rows_by_id = {r['publisher_reference_id']: r for r in rows}
    first, second = rows_by_id['futaba-product:opaque-0'], rows_by_id['futaba-product:opaque-1']
    title = books[0]['grouping']
    url = _FIRST.replace('&label_name='+quote('モンスター文庫'), '&label_name=').replace('&grouping=&', '&grouping='+quote(title)+'&')
    query_books = copy.deepcopy(books[:2])
    results = {'total_counts': 2, 'total_pages': 1, 'per_page': 25, 'current_page': 1}
    env['query_receipt'] = raw_source(env, 'query.json', url, {'books': {'results': results, 'book_informations': query_books}})
    child = {k: books[1][k] for k in ['jdcn_code', 'book_name', 'web_rbooks_id', 'web_ebooks_id', 'web_abooks_id']}
    detail = copy.deepcopy(books[0]); detail['grouping_books'] = [child]
    detail_url = 'https://book-api.futabasha.co.jp/book_details?media=1&jdcn_code=opaque-0&image_size=330+55+1000&grouping_media=0'
    env['detail_receipt'] = raw_source(env, 'detail.json', detail_url, {'book_details': {'book_informations': detail}})
    env['relation'] = {'schema_version': 'futaba-single-grouping-existing-books-projection-v1', 'source_unit': 'publisher_work_group',
        'grouping_title': title, 'independent_official_group_id': None, 'story_series_asserted': False,
        'original_work_identity_asserted': False, 'independent_volume_numbers_inferred': False,
        'parent_product': {'title': books[0]['book_name'], 'source_url': first['source_url'], 'opaque_jdcn_code': 'opaque-0',
            'isbn_source_literal': books[0]['isbn_code'], 'label': books[0]['label_name'], 'label_id': 12,
            'credits': [{'role_literal': c['role'], 'name': c['name'], 'role_field': c['role_field']} for c in first['credit_literals']]},
        'members': [{'title': books[1]['book_name'], 'opaque_jdcn_code': 'opaque-1', 'source_url': second['source_url'], 'isbn': None, 'credits': [],
            'member_evidence': {'source_url': detail_url, 'raw_sha256': env['detail_receipt']['sha256'], 'locator': '/book_details/book_informations/grouping_books/0'}}],
        'member_count_observed': 1, 'more_link': {'text': 'もっと見る', 'href_literal': '/search?grouping='+title, 'navigation_performed': False}}
    env['dom'] = {'schema_version': 'futaba-observed-grouping-search-dom-v1', 'page_url': 'https://www.futabasha.co.jp/search?grouping='+quote(title),
        'document_title': '「'+title+'」一覧 | 双葉社', 'observed_first_api_url': url, 'h3_product_card_count': 2, 'own_book_link_count': 2,
        'first_title': first['title'], 'first_relative_href': '/book/opaque-0?type=1', 'last_title': second['title'], 'last_relative_href': '/book/opaque-1?type=2', 'more_control_count': 0,
        'raw_html_saved': False, 'raw_html_sha256': None}
    env['terminal'] = {'schema_version': 'futaba-single-publisher-workgroup-terminal-frame-v1', 'source_unit': 'publisher_work_group',
        'grouping_title': title, 'independent_official_group_id': None, 'search_url': env['dom']['page_url'], 'first_api_url': url,
        'status': 'complete_exact_observed_api_filter', 'reported_total': 2, 'first_page': 1, 'final_page': 1, 'per_page': 25,
        'pagination_pages': [{'page': 1, 'source_url': url, 'raw_sha256': env['query_receipt']['sha256'], 'raw_path': 'query.json', 'results': results,
            'count': 2, 'has_more_by_official_rule': False, 'next_url': None, 'results_locator': '/books/results', 'items_locator': '/books/book_informations'}]}
    selected = []
    for normalized in [first, second]:
        row = copy.deepcopy(normalized)
        row['opaque_jdcn_code'] = row['publisher_reference_id'].removeprefix('futaba-product:')
        book = books[int(row['opaque_jdcn_code'].split('-')[1])]
        row.update(grouping_title_literal=book['grouping'], grouping_no_source_literal=book['grouping_no'],
                   media_source_literals={key: book.get(key) for key in ['web_rbooks_id', 'web_ebooks_id', 'web_abooks_id']})
        row['credit_literals'] = [{'role_literal': c['role'], 'name': c['name'], 'role_field': c['role_field'], 'locator': c['locator']} for c in row['credit_literals']]
        selected.append(row)
    env['selected'] = {'members': selected}
    env['example'] = {'schema_version': 'futaba-single-publisher-workgroup-proof-v1', 'source_unit': 'publisher_work_group'}
    save(env)
    return env


def verify(env):
    return module.verify_futaba_mnovels_workgroups(env['root']/'proof.json', env['manifest_sha'])


def test_source_only_groups_preserve_singletons_exact_literals_media_and_unknowns(source):
    groups = verify(source)
    assert len(source['dependency_calls']) == 1
    assert source['dependency_calls'][0] == (source['root']/'m-proof.json', source['proof']['mnovels_references']['sha256'])
    assert len(groups) == 4 and sum(len(g['members']) for g in groups) == 5
    first, other_label, spaced, singleton = groups
    assert other_label['title'] == singleton['title'] and other_label['reference_id'] != singleton['reference_id']
    assert other_label['source_scope']['label_ids'] == [12] and singleton['source_scope']['label_ids'] == [21]
    assert first['title'] == '架空グループ' and spaced['title'] == '架空グループ '
    assert first['reference_id'] != spaced['reference_id']
    assert first['reference_id_kind'] == 'local_hash_of_publisher_source_field_key'
    assert first['independent_official_group_id'] is None
    assert [len(m['media_references']) for m in first['members']] == [2, 1]
    assert first['members'][1]['source_url'].endswith('?type=2')
    assert first['members'][0]['credits'][1]['role'] == 'illustrator'
    assert first['frame_membership_complete'] is True and first['exact_grouping_filter_complete'] is True
    assert spaced['exact_grouping_filter_complete'] is None
    assert all(g['global_membership_complete'] is None and g['authors'] is None and g['publisher'] is None
               and g['label'] is None and g['official_lightnovel_tag'] is None and not g['story_series_asserted'] for g in groups)
    assert singleton['members'][0]['isbn'] is None and singleton['members'][0]['credits'][0]['role'] == 'unknown'
    assert singleton['members'][0]['grouping_no_source_literal'] == 996
    assert 'PRIVATE_PROSE' not in json.dumps(groups) and 'image.jpg' not in json.dumps(groups)


@pytest.mark.parametrize('kind', ['raw_hash', 'path_escape', 'typed_member_title', 'typed_story', 'typed_parent_role',
                                 'typed_selected_role', 'independent_group_id', 'query', 'terminal', 'js_semantics',
                                 'missing_grouping', 'wrong_grouping_type'])
def test_raw_typed_scope_and_shared_semantic_mutations_fail_closed(source, kind):
    if kind == 'raw_hash': (source['root']/'m-page0.json').write_bytes(b'{}')
    elif kind == 'path_escape': source['pages'][0]['receipt']['raw_path'] = '../outside.json'
    elif kind == 'typed_member_title': source['relation']['members'][0]['title'] = '偽造題'
    elif kind == 'typed_story': source['relation']['story_series_asserted'] = True
    elif kind == 'typed_parent_role': source['relation']['parent_product']['credits'][0]['role_literal'] = '原作'
    elif kind == 'typed_selected_role': source['selected']['members'][0]['credits'][0]['role'] = 'original_author'
    elif kind == 'independent_group_id': source['relation']['independent_official_group_id'] = 'invented-id'
    elif kind == 'query': source['terminal']['first_api_url'] += '&media=2'
    elif kind == 'terminal': source['terminal']['pagination_pages'][0]['next_url'] = 'https://example.invalid/next'
    elif kind == 'js_semantics': source['bad_js'] = True
    else:
        p = source['root']/'m-page1.json'; data = json.loads(p.read_bytes())
        if kind == 'missing_grouping': data['books']['book_informations'][0].pop('grouping')
        else: data['books']['book_informations'][0]['grouping'] = 123
        raw = json.dumps(data, ensure_ascii=False).encode();p.write_bytes(raw)
        source['pages'][1]['receipt'].update(sha256=sha(raw), byte_length=len(raw))
    save(source)
    with pytest.raises(ValueError): verify(source)


def test_manifest_expected_hash_and_claimed_group_counts_are_checked(source):
    with pytest.raises(ValueError, match='hash'):
        module.verify_futaba_mnovels_workgroups(source['root']/'proof.json', '0'*64)
    source['proof']['expected_nonempty_groups'] = 2
    save(source)
    with pytest.raises(ValueError): verify(source)
