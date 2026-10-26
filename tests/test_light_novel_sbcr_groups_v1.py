"""Synthetic-only evidence for the bounded, saved SB group adapter."""
import gzip
import hashlib
import json

import pytest

from recommend_book.light_novel_sbcr_groups_v1 import verify_sbcr_groups


PAGE = 'https://ebook.sbcr.jp/lightnovel/series'
PARENT = PAGE + '/id/7'
RIGHTS = 'Reference-only publisher page; no synopsis, image or AI-input reuse inferred'
USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
         'prose_ai_permission': False, 'images_permission': False,
         'raw_redistribution_permission': False}
ROBOTS = {'status': 'not_saved_in_existing_source_evidence', 'permission_inferred': False}
OPEN = '<!-- ↓↓↓↓ partial.comic.bookloop ↓↓↓↓ -->'
CLOSE = '<!-- ↑↑↑↑ partial.comic.bookloop↑↑↑↑ -->'
CONTRACT = {'scope': 'partial.comic.bookloop',
    'cards': 'div.bookarea > div.book_line > div.book > dl',
    'complete_title': 'dd.title > a[href]', 'cover_alt_is_title': False,
    'unscoped_links_are_members': False, 'conflicting_explicit_titles': 'reject',
    'legacy_handoff': 'exact_existing_helper_projection_only',
    'canonical_title_agreement_with_legacy_required': False,
    'canonical_url_set_and_count_agreement': True}


def bookcard(opaque, title, alt='cover display'):
    return (f'<div class="book"><dl><dt><a href="/product/{opaque}"><img alt="{alt}"></a></dt>'
            f'<dd class="title"><a href="/product/{opaque}">{title}</a></dd></dl></div>')


def bookloop(cards, extra=''):
    return OPEN+'<div class="bookarea"><div class="book_line">'+cards+'</div></div>'+extra+CLOSE


def digest(data):
    return hashlib.sha256(data).hexdigest()


def old_id(url):
    return 'publisher-series:' + digest(url.encode())[:24]


