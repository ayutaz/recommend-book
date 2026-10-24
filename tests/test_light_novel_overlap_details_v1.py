import hashlib
import json

import pytest

from recommend_book.light_novel_overlap_details_v1 import verify_overlap_product_details


LABELS = {'NVL': 'オーバーラップノベルス', 'NVF': 'オーバーラップノベルスf', 'NVR': 'ロサージュノベルス'}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receipt(root, name, url, html):
    raw = root / ('raw/' + name + '.html')
    raw.parent.mkdir(exist_ok=True)
    raw.write_text(html, encoding='utf-8')
    return {'source_url': url, 'final_url': url, 'http_status': 200, 'location': None,
        'redirects_followed': False, 'retry_count': 0, 'content_type': 'text/html; charset=utf-8',
        'raw_path': str(raw.relative_to(root)), 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
        'byte_length': raw.stat().st_size, 'retrieved_at': '2026-10-24T00:00:00+00:00'}


def fixture(tmp_path, count=1, category='NVL'):
    frames = []
    for frame_category, total, pages in [('NVL', 427, 15), ('NVF', 187, 7), ('NVR', 25, 1)]:
        source = 'https://over-lap.co.jp/Form/Product/ProductList.aspx?cat=' + frame_category
        frames.append({'category': frame_category, 'label': LABELS[frame_category], 'source_url': source,
            'fixed_final_page': pages, 'terminal': True, 'card_occurrences': total,
            'unique_product_references': total,
            'pages': [{'page': n, 'card_count': 30 if n < pages else total - 30 * (pages-1),
                       'source_snapshot': {'unused_unrequested_page': True}} for n in range(1, pages+1)]})
    selected = next(f for f in frames if f['category'] == category)
    cards = ''.join(f'<div class="product-card"><a href="/title/product/0/opaque{i}/?cat={category}&amp;swrd=">'
        f'<span class="{category.lower()}__badge">{LABELS[category]}</span><div class="product-card__title">完全題{i}　３ 副題【合本】</div>'
        '<p>PRIVATE_CARD_PROSE</p><img alt="PRIVATE_IMAGE"></a></div>' for i in range(1, selected['pages'][0]['card_count'] + 1))
    card_html = '<title>' + LABELS[category] + '｜OVERLAP</title><div class="product-cards">' + cards + '</div>'
    card = receipt(tmp_path, 'card', selected['source_url'], card_html)
    selected['pages'][0]['source_snapshot'] = card
    base = {'schema_version': 'overlap-three-additional-product-frames-v1',
        'status': 'terminal_three_fixed_brand_frames', 'index_pages_complete': True,
        'collected_pages': 23, 'card_occurrences': 639, 'unique_opaque_product_ids': 639, 'frames': frames}
    bp = tmp_path / 'source-frame.json'
    base_sha = dump(bp, base)
    details = []
    for i in range(1, count + 1):
        url = f'https://over-lap.co.jp/title/product/0/opaque{i}/?cat={category}&swrd='
        spec = ('<div class="sidebar-buy__specs"><div class="sidebar-buy__spec-row">'
            '<span class="sidebar-buy__spec-label">ISBN</span><span class="sidebar-buy__spec-value">978-4-8240-1931-8</span></div>'
            '<div class="sidebar-buy__spec-row"><span class="sidebar-buy__spec-label">判　型</span>'
            '<span class="sidebar-buy__spec-value">B6判</span></div></div>')
        html = (f'<main class="product-main"><div class="product-contents"><section class="goods-explanation">'
            f'<div class="goods-explanation__body"><h1 class="goods-explanation__title">完全題{i}　３ 副題【合本】</h1>'
            '<p class="goods-explanation__author">人物\u2003イラスト／画家</p><p>PRIVATE_PROSE</p></div></section>'
            f'<aside class="product-sidebar sp"><div class="sidebar-buy">{spec}</div></aside></div>'
            f'<aside class="product-sidebar pc"><div class="sidebar-buy">{spec}</div></aside></main>'
            '<div class="product-card"><span class="nvf__badge">別レーベル</span>'
            '<span class="sidebar-buy__spec-value">PRIVATE_RELATED</span></div>'
            '<input value="PRIVATE_HIDDEN"><script>PRIVATE_SCRIPT</script>')
        r = receipt(tmp_path, 'detail' + str(i), url, html)
        rp = tmp_path / ('detail' + str(i) + '.receipt.json')
        details.append({'source_url': url, 'frame_index': 0, 'category': category, 'page': 1,
            'card_ordinal': i, 'receipt_path': rp.name, 'receipt_sha256': dump(rp, r)})
    manifest = {'schema_version': 'overlap-product-details-proof-v1',
        'source_frames': [{'manifest_path': bp.name, 'manifest_sha256': base_sha}], 'details': details,
        'projection_path': 'FORGED_PRIVATE_PROJECTION'}
    env = {'root': tmp_path, 'base': base, 'base_path': bp, 'manifest': manifest, 'path': tmp_path / 'manifest.json'}
    save(env)
    return env


