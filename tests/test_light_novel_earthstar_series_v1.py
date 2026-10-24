import hashlib
import json

import pytest

from recommend_book.light_novel_earthstar_series_v1 import verify_earthstar_series


def pin(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_receipt(root, path, url, document, *, parent=False):
    raw = document.encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    receipt = {'source_url': url, 'final_url': url, 'http_status': 200,
        'content_type': 'text/html; charset=utf-8', 'sha256': hashlib.sha256(raw).hexdigest(),
        'byte_length': len(raw), 'retrieved_at': '2026-10-25T00:00:00Z',
        'raw_path': str(path.relative_to(root.parent if parent else root))}
    if parent:
        receipt['raw_path_relative_to'] = 'data/light-novels-v1'
    return receipt


def book_card(i, *, author_role='著者：', suffix='', child='opaque-Child_A'):
    return f'''<article class="c-card01"><a class="c-card01__main" href="https://earthstar-novel.com/vol/detail/{child}{i}/">
    <p class="c-card01__head"><span>完全な巻題{i}　最終巻・上 13【特装版】{suffix}</span></p></a>
    <div class="c-card01__info"><div class="c-card01__info-item"><b class="c-card01__info-head">{author_role}</b>
    <span class="c-card01__info-txt">書き手{i}</span></div><div class="c-card01__info-item">
    <b class="c-card01__info-head">イラスト：</b><span class="c-card01__info-txt">絵師{i}</span></div></div></article>'''


def fixture(tmp_path):
    base = tmp_path / 'light-novels-v1'
    root = base / 'earthstar-sourceframes-v1'
    out = root / 'parent-details-v1'
    root.mkdir(parents=True)
    roster, indexcards, pages, source_rows, projections = [], [], [], [], []
    for i in (1, 2):
        workid = f'opaque-Work_A{i}'
        url = f'https://earthstar-novel.com/book/detail/{workid}/'
        indexcards.append(f'''<article class="c-card06"><a href="{url}"><h2 class="c-card06__head">親作品{i}</h2></a>
        <div class="c-card06__info-item"><b class="c-card06__info-head">著者：</b><span class="c-card06__info-txt">書き手{i}</span></div></article>''')
        title = f'親作品{i}'
        doc = f'''<title>作品一覧 | アース・スターノベル</title><link rel="canonical" href="{url}">
        <h1 class="c-book-intro01__head">{title}</h1><p>PRIVATE_PROSE</p>
        <section class="c-book-archive01"><div class="c-book-archive01__inner"><h2 class="c-head01">書籍一覧</h2>
        <div class="c-card01-list">{book_card(i)}</div></div></section>
        <section class="recommendations">{book_card(99)}</section><p>ABJマーク 著作権</p>'''
        receipt = raw_receipt(root, out / 'raw' / f'{i}.bin', url, doc, parent=True)
        source_rows.append(receipt)
        roster.append({'publisher_work_reference_id': 'earthstar-work:' + workid, 'kind': 'publisher_work_reference',
            'source_url': url, 'title': title, 'credits': [{'role': 'author', 'name': f'書き手{i}', 'role_display': '著者：'}],
            'isbn': None, 'label_display': None, 'novel_or_comic_form': 'unknown'})
    index_url = 'https://earthstar-novel.com/book/'
    index_source = raw_receipt(root, root / 'raw' / 'index.bin', index_url, ''.join(indexcards))
    for i, row in enumerate(roster, 1):
        row['index_source_snapshot'] = index_source
        child_url = f'https://earthstar-novel.com/vol/detail/opaque-Child_A{i}/'
        child = {'publisher_child_reference_id': f'earthstar-volume-reference:opaque-Child_A{i}',
            'source_url': child_url, 'title_literal': f'完全な巻題{i} 最終巻・上 13【特装版】',
            'credits': [{'role_display': '著者：', 'role': 'author', 'name': f'書き手{i}'},
                        {'role_display': 'イラスト：', 'role': 'illustrator', 'name': f'絵師{i}'}],
            'isbn': None, 'label': None, 'source_novel_or_comic_form': 'unknown',
            'publisher_parent_reference_id': row['publisher_work_reference_id'],
            'parent_work_reference_url': row['source_url'], 'publisher_group_membership_explicit': True,
            'story_series_asserted': False,
            'source_locator': '.c-book-archive01[1] .c-card01[1] title/explicit credits/a[href]', 'child_detail_fetched': False}
        projections.append({'publisher_parent_reference_id': row['publisher_work_reference_id'], 'source_unit': 'publisher_work_reference',
            'source_url': row['source_url'], 'parent_h1_title_literal': row['title'],
            'parent_title_differs_from_index_literal': False, 'direct_parent_author_credits': None,
            'index_parent_title_literal': row['title'], 'index_parent_explicit_credits': row['credits'],
            'index_source_snapshot': index_source, 'source_snapshot': source_rows[i - 1],
            'isbn': None, 'label': None, 'source_novel_or_comic_form': 'unknown', 'story_series_asserted': False,
            'explicit_child_references': [child], 'child_reference_count': 1,
            'duplicate_child_reference_ids': [], 'conflicting_child_reference_ids': [], 'holds': []})
    pages.append({'source_url': index_url, 'source_snapshot': index_source, 'cards': roster, 'next_urls': [], 'last_urls': []})
    env = {'base': base, 'root': root, 'out': out, 'roster': roster, 'pages': pages,
           'sources': source_rows, 'projections': projections}
    env['index_manifest'] = {'schema_version': 'earthstar-publisher-work-index-v1', 'source_url': index_url,
        'unit': 'publisher_work_reference_not_edition_or_story_series', 'index_pages_complete': True,
        'expected_linked_pages': 1, 'retrieved_pages': 1, 'observed_card_occurrences': 2, 'unique_work_references': 2,
        'roster_path': 'roster.json', 'pages_checkpoint_path': 'pages.jsonl'}
    env['terminal'] = {'schema_version': 'earthstar-parent-details-progress-v1',
        'status': 'terminal_all_fixed_parents_attempted', 'fixed_parent_count': 2, 'attempted_parents': 2,
        'successful_parent_projections': 2, 'projected_parent_count': 2, 'finite_parent_requests_complete': True,
        'all_parent_projections_complete': True, 'failure_or_holds': [], 'story_series_asserted': False,
        'raw_child_or_preview_or_image_or_store_requests': 0,
        'roster_path': 'data/light-novels-v1/earthstar-sourceframes-v1/roster.json',
        'projection_path': 'earthstar-sourceframes-v1/parent-details-v1/projection.json',
        'child_reference_occurrences': 2, 'unique_child_reference_urls': 2,
        'conditions': {'bibliographic_reference_only': True, 'publisher_prose_ai_input': False,
            'images_fetched_or_reused': False, 'raw_public_redistribution': False,
            'separate_content_licence': 'not_granted_or_inferred'}}
    env['proof'] = {'schema_version': 'earthstar-parent-group-proof-v1', 'expected_parent_references': 2,
        'expected_child_references': 2, 'index_manifest_path': 'index.json',
        'terminal_manifest_path': 'parent-details-v1/terminal.json'}
    save(env)
    return env


def save(env):
    root, out, base = env['root'], env['out'], env['base']
    index = env['index_manifest']
    index['roster_sha256'] = pin(root / 'roster.json', env['roster'])
    pages = root / 'pages.jsonl'
    pages.write_text('\n'.join(json.dumps(p, ensure_ascii=False) for p in env['pages']) + '\n', encoding='utf-8')
    index['pages_checkpoint_sha256'] = hashlib.sha256(pages.read_bytes()).hexdigest()
    env['proof']['index_manifest_sha256'] = pin(root / 'index.json', index)
    terminal = env['terminal']
    terminal['roster_sha256'] = index['roster_sha256']
    terminal['projection_sha256'] = pin(base / terminal['projection_path'], env['projections'])
    terminal['parents'] = []
    for i, (source, projection) in enumerate(zip(env['sources'], env['projections']), 1):
        receipt_path, projection_path = out / 'receipts' / f'{i}.json', out / 'projections' / f'{i}.json'
        terminal['parents'].append({'parent_reference_id': projection['publisher_parent_reference_id'],
            'source_url': projection['source_url'], 'receipt_path': str(receipt_path.relative_to(base)),
            'receipt_sha256': pin(receipt_path, source), 'projection_path': str(projection_path.relative_to(base)),
            'projection_sha256': pin(projection_path, projection), 'child_reference_count': projection['child_reference_count'], 'holds': []})
    env['proof']['terminal_manifest_sha256'] = pin(out / 'terminal.json', terminal)
    env['proof_path'] = root / 'proof.json'
    env['sha'] = pin(env['proof_path'], env['proof'])


def verify(env):
    return verify_earthstar_series(env['proof_path'], env['sha'])


def replace_parent_raw(env, old, new, *, i=0):
    receipt = env['sources'][i]
    path = env['base'] / receipt['raw_path']
    document = path.read_text(encoding='utf-8')
    assert old in document
    path.write_text(document.replace(old, new), encoding='utf-8')
    receipt.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), byte_length=path.stat().st_size)
    save(env)