def fixture(tmp_path):
    source = tmp_path / 'coverage'
    source.mkdir()

    def save(name, value):
        data = json.dumps(value, ensure_ascii=False, indent=2).encode()
        path = tmp_path / name
        path.write_bytes(data)
        return {'path': name, 'sha256': digest(data)}

    def receipt(name, url, text):
        data = text.encode()
        (source / name).write_bytes(data)
        return {'source_url': url, 'final_url': url, 'http_status': 200,
                'content_type': 'text/html; charset=UTF-8', 'retrieved_at': '2026-10-24T00:00:00Z',
                'rights': RIGHTS, 'raw_path': name, 'sha256': digest(data), 'byte_length': len(data)}

    index = receipt('index.html', PAGE, '<a href="/lightnovel/series/id/7">「完全な親題」シリーズ (2)</a>'
                    '<a href="/lightnovel/series/id/8">「既存」シリーズ (1)</a>'
                    '<a href="/lightnovel/series/id/9">「未完」シリーズ (2)</a>')
    parent = receipt('parent.html', PARENT, '<h1>完全な親題</h1><p>SECRET PROSE</p>'
                     + bookloop(bookcard('OPAQUE1', '完全な巻題 1')+bookcard('OPAQUE2', '完全な巻題 2'))
                     + '<a href="/product/OUTSIDE">外側の関連商品</a>')
    frame = {'id': 'ga-ebook-series', 'publisher': 'SBクリエイティブ',
             'label': 'GA文庫 / GAノベル / ツギクルブックス', 'url': PAGE,
             'kind': 'series_index', 'index_pages_complete': True,
             'completion_reason': 'all_linked_pages_exhausted', 'pages': [index],
             'reference_series_count': 3, 'historical_entire_label_complete': False}
    frames = [{'id': k} for k in ['hj-bunko-series', 'hj-novels-series', 'arianrose-series']] + [frame]
    books = [{'book_url': 'https://ebook.sbcr.jp/product/OPAQUE1', 'title': '完全な巻題 1'},
             {'book_url': 'https://ebook.sbcr.jp/product/OPAQUE2', 'title': '完全な巻題 2'}]
    rows = []
    for number, title, count, complete in [(7, '完全な親題', 2, True), (8, '既存', 1, True), (9, '未完', 2, False)]:
        url = PAGE + '/id/' + str(number)
        rows.append({'reference_id': old_id(url), 'source_url': url, 'title': title,
            'kind': 'series', 'index_id': 'ga-ebook-series', 'index_source_url': PAGE,
            'index_source_sha256': index['sha256'], 'index_retrieved_at': index['retrieved_at'],
            'publisher_reported_edition_count': count,
            'membership_index_complete': complete,
            'membership_hold_reason': None if complete else 'source_count_or_pagination_unresolved',
            'series_source_snapshot': parent if number == 7 else {'raw_path': 'MUST_NOT_OPEN'},
            'explicit_book_memberships': books if number == 7 else [],
            'author_credit': 'ROLELESS PROXY', 'representative_products': [{'MUST_NOT_OPEN': True}],
            'individual_novel_form_unproved': True, 'synopsis_reuse': 'not_granted_or_used'})
    source_roster = [{k: r[k] for k in ['reference_id', 'source_url', 'title', 'index_id',
                                      'index_source_url', 'index_source_sha256', 'index_retrieved_at']} for r in rows]
    report = {'covered_reference_ids': [old_id(PAGE+'/id/8')],
              'next_single_provider': 'ga-ebook-series',
              'next_provider_uncovered_reference_ids': [old_id(PARENT), old_id(PAGE+'/id/9')],
              'next_provider_complete_membership_reference_ids': [old_id(PARENT)],
              'next_provider_incomplete_membership_reference_ids': [old_id(PAGE+'/id/9')],
              'next_provider_complete_group_child_references': 2,
              'next_provider_complete_group_unique_child_urls': 2,
              'next_provider_cross_group_child_title_conflicts': []}
    pins = {'source_frames': save('coverage/frames.json', frames),
            'source_handoff': save('coverage/handoff.json', rows),
            'source_roster': save('coverage/roster.json', source_roster),
            'coverage_report': save('report.json', report)}
    pins['source_handoff_manifest'] = save('coverage/handoff-manifest.json', {
        'schema_version': 'sbcr-publisher-series-handoff-v3', 'rows': 3,
        'snapshot_sha256': pins['source_handoff']['sha256'], 'membership_index_complete': 2,
        'national_coverage_proved': False})
    proof = {'schema_version': 'sbcr-selected-publisher-groups-proof-v2', 'source_root': 'coverage',
             'booklist_contract': CONTRACT,
             **pins, 'usage_scope': USAGE, 'robots_evidence': ROBOTS,
             'source_rights_literal': RIGHTS, 'selected_reference_ids': [old_id(PARENT)],
             'covered_reference_ids': report['covered_reference_ids'],
             'held_reference_ids': report['next_provider_incomplete_membership_reference_ids'],
             'expected_counts': {'index_groups': 3, 'selected_groups': 1, 'selected_members': 2,
                                 'unique_selected_members': 2}}
    pin = save('proof.json', proof)
    return {'root': tmp_path, 'proof': proof, 'save': save, 'pin': pin, 'frames': frames,
            'rows': rows, 'roster': source_roster, 'report': report}


def run(f):
    return verify_sbcr_groups(f['root'] / f['pin']['path'], f['pin']['sha256'])


def update(f, key, value):
    f['proof'][key] = f['save'](f['proof'][key]['path'], value)
    if key == 'source_handoff':
        p = f['proof']['source_handoff_manifest']
        manifest = json.loads((f['root']/p['path']).read_bytes())
        manifest['snapshot_sha256'] = f['proof'][key]['sha256']
        f['proof']['source_handoff_manifest'] = f['save'](p['path'], manifest)
    f['pin'] = f['save']('proof.json', f['proof'])


