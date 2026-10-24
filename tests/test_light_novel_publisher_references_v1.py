import gzip
import hashlib
import json
from pathlib import Path

import pytest

from recommend_book.light_novel_publisher_references_v1 import verify_publisher_references


ISBN = '9784758047678'


@pytest.mark.parametrize(('provider', 'module', 'function'), [
    ('kodansha', 'recommend_book.light_novel_kodansha_references_v1', 'verify_kodansha_references'),
    ('hifumi', 'recommend_book.light_novel_hifumi_references_v1', 'verify_hifumi_references'),
    ('overlap', 'recommend_book.light_novel_overlap_references_v1', 'verify_overlap_references'),
])
def test_additional_reference_providers_receive_the_original_pinned_manifest(tmp_path, monkeypatch, provider, module, function):
    import importlib
    path = tmp_path / 'source-frame.json'
    digest = 'a' * 64
    calls = []
    def verify(pinned_path, expected_sha):
        calls.append((pinned_path, expected_sha))
        return [{'publisher_reference_id': provider + '-product:opaque', 'isbn': None}]
    monkeypatch.setattr(importlib.import_module(module), function, verify)
    rows = verify_publisher_references(provider, path, digest)
    assert calls == [(path, digest)]
    assert rows == [{'publisher_reference_id': provider + '-product:opaque', 'isbn': None}]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receipt(root, url, document, *, compressed=False):
    raw = document.encode('utf-8')
    if compressed:
        raw = gzip.compress(raw)
    sha = hashlib.sha256(raw).hexdigest()
    path = root / 'raw' / (sha + '.bin')
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(raw)
    return {'source_url': url, 'final_url': url, 'http_status': 200, 'status': 200,
            'sha256': sha, 'byte_length': len(raw), 'raw_path': str(path.relative_to(root)),
            'content_type': 'text/html; charset=utf-8', 'retrieved_at': '2026-10-25T00:00:00Z'}


def earth_card(title='作品の完全な題２', author='著者名', slug='esn001'):
    return f'''<article class="c-card06"><a href="https://earthstar-novel.com/book/detail/{slug}/">
    <h2 class="c-card06__head">{title}</h2></a>
    <div class="c-card06__info-item"><b class="c-card06__info-head">著者：</b><span class="c-card06__info-txt">{author}</span></div>
    <div class="c-card06__info-item"><b class="c-card06__info-head">イラスト：</b><span class="c-card06__info-txt">絵の人</span></div>
    <span class="c-card06__label-item">コミカライズ</span><p class="synopsis">PRIVATE_PROSE</p></article>'''


def earth_fixture(tmp_path, *, compressed=False, cards=None):
    url = 'https://earthstar-novel.com/book/'
    source = receipt(tmp_path, url, '<title>作品一覧</title>' + (cards or earth_card()), compressed=compressed)
    row = {'publisher_work_reference_id': 'earthstar-work:esn001', 'official_work_reference_id': 'esn001',
           'kind': 'publisher_work_reference', 'source_url': url + 'detail/esn001/', 'title': '作品の完全な題２',
           'credits': [{'role': 'author', 'role_display': '著者：', 'name': '著者名'},
                       {'role': 'illustrator', 'role_display': 'イラスト：', 'name': '絵の人'}],
           'isbn': None, 'label_display': None, 'novel_or_comic_form': 'unknown',
           'card_markers': ['コミカライズ'], 'explicit_form_markers': [], 'index_source_snapshot': source}
    roster_sha = write_json(tmp_path / 'roster.json', [row])
    page = {'source_url': url, 'source_snapshot': source, 'cards': [row], 'next_urls': [], 'last_urls': []}
    pages = tmp_path / 'pages.jsonl'
    pages.write_text(json.dumps(page, ensure_ascii=False) + '\n', encoding='utf-8')
    manifest = {'schema_version': 'earthstar-publisher-work-index-v1', 'source_url': url,
                'unit': 'publisher_work_reference_not_edition_or_story_series',
                'index_pages_complete': True, 'expected_linked_pages': 1, 'retrieved_pages': 1,
                'observed_card_occurrences': 1, 'unique_work_references': 1,
                'roster_path': 'roster.json', 'roster_sha256': roster_sha,
                'pages_checkpoint_path': 'pages.jsonl', 'pages_checkpoint_sha256': hashlib.sha256(pages.read_bytes()).hexdigest()}
    path = tmp_path / 'manifest.json'
    return path, manifest, source, row


