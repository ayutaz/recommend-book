import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from recommend_book.light_novel_futaba_references_v1 import verify_futaba_references


LABEL = 'モンスター文庫'
SEARCH = 'https://www.futabasha.co.jp/search?label_name=%E3%83%A2%E3%83%B3%E3%82%B9%E3%82%BF%E3%83%BC%E6%96%87%E5%BA%AB'
FIRST = ('https://book-api.futabasha.co.jp/books?v=2&request_mode=1&media=0&age_verification=3&request_from=&request_to=&keyword=&publication_form=&label_name=%E3%83%A2%E3%83%B3%E3%82%B9%E3%82%BF%E3%83%BC%E6%96%87%E5%BA%AB&series_name=&tag_name=&grouping=&author_name=&jdcn_code=&per_page=25&page=1&display_order=0&image_size=330')
SCRIPT_URLS = ['https://www.futabasha.co.jp/js/app.js?id=69e58d6027437571400034f11aa419a5',
              'https://www.futabasha.co.jp/js/SearchResult.207854cf2f689f4e.js']
WITNESSES = [
    'window.API_URL="https://book-api.futabasha.co.jp",window.API_URL_PROD="https://book-api.futabasha.co.jp"',
    'showMore:function(){this.page=this.page+1,this.fetchTargetItems()}',
    'n.data.books.results.total_pages>e.page?e.showMoreFlag=!0:e.showMoreFlag=!1',
    'resultContentCount=n.data.books.results.total_counts',
    'axios.get(API_URL+"/books",{params:{v:2,request_mode:e.request_mode,media:e.media,age_verification:e.age_verification,request_from:e.request_from,request_to:e.request_to,keyword:e.toSpaceJoinedString(e.keyword),publication_form:e.toSpaceJoinedString(e.publication_form),label_name:e.label_name,series_name:e.series_name,tag_name:e.tag_name,grouping:e.grouping,author_name:e.author_name,jdcn_code:e.jdcn_code?"".concat(e.jdcn_code,"0000000"):"",per_page:e.per_page,page:e.page,display_order:e.display_order,image_size:"330"}})',
    'defaultBookType:function(){if("rbook"==this.filter)return 1;if("ebook"==this.filter)return 2;if("abook"==this.filter)return 3;var e=this.bookInfoFromParent.web_rbooks_id,t=this.bookInfoFromParent.web_ebooks_id,o=this.bookInfoFromParent.web_abooks_id;return e?1:t?2:o?3:1}',
    '"/book/"+P.magazinePath+o.bookInfoFromParent.jdcn_code+"?type="+P.defaultBookType',
    'web_author_type_overwrite?o.bookInfoFromParent.book_authors[e-1].web_author_type_overwrite:o.bookInfoFromParent.book_authors[e-1].author_type_name',
]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return {'path': path.name, 'sha256': digest(path.read_bytes())}


def save(env):
    p = env['root'] / 'receipts.jsonl'
    p.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in env['receipts']), encoding='utf-8')
    env['manifest']['source_receipts'] = {'path': p.name, 'sha256': digest(p.read_bytes())}
    env['manifest']['observed_route_proof'] = pin(env['root'] / 'proof.json', env['proof'])
    env['sha'] = pin(env['path'], env['manifest'])['sha256']


def source(env, name, url, raw, content_type):
    (env['root'] / name).write_bytes(raw)
    r = {'source_url': url, 'final_url': url, 'status': 200, 'content_type': content_type,
         'raw_path': name, 'sha256': digest(raw), 'byte_length': len(raw)}
    env['receipts'].append(r)
    return r