def test_minimal_projection_and_selected_raw_only(tmp_path):
    f = fixture(tmp_path)
    group, = run(f)
    assert group['title'] == '完全な親題'
    assert group['local_reference_key'] == {'provider': 'sbcr', 'source_page': PAGE,
        'source_scope': 'selected_saved_parent_bookloops', 'official_parent_url': PARENT}
    key = json.dumps(group['local_reference_key'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    assert group['reference_id'] == 'sbcr-group-local:' + digest(key.encode())
    assert group['reference_id_is_official'] is False
    assert group['source_unit'] == 'publisher_work_group'
    assert group['authors'] is None
    assert group['robots_evidence'] == ROBOTS
    assert group['usage_permissions'] == USAGE
    assert group['parent_page_membership_complete'] is True
    assert group['global_membership_complete'] is None
    assert group['source_evidence'][1]['raw_sha256'] == f['rows'][0]['series_source_snapshot']['sha256']
    assert [m['title'] for m in group['members']] == ['完全な巻題 1', '完全な巻題 2']
    for row in [group, *group['members']]:
        for key in ['publisher', 'label', 'form', 'isbn', 'official_lightnovel_tag']:
            assert row[key] is None
        assert row['story_series_asserted'] is False
    for member in group['members']:
        assert member['credits'] == []
        assert member['label_literals'] == []
        assert member['publisher_reference_id'].startswith('sbcr-product:OPAQUE')
        assert member['media_references'] is None
    serialized = json.dumps(group)
    for secret in ['SECRET', 'PROXY', 'secret-image', 'OUTSIDE', 'MUST_NOT_OPEN']:
        assert secret not in serialized


def test_proof_hash_is_required(tmp_path):
    f = fixture(tmp_path)
    with pytest.raises(ValueError, match='hash'):
        verify_sbcr_groups(f['root']/'proof.json', '0'*64)


@pytest.mark.parametrize('key', ['source_frames', 'source_handoff', 'source_handoff_manifest', 'source_roster', 'coverage_report'])
def test_metadata_pin_rejects_tamper(tmp_path, key):
    f = fixture(tmp_path)
    p = f['root']/f['proof'][key]['path']
    p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError, match='hash'):
        run(f)


@pytest.mark.parametrize('field,value', [('raw_path', '../escape.html'), ('raw_path', 'C:/private.html'),
    ('http_status', 302), ('final_url', 'https://elsewhere.invalid/'), ('rights', 'permitted'),
    ('content_type', 'image/png'), ('byte_length', 1), ('byte_length', 5_000_001)])
def test_receipt_scope_and_size(tmp_path, field, value):
    f = fixture(tmp_path)
    f['rows'][0]['series_source_snapshot'][field] = value
    update(f, 'source_handoff', f['rows'])
    with pytest.raises(ValueError):
        run(f)


@pytest.mark.parametrize('name', ['parent.html', 'index.html'])
def test_raw_sha_required(tmp_path, name):
    f = fixture(tmp_path)
    p = tmp_path/'coverage'/name
    p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError, match='hash|length'):
        run(f)


def change_raw(f, text, *, compressed=False):
    raw = gzip.compress(text.encode()) if compressed else text.encode()
    (f['root']/'coverage/parent.html').write_bytes(raw)
    r = f['rows'][0]['series_source_snapshot']
    r.update(sha256=digest(raw), byte_length=len(raw))
    update(f, 'source_handoff', f['rows'])


@pytest.mark.parametrize('text', [
    OPEN+'<a href="/product/OPAQUE1">完全な巻題 1</a>',
    CLOSE+OPEN+'<a href="/product/OPAQUE1">完全な巻題 1</a>',
    OPEN+CLOSE+OPEN+CLOSE,
    OPEN+'<a href="/product/OPAQUE1">完全な巻題 1</a>'+CLOSE,
    OPEN+'<a href="/product/OPAQUE1">完全な巻題 1</a><a href="/product/OPAQUE1">違う題</a>'+CLOSE,
    OPEN+'<a href="/product/OPAQUE1">改竄題</a><a href="/product/OPAQUE2">完全な巻題 2</a>'+CLOSE,
    OPEN+'<a href="/product/OPAQUE1">完全な巻題 1</a><a href="/product/OPAQUE2">完全な巻題 2</a><a rel="next" href="?page=2">next</a>'+CLOSE,
    OPEN+'<a href="https://elsewhere.invalid/product/OPAQUE1">完全な巻題 1</a><a href="/product/OPAQUE2">完全な巻題 2</a>'+CLOSE,
    '<script>'+OPEN+'<a href="/product/OPAQUE1">完全な巻題 1</a><a href="/product/OPAQUE2">完全な巻題 2</a>'+CLOSE+'</script>',
    OPEN+'<a href="/product/OPAQUE1"></a><a href="/product/OPAQUE2">完全な巻題 2</a>'+CLOSE,
])
def test_bookloop_and_projection_closed(tmp_path, text):
    f = fixture(tmp_path)
    change_raw(f, text)
    with pytest.raises(ValueError):
        run(f)