def to_card(product='1', title='作品２', isbn=ISBN):
    field = f'<p><b>ISBN：</b>{isbn}</p>' if isbn else ''
    return f'''<li><a class="search_ul_a opac" href="https://www.tobooks.jp/contents/{product}">
    <div class="search_ul_tags"><p>ライトノベル</p><p>Celicaノベルス</p></div><h2 class="search_ul_ttl">{title}</h2>
    <div class="search_ul_author"><p>名前一</p><p>名前二</p></div><div class="search_ul_data">{field}<p><b>判型：</b>B6判</p></div>
    <p class="synopsis">PRIVATE_PROSE</p></a></li>'''


def to_fixture(tmp_path):
    url = 'https://www.tobooks.jp/contents?sp%5Blightnovel%5D=1&status%5Bavailable%5D=1&status%5Bpreorder%5D=1'
    document = f'''<form action="https://www.tobooks.jp/contents" method="GET"></form><div id="search_sort"><p>検索結果 2 件</p></div>
    <ul id="search_ul">{to_card()}{to_card('2', '作品２ 特典付き')}</ul><div class="pagination-container"><span aria-current="page">1</span></div>'''
    source = receipt(tmp_path, url, document)
    rows = []
    for identity, title in [('1', '作品２'), ('2', '作品２ 特典付き')]:
        rows.append({'publisher_product_reference_id': 'tobooks-product:' + identity, 'official_product_id': identity,
                     'kind': 'publisher_product_reference', 'source_url': 'https://www.tobooks.jp/contents/' + identity,
                     'title': title, 'isbn': ISBN, 'credits': [{'role': 'unknown', 'name': '名前一'}, {'role': 'unknown', 'name': '名前二'}],
                     'label_display': ['Celicaノベルス'], 'format_display': 'B6判', 'official_genre_lightnovel': True,
                     'index_source_snapshot': source})
    roster_sha = write_json(tmp_path / 'roster.json', rows)
    page = {'page': 1, 'source_snapshot': source, 'product_cards': 2, 'next_literal_hrefs': [], 'linked_max_page': 1, 'current_page': [1]}
    pages = tmp_path / 'pages.jsonl'
    pages.write_text(json.dumps(page) + '\n', encoding='utf-8')
    manifest = {'schema_version': 'tobooks-lightnovel-product-frame-v2', 'source_url': url,
                'unit': 'publisher_product_reference_not_story_series', 'index_pages_complete': True,
                'expected_linked_pages_at_entry': 1, 'retrieved_pages': 1, 'publisher_reported_total_items': 2,
                'observed_card_occurrences': 2, 'unique_product_references': 2,
                'roster_path': 'roster.json', 'roster_sha256': roster_sha,
                'pages_checkpoint_path': 'pages.jsonl', 'pages_checkpoint_sha256': hashlib.sha256(pages.read_bytes()).hexdigest()}
    return tmp_path / 'manifest.json', manifest, source, rows


def gagaga_fixture(tmp_path, *, duplicate_variant=False):
    current = '<ul><li><a href="202609.html#01"><p>完全な題２<br>著／書き手 イラスト／絵の人</p></a></li></ul>'
    old = '<ul><li><a href="200705.html#01">旧題１</a>著／旧著者 イラスト／旧絵師</li></ul>'
    if duplicate_variant:
        old += '<ul><li><a href="200705.html#01">旧題１ 別の表示</a>著／旧著者 イラスト／旧絵師</li></ul>'
    release = f'''<section id="01"><div id="title"><h3><span>＜ガガガブックスf＞</span>新刊の題１</h3>
    <span class="textsize14">著／新著者 イラスト／新絵師</span>ISBN {ISBN}</div><p>PRIVATE_PROSE</p></section>'''
    cover = '<a href="200705.html#01"><img alt="旧題１" src="not-fetched.jpg"></a>'
    pages = [receipt(tmp_path, 'https://gagagabunko.jp/' + route, doc) for route, doc in [
        ('lineup/index.html', current), ('lineup/indexOld.html', old), ('release/index.html', release), ('lineup/index_cover.html', cover)]]
    manifest = {'version': 1, 'frame_id': 'gagaga-official-lineup-indexes-v1',
                'unit': 'publisher_lineup_item_reference_not_story_series_or_edition',
                'source_pages': pages, 'finite_index_complete': True, 'finite_primary_pages': 3,
                'alternative_view_validation_pages': 1, 'unique_item_references': 3,
                'all_rows_before_url_dedup': 4 if duplicate_variant else 3,
                'current_release_items': 1, 'current_release_isbn13': 1,
                'unique_url_metadata_variants': int(duplicate_variant)}
    return tmp_path / 'manifest.json', manifest, pages


