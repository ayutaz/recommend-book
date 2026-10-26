from copy import deepcopy
import hashlib
import json

import pytest

from recommend_book.light_novel_dre_dual_series_v2 import FRAME_VERSION, INDEX_ID, match_member, verify_frame
from recommend_book.light_novel_ndl_series_v1 import _cards, publisher_assertions
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from test_light_novel_explicit_series_match_v1 import parts as own_parts, BASE

PRODUCT = 'https://drecom-media.jp/drenovels/product/157'
SERIES = 'https://drecom-media.jp/drenovels/series/example'


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, member='合成シリーズ〈上〉'):
    def cards(heading):
        return f'''<section><h2 id="series" class="{heading}">シリーズ既刊一覧</h2>
        <ul><li class="bookColumn__item"><a href="{PRODUCT}"><span class="bookColumn__title">{member}</span></a>
        <span class="bookColumn__author">著 / 架空著者</span><span class="bookColumn__author">イラスト / 架空画家</span>
        <div class="productColumn__info"><span class="productColumn__infoLabel">ISBN</span>
        <span class="productColumn__infoValue">978-4-434-36579-9</span></div></li></ul></section>'''
    product = f'''<link rel="canonical" href="{PRODUCT}"><section class="lineupColumn__item">
    <div class="lineupColumn__label drenovels">DREノベルス</div><h1 class="lineupColumn__title">{member}</h1>
    <span class="lineupColumn__author">著 / 架空著者</span><span class="lineupColumn__author">イラスト / 架空画家</span>
    <div class="lineupColumn__info"><span class="lineupColumn__infoLabel">ISBN</span><span class="lineupColumn__infoValue">9784434365799</span></div>
    <a href="{SERIES}">シリーズ紹介</a><p>PRIVATE_PROSE</p><input value="PRIVATE_HIDDEN"></section>
    {cards('productDetail__subTitle--novels')}'''
    series = f'''<link rel="canonical" href="{SERIES}"><section class="seriesDetail__main">
    <h1 class="seriesDetail__mainTitle">合成シリーズ</h1><p class="seriesDetail__mainAuthor">著 / 架空著者</p>
    <p class="seriesDetail__mainAuthor">イラスト / 架空画家</p><p>PRIVATE_PROSE</p></section>
    {cards('seriesDetail__subTitle--novels')}<section class="pickup"><a href="https://evil.test">PRIVATE_NAVIGATION</a></section>'''
    pins = {}
    for kind, url, text in [('product', PRODUCT, product), ('series', SERIES, series)]:
        raw = tmp_path / (kind + '.html'); raw.write_text(text, encoding='utf8')
        rec = {'source_url': url, 'final_url': url, 'http_status': 200, 'http_method': 'GET', 'retry_count': 0,
            'content_type': 'text/html; charset=utf-8', 'raw_path': raw.name,
            'sha256': hashlib.sha256(raw.read_bytes()).hexdigest(), 'byte_length': len(raw.read_bytes())}
        rp = tmp_path / (kind + '.receipt.json')
        pins[kind] = {'receipt_path': rp.name, 'receipt_sha256': dump(rp, rec)}
    frame = {'id': INDEX_ID, 'version': FRAME_VERSION, 'publisher': 'ドリコム', 'label': 'DREノベルス', 'pairs': [pins]}
    return frame


def verify(tmp_path, frame):
    return verify_frame(frame, tmp_path, LightNovelCatalogue.__new__(LightNovelCatalogue)._artifact, {})


def mutate(tmp_path, frame, kind, old, new):
    pin = frame['pairs'][0][kind]; rp = tmp_path / pin['receipt_path']; rec = json.loads(rp.read_bytes())
    raw = tmp_path / rec['raw_path']; text = raw.read_text('utf8'); assert old in text
    raw.write_text(text.replace(old, new), encoding='utf8')
    rec.update(sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), byte_length=len(raw.read_bytes()))
    pin['receipt_sha256'] = dump(rp, rec)


def test_dual_primary_exact_link_canonical_roles_and_novel_members(tmp_path):
    frame = fixture(tmp_path); rows = verify(tmp_path, frame); row, = rows.values()
    assert row['index_id'] == INDEX_ID and row['reference_id'].startswith('publisher-series-dre-v2:')
    assert row['title'] == '合成シリーズ' and row['authors'] == ['架空著者']
    assert row['explicit_book_memberships'][0]['isbn'] == '9784434365799'
    assert 'PRIVATE_' not in json.dumps(row)
    path = tmp_path / 'frames.json'; pin = dump(path, [frame])
    proof = {'frames_path': str(path), 'frames_sha256': pin, 'source_root': str(tmp_path)}
    assert publisher_assertions(proof, LightNovelCatalogue.__new__(LightNovelCatalogue)._artifact, {}) == rows