def test_identical_child_link_dedup_and_gzip(tmp_path):
    f = fixture(tmp_path)
    text = bookloop(bookcard('OPAQUE1', '完全な巻題 1')*2+bookcard('OPAQUE2', '完全な巻題 2'))
    change_raw(f, text, compressed=True)
    assert len(run(f)[0]['members']) == 2


def test_explicit_title_field_wins_over_nonbibliographic_cover_alt(tmp_path):
    f = fixture(tmp_path)
    text = OPEN + '<div class="bookarea"><div class="book_line">'
    text += ('<div class="book"><dl><dt><a href="/product/OPAQUE1">'
             '<img alt="短い表紙用別表示"></a></dt>'
             '<dd class="title"><a href="/product/OPAQUE1">完全な巻題 1</a></dd></dl></div>')
    text += ('<div class="book"><dl><dt><a href="/product/OPAQUE2">'
             '<img alt="別の表紙表示"></a></dt>'
             '<dd class="title"><a href="/product/OPAQUE2">完全な巻題 2</a></dd></dl></div>')
    text += '</div></div>' + CLOSE
    change_raw(f, text)
    assert [m['title'] for m in run(f)[0]['members']] == ['完全な巻題 1', '完全な巻題 2']


def test_legacy_cover_display_is_replayed_but_never_used_as_safe_title(tmp_path):
    f = fixture(tmp_path)
    first = ('<div class="book"><dl><dd class="title"><a href="/product/OPAQUE1">完全な巻題 1</a></dd>'
             '<dt><a href="/product/OPAQUE1"><img alt="旧cover別表示"></a></dt></dl></div>')
    f['rows'][0]['explicit_book_memberships'][0]['title'] = '旧cover別表示'
    change_raw(f, bookloop(first+bookcard('OPAQUE2', '完全な巻題 2')))
    group, = run(f)
    assert group['legacy_projection_reproduced'] is True
    assert group['legacy_projection_is_canonical_title'] is False
    assert group['members'][0]['title'] == '完全な巻題 1'
    assert '旧cover別表示' not in json.dumps(group, ensure_ascii=False)


def test_conflicting_explicit_card_title_fields_are_rejected_with_parent_locator(tmp_path):
    f = fixture(tmp_path)
    cards = bookcard('OPAQUE1', '完全な巻題 1')+bookcard('OPAQUE1', '真の別題')+bookcard('OPAQUE2', '完全な巻題 2')
    change_raw(f, bookloop(cards))
    with pytest.raises(ValueError, match=r'reference=publisher-series:.*source_url=https://ebook.sbcr.jp/.*conflicting explicit') as exc:
        run(f)
    assert 'raw_sha256=' in str(exc.value)
    assert str(tmp_path) not in str(exc.value)


def test_conflicting_links_within_same_title_field_are_rejected(tmp_path):
    f = fixture(tmp_path)
    first = ('<div class="book"><dl><dd class="title"><a href="/product/OPAQUE1">完全な巻題 1</a>'
             '<a href="/product/OPAQUE1">真の別題</a></dd></dl></div>')
    change_raw(f, bookloop(first+bookcard('OPAQUE2', '完全な巻題 2')))
    with pytest.raises(ValueError, match='explicit complete title link'):
        run(f)


def test_global_product_links_do_not_create_parent_membership(tmp_path):
    f = fixture(tmp_path)
    text = '<a href="/product/GLOBAL">グローバル掲載</a>'
    text += bookloop(bookcard('OPAQUE1', '完全な巻題 1')+bookcard('OPAQUE2', '完全な巻題 2'))
    text += '<a href="/product/GLOBAL2">別のグローバル掲載</a>'
    change_raw(f, text)
    group, = run(f)
    assert [m['publisher_reference_id'] for m in group['members']] == ['sbcr-product:OPAQUE1', 'sbcr-product:OPAQUE2']
    assert group['source_url'] == PARENT
    assert all(m['source_evidence'][0]['source_url'] == PARENT for m in group['members'])