def test_replays_explicit_parent_groups_without_story_or_book_identity(tmp_path):
    env = fixture(tmp_path)
    rows = verify(env)
    assert len(rows) == 2 and sum(len(g['books']) for g in rows) == 2
    first = rows[0]
    assert first['kind'] == 'official_work_group_reference'
    assert first['official_work_reference_id'] == 'opaque-Work_A1'
    assert first['title'] == '親作品1'
    assert first['index_author_credits'] == [{'role': 'author', 'name': '書き手1'}]
    book = first['books'][0]
    assert book['title'] == '完全な巻題1　最終巻・上 13【特装版】'
    assert book['official_child_reference_id'] == 'opaque-Child_A1'
    assert book['credits'][0]['role'] == 'author'
    assert book['isbn'] is None and book['publisher'] is None and book['label'] is None and book['form'] is None
    assert not first['story_series_asserted'] and first['publisher'] is None and first['label'] is None
    assert first['rights']['separate_content_licence'] == 'not_granted_or_inferred'
    assert 'PRIVATE_PROSE' not in json.dumps(rows) and 'Child_A99' not in json.dumps(rows)


@pytest.mark.parametrize('field', ['title', 'child_title', 'child_url', 'role', 'name', 'parent_id', 'isbn', 'label', 'form', 'series'])
def test_rehashed_projection_tamper_is_rejected(tmp_path, field):
    env = fixture(tmp_path)
    parent, book = env['projections'][0], env['projections'][0]['explicit_child_references'][0]
    if field == 'title': parent['parent_h1_title_literal'] = '別の親'
    elif field == 'child_title': book['title_literal'] = '完全な巻題1'
    elif field == 'child_url': book['source_url'] = 'https://earthstar-novel.com/vol/detail/another/'
    elif field == 'role': book['credits'][0]['role'] = 'illustrator'
    elif field == 'name': book['credits'][0]['name'] = '別の人'
    elif field == 'parent_id': book['publisher_parent_reference_id'] = env['projections'][1]['publisher_parent_reference_id']
    elif field == 'isbn': book['isbn'] = '9784758047678'
    elif field == 'label': book['label'] = 'アース・スターノベル'
    elif field == 'form': book['source_novel_or_comic_form'] = 'novel'
    else: parent['story_series_asserted'] = True
    save(env)
    with pytest.raises(ValueError):
        verify(env)