def verify(provider, path, manifest):
    return verify_publisher_references(provider, path, write_json(path, manifest))


def test_earthstar_minimal_work_reference_and_adaptation_is_not_form(tmp_path):
    path, manifest, _, _ = earth_fixture(tmp_path)
    rows = verify('earthstar', path, manifest)
    assert len(rows) == 1
    row = rows[0]
    assert row['publisher_reference_id'] == 'earthstar-work:esn001'
    assert row['title'] == '作品の完全な題２'
    assert row['source_unit'] == 'publisher_work_reference'
    assert row['isbn'] is None and row['form'] is None and row['label'] is None
    assert row['credits'][0] == {'role': 'author', 'name': '著者名'}
    assert row['story_series_asserted'] is False
    assert 'PRIVATE_PROSE' not in json.dumps(rows)


def test_earthstar_site_title_is_not_per_card_imprint_evidence(tmp_path):
    path, manifest, source, _ = earth_fixture(tmp_path)
    doc = (tmp_path / source['raw_path']).read_text(encoding='utf-8').replace(
        '<title>作品一覧</title>', '<title>作品一覧 | アース・スターノベル</title>')
    replacement = receipt(tmp_path, source['source_url'], doc)
    pages = tmp_path / 'pages.jsonl'
    page = json.loads(pages.read_text(encoding='utf-8'))
    page['source_snapshot'] = replacement
    pages.write_text(json.dumps(page), encoding='utf-8')
    manifest['pages_checkpoint_sha256'] = hashlib.sha256(pages.read_bytes()).hexdigest()
    rows = verify('earthstar', path, manifest)
    assert rows[0]['label'] is None
    assert rows[0]['label_literals'] == []


def test_tobooks_shared_isbn_products_and_unknown_credit_roles_stay_separate(tmp_path):
    path, manifest, _, _ = to_fixture(tmp_path)
    rows = verify('tobooks', path, manifest)
    assert len(rows) == 2 and len({r['publisher_reference_id'] for r in rows}) == 2
    assert {r['isbn'] for r in rows} == {ISBN}
    assert all(r['credits'][0]['role'] == 'unknown' for r in rows)
    assert rows[0]['label'] == 'Celicaノベルス' and rows[0]['form'] == 'B6判'
    assert 'PRIVATE_PROSE' not in json.dumps(rows)


def test_gagaga_raw_only_contract_ignores_unpinned_private_projection(tmp_path):
    path, manifest, _ = gagaga_fixture(tmp_path)
    write_json(tmp_path / 'gagaga-lineup-items-v1.json', [{'title': 'TAMPERED_PROJECTION'}])
    rows = verify('gagaga', path, manifest)
    assert len(rows) == 3
    assert {r['isbn'] for r in rows} == {None, ISBN}
    assert 'TAMPERED_PROJECTION' not in json.dumps(rows)
    assert 'PRIVATE_PROSE' not in json.dumps(rows)
    assert all(not r['story_series_asserted'] for r in rows)


def test_gagaga_same_url_variants_remain_explicitly_held(tmp_path):
    path, manifest, _ = gagaga_fixture(tmp_path, duplicate_variant=True)
    rows = verify('gagaga', path, manifest)
    held = [r for r in rows if r['metadata_conflict']]
    assert len(held) == 1 and len(held[0]['bibliographic_variants']) == 1
    assert held[0]['title'] == '旧題１'


