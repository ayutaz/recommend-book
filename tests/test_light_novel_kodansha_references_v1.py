import hashlib
import json

import pytest

from recommend_book.light_novel_kodansha_references_v1 import verify_kodansha_references


START = 'https://www.kodansha.co.jp/comic/labels/lanove'
LABEL = '講談社ラノベ文庫'


def pin(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def card(number):
    return f'''<a class="app-product-list-item" href="https://www.kodansha.co.jp/comic/products/{number:010d}">
        <div class="app-product-list-item-detail-name"><strong>試験作品{number}　巻３【特装版】</strong></div>
        <div class="app-product-list-item-detail-authors">著：架空著者，イラスト：架空絵師</div>
        <div class="app-product-list-item-detail-label">{LABEL}</div>
        <div class="app-product-list-item-detail-introduction">PRIVATE_PROSE</div>
        <img src="https://example.invalid/private.jpg" alt="PRIVATE_IMAGE"></a>'''


def fixture(tmp_path):
    pages = []
    for page in range(1, 36):
        url = START if page == 1 else START + f'?page={page}'
        links = [START, START + '?page=35']
        if page < 35:
            links.append(START + f'?page={page + 1}')
        links = sorted(set(links))
        count = 20 if page < 35 else 4
        document = f'<h2><strong>{LABEL}作品一覧</strong></h2><strong>684件</strong>'
        document += ''.join(card((page - 1) * 20 + i) for i in range(1, count + 1))
        document += ''.join(f'<a href="{href}">頁</a>' for href in links)
        raw = document.encode('utf-8')
        target = tmp_path / f'page-{page}.bin'
        target.write_bytes(raw)
        source = {'source_url': url, 'final_url': url, 'status': 200,
            'content_type': 'text/html; charset=utf-8', 'sha256': hashlib.sha256(raw).hexdigest(),
            'byte_length': len(raw), 'raw_path': target.name}
        pages.append({'page': page, 'source_receipt': source, 'cards': count,
            'linked_pagination_urls': links,
            'explicit_next_url': START + f'?page={page + 1}' if page < 35 else None})
    manifest = {'version': 1, 'frame_id': 'kodansha-lanove-product-index-v1', 'label': LABEL,
        'source_url': START, 'unit': 'publisher_product_reference_not_story_series_original_work_or_edition',
        'fixed_publisher_reported_total': 684, 'fixed_last_linked_page': 35,
        'pages_acquired': 35, 'product_reference_occurrences': 684, 'unique_product_ids': 684,
        'index_pages_complete': True, 'terminal_no_next_observed': True, 'holds': [],
        'page_receipts_path': 'pages.json', 'roster_path': 'roster.json'}
    env = {'root': tmp_path, 'pages': pages, 'manifest': manifest, 'path': tmp_path / 'manifest.json'}
    # An exported roster is intentionally not a metadata authority.
    manifest['roster_sha256'] = pin(tmp_path / 'roster.json', [{'title': 'FORGED_ROSTER_TEXT'}])
    save(env)
    return env


def save(env):
    env['manifest']['page_receipts_sha256'] = pin(env['root'] / 'pages.json', env['pages'])
    env['sha'] = pin(env['path'], env['manifest'])


def change_raw(env, old, new, *, page=1):
    source = env['pages'][page - 1]['source_receipt']
    target = env['root'] / source['raw_path']
    document = target.read_text(encoding='utf-8')
    assert old in document
    raw = document.replace(old, new, 1).encode('utf-8')
    target.write_bytes(raw)
    source.update(sha256=hashlib.sha256(raw).hexdigest(), byte_length=len(raw))
    save(env)


def verify(env):
    return verify_kodansha_references(env['path'], env['sha'])


def test_replays_fixed_finite_product_frame_without_roster_prose_or_identity_guess(tmp_path):
    env = fixture(tmp_path)
    rows = verify(env)
    assert len(rows) == 684
    row = rows[0]
    assert row['publisher_reference_id'] == 'kodansha-product:0000000001'
    assert row['source_unit'] == 'publisher_product_reference'
    assert row['title'] == '試験作品1　巻３【特装版】'
    assert row['credits'] == [{'role': 'author', 'name': '架空著者'}, {'role': 'illustrator', 'name': '架空絵師'}]
    assert row['publisher'] is None and row['isbn'] is None and row['form'] is None
    assert row['label'] == LABEL and row['label_literals'] == [LABEL]
    assert row['official_lightnovel_tag'] is True and row['story_series_asserted'] is False
    assert row['metadata_conflict'] is False and row['bibliographic_variants'] == []
    dumped = json.dumps(rows, ensure_ascii=False)
    assert 'FORGED_ROSTER_TEXT' not in dumped and 'PRIVATE_PROSE' not in dumped
    assert 'PRIVATE_IMAGE' not in dumped and 'private.jpg' not in dumped


@pytest.mark.parametrize('change', ['total', 'label_heading', 'card_label', 'product_host',
    'product_query', 'duplicate_product', 'empty_title', 'missing_next', 'terminal_next'])
def test_semantic_changes_are_held_even_after_repinning_raw_and_receipts(tmp_path, change):
    env = fixture(tmp_path)
    if change == 'total': change_raw(env, '684件', '685件')
    elif change == 'label_heading': change_raw(env, LABEL + '作品一覧', 'Kラノベブックス作品一覧')
    elif change == 'card_label': change_raw(env, f'>{LABEL}</div>', '>Kラノベブックス</div>')
    elif change == 'product_host': change_raw(env, 'https://www.kodansha.co.jp/comic/products/0000000001', 'https://example.invalid/products/0000000001')
    elif change == 'product_query': change_raw(env, '/comic/products/0000000001"', '/comic/products/0000000001?extra=1"')
    elif change == 'duplicate_product': change_raw(env, '/comic/products/0000000002"', '/comic/products/0000000001"')
    elif change == 'empty_title': change_raw(env, '試験作品1　巻３【特装版】', '')
    elif change == 'missing_next': change_raw(env, f'<a href="{START}?page=2">頁</a>', '')
    else: change_raw(env, '</a>', f'</a><a href="{START}?page=36">次</a>', page=35)
    with pytest.raises(ValueError):
        verify(env)


@pytest.mark.parametrize('change', ['receipt_status', 'receipt_redirect', 'receipt_size',
    'receipt_url', 'source_path', 'checkpoint_next', 'checkpoint_pages', 'checkpoint_cards'])
def test_receipt_scope_hashes_and_complete_checkpoint_are_required(tmp_path, change):
    env = fixture(tmp_path)
    page = env['pages'][0]
    source = page['source_receipt']
    if change == 'receipt_status': source['status'] = 503
    elif change == 'receipt_redirect': source['final_url'] = 'https://example.invalid/'
    elif change == 'receipt_size': source['byte_length'] += 1
    elif change == 'receipt_url': source['source_url'] += '?page=1'
    elif change == 'source_path': source['raw_path'] = '..\\outside.bin'
    elif change == 'checkpoint_next': page['explicit_next_url'] = START + '?page=3'
    elif change == 'checkpoint_pages': env['pages'].pop()
    else: page['cards'] = 19
    save(env)
    with pytest.raises(ValueError):
        verify(env)


def test_raw_checkpoint_and_manifest_hash_changes_are_rejected(tmp_path):
    env = fixture(tmp_path)
    with pytest.raises(ValueError, match='hash'):
        verify_kodansha_references(env['path'], '0' * 64)
    (tmp_path / 'pages.json').write_bytes(b'[]')
    with pytest.raises(ValueError, match='hash'):
        verify(env)
    save(env)
    (tmp_path / 'page-1.bin').write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash'):
        verify(env)


def test_unmarked_creator_names_never_become_order_inferred_authors(tmp_path):
    env = fixture(tmp_path)
    change_raw(env, '著：架空著者，イラスト：架空絵師', '名前一 名前二')
    assert verify(env)[0]['credits'] == [{'role': 'unknown', 'name': '名前一 名前二'}]


def test_opaque_numeric_id_does_not_become_isbn_or_volume(tmp_path):
    env = fixture(tmp_path)
    change_raw(env, '/comic/products/0000000001"', '/comic/products/9781234567897"')
    row = verify(env)[0]
    assert row['publisher_reference_id'] == 'kodansha-product:9781234567897'
    assert row['isbn'] is None and 'volume' not in row