@pytest.mark.parametrize('case', ['raw_hash', 'receipt_status', 'receipt_url', 'final_url', 'size', 'path_escape', 'wrong_hash'])
def test_pinned_source_receipts_and_paths_are_enforced(tmp_path, case):
    env = fixture(tmp_path)
    source = env['sources'][0]
    if case == 'raw_hash': (env['base'] / source['raw_path']).write_bytes(b'corrupt')
    elif case == 'receipt_status': source['http_status'] = 403
    elif case == 'receipt_url': source['source_url'] = 'https://earthstar-novel.com/vol/detail/opaque-Work_A1/'
    elif case == 'final_url': source['final_url'] = 'https://evil.invalid/book/'
    elif case == 'size': source['byte_length'] += 1
    elif case == 'path_escape': source['raw_path'] = '../outside.bin'
    else: source['sha256'] = '0' * 64
    save(env)
    with pytest.raises(ValueError):
        verify(env)


@pytest.mark.parametrize('case', ['booklist_heading', 'author_role', 'canonical', 'child_query', 'child_foreign', 'nested_list', 'duplicate_conflict'])
def test_raw_group_boundaries_and_role_are_required(tmp_path, case):
    env = fixture(tmp_path)
    if case == 'booklist_heading': old, new = '>書籍一覧<', '>関連作品<'
    elif case == 'author_role': old, new = '>著者：<', '>イラスト：<'
    elif case == 'canonical': old, new = 'rel="canonical"', 'rel="alternate"'
    elif case == 'child_query': old, new = '/opaque-Child_A1/', '/opaque-Child_A1/?other=1'
    elif case == 'child_foreign': old, new = 'https://earthstar-novel.com/vol/detail/opaque-Child_A1/', 'https://evil.invalid/vol/detail/opaque-Child_A1/'
    elif case == 'nested_list': old, new = '<div class="c-card01-list">', '<section><div class="c-card01-list">'
    else: old, new = book_card(1), book_card(1) + book_card(1, suffix='別の巻')
    replace_parent_raw(env, old, new)
    with pytest.raises(ValueError):
        verify(env)