@pytest.mark.parametrize('provider', ['earthstar', 'tobooks', 'gagaga'])
def test_manifest_hash_mismatch(provider, tmp_path):
    path = tmp_path / 'manifest.json'
    write_json(path, {})
    with pytest.raises(ValueError, match='hash'):
        verify_publisher_references(provider, path, '0' * 64)


@pytest.mark.parametrize('case', ['raw_hash', 'status', 'final_origin', 'path_escape', 'absolute_path', 'declared_size'])
def test_source_receipt_and_raw_rejected(tmp_path, case):
    path, manifest, source, _ = earth_fixture(tmp_path)
    pages_path = tmp_path / 'pages.jsonl'
    page = json.loads(pages_path.read_text(encoding='utf-8'))
    receipt_value = page['source_snapshot']
    if case == 'raw_hash':
        (tmp_path / receipt_value['raw_path']).write_bytes(b'altered')
    elif case == 'status':
        receipt_value['http_status'] = 403
    elif case == 'final_origin':
        receipt_value['final_url'] = 'https://evil.invalid/book/'
    elif case == 'path_escape':
        receipt_value['raw_path'] = '../outside.bin'
    elif case == 'absolute_path':
        receipt_value['raw_path'] = str((tmp_path / receipt_value['raw_path']).resolve())
    else:
        receipt_value['byte_length'] += 1
    pages_path.write_text(json.dumps(page), encoding='utf-8')
    manifest['pages_checkpoint_sha256'] = hashlib.sha256(pages_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        verify('earthstar', path, manifest)


def test_pinned_roster_projection_tamper_even_after_resigning_container(tmp_path):
    path, manifest, _, _ = earth_fixture(tmp_path)
    roster = json.loads((tmp_path / 'roster.json').read_text(encoding='utf-8'))
    roster[0]['credits'][0]['name'] = 'forged author'
    manifest['roster_sha256'] = write_json(tmp_path / 'roster.json', roster)
    with pytest.raises(ValueError, match='projection'):
        verify('earthstar', path, manifest)


def test_tobooks_never_accepts_detail_as_source(tmp_path):
    path, manifest, _, _ = to_fixture(tmp_path)
    page = json.loads((tmp_path / 'pages.jsonl').read_text(encoding='utf-8'))
    page['source_snapshot']['source_url'] = 'https://www.tobooks.jp/contents/1'
    (tmp_path / 'pages.jsonl').write_text(json.dumps(page), encoding='utf-8')
    manifest['pages_checkpoint_sha256'] = hashlib.sha256((tmp_path / 'pages.jsonl').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='URL|route'):
        verify('tobooks', path, manifest)


def test_conflicting_same_id_is_rejected(tmp_path):
    path, manifest, _, _ = earth_fixture(tmp_path, cards=earth_card() + earth_card('違う題'))
    manifest['observed_card_occurrences'] = 2
    with pytest.raises(ValueError, match='conflict'):
        verify('earthstar', path, manifest)


def test_compressed_source_and_comments_scripts_are_not_cards(tmp_path):
    path, manifest, _, _ = earth_fixture(tmp_path, compressed=True,
        cards=earth_card() + '<!--' + earth_card('隠し題', slug='hidden') + '--><script>' + earth_card('偽題', slug='script') + '</script>')
    assert len(verify('earthstar', path, manifest)) == 1


def test_bounded_decompression(tmp_path, monkeypatch):
    import recommend_book.light_novel_publisher_references_v1 as module
    path, manifest, _, _ = earth_fixture(tmp_path, compressed=True, cards=earth_card() + 'x' * 20000)
    monkeypatch.setattr(module, 'MAX_SOURCE_BYTES', 10000)
    with pytest.raises(ValueError, match='size|bound'):
        verify('earthstar', path, manifest)


def test_unsupported_provider_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='provider'):
        verify_publisher_references('unknown', tmp_path / 'unused', '0' * 64)


@pytest.mark.parametrize('field,value', [('title', 'forged'), ('isbn', '9784758046428'),
                                        ('label_display', ['invented label']), ('format_display', '漫画'),
                                        ('credits', [{'role': 'author', 'name': '名前一'}])])