def fixture(tmp_path):
    assert parse_qs(urlsplit(SEARCH).query)['label_name'] == [LABEL]
    assert parse_qs(urlsplit(FIRST).query)['label_name'] == [LABEL]
    env = {'root': tmp_path, 'path': tmp_path / 'manifest.json', 'receipts': []}
    witnesses = []
    for script, literals in enumerate([WITNESSES[:1], WITNESSES[1:]]):
        text = ';'.join(literals)
        r = source(env, f'asset{script}.js', SCRIPT_URLS[script], text.encode(), 'application/x-javascript')
        for literal in literals:
            start = text.index(literal)
            witnesses.append({'source_url': r['source_url'], 'raw_sha256': r['sha256'], 'literal': literal,
                              'locator': {'kind': 'utf8_decoded_character_range', 'start': start, 'end': start + len(literal)}})
    pages = []
    for number in range(1, 17):
        url = FIRST.replace('&page=1&', f'&page={number}&')
        books = []
        for ordinal in range(25 if number < 16 else 3):
            ident = (number - 1) * 25 + ordinal + 1
            books.append({'jdcn_code': f'opaque-{ident}', 'book_name': f'架空作品 {ident} 巻３【特装版】',
                'isbn_code': '9781234567897', 'label_id': 14, 'label_name': LABEL,
                'publication_form': 4, 'publication_form_value': '文庫', 'release_dt': '2026-11-30',
                'start_dt': '2026-11-30', 'grouping_no': 999, 'web_rbooks_id': ident,
                'web_ebooks_id': ident + 1000, 'web_abooks_id': None,
                'book_authors': [{'author_number': 2, 'author_name': '架空著者', 'author_type_name': '著', 'web_author_type_overwrite': None},
                                 {'author_number': 1, 'author_name': '架空絵師', 'author_type_name': '著', 'web_author_type_overwrite': 'イラスト'}],
                'introductions_400': 'PRIVATE_PROSE', 'web_introductions': 'PRIVATE_DESCRIPTION',
                'book_images': [{'url': 'https://example.invalid/private.jpg'}]})
        result = {'total_counts': 378, 'total_pages': 16, 'per_page': 25, 'current_page': number}
        raw = json.dumps({'books': {'book_informations': books, 'results': result}}, ensure_ascii=False).encode()
        r = source(env, f'page{number}.json', url, raw, 'application/json')
        pages.append({'page': number, 'source_url': url, 'raw_path': r['raw_path'], 'raw_sha256': r['sha256'],
                      'byte_length': len(raw), 'results': result, 'count': len(books),
                      'has_more_by_official_rule': number < 16,
                      'next_url': FIRST.replace('&page=1&', f'&page={number+1}&') if number < 16 else None,
                      'results_locator': '/books/results', 'items_locator': '/books/book_informations'})
    env['proof'] = {'frame_id': 'futaba-monster-bunko-ui-observed-finite-route-v1',
        'navigation': [{'source_url': 'https://www.futabasha.co.jp/', 'observed_link_text': 'レーベル一覧', 'link_url': 'https://www.futabasha.co.jp/labels/'},
                       {'source_url': 'https://www.futabasha.co.jp/labels', 'observed_heading': LABEL, 'observed_link_url': SEARCH}],
        'rendered_search': {'source_url': SEARCH, 'document_title': f'「レーベル : {LABEL}」一覧 | 双葉社',
            'initial_card_count': 25, 'first_title_literal': '架空作品 1 巻３【特装版】',
            'first_title_url': 'https://www.futabasha.co.jp/book/opaque-1?type=1', 'next_control_text': 'もっと見る'},
        'first_request_url_observed_in_page_resource_inventory': FIRST, 'official_js_assets_read': 2,
        'finite_pagination': {'fixed_first_page': 1, 'fixed_final_page': 16, 'reported_total': 378, 'per_page': 25,
                              'only_parameter_changed': 'page', 'next_rule': 'page + 1 only while total_pages > page'},
        'script_witnesses': witnesses}
    env['manifest'] = {'version': 1, 'frame_id': 'futaba-monster-bunko-products-v1', 'unit': 'publisher_product_reference',
        'label': LABEL, 'status': 'complete_observed_finite_index', 'index_pages_complete': True,
        'publisher_reported_total': 378, 'fixed_first_page': 1, 'fixed_final_page': 16, 'per_page': 25,
        'search_url': SEARCH, 'first_api_url': FIRST, 'pagination_pages': pages, 'collection_holds': [],
        'roster': pin(tmp_path / 'roster.json', {'references': [{'title': 'FORGED_ROSTER'}]})}
    save(env)
    return env


def modify_book(env, edit, page=1):
    r = env['receipts'][page + 1]
    path = env['root'] / r['raw_path']
    data = json.loads(path.read_bytes())
    edit(data['books'])
    raw = json.dumps(data, ensure_ascii=False).encode()
    path.write_bytes(raw)
    r.update(sha256=digest(raw), byte_length=len(raw))
    env['manifest']['pagination_pages'][page - 1].update(raw_sha256=r['sha256'], byte_length=len(raw))
    save(env)


def verify(env):
    return verify_futaba_references(env['path'], env['sha'])