@pytest.mark.parametrize('kind,old,new', [
    ('product', '>シリーズ紹介<', '>関連記事<'),
    ('product', 'lineupColumn__label drenovels">DREノベルス', 'lineupColumn__label drenovels">DREコミックス'),
    ('product', '著 / 架空著者', '架空著者'),
    ('series', '<link rel="canonical" href="' + SERIES, '<link rel="canonical" href="https://evil.test'),
    ('series', '<h1 class="seriesDetail__mainTitle">合成シリーズ</h1>', '<h1 class="seriesDetail__mainTitle"><img alt="合成シリーズ"></h1>'),
    ('series', '著 / 架空著者', '著 / 別著者'),
    ('series', 'seriesDetail__subTitle--novels', 'seriesDetail__subTitle--comics'),
    ('series', '978-4-434-36579-9', '978-4-434-37277-3'),
    ('series', '合成シリーズ〈上〉', '合成シリーズ〈下〉'),
    ('series', PRODUCT, PRODUCT.replace('/drenovels/', '/drecomics/')),
])
def test_dual_source_rejects_semantic_scope_tamper(tmp_path, kind, old, new):
    frame = fixture(tmp_path); mutate(tmp_path, frame, kind, old, new)
    with pytest.raises(ValueError): verify(tmp_path, frame)


@pytest.mark.parametrize('change', ['hash', 'status', 'redirect', 'retry', 'final_url', 'size', 'escape', 'duplicate_pair', 'old_identity'])
def test_raw_receipt_and_version_tamper_hold(tmp_path, change):
    frame = fixture(tmp_path); pin = frame['pairs'][0]['series']; rp = tmp_path / pin['receipt_path']; rec = json.loads(rp.read_bytes())
    if change == 'hash': rec['sha256'] = '0' * 64
    elif change == 'status': rec['http_status'] = 403
    elif change == 'redirect': rec['location'] = '/other'
    elif change == 'retry': rec['retry_count'] = 1
    elif change == 'final_url': rec['final_url'] += '/other'
    elif change == 'size': rec['byte_length'] = 1
    elif change == 'escape': rec['raw_path'] = '../outside'
    elif change == 'duplicate_pair': frame['pairs'] *= 2
    else: frame['id'] = 'dre-novels-series'
    pin['receipt_sha256'] = dump(rp, rec)
    with pytest.raises(ValueError): verify(tmp_path, frame)


def parts(title='合成シリーズ. 上', volume='上'):
    value = own_parts(title=title, volume=volume)
    value['source'].update(provider_id='iss-ndl-opac-national', record_kind='edition', publisher='ドリコム', series='DREノベルス',
        identifiers=[{'source': 'isbn', 'value': '9784434365799'}])
    value['publisher_agents'][0]['name'] = 'ドリコム'
    value['literal_authors'] = [{'name': '架空著者', 'literal': '架空著者 著', 'locator': BASE + '/{http://purl.org/dc/elements/1.1/}creator[2]'}]
    return value


@pytest.mark.parametrize('volume,raw,official,order', [
    ('上', '合成シリーズ. 上', '合成シリーズ〈上〉', None),
    ('下', '合成シリーズ. 下', '合成シリーズ〈下〉', None),
    (None, '合成シリーズ', '合成シリーズ', None),
    ('2', '合成シリーズ. 2', '合成シリーズ2', 2),
])
def test_own_NDL_fields_match_exact_member_without_default_volume(tmp_path, volume, raw, official, order):
    row, = verify(tmp_path, fixture(tmp_path, member=official)).values(); own = parts(title=raw, volume=volume)
    before = deepcopy((row, own)); match = match_member(row, own)
    assert match['title'] == '合成シリーズ' and match['volume'] == volume and match['volume_number'] == order
    assert match['rule_version'] == 'dre-dual-source-explicit-ndl-member-match-v2'
    assert (row, own) == before


@pytest.mark.parametrize('change', ['publisher', 'imprint', 'language', 'ndc', 'isbn', 'authority', 'explicit_writer', 'writer', 'title', 'volume', 'foreign_locator', 'extra_title', 'source_sha', 'old_index'])
def test_own_member_gate_rejects_wrong_or_nonown_fields(tmp_path, change):
    row, = verify(tmp_path, fixture(tmp_path)).values(); own = parts()
    if change == 'publisher': own['source']['publisher'] = '別会社'
    elif change == 'imprint': own['source']['series'] = 'DREコミックス'
    elif change == 'language': own['source']['language'] = 'eng'
    elif change == 'ndc': own['source']['extra']['ndc_codes'] = ['726.1']
    elif change == 'isbn': own['source']['identifiers'][0]['value'] = '9784434372773'
    elif change == 'authority': own['source']['contributors'][0]['identifiers'] = []
    elif change == 'explicit_writer': own['literal_authors'] = []
    elif change == 'writer': own['source']['contributors'][0]['role'] = 'illustrator'
    elif change == 'title': own['titles'][0]['value'] = '似たシリーズ. 上'
    elif change == 'volume': own['volume']['value'] = '中'
    elif change == 'foreign_locator': own['titles'][0]['locator'] = 'related/elsewhere/{http://purl.org/dc/terms/}title[1]'
    elif change == 'extra_title': own['titles'].append({**own['titles'][0], 'value': '矛盾する別書名. 上'})
    elif change == 'source_sha': own['source_raw_sha256'] = ''
    else: row['index_id'] = 'dre-novels-series'
    assert match_member(row, own) is None


