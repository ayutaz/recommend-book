from copy import deepcopy
import hashlib
import json

import pytest

from recommend_book.light_novel_hifumi_details_v1 import (
    match_hifumi_product_detail, verify_hifumi_product_details,
)


OWN = 'records[1]/recordData/dcndl:BibResource'
DCT = '/{http://purl.org/dc/terms/}'
DC = '/{http://purl.org/dc/elements/1.1/}'
ISBN = '9784891996581'
URL = 'https://hifumi.co.jp/lineup/opaque-book-1/'


def save(path, value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    path.write_text(value, encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def detail(**changes):
    row = {'publisher_book_id': 'hifumi:opaque-book-1', 'source_unit': 'publisher_book_detail',
        'source_url': URL, 'title': '完全題 ～副題～ ３', 'isbn': ISBN,
        'credits': [{'role': 'author', 'name': '架空著者'}, {'role': 'illustrator', 'name': '絵担当'}],
        'label': 'サーガフォレスト', 'label_literals': ['サーガフォレスト'],
        'catalogue_owner': '一二三書房', 'individual_publisher': None, 'publisher': None,
        'official_lightnovel_tag': True, 'story_series_asserted': False,
        'metadata_conflict': False, 'bibliographic_variants': [],
        'source_evidence': [{'source_url': URL, 'raw_sha256': 'a' * 64, 'locator': 'h1.hifumi-title-v2; explicit ISBN/credits/label'},
            {'source_url': 'https://hifumi.co.jp/lineup_all/', 'raw_sha256': 'c' * 64, 'locator': 'own LN nav and card'}]}
    row.update(changes)
    return row


def parts(*, known=True):
    return {'source': {'source_id': 'R100000002-I1', 'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'provider_id': 'iss-ndl-opac', 'record_kind': 'edition', 'language': 'jpn',
        'publisher': '一二三書房', 'series': 'Saga Forest',
        'identifiers': [{'source': 'isbn', 'value': '978-4-89199-658-1'}],
        'contributors': [{'role': 'author', 'name': '架空著者', 'identifiers':
            [{'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/01234567'}] if known else []}],
        'extra': {'ndc_codes': ['913.6']}}, 'source_raw_sha256': 'b' * 64,
        'titles': [{'value': '完全題 : 副題. 3', 'locator': OWN + DCT + 'title[1]'}],
        'volume': {'value': '3', 'locator': OWN + '/{http://ndl.go.jp/dcndl/terms/}volume[2]'},
        'publisher_agents': [{'name': '一二三書房', 'locator': OWN + DCT + 'publisher[3]',
            'structured_agent': True, 'description_values': [], 'description_locators': []}],
        'literal_authors': [{'name': '架空著者', 'literal': '架空著者 著', 'locator': OWN + DC + 'creator[4]'}]}


@pytest.fixture
def frame(tmp_path):
    def source(name, url, html):
        raw = tmp_path / (name + '.html')
        raw_sha = save(raw, html)
        receipt = {'source_url': url, 'final_url': url, 'http_status': 200, 'content_type': 'text/html; charset=utf-8',
                   'sha256': raw_sha, 'byte_length': raw.stat().st_size}
        receipt_path = tmp_path / (name + '.receipt.json')
        return {'source_url': url, 'raw_path': raw.name, 'raw_sha256': raw_sha,
                'receipt_path': receipt_path.name, 'receipt_sha256': save(receipt_path, receipt)}
    nav = ('<li id="menu-item-21093"><a>ライトノベル</a><ul>'
        '<li><a href="/saga_forest/">サーガフォレスト</a></li>'
        '<li><a href="/meltia/">メルティアブックス</a></li>'
        '<li><a href="/bravenovel/">ブレイブ文庫</a></li></ul></li>'
        '<li id="menu-item-21097"><a>コミック</a></li>')
    mixed = nav + ('<div id="lineup-container"><div class="custom-item-row"><div class="custom-item-body">'
        '<span class="label-badge">サーガフォレスト</span>'
        '<h2><a href="' + URL + '">完全題３</a></h2></div></div></div>')
    base = {'schema_version': 'hifumi-three-label-bibliographic-proof-v1',
            'mixed_index': source('mixed', 'https://hifumi.co.jp/lineup_all/', mixed)}
    base_path = tmp_path / 'base.json'
    html = ('<head><link rel="canonical" href="' + URL + '"></head><main class="hifumi-book-page-root">'
        '<div class="hifumi-book-main-container"><div class="detail-right-side">'
        '<h1 class="hifumi-title-v2">完全題<span>～副題～</span>３</h1>'
        '<div class="label-badge-row-v2"><span class="label-badge">サーガフォレスト</span></div>'
        '<div class="author-credits-v2"><p class="credit-item"><span class="role-label">著 者</span>'
        '<span class="name-label">架空著者</span></p></div></div>'
        '<div class="spec-content-v2"><p><span>ISBN</span>' + ISBN + '</p></div>'
        '<h3>PRIVATE-PROSE-SENTINEL</h3><div>PRIVATE-SYNOPSIS-SENTINEL</div></div></main>')
    proof = {'schema_version': 'hifumi-product-details-proof-v1',
        'base_frame': {'manifest_path': base_path.name, 'manifest_sha256': save(base_path, base)},
        'details': [source('detail', URL, html)]}
    path = tmp_path / 'proof.json'
    return path, save(path, proof), proof


def test_raw_detail_and_ln_nav_supply_only_explicit_book_metadata(frame):
    path, sha, _ = frame
    row, = verify_hifumi_product_details(path, sha)
    assert row['publisher_book_id'] == 'hifumi:opaque-book-1'
    assert row['isbn'] == ISBN  # Opaque URL contains no ISBN.
    assert row['title'] == '完全題 ～副題～ ３'
    assert row['credits'] == [{'role': 'author', 'name': '架空著者'}]
    assert row['label'] == 'サーガフォレスト' and row['official_lightnovel_tag'] is True
    assert row['catalogue_owner'] == '一二三書房'
    assert row['individual_publisher'] is None and row['publisher'] is None
    assert 'PRIVATE-' not in json.dumps(row) and str(path.parent) not in json.dumps(row)
    assert match_hifumi_product_detail(row, parts())


def test_more_than_three_details_still_require_each_book_in_the_pinned_ln_index(frame):
    path, _, proof = frame
    base_path = path.parent / proof['base_frame']['manifest_path']
    base = json.loads(base_path.read_text(encoding='utf-8'))
    original = proof['details'][0]
    original_html = (path.parent / original['raw_path']).read_text(encoding='utf-8')
    cards, pins = [], []
    for number in range(1, 5):
        url = URL.replace('opaque-book-1', f'opaque-book-{number}')
        raw = path.parent / f'expanded-{number}.html'
        raw_sha = save(raw, original_html.replace(URL, url))
        receipt = {'source_url': url, 'final_url': url, 'http_status': 200,
            'content_type': 'text/html; charset=utf-8', 'sha256': raw_sha, 'byte_length': raw.stat().st_size}
        receipt_path = path.parent / f'expanded-{number}.receipt.json'
        pins.append({'source_url': url, 'raw_path': raw.name, 'raw_sha256': raw_sha,
            'receipt_path': receipt_path.name, 'receipt_sha256': save(receipt_path, receipt)})
        cards.append('<div class="custom-item-row"><div class="custom-item-body">'
            '<span class="label-badge">サーガフォレスト</span><h2><a href="' + url
            + '">完全題３</a></h2></div></div>')
    mixed_pin = base['mixed_index']
    mixed_path = path.parent / mixed_pin['raw_path']
    nav = mixed_path.read_text(encoding='utf-8').split('<div id="lineup-container">')[0]
    mixed_pin['raw_sha256'] = save(mixed_path, nav + '<div id="lineup-container">' + ''.join(cards) + '</div>')
    receipt_path = path.parent / mixed_pin['receipt_path']
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    receipt.update(sha256=mixed_pin['raw_sha256'], byte_length=mixed_path.stat().st_size)
    mixed_pin['receipt_sha256'] = save(receipt_path, receipt)
    proof['base_frame']['manifest_sha256'] = save(base_path, base)
    proof['details'] = pins
    rows = verify_hifumi_product_details(path, save(path, proof))
    assert len(rows) == 4 and len({row['publisher_book_id'] for row in rows}) == 4
    # A valid detail cannot be admitted after its exact card disappears.
    mixed_pin['raw_sha256'] = save(mixed_path, nav + '<div id="lineup-container">' + ''.join(cards[:-1]) + '</div>')
    receipt.update(sha256=mixed_pin['raw_sha256'], byte_length=mixed_path.stat().st_size)
    mixed_pin['receipt_sha256'] = save(receipt_path, receipt)
    proof['base_frame']['manifest_sha256'] = save(base_path, base)
    with pytest.raises(ValueError, match='contained'):
        verify_hifumi_product_details(path, save(path, proof))


@pytest.mark.parametrize('change', ['raw', 'redirect', 'canonical', 'no_isbn', 'nav', 'label', 'path_escape'])
def test_modified_or_unbound_raw_cannot_supply_detail_metadata(frame, change):
    path, _, proof = frame
    pin = proof['details'][0]
    if change == 'raw':
        (path.parent / pin['raw_path']).write_text('changed', encoding='utf-8')
    elif change == 'path_escape':
        pin['raw_path'] = '../outside.html'
    elif change == 'redirect':
        receipt = json.loads((path.parent / pin['receipt_path']).read_text(encoding='utf-8'))
        receipt['final_url'] = 'https://other.example/book/'
        pin['receipt_sha256'] = save(path.parent / pin['receipt_path'], receipt)
    else:
        target = pin
        if change == 'nav':
            base_path = path.parent / proof['base_frame']['manifest_path']
            base = json.loads(base_path.read_text(encoding='utf-8'))
            target = base['mixed_index']
        raw = path.parent / target['raw_path']
        text = raw.read_text(encoding='utf-8')
        text = text.replace({'canonical': URL, 'no_isbn': 'ISBN', 'nav': 'ライトノベル', 'label': 'サーガフォレスト'}[change],
                            {'canonical': 'https://hifumi.co.jp/lineup/other/', 'no_isbn': '番号', 'nav': '一般文芸', 'label': 'メルティアブックス'}[change], 1)
        target['raw_sha256'] = save(raw, text)
        receipt = json.loads((path.parent / target['receipt_path']).read_text(encoding='utf-8'))
        receipt.update(sha256=target['raw_sha256'], byte_length=raw.stat().st_size)
        target['receipt_sha256'] = save(path.parent / target['receipt_path'], receipt)
        if change == 'nav':
            proof['base_frame']['manifest_sha256'] = save(base_path, base)
    with pytest.raises(ValueError):
        verify_hifumi_product_details(path, save(path, proof))


@pytest.mark.parametrize('known', [True, False])
def test_isbn_scoped_pair_keeps_unknown_authority_and_all_identity_unknowns(known):
    ref, own = detail(), parts(known=known)
    before = deepcopy((ref, own))
    proof = match_hifumi_product_detail(ref, own)
    assert proof['isbn13'] == ISBN and proof['scope'] == 'edition_only'
    assert proof['confirmed_scope'] == 'contained_bibliographic_edition_only'
    assert proof['same_isbn_bibliographic_correspondence_asserted'] is True
    assert proof['imprint_match']['scope'] == 'this_isbn_bibliography_only'
    assert proof['authors'][0]['authority_status'] == ('known' if known else 'unknown')
    assert proof['individual_publisher_agreement'] == 'unknown'
    for field in ('global_imprint_alias_asserted', 'same_printing_identity_asserted', 'original_work_identity_asserted',
                  'story_series_identity_asserted', 'product_sku_identity_asserted'):
        assert proof[field] is False
    assert 'same_edition_identity_asserted' not in proof
    assert (ref, own) == before
    ref.update(label='メルティアブックス', label_literals=['メルティアブックス'])
    own['source']['series'] = 'Meltia Books'
    assert match_hifumi_product_detail(ref, own)


@pytest.mark.parametrize('change', ['isbn', 'title', 'volume', 'label', 'role', 'literal_role', 'nested_writer',
    'publisher', 'provider', 'language', 'ndc', 'related_title', 'individual_publisher'])
def test_another_isbn_role_label_or_primary_scope_is_held(change):
    ref, own = detail(), parts(known=False)
    if change == 'isbn': own['source']['identifiers'][0]['value'] = '9784824207197'
    elif change == 'title': ref['title'] = '完全題 ～別副題～ ３'
    elif change == 'volume': own['volume']['value'] = '2'
    elif change == 'label': ref.update(label='メルティアブックス', label_literals=['メルティアブックス'])
    elif change == 'role': ref['credits'][0]['role'] = 'illustrator'
    elif change == 'literal_role': own['literal_authors'][0]['literal'] = '架空著者 編'
    elif change == 'nested_writer': own['literal_authors'][0]['locator'] = OWN + '/contents[1]' + DC + 'creator[4]'
    elif change == 'publisher': own['publisher_agents'].append(deepcopy(own['publisher_agents'][0]))
    elif change == 'provider': own['source']['provider_id'] = 'thirdparty'
    elif change == 'language': own['source']['language'] = 'eng'
    elif change == 'ndc': own['source']['extra']['ndc_codes'] += ['726.1']
    elif change == 'related_title': own['titles'][0]['locator'] = OWN + '/related[1]' + DCT + 'title[1]'
    else: ref['individual_publisher'] = '一二三書房'
    assert match_hifumi_product_detail(ref, own) is None


def test_parallel_japanese_title_needs_independent_own_dc_title_and_volume():
    own = parts(known=False)
    own['titles'][0]['value'] = '完全題 = Full title : 副題. 3'
    own['titles'].append({'value': '完全題 : 副題', 'locator': OWN + DC + 'title[5]'})
    assert match_hifumi_product_detail(detail(), own)
    own['titles'][1]['value'] = '別題 : 副題'
    assert match_hifumi_product_detail(detail(), own) is None


@pytest.mark.parametrize('role', ['発売', '頒布'])
def test_only_explicit_structured_distribution_can_accompany_publisher(role):
    own = parts()
    locator = OWN + DCT + 'publisher[6]'
    own['source']['publisher'] += '; 別社'
    own['publisher_agents'].append({'name': '別社', 'locator': locator, 'structured_agent': True,
        'description_values': [role], 'description_locators': [locator + DCT + 'description[1]']})
    assert match_hifumi_product_detail(detail(), own)
    own['publisher_agents'][1]['description_values'] = ['不明']
    assert match_hifumi_product_detail(detail(), own) is None
