import hashlib
import json

import pytest

from recommend_book.light_novel_alphapolis_references_v1 import verify_alphapolis_references


SPECS = [('alphapolis', 'アルファポリス', 'https://www.alphapolis.co.jp/book/alphapolis/tankou/100', 1753, 1752, 44),
         ('regina', 'レジーナブックス', 'https://www.alphapolis.co.jp/book/regina/tankou', 886, 886, 23)]
DUP = 'alphapolis-commercial-book:1048860/14281'


def pin(env):
    env['path'].write_text(json.dumps(env['manifest'], ensure_ascii=False), encoding='utf-8')
    env['sha'] = hashlib.sha256(env['path'].read_bytes()).hexdigest()


def fixture(tmp_path):
    frames = []
    for brand, label, entry, total, unique, last in SPECS:
        frame = {'brand': brand, 'label': label, 'entry_url': entry, 'declared_card_total': total,
            'observed_cards': total, 'unique_book_references': unique, 'pages': last,
            'next_terminal_observed': True, 'card_frame_terminal': True,
            'one_to_one_unique_total_verified': brand == 'regina',
            'duplicates': [DUP] if brand == 'alphapolis' else [], 'unresolved_metadata_conflicts': 0,
            'unknown_author_reference_count': 3 if brand == 'alphapolis' else 0,
            'isbn_known_count': 0, 'volume_known_count': 0, 'source_pages': [],
            'roster': {'path': 'forged-roster.json', 'sha256': '0' * 64}}
        for page in range(1, last + 1):
            url = entry if page == 1 else entry + f'?limit=40&page={page}'
            document = f'<div class="paginator">{total:,} 件 <a href="{entry}?limit=40&page={last}">終端</a>'
            if page < last:
                document += f'<a href="{entry}?limit=40&page={page+1}">次</a>'
            document += '</div><div class="book-blocks">'
            for ordinal in range(1, min(40, total - (page - 1) * 40) + 1):
                number = (page - 1) * 40 + ordinal
                duplicate = brand == 'alphapolis' and (page, ordinal) in {(1, 40), (2, 3)}
                ident = '1048860/14281' if duplicate else f'{1 if brand == "alphapolis" else 2}/{number}'
                title = '同じ完全題　巻３【特装版】' if duplicate else f'完全題{brand}{number} 巻３【特装版】'
                role = '作' if brand == 'alphapolis' and number <= 3 else '著'
                document += f'''<div class="book section {brand}"><h2 class="title"><a href="/book/detail/{ident}">{title}</a></h2>
                    <h3 class="sub-title">独立の副題を保持</h3><span class="label-tag {brand}">{label}</span>
                    <div class="authors"><div class="author"><a>架空名</a><span>/</span><span>{role}</span></div></div>
                    <div class="abstract">PRIVATE_PROSE<img src="https://example.invalid/private.jpg"></div></div>'''
            document += '</div>'
            raw = document.encode('utf-8')
            name = f'{brand}-{page}.raw'
            (tmp_path / name).write_bytes(raw)
            frame['source_pages'].append({'source_url': url, 'raw_path': name,
                                         'raw_sha256': hashlib.sha256(raw).hexdigest(), 'http_status': 200})
        frames.append(frame)
    env = {'root': tmp_path, 'path': tmp_path / 'manifest.json', 'manifest': {
        'schema_version': 'alphapolis-two-commercial-novel-bookframes-v1', 'frames': frames,
        'source_frame_status': 'two_declared_card_frames_terminal_with_one_duplicate_reference_hold',
        'declared_card_count': 2639, 'unique_book_references': 2638, 'same_metadata_duplicate_occurrences': 1,
        'novel_or_light_novel_positive_admissions': 0, 'original_work_or_story_series_identities_asserted': 0}}
    (tmp_path / 'forged-roster.json').write_text('[{"title":"FORGED_ROSTER"}]', encoding='utf-8')
    pin(env)
    return env


def verify(env):
    return verify_alphapolis_references(env['path'], env['sha'])