def save(env):
    env['manifest']['source_frames'][0]['manifest_sha256'] = dump(env['base_path'], env['base'])
    env['sha'] = dump(env['path'], env['manifest'])


def change(env, old, new, *, card=False, nth=1):
    if card:
        category = env['manifest']['details'][0]['category']
        r = next(f for f in env['base']['frames'] if f['category'] == category)['pages'][0]['source_snapshot']
    else:
        detail = env['manifest']['details'][0]
        rp = env['root'] / detail['receipt_path']
        r = json.loads(rp.read_text(encoding='utf-8'))
    raw = env['root'] / r['raw_path']
    text = raw.read_text(encoding='utf-8')
    assert old in text
    raw.write_text(text.replace(old, new, nth), encoding='utf-8')
    r.update(sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), byte_length=raw.stat().st_size)
    if not card:
        detail['receipt_sha256'] = dump(rp, r)
    save(env)


def verify(env):
    return verify_overlap_product_details(env['path'], env['sha'])


def test_replays_only_selected_card_and_detail_preserving_unknowns(tmp_path):
    env = fixture(tmp_path)
    row, = verify(env)
    assert row['publisher_reference_id'] == row['publisher_product_reference_id'] == 'overlap-nvl-product:opaque1'
    assert row['title'] == row['owncard_title_literal'] == '完全題1　３ 副題【合本】'
    assert row['isbn'] == '9784824019318' and row['isbn_source_literal'] == '978-4-8240-1931-8'
    assert row['form'] == 'B6判' and len(row['bibliographic_field_literals']) == 4
    assert row['credits'][0]['role'] == 'unknown' and row['credits'][0]['role_literal'] is None
    assert row['credits'][1]['role'] == 'illustrator' and row['credits'][1]['role_marker_literal'] == 'イラスト／'
    assert row['publisher'] is None and row['detail_label'] is None
    assert row['canonical_urls'] == [] and row['canonical_status'] == 'not_present_no_redirect_observed'
    assert row['label'] == LABELS['NVL'] and row['label_source_scope'] == 'own_card_label_only_detail_own_label_not_observed'
    assert all(row[k] is False for k in ['edition_identity_asserted', 'original_work_identity_asserted', 'story_series_identity_asserted'])
    assert 'PRIVATE_' not in json.dumps([row]) and 'FORGED_' not in json.dumps([row])


def test_source_frame_bound_supports_more_than_three_without_opening_unused_pages(tmp_path):
    env = fixture(tmp_path, count=4)
    assert len(verify(env)) == 4


@pytest.mark.parametrize('category', LABELS)
def test_each_observed_brand_binds_its_own_card_without_individual_publisher_assertion(tmp_path, category):
    env = fixture(tmp_path, category=category)
    row, = verify(env)
    assert row['label'] == LABELS[category]
    assert row['publisher_product_reference_id'] == 'overlap-' + category.lower() + '-product:opaque1'
    assert row['publisher'] is None and row['credits'][0]['role'] == 'unknown'


@pytest.mark.parametrize('field,value', [('http_status', 302), ('http_status', 503),
    ('final_url', 'https://example.org/'), ('location', '/changed'), ('redirects_followed', True),
    ('retry_count', 1), ('error', 'failure'), ('content_type', 'image/png'), ('byte_length', 1),
    ('sha256', '0' * 64), ('raw_path', '../escape'), ('raw_path', 'C:/escape')])
def test_rejects_detail_receipt_tamper(tmp_path, field, value):
    env = fixture(tmp_path)
    d = env['manifest']['details'][0]
    rp = env['root'] / d['receipt_path']
    r = json.loads(rp.read_text(encoding='utf-8')); r[field] = value
    d['receipt_sha256'] = dump(rp, r); save(env)
    with pytest.raises(ValueError): verify(env)


@pytest.mark.parametrize('change_kind', ['frame_hash', 'receipt_hash', 'manifest_hash', 'frame_escape',
    'receipt_escape', 'duplicate_detail', 'wrong_category', 'card_ordinal', 'page', 'bound', 'incomplete_frame'])
def test_rejects_binding_scope_path_duplicate_and_bound_tamper(tmp_path, change_kind):
    env = fixture(tmp_path)
    d = env['manifest']['details'][0]
    if change_kind == 'frame_hash': env['manifest']['source_frames'][0]['manifest_sha256'] = '0' * 64
    elif change_kind == 'receipt_hash': d['receipt_sha256'] = '0' * 64
    elif change_kind == 'frame_escape': env['manifest']['source_frames'][0]['manifest_path'] = '../escape'
    elif change_kind == 'receipt_escape': d['receipt_path'] = '../escape'
    elif change_kind == 'duplicate_detail': env['manifest']['details'].append(dict(d))
    elif change_kind == 'wrong_category': d['category'] = 'NVF'
    elif change_kind == 'card_ordinal': d['card_ordinal'] = 2
    elif change_kind == 'page': d['page'] = 16
    elif change_kind == 'bound': env['manifest']['details'] *= 640
    elif change_kind == 'incomplete_frame': env['base']['index_pages_complete'] = False; save(env)
    env['sha'] = dump(env['path'], env['manifest'])
    if change_kind == 'manifest_hash': env['sha'] = '0' * 64
    with pytest.raises(ValueError): verify(env)