def test_replays_full_titles_roles_and_finite_scope_without_roster_or_prose(tmp_path):
    env = fixture(tmp_path)
    rows = verify(env)
    assert len(rows) == len({r['publisher_reference_id'] for r in rows}) == 378
    row = rows[0]
    assert row['publisher_reference_id'] == 'futaba-product:opaque-1'
    assert row['source_unit'] == 'publisher_product_reference'
    assert row['source_url'] == 'https://www.futabasha.co.jp/book/opaque-1?type=1'
    assert row['title'] == '架空作品 1 巻３【特装版】' and row['isbn'] == '9781234567897'
    assert row['credits'] == [{'role': 'author', 'name': '架空著者'}, {'role': 'illustrator', 'name': '架空絵師'}]
    assert row['credit_literals'][1]['role'] == 'イラスト'
    assert row['publisher'] is None and row['form'] == '文庫'
    assert row['label'] == LABEL and row['label_literals'] == [LABEL]
    assert row['official_lightnovel_tag'] is None and not row['story_series_asserted']
    assert row['metadata_conflict'] is False and row['bibliographic_variants'] == []
    assert row['source_scope_membership']['filter_literal'] == LABEL
    assert row['source_scope_membership']['source_label_id_literal'] == 14
    assert 'volume' not in row and 'series' not in row
    dumped = json.dumps(rows)
    assert all(value not in dumped for value in ['FORGED_ROSTER', 'PRIVATE_PROSE', 'PRIVATE_DESCRIPTION', 'private.jpg'])


def test_unknown_card_label_isbn_and_unmarked_role_are_kept_separate_from_explicit_filter_membership(tmp_path):
    env = fixture(tmp_path)
    def edit(books):
        b = books['book_informations'][0]
        b.update(label_name=None, isbn_code='9780000000000')
        b['book_authors'][0].update(author_type_name=None, author_number=1)
    modify_book(env, edit)
    row = verify(env)[0]
    assert row['label'] is None and row['label_literals'] == []
    assert row['source_scope_membership']['card_label_literal'] is None
    assert row['source_scope_membership']['filter_literal'] == LABEL
    assert row['isbn'] is None and row['isbn_source_literal'] == '9780000000000'
    assert row['credits'][0] == {'role': 'unknown', 'name': '架空著者'}


@pytest.mark.parametrize('kind', ['label_id', 'label_name', 'total', 'duplicate', 'empty_title', 'last_count'])
def test_semantic_mutations_remain_rejected_after_repinning_raw(tmp_path, kind):
    env = fixture(tmp_path)
    def edit(books):
        book = books['book_informations'][0]
        if kind == 'label_id': book['label_id'] = 15
        elif kind == 'label_name': book['label_name'] = 'Mノベルス'
        elif kind == 'total': books['results']['total_counts'] = 379
        elif kind == 'duplicate': books['book_informations'][1]['jdcn_code'] = book['jdcn_code']
        elif kind == 'empty_title': book['book_name'] = ''
        else: books['book_informations'].pop()
    modify_book(env, edit, page=16 if kind == 'last_count' else 1)
    with pytest.raises(ValueError): verify(env)


@pytest.mark.parametrize('kind', ['next', 'query', 'receipt_query', 'script_literal', 'receipt_status', 'path_escape'])
def test_own_query_next_route_witness_and_receipt_scope_are_verified(tmp_path, kind):
    env = fixture(tmp_path)
    if kind == 'next': env['manifest']['pagination_pages'][0]['next_url'] = FIRST.replace('&page=1&', '&page=3&')
    elif kind == 'query': env['manifest']['first_api_url'] = FIRST.replace('media=0', 'media=2')
    elif kind == 'receipt_query': env['receipts'][2]['source_url'] = FIRST.replace('media=0', 'media=2')
    elif kind == 'script_literal': env['proof']['script_witnesses'][1]['literal'] += 'forged'
    elif kind == 'receipt_status': env['receipts'][2]['status'] = 503
    else: env['receipts'][2]['raw_path'] = '..\\outside.json'
    save(env)
    with pytest.raises(ValueError): verify(env)


def test_only_explicit_isbn_and_observed_media_url_template_are_used(tmp_path):
    env = fixture(tmp_path)
    def edit(books):
        books['book_informations'][1].update(jdcn_code='9781234567897', isbn_code=None, web_rbooks_id=None)
    modify_book(env, edit)
    row = verify(env)[1]
    assert row['publisher_reference_id'] == 'futaba-product:9781234567897'
    assert row['source_url'] == 'https://www.futabasha.co.jp/book/9781234567897?type=2'
    assert row['isbn'] is None and row['isbn_source_literal'] is None


def test_manifest_receipt_and_raw_hash_tampering_fail_closed(tmp_path):
    env = fixture(tmp_path)
    with pytest.raises(ValueError, match='hash'): verify_futaba_references(env['path'], '0' * 64)
    (tmp_path / 'receipts.jsonl').write_bytes(b'[]')
    with pytest.raises(ValueError, match='hash'): verify(env)
    save(env)
    (tmp_path / 'page1.json').write_bytes(b'{}')
    with pytest.raises(ValueError, match='hash'): verify(env)