def test_unscoped_product_link_within_loop_does_not_become_fake_member(tmp_path):
    f = fixture(tmp_path)
    text = bookloop(bookcard('OPAQUE1', '完全な巻題 1')+bookcard('OPAQUE2', '完全な巻題 2'),
                    extra='<a href="/product/UNSCOPED">無所属リンク</a>')
    f['rows'][0]['explicit_book_memberships'].append({'book_url': 'https://ebook.sbcr.jp/product/UNSCOPED', 'title': '無所属リンク'})
    change_raw(f, text)
    with pytest.raises(ValueError, match='own child URL set/count'):
        run(f)


@pytest.mark.parametrize('field,value', [('title', '偽親題'), ('source_url', PAGE+'/id/8'),
    ('index_source_sha256', '0'*64), ('membership_index_complete', False),
    ('publisher_reported_edition_count', 3), ('synopsis_reuse', 'granted')])
def test_declared_parent_must_match_own_index_and_scope(tmp_path, field, value):
    f = fixture(tmp_path)
    f['rows'][0][field] = value
    update(f, 'source_handoff', f['rows'])
    with pytest.raises(ValueError):
        run(f)


@pytest.mark.parametrize('field,value', [('usage_scope', {**USAGE, 'prose_ai_permission': True}),
    ('robots_evidence', {'status': 'allowed', 'permission_inferred': True}),
    ('source_root', '..'), ('source_rights_literal', 'unknown'),
    ('expected_counts', {'index_groups': 3, 'selected_groups': 216, 'selected_members': 2, 'unique_selected_members': 2}),
    ('selected_reference_ids', [old_id(PAGE+'/id/8')]),
    ('held_reference_ids', []), ('covered_reference_ids', []),
    ('schema_version', 'sbcr-selected-publisher-groups-proof-v1'),
    ('booklist_contract', {**CONTRACT, 'cover_alt_is_title': True})])
def test_selection_rights_and_partition_are_fixed(tmp_path, field, value):
    f = fixture(tmp_path)
    f['proof'][field] = value
    f['pin'] = f['save']('proof.json', f['proof'])
    with pytest.raises(ValueError):
        run(f)


def test_selected_member_projection_tamper(tmp_path):
    f = fixture(tmp_path)
    f['rows'][0]['explicit_book_memberships'][0]['title'] = 'invented'
    update(f, 'source_handoff', f['rows'])
    with pytest.raises(ValueError):
        run(f)


def test_roster_title_tamper(tmp_path):
    f = fixture(tmp_path)
    f['roster'][0]['title'] = 'invented'
    update(f, 'source_roster', f['roster'])
    with pytest.raises(ValueError):
        run(f)


def test_gzip_expansion_is_bounded(tmp_path):
    f = fixture(tmp_path)
    change_raw(f, OPEN+'x'*5_000_001+CLOSE, compressed=True)
    with pytest.raises(ValueError, match='bounded size'):
        run(f)


def test_member_url_is_opaque_even_when_it_looks_like_isbn(tmp_path):
    f = fixture(tmp_path)
    text = bookloop(bookcard('9781234567897', '完全な巻題 1')+bookcard('OPAQUE2', '完全な巻題 2'))
    f['rows'][0]['explicit_book_memberships'][0]['book_url'] = 'https://ebook.sbcr.jp/product/9781234567897'
    change_raw(f, text)
    member = run(f)[0]['members'][0]
    assert member['publisher_reference_id'] == 'sbcr-product:9781234567897'
    assert member['isbn'] is None


def test_safe_output_does_not_depend_on_representative_product_proxy(tmp_path):
    f = fixture(tmp_path)
    before = run(f)
    f['rows'][0]['author_credit'] = 'UNVERIFIED DIFFERENT NAME'
    f['rows'][0]['representative_products'] = [{'source_snapshot': {'raw_path': 'NOT_OPENED'}}]
    update(f, 'source_handoff', f['rows'])
    assert run(f) == before


def test_duplicate_source_index_parent_rejected(tmp_path):
    f = fixture(tmp_path)
    r = f['frames'][-1]['pages'][0]
    p = tmp_path/'coverage/index.html'
    data = p.read_bytes()+b'<a href="/lightnovel/series/id/7">duplicate</a>'
    # Only a syntactically recognized contradictory card is a second assertion.
    data += '<a href="/lightnovel/series/id/7">「違う親題」シリーズ (2)</a>'.encode()
    p.write_bytes(data)
    r.update(sha256=digest(data), byte_length=len(data))
    update(f, 'source_frames', f['frames'])
    with pytest.raises(ValueError):
        run(f)