def test_v1_product_card_shape_stays_unchanged(tmp_path):
    frame = fixture(tmp_path); product = (tmp_path / 'product.html').read_text('utf8')
    product += f'<a class="pickupSeriesList_item" href="{SERIES}"><p class="pickupSeriesList_title">合成シリーズ</p></a>'
    before = _cards('dre-novels-series', product)
    assert len(before) == 1 and before[0]['title'] == '合成シリーズ'
    verify(tmp_path, frame)
    assert _cards('dre-novels-series', product) == before
    assert not any(k in before[0] for k in ['proof_version', 'primary_source_snapshots', 'index_id'])


def test_english_imprint_and_explicit_own_distributor_are_member_scoped(tmp_path):
    row, = verify(tmp_path, fixture(tmp_path)).values(); own = parts()
    own['source'].update(publisher='ドリコム; 星雲社', series='DRE NOVELS ; い-2-1-1')
    own['publisher_agents'].append({'name': '星雲社', 'locator': BASE + '/publisher[4]', 'structured_agent': True,
        'description_values': ['頒布'], 'description_locators': [BASE + '/publisher[4]/description[1]']})
    matched = match_member(row, own)
    assert matched['volume_number'] is None
    assert matched['edition_scoped_label_witness']['explicit_member_isbn'] == '9784434365799'
    assert matched['publisher_agent_policy']['publisher']['name'] == 'ドリコム'
    own['publisher_agents'][1]['description_values'] = []
    assert match_member(row, own) is None
    own['publisher_agents'][1]['description_values'] = ['頒布']
    own['publisher_agents'][1]['structured_agent'] = False
    assert match_member(row, own) is None


@pytest.mark.parametrize('identity,record,reason', [
    ('lns:wd:Q1', {'title': '合成シリーズ', 'aliases': []}, 'potential_existing_series_title_or_alias_overlap_requires_review'),
    ('lns:wd:Q1', {'title': '別名', 'aliases': ['合成シリーズ']}, 'potential_existing_series_title_or_alias_overlap_requires_review'),
    ('lns:ndl:R100000002-I2', {'id': 'R100000002-I2', 'title': '合成シリーズ'}, 'potential_existing_series_title_or_alias_overlap_requires_review'),
    ('lns:ndl:R100000002-I1', {'id': 'R100000002-I1', 'title': '別書名'}, 'existing_series_anchor_conflict'),
])
def test_canonical_title_alias_and_anchor_overlap_hold(tmp_path, identity, record, reason):
    from recommend_book.light_novel_dre_dual_series_v2 import admission_conflict
    row, = verify(tmp_path, fixture(tmp_path)).values()
    assert admission_conflict(row, {identity: record}, 'R100000002-I1') == reason
    assert admission_conflict(row, {}, 'R100000002-I1', [{'edition_id': 'lne:ndl:R100000002-I1', 'series_id': 'lns:wd:Q1'}]) == 'existing_edition_membership_anchor_conflict'


@pytest.mark.parametrize('record', [
    {'anchor_edition_record': {'source_record': {'titles': [{'value': '合成シリーズ : 完全な副題'}]}}},
    {'anchor_edition_record': {'source_record': {'extra': {'title_readings': ['合成シリーズ']}}}},
    {'kind': 'publisher_group', 'title': '合成シリーズ', 'aliases': []},
    {'kind': 'publisher_group', 'title': '別題', 'aliases': ['合成シリーズ']},
])
def test_stored_title_closure_holds_titleless_NDL_and_generic_groups(tmp_path, record):
    from recommend_book.light_novel_dre_dual_series_v2 import admission_conflict
    row, = verify(tmp_path, fixture(tmp_path)).values()
    identity = 'lng:pub:generic:old' if record.get('kind') == 'publisher_group' else 'lns:ndl:R100000002-I2'
    assert admission_conflict(row, {identity: record}, 'R100000002-I1') == 'potential_existing_series_title_or_alias_overlap_requires_review'


def test_duplicate_key_conservatively_normalizes_kana_and_punctuation(tmp_path):
    from recommend_book.light_novel_dre_dual_series_v2 import admission_conflict
    row, = verify(tmp_path, fixture(tmp_path)).values(); row['title'] = 'ひらがな。物語'
    record = {'anchor_edition_record': {'source_record': {'titles': [{'value': 'ヒラガナ物語 : 副題'}]}}}
    assert admission_conflict(row, {'lns:ndl:R100000002-I2': record}, 'R100000002-I1') == 'potential_existing_series_title_or_alias_overlap_requires_review'
