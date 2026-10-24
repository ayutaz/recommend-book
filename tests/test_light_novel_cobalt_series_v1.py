import hashlib
import json

import pytest

from recommend_book.light_novel_cobalt_series_v1 import verify_cobalt_series


IDS = ('52807', '52812', '47089', '52670', '52520', '52973', '69979', '52881')
ANNIVERSARY = 'https://orangebunko.shueisha.co.jp/feature/cobalt50th_specialbooks'
EBOOKS = 'https://ebooks.shueisha.co.jp/cobalt/'


def pin(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, *, role=True):
    anniversary = []
    ebook = ['<h1>eコバルト文庫</h1>']
    if role:
        ebook.append('<h2><img alt="著者50音別書籍リスト"></h2>')
    else:
        ebook.append('<!-- <h2><img alt="著者50音別書籍リスト"></h2> -->')
    rows = []
    for i, sid in enumerate(IDS, 1):
        title, author = f'合成作品{i}', f'架空著者{i}'
        series_url = f'https://www.shueisha.co.jp/books/search/search.html?seriesid={sid}'
        book_url = f'https://ebooks.shueisha.co.jp/detail.php?book=sc{i}'
        anniversary.append(f'''<li><p class="anthology-title">{author}<span>●『{title}』シリーズ</span></p>
            <p>PRIVATE_PROSE</p><a href="{series_url}"><span class="label">シリーズ本編購入はこちら</span></a></li>''')
        ebook.append(f'''<dl id="word_{i}"><dt>あ</dt>
            <dd><b>{author}</b><span><em>＜{title}シリーズ＞</em></span></dd>
            <dd><a href="/detail.php?book=sc{i}"><b>{author}</b><span>{title}２　完全な副題【電子版限定】</span></a></dd>
            <dd><b>{author}</b><span><em>＜次の別作品シリーズ＞</em></span></dd>
            <dd><a href="/detail.php?book=outside{i}"><b>{author}</b><span>外側の本</span></a></dd></dl>''')
        rows.append({
            'kind': 'official_series_reference', 'publisher': '集英社',
            'official_series_id': sid, 'official_series_url': series_url, 'series_title': title,
            'series_author': {'role': '著者', 'display_text': author, 'role_source': 'ebook_author_list',
                'role_label_selector': 'h2 img[alt="著者50音別書籍リスト"]',
                'author_field_selector': f'dl#word_{i} dd:nth-of-type(1) > b'},
            'independent_series_evidence': {'source_key': 'cobalt_anniversary',
                'selector': f'ul.anthology-list li:nth-child({i}) p.anthology-title; a[href="{series_url}"]',
                'label_text': 'シリーズ本編購入はこちら'},
            'ebook_group_evidence': {'source_key': 'ebook_author_list',
                'selector': f'dl#word_{i} dd:nth-of-type(1) span em', 'heading_text': title + 'シリーズ',
                'independent_group_id': None,
                'group_end': {'selector': f'dl#word_{i} dd:nth-of-type(3)', 'reason': 'next explicit publisher series heading'},
                'book_inclusion_basis': 'explicit_series_heading_followed_by_book_dd_links_inside_same_official_author_list_group',
                'book_count': 1},
            'books': [{'title': title + '２　完全な副題【電子版限定】', 'source_url': book_url,
                'publisher_book_id': f'sc{i}', 'author_display_text': author, 'isbn': None,
                'source_selector': f'dl#word_{i} dd:nth-of-type(2) a > b / span'}],
        })
    sources = {}
    for key, url, document in (
        ('cobalt_anniversary', ANNIVERSARY, '<ul class="anthology-list">' + ''.join(anniversary) + '</ul>'),
        ('ebook_author_list', EBOOKS, ''.join(ebook)),
    ):
        raw = document.encode('utf-8')
        target = tmp_path / (key + '.bin')
        target.write_bytes(raw)
        sources[key] = {'source_url': url, 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'raw_path': target.name, 'retrieved_at': '2026-10-25T00:00:00Z'}
    manifest = {'version': 1, 'frame_id': 'cobalt-official-series-projection-v1', 'sources': sources,
        'finite_candidate_refs': 8, 'established_refs': 8, 'book_link_references': 8,
        'unique_book_urls': 8, 'official_id_scope_complete': True, 'series': rows, 'holds': [],
        'remaining_ebook_heading_groups': 203}
    path = tmp_path / 'manifest.json'
    return path, manifest, pin(path, manifest)


def test_replays_fixed_series_without_brand_or_original_work_inference(tmp_path):
    path, _, sha = fixture(tmp_path)
    rows = verify_cobalt_series(path, sha)
    assert len(rows) == 8
    assert {r['official_series_id'] for r in rows} == set(IDS)
    assert rows[0]['books'][0]['title'] == '合成作品1２　完全な副題【電子版限定】'
    assert rows[0]['series_author']['role'] == '著者'
    assert all(r['publisher'] is None and r['label'] is None for r in rows)
    assert 'PRIVATE_PROSE' not in json.dumps(rows, ensure_ascii=False)
    assert '外側の本' not in json.dumps(rows, ensure_ascii=False)