def test_tobooks_pinned_projection_cannot_invent_bibliography(tmp_path, field, value):
    path, manifest, _, rows = to_fixture(tmp_path)
    rows[0][field] = value
    manifest['roster_sha256'] = write_json(tmp_path / 'roster.json', rows)
    with pytest.raises(ValueError, match='projection'):
        verify('tobooks', path, manifest)


def test_identical_same_id_deduplicates_without_inflation(tmp_path):
    path, manifest, _, row = earth_fixture(tmp_path, cards=earth_card() * 2)
    page = json.loads((tmp_path / 'pages.jsonl').read_text(encoding='utf-8'))
    page['cards'] = [row, row]
    (tmp_path / 'pages.jsonl').write_text(json.dumps(page), encoding='utf-8')
    manifest['pages_checkpoint_sha256'] = hashlib.sha256((tmp_path / 'pages.jsonl').read_bytes()).hexdigest()
    manifest['observed_card_occurrences'] = 2
    rows = verify('earthstar', path, manifest)
    assert len(rows) == 1 and len(rows[0]['source_evidence']) == 2


def test_unrelated_to_sidebar_product_is_not_in_list(tmp_path):
    path, manifest, source, _ = to_fixture(tmp_path)
    oldpath = tmp_path / source['raw_path']
    doc = oldpath.read_text(encoding='utf-8') + '<ul id="sidebar">' + to_card('99', 'sidebar only') + '</ul>'
    replacement = receipt(tmp_path, source['source_url'], doc)
    page = json.loads((tmp_path / 'pages.jsonl').read_text(encoding='utf-8'))
    page['source_snapshot'] = replacement
    (tmp_path / 'pages.jsonl').write_text(json.dumps(page), encoding='utf-8')
    manifest['pages_checkpoint_sha256'] = hashlib.sha256((tmp_path / 'pages.jsonl').read_bytes()).hexdigest()
    assert len(verify('tobooks', path, manifest)) == 2


def test_invalid_isbn_and_gagaga_source_route_are_rejected(tmp_path):
    path, manifest, pages = gagaga_fixture(tmp_path)
    release = pages[2]
    doc = (tmp_path / release['raw_path']).read_text(encoding='utf-8').replace(ISBN, '9784758047679')
    manifest['source_pages'][2] = receipt(tmp_path, release['source_url'], doc)
    with pytest.raises(ValueError, match='ISBN'):
        verify('gagaga', path, manifest)
    manifest['source_pages'][2]['source_url'] = 'https://gagagabunko.jp/lineup/202610.html'
    with pytest.raises(ValueError, match='URL'):
        verify('gagaga', path, manifest)


def test_windows_relative_raw_paths_work_without_absolute_paths(tmp_path):
    path, manifest, _, _ = earth_fixture(tmp_path)
    page = json.loads((tmp_path / 'pages.jsonl').read_text(encoding='utf-8'))
    page['source_snapshot']['raw_path'] = page['source_snapshot']['raw_path'].replace('/', '\\')
    (tmp_path / 'pages.jsonl').write_text(json.dumps(page), encoding='utf-8')
    manifest['pages_checkpoint_sha256'] = hashlib.sha256((tmp_path / 'pages.jsonl').read_bytes()).hexdigest()
    assert len(verify('earthstar', path, manifest)) == 1


def test_tobooks_publisher_total_cannot_be_replaced_with_card_count(tmp_path):
    path, manifest, source, _ = to_fixture(tmp_path)
    doc = (tmp_path / source['raw_path']).read_text(encoding='utf-8').replace('検索結果 2 件', '検索結果 3 件')
    page = json.loads((tmp_path / 'pages.jsonl').read_text(encoding='utf-8'))
    page['source_snapshot'] = receipt(tmp_path, source['source_url'], doc)
    (tmp_path / 'pages.jsonl').write_text(json.dumps(page), encoding='utf-8')
    manifest['pages_checkpoint_sha256'] = hashlib.sha256((tmp_path / 'pages.jsonl').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='total'):
        verify('tobooks', path, manifest)