@pytest.mark.parametrize('old,new,card', [
    ('完全題1　３ 副題【合本】', '別題1　３ 副題【合本】', False),
    ('class="nvl__badge">オーバーラップノベルス', 'class="nvl__badge">別ブランド', True),
    ('オーバーラップノベルス｜OVERLAP', '別ブランド｜OVERLAP', True),
    ('opaque1/?cat=NVL', 'opaque2/?cat=NVL', True),
    ('978-4-8240-1931-8', '978-4-8240-1936-3', False),
    ('<main class="product-main">', '<main class="outside">', False),
    ('<p class="goods-explanation__author">', '<p class="outside">', False),
    ('人物\u2003イラスト／画家', '著者／人物 イラスト／画家', False),
    ('<main class="product-main">', '<link rel="canonical" href="https://evil.invalid/"><main class="product-main">', False),
    ('<p>PRIVATE_PROSE</p>', '<span class="nvl__badge">別ブランド</span><p>PRIVATE_PROSE</p>', False),
])
def test_rejects_raw_card_title_isbn_role_context_and_canonical_contradictions(tmp_path, old, new, card):
    env = fixture(tmp_path); change(env, old, new, card=card)
    with pytest.raises(ValueError): verify(env)


def test_missing_isbn_and_form_remain_unknown_instead_of_using_url_or_related_values(tmp_path):
    env = fixture(tmp_path)
    change(env, '>ISBN<', '>仕様<', nth=2)
    change(env, '>判　型<', '>仕様<', nth=2)
    row, = verify(env)
    assert row['isbn'] is None and row['isbn_source_literal'] is None and row['form'] is None
    assert row['bibliographic_field_literals'] == []
    assert 'explicit_isbn_unknown' in row['hold_reasons']


def test_explicit_equal_canonical_is_preserved_without_publisher_or_author_inference(tmp_path):
    env = fixture(tmp_path)
    url = env['manifest']['details'][0]['source_url']
    change(env, '<main class="product-main">', '<link rel="canonical" href="' + url.replace('&', '&amp;') + '"><main class="product-main">')
    row, = verify(env)
    assert row['canonical_urls'] == [url]
    assert row['publisher'] is None and row['credits'][0]['role'] == 'unknown'


@pytest.mark.parametrize('key,value', [('http_status', 503), ('final_url', 'https://evil.invalid/'),
    ('redirects_followed', True), ('retry_count', 1), ('raw_path', '../escape'), ('sha256', '0' * 64)])
def test_selected_owncard_receipt_is_authenticated_independently(tmp_path, key, value):
    env = fixture(tmp_path)
    env['base']['frames'][0]['pages'][0]['source_snapshot'][key] = value
    save(env)
    with pytest.raises(ValueError): verify(env)


def test_related_badges_within_detail_body_do_not_become_own_brand(tmp_path):
    env = fixture(tmp_path)
    change(env, '<p>PRIVATE_PROSE</p>', '<div class="product-card"><b class="nvf__badge">別ブランド</b></div><p>PRIVATE_PROSE</p>')
    row, = verify(env)
    assert row['label'] == LABELS['NVL'] and row['detail_label'] is None


def test_only_nfkc_whitespace_title_equality_is_accepted(tmp_path):
    env = fixture(tmp_path)
    change(env, '完全題1　３ 副題【合本】', '完全題1 3 副題【合本】')
    row, = verify(env)
    assert row['title_exact_equal_to_owncard'] is False and row['title_nfkc_whitespace_equal_to_owncard'] is True


def test_css_writer_class_never_promotes_unmarked_creator(tmp_path):
    env = fixture(tmp_path)
    change(env, '人物\u2003イラスト／画家', '<span class="writer author">人物</span>\u2003イラスト／画家')
    row, = verify(env)
    assert row['credits'][0]['name'] == '人物' and row['credits'][0]['role'] == 'unknown'


def test_conflicting_second_brand_badge_on_own_card_is_ambiguous(tmp_path):
    env = fixture(tmp_path)
    change(env, '<span class="nvl__badge">オーバーラップノベルス</span>',
           '<span class="nvl__badge">オーバーラップノベルス</span><span class="nvf__badge">オーバーラップノベルスf</span>', card=True)
    with pytest.raises(ValueError): verify(env)


def test_present_but_empty_canonical_is_not_explicit_source_identity(tmp_path):
    env = fixture(tmp_path)
    change(env, '<main class="product-main">', '<link rel="canonical" href=""><main class="product-main">')
    with pytest.raises(ValueError): verify(env)


def test_brand_page_title_accepts_only_nfkc_equivalent_fullwidth_brand_suffix(tmp_path):
    env = fixture(tmp_path, category='NVF')
    change(env, 'f｜OVERLAP', 'ｆ｜OVERLAP', card=True)
    row, = verify(env)
    assert row['label'] == LABELS['NVF']