def test_series_cache_is_bound_to_cobalt_manifest_location_and_hash(tmp_path):
    from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
    from recommend_book.light_novel_ndl_series_v1 import publisher_assertions
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    first_path, _, first_sha = fixture(first)
    second_path, _, second_sha = fixture(second)
    assert first_sha == second_sha
    frames = tmp_path / 'frames.json'; frame_sha = pin(frames, [])
    proof = {'frames_path': str(frames), 'frames_sha256': frame_sha, 'source_root': str(tmp_path),
        'cobalt_series_path': str(first_path), 'cobalt_series_sha256': first_sha}
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite'); cache = {}
    assert len(publisher_assertions(proof, cat._artifact, cache)) == 8
    (second / 'ebook_author_list.bin').write_bytes(b'corrupt relative raw in second source directory')
    with pytest.raises(ValueError, match='hash'):
        publisher_assertions({**proof, 'cobalt_series_path': str(second_path)}, cat._artifact, cache)


@pytest.mark.parametrize('field', ['id', 'url', 'title', 'role', 'author', 'book_title', 'book_url', 'start', 'end'])
def test_rejects_rehashed_projection_changes(tmp_path, field):
    path, m, _ = fixture(tmp_path)
    row = m['series'][0]
    if field == 'id': row['official_series_id'] = '99999'
    elif field == 'url': row['official_series_url'] += '&different=1'
    elif field == 'title': row['series_title'] = '別の作品'
    elif field == 'role': row['series_author']['role'] = 'イラスト'
    elif field == 'author': row['series_author']['display_text'] = '別の著者'
    elif field == 'book_title': row['books'][0]['title'] = '合成作品1'
    elif field == 'book_url': row['books'][0]['source_url'] += '&extra=1'
    elif field == 'start': row['ebook_group_evidence']['selector'] = 'dl#word_1 dd:nth-of-type(3) span em'
    elif field == 'end': row['ebook_group_evidence']['group_end']['selector'] = 'dl#word_1 dd:nth-of-type(4)'
    with pytest.raises(ValueError):
        verify_cobalt_series(path, pin(path, m))


def test_rejects_missing_author_role_even_if_hidden_in_comment(tmp_path):
    path, _, sha = fixture(tmp_path, role=False)
    with pytest.raises(ValueError, match='author role'):
        verify_cobalt_series(path, sha)


def test_rejects_changed_raw_hash_and_escaping_path(tmp_path):
    path, m, sha = fixture(tmp_path)
    (tmp_path / 'ebook_author_list.bin').write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash'):
        verify_cobalt_series(path, sha)
    path, m, _ = fixture(tmp_path)
    m['sources']['ebook_author_list']['raw_path'] = '..\\outside.bin'
    with pytest.raises(ValueError, match='path'):
        verify_cobalt_series(path, pin(path, m))


def test_rejects_subset_and_unobserved_extra_member(tmp_path):
    path, m, _ = fixture(tmp_path)
    m['series'].pop()
    with pytest.raises(ValueError):
        verify_cobalt_series(path, pin(path, m))
    path, m, _ = fixture(tmp_path)
    m['series'][0]['books'].append({**m['series'][0]['books'][0], 'title': '外側の本'})
    with pytest.raises(ValueError):
        verify_cobalt_series(path, pin(path, m))


@pytest.mark.parametrize('boundary', ['next_author', 'end_dl'])
def test_respects_author_change_and_end_of_kana_group(tmp_path, boundary):
    path, m, _ = fixture(tmp_path)
    source = m['sources']['ebook_author_list']
    target = tmp_path / source['raw_path']
    document = target.read_text(encoding='utf-8')
    outside = '''<dd><b>架空著者1</b><span><em>＜次の別作品シリーズ＞</em></span></dd>
            <dd><a href="/detail.php?book=outside1"><b>架空著者1</b><span>外側の本</span></a></dd>'''
    replacement = ('<dd><a href="/detail.php?book=outside1"><b>別の著者</b><span>外側の本</span></a></dd>'
                   if boundary == 'next_author' else '')
    assert outside in document
    target.write_text(document.replace(outside, replacement), encoding='utf-8')
    source['source_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    m['series'][0]['ebook_group_evidence']['group_end'] = (
        {'selector': 'dl#word_1 dd:nth-of-type(3)', 'reason': 'next explicit author field value'}
        if boundary == 'next_author' else {'selector': 'dl#word_1', 'reason': 'end of explicit kana dl'})
    rows = verify_cobalt_series(path, pin(path, m))
    assert len(rows[0]['books']) == 1
    assert '外側の本' not in json.dumps(rows, ensure_ascii=False)