def test_outer_manifest_hash_and_index_manifest_location_are_pinned(tmp_path):
    env = fixture(tmp_path)
    with pytest.raises(ValueError, match='hash'):
        verify_earthstar_series(env['proof_path'], '0' * 64)
    env['proof']['index_manifest_path'] = '../index.json'
    env['sha'] = pin(env['proof_path'], env['proof'])
    with pytest.raises(ValueError, match='path'):
        verify(env)


def test_same_manifest_bytes_at_another_location_replay_own_local_raw(tmp_path):
    first, second = fixture(tmp_path / 'first'), fixture(tmp_path / 'second')
    assert first['sha'] == second['sha']
    assert len(verify(first)) == 2
    (second['base'] / second['sources'][0]['raw_path']).write_bytes(b'changed other directory')
    with pytest.raises(ValueError, match='hash'):
        verify(second)


def test_subset_and_unobserved_extra_book_cannot_be_resigned(tmp_path):
    env = fixture(tmp_path)
    env['projections'][0]['explicit_child_references'] = []
    env['projections'][0]['child_reference_count'] = 0
    save(env)
    with pytest.raises(ValueError):
        verify(env)
    env = fixture(tmp_path / 'extra')
    books = env['projections'][0]['explicit_child_references']
    books.append({**books[0], 'source_url': 'https://earthstar-novel.com/vol/detail/outside/'})
    env['projections'][0]['child_reference_count'] = 2
    save(env)
    with pytest.raises(ValueError):
        verify(env)


@pytest.mark.parametrize('field,value', [('finite_parent_requests_complete', False),
    ('all_parent_projections_complete', False), ('story_series_asserted', True),
    ('raw_child_or_preview_or_image_or_store_requests', 1)])
def test_terminal_scope_cannot_be_resigned_to_claim_unsupported_completion(tmp_path, field, value):
    env = fixture(tmp_path)
    env['terminal'][field] = value
    save(env)
    with pytest.raises(ValueError, match='terminal scope'):
        verify(env)


def test_abj_notice_is_not_a_content_reuse_permission(tmp_path):
    env = fixture(tmp_path)
    env['terminal']['conditions']['publisher_prose_ai_input'] = True
    save(env)
    with pytest.raises(ValueError, match='conditions'):
        verify(env)


def test_source_size_is_bounded_before_parsing(tmp_path, monkeypatch):
    import recommend_book.light_novel_earthstar_series_v1 as module
    env = fixture(tmp_path)
    monkeypatch.setattr(module, 'MAX_SOURCE_BYTES', 256)
    with pytest.raises(ValueError, match='bounded size'):
        verify(env)


def test_identical_duplicate_child_is_not_silently_counted_twice(tmp_path):
    env = fixture(tmp_path)
    replace_parent_raw(env, book_card(1), book_card(1) * 2)
    with pytest.raises(ValueError, match='duplicate'):
        verify(env)