def change(env, old, new, brand=0, page=1):
    source = env['manifest']['frames'][brand]['source_pages'][page - 1]
    path = env['root'] / source['raw_path']
    document = path.read_text(encoding='utf-8')
    assert old in document
    raw = document.replace(old, new, 1).encode('utf-8')
    path.write_bytes(raw)
    source['raw_sha256'] = hashlib.sha256(raw).hexdigest()
    pin(env)


def test_replays_two_sparse_frames_and_merges_only_equal_duplicate_with_both_raw_pointers(tmp_path):
    env = fixture(tmp_path)
    rows = verify(env)
    assert len(rows) == len({r['publisher_reference_id'] for r in rows}) == 2638
    first = rows[0]
    assert first['source_unit'] == 'publisher_product_reference'
    assert first['title'] == '完全題alphapolis1 巻３【特装版】\n独立の副題を保持'
    assert first['title_literal'] == '完全題alphapolis1 巻３【特装版】'
    assert first['subtitle_literal'] == '独立の副題を保持'
    assert first['credits'] == [{'name': '架空名', 'role_display': '作', 'role': 'unknown'}]
    assert first['label'] == 'アルファポリス' and first['label_literals'] == ['アルファポリス']
    assert first['source_scope_membership']['brand'] == 'alphapolis'
    assert all(r[k] is None for r in rows for k in ['isbn', 'publisher', 'form', 'volume', 'official_lightnovel_tag'])
    assert all(r['story_series_asserted'] is False and r['metadata_conflict'] is False and r['bibliographic_variants'] == [] for r in rows)
    duplicate = next(r for r in rows if r['publisher_reference_id'] == DUP)
    assert len(duplicate['source_evidence']) == duplicate['occurrence_count'] == 2
    assert len({e['raw_sha256'] for e in duplicate['source_evidence']}) == 2
    dumped = json.dumps(rows)
    assert all(value not in dumped for value in ['FORGED_ROSTER', 'PRIVATE_PROSE', 'private.jpg'])


@pytest.mark.parametrize('kind', ['total', 'label', 'detail_host', 'next', 'terminal_next', 'duplicate_conflict', 'title', 'card_brand'])
def test_changed_raw_semantics_fail_even_after_repinning(tmp_path, kind):
    env = fixture(tmp_path)
    entry = SPECS[0][2]
    if kind == 'total': change(env, '1,753 件', '1,754 件')
    elif kind == 'label': change(env, '>アルファポリス</span>', '>別レーベル</span>')
    elif kind == 'detail_host': change(env, 'href="/book/detail/1/1"', 'href="https://example.invalid/book/detail/1/1"')
    elif kind == 'next': change(env, f'<a href="{entry}?limit=40&page=2">次</a>', '')
    elif kind == 'terminal_next': change(env, '</div><div class="book-blocks">', f'<a href="{entry}?limit=40&page=45">次</a></div><div class="book-blocks">', page=44)
    elif kind == 'duplicate_conflict': change(env, '同じ完全題　巻３【特装版】', '別の完全題', page=2)
    elif kind == 'title': change(env, '完全題alphapolis1 巻３【特装版】', '')
    else: change(env, 'book section alphapolis', 'book section comics')
    with pytest.raises(ValueError): verify(env)


@pytest.mark.parametrize('kind', ['status', 'path_escape', 'wrong_query', 'missing_page', 'wrong_duplicate'])
def test_manifest_scope_paths_status_and_all_pages_are_required(tmp_path, kind):
    env = fixture(tmp_path)
    frame = env['manifest']['frames'][0]
    if kind == 'status': frame['source_pages'][0]['http_status'] = 503
    elif kind == 'path_escape': frame['source_pages'][0]['raw_path'] = '..\\outside.raw'
    elif kind == 'wrong_query': frame['source_pages'][1]['source_url'] = SPECS[0][2] + '?limit=40&page=3'
    elif kind == 'missing_page': frame['source_pages'].pop()
    else: frame['duplicates'] = []
    pin(env)
    with pytest.raises(ValueError): verify(env)


def test_manifest_and_raw_hashes_are_required(tmp_path):
    env = fixture(tmp_path)
    with pytest.raises(ValueError, match='hash'): verify_alphapolis_references(env['path'], '0' * 64)
    (tmp_path / 'alphapolis-1.raw').write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash'): verify(env)
