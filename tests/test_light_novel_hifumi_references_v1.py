import hashlib
import json

import pytest

from recommend_book.light_novel_hifumi_references_v1 import verify_hifumi_references


LABELS = [('sagaforest', 'サーガフォレスト', 'saga_forest'),
          ('meltia', 'メルティアブックス', 'meltia'), ('bravenovel', 'ブレイブ文庫', 'bravenovel')]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, dict) or isinstance(value, list):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(value, encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def card(number, label, *, writer=True):
    credits = ('<div class="author-tag-item"><span class="author-role">著　者</span>'
               '<span class="author-name">架空著者</span></div>') if writer else ''
    return (f'<div class="custom-item-row"><div class="custom-item-body"><span class="label-badge">{label}</span>'
        f'<p class="ymd">2026/10/25発売</p><h2><a href="https://hifumi.co.jp/lineup/{number}/">完全な題　３：副題も保持</a></h2>'
        '<p class="hifumi-catch">PRIVATE-PROSE-SENTINEL</p>' + credits + '</div></div>')


@pytest.fixture
def frame(tmp_path):
    nav = '<li id="menu-item-21093"><a href="#">ライトノベル</a><ul>' + ''.join(
        f'<li><a href="/{route}/">{label}</a></li>' for _, label, route in LABELS) + '</ul></li>'
    nav += '<li id="menu-item-21097"><a href="#">コミック</a><ul><li><a href="/comic/">漫画</a></li></ul></li>'
    mixed = nav + '<div id="lineup-container">' + ''.join(card(9780000000000 + i, label, writer=i != 2)
        for i, (_, label, _) in enumerate(LABELS)) + '</div>'
    def snapshot(name, url, text):
        raw = tmp_path / (name + '.html')
        digest = save(raw, text)
        receipt = {'source_url': url, 'final_url': url, 'http_status': 200,
                   'content_type': 'text/html; charset=UTF-8', 'sha256': digest,
                   'byte_length': raw.stat().st_size, 'raw_path': raw.name}
        receipt_path = tmp_path / (name + '.receipt.json')
        return receipt, {'source_url': url, 'receipt_path': receipt_path.name,
                         'receipt_sha256': save(receipt_path, receipt), 'raw_path': raw.name, 'raw_sha256': digest}
    _, mixed_pin = snapshot('mixed', 'https://hifumi.co.jp/lineup_all/', mixed)
    brands = []
    for i, (identity, label, route) in enumerate(LABELS):
        url = 'https://hifumi.co.jp/' + route + '/'
        raw = '<div class="label_title"><h2>' + label + '</h2></div><div id="lineup-container">' + card(9780000000000 + i, label, writer=i != 2) + '</div>'
        receipt, pin = snapshot(identity, url, raw)
        roster = [{'publisher_reference_id': 'hifumi-book-reference:' + str(9780000000000 + i),
            'source_url': 'https://hifumi.co.jp/lineup/' + str(9780000000000 + i) + '/',
            'title': '完全な題　３：副題も保持', 'label': label, 'isbn': None, 'volume': None,
            'credits': [{'role': 'author', 'role_display': '著　者', 'name': '架空著者'}] if i != 2 else [],
            'publication_date_literals': ['2026/10/25発売']}]
        rosterpath = tmp_path / (identity + '.roster.json')
        manifest = {'source_url': url, 'label': label,
            'frame_unit': 'explicit_book_cards_in_one_current_official_label_listing',
            'official_declared_total': None, 'publisher_historical_catalogue_complete': False,
            'finite_embedded_listing_complete': True, 'observed_embedded_book_cards': 1,
            'parsed_references': 1, 'unique_book_references': 1, 'saved_mixed_label_cards': 1,
            'saved_mixed_reference_set_matches': True, 'next_hrefs': [], 'numbered_pagination_hrefs': [],
            'holds': [], 'duplicates': [], 'raw_source': receipt,
            'roster': {'path': rosterpath.name, 'sha256': save(rosterpath, roster)}}
        manifestpath = tmp_path / (identity + '.manifest.json')
        brands.append(dict(pin, id=identity, label=label, manifest_path=manifestpath.name,
                           manifest_sha256=save(manifestpath, manifest)))
    proof = {'schema_version': 'hifumi-three-label-bibliographic-proof-v1', 'mixed_index': mixed_pin,
        'brands': brands, 'expected_book_references': 3, 'historical_catalogue_complete': False,
        'identity_assertions': {'edition': False, 'original_work': False, 'story_series': False}}
    path = tmp_path / 'proof.json'
    digest = save(path, proof)
    return path, digest, proof


def test_replays_three_explicit_ln_labels_without_inferred_isbn_identity_or_prose(frame):
    path, digest, _ = frame
    rows = verify_hifumi_references(path, digest)
    assert len(rows) == 3
    assert rows[0]['publisher_reference_id'] == 'hifumi-product:9780000000000'
    assert all(row['title'] == '完全な題　３：副題も保持' for row in rows)
    assert all(row['isbn'] is None and row['publisher'] is None and row['form'] is None for row in rows)
    assert all(row['official_lightnovel_tag'] is True and row['story_series_asserted'] is False for row in rows)
    assert rows[-1]['credits'] == []
    assert all(row['source_evidence'] for row in rows)
    public = json.dumps(rows, ensure_ascii=False)
    assert 'PRIVATE-PROSE-SENTINEL' not in public and str(path.parent) not in public


@pytest.mark.parametrize('change', ['raw', 'receipt', 'nav', 'next', 'mixed_title', 'roster',
    'wrong_host', 'redirect', 'total_claim', 'historical_claim', 'duplicate', 'path_escape'])
def test_held_or_changed_evidence_cannot_become_a_complete_ln_frame(frame, change):
    path, _, proof = frame
    root = path.parent
    selected = proof['brands'][0]
    if change == 'raw':
        save(root / selected['raw_path'], '<div>changed raw</div>')
    elif change == 'receipt':
        save(root / selected['receipt_path'], {})
    elif change in ('nav', 'mixed_title'):
        pin = proof['mixed_index']
        raw = root / pin['raw_path']
        text = raw.read_text(encoding='utf-8').replace('ライトノベル', '一般文芸') if change == 'nav' else raw.read_text(encoding='utf-8').replace('完全な題', '矛盾した題', 1)
        pin['raw_sha256'] = save(raw, text)
        receipt = json.loads((root / pin['receipt_path']).read_text(encoding='utf-8'))
        receipt.update(sha256=pin['raw_sha256'], byte_length=raw.stat().st_size)
        pin['receipt_sha256'] = save(root / pin['receipt_path'], receipt)
    elif change in ('next', 'duplicate'):
        raw = root / selected['raw_path']
        text = raw.read_text(encoding='utf-8')
        if change == 'next':
            text += '<a rel="next" href="/saga_forest/page/2/">次のページ</a>'
        else:
            text = text.replace('<div id="lineup-container">', '<div id="lineup-container">' + card(9780000000000, selected['label']))
        selected['raw_sha256'] = save(raw, text)
        receipt = json.loads((root / selected['receipt_path']).read_text(encoding='utf-8'))
        receipt.update(sha256=selected['raw_sha256'], byte_length=raw.stat().st_size)
        selected['receipt_sha256'] = save(root / selected['receipt_path'], receipt)
        manifest = json.loads((root / selected['manifest_path']).read_text(encoding='utf-8'))
        manifest['raw_source'] = receipt
        selected['manifest_sha256'] = save(root / selected['manifest_path'], manifest)
    elif change in ('wrong_host', 'redirect'):
        receipt = json.loads((root / selected['receipt_path']).read_text(encoding='utf-8'))
        receipt['source_url' if change == 'wrong_host' else 'final_url'] = 'https://other.example/saga_forest/'
        selected['receipt_sha256'] = save(root / selected['receipt_path'], receipt)
    elif change in ('total_claim', 'historical_claim', 'roster'):
        manifestpath = root / selected['manifest_path']
        manifest = json.loads(manifestpath.read_text(encoding='utf-8'))
        if change == 'total_claim': manifest['official_declared_total'] = 1
        elif change == 'historical_claim': manifest['publisher_historical_catalogue_complete'] = True
        else:
            rosterpath = root / manifest['roster']['path']
            roster = json.loads(rosterpath.read_text(encoding='utf-8')); roster[0]['isbn'] = '9780000000000'
            manifest['roster']['sha256'] = save(rosterpath, roster)
        selected['manifest_sha256'] = save(manifestpath, manifest)
    else:
        selected['raw_path'] = '../outside.html'
    with pytest.raises(ValueError):
        verify_hifumi_references(path, save(path, proof))
