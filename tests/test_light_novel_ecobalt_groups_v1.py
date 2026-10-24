import copy
import hashlib
import json

import pytest

from recommend_book.light_novel_ecobalt_groups_v1 import verify_ecobalt_groups


PAGE = 'https://ebooks.shueisha.co.jp/cobalt/'
CAPTION = '著者50音別書籍リスト'
LOG_RIGHTS = 'reference/existence only; synopsis/images excluded from AI input and redistribution'
FRAME_RIGHTS = 'Reference/existence only; tooltip synopsis and images excluded from output'
LEDGER_RIGHTS = 'reference/existence only; publisher synopsis, prose and images excluded from AI/public exports'
USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
         'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False}
CORRECTION = {'legacy_selector': 'div.list dl dd; dd span em',
              'verified_selector': '#leftBlock > div.serchKana > div.line > dl[id] > dd',
              'legacy_selector_is_authoritative': False}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pin(env, name, value):
    p = env['root']/name
    p.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return {'path': name, 'sha256': sha(p.read_bytes())}


def evidence(r, selector):
    return {'source_url': r['source_url'], 'source_sha256': r['sha256'], 'raw_path': r['raw_path'],
            'retrieved_at': r['retrieved_at'], 'selector': selector}


def save(e):
    for key, name in [('source_manifest', 'frame'), ('source_handoff', 'handoff'),
                      ('source_books', 'books'), ('source_ungrouped', 'ungrouped')]:
        e['proof'][key] = pin(e, name+'.json', e[name])
    entry = e['ledger']['frames'][0]
    entry.update(manifest='frame.json', manifest_sha256=e['proof']['source_manifest']['sha256'],
                 handoff='handoff.json', handoff_sha256=e['proof']['source_handoff']['sha256'])
    e['proof']['rights_ledger'] = pin(e, 'ledger.json', e['ledger'])
    log = e['root']/'receipts.jsonl'
    log.write_text(''.join(json.dumps(r)+'\n' for r in e['receipts']), encoding='utf-8')
    e['proof']['source_receipts'] = {'path': log.name, 'sha256': sha(log.read_bytes())}
    e['sha'] = pin(e, 'proof.json', e['proof'])['sha256']


@pytest.fixture
def source(tmp_path):
    e = {'root': tmp_path, 'handoff': [], 'books': [], 'ungrouped': [], 'receipts': []}
    for name, url, text, ct in [('robots.txt', 'https://ebooks.shueisha.co.jp/robots.txt', 'User-agent: *\nDisallow:\n', 'text/plain'),
                               ('page.html', PAGE, '', 'text/html; charset=UTF-8')]:
        raw = text.encode(); (tmp_path/name).write_bytes(raw)
        e['receipts'].append({'source_url': url, 'status': 200, 'location': None, 'content_type': ct,
            'raw_path': name, 'sha256': sha(raw), 'byte_length': len(raw), 'retrieved_at': '2026-10-24T15:26:41+00:00', 'rights': LOG_RIGHTS})
    specs = [('word_あ', [('book', '甲', '未所属の完全題', 'sc0'), ('group', '甲', '同題アンソロジー', None),
        ('book', '甲', '完全題　１【電子限定】', 'sc1'), ('group', '甲', '次の群', None),
        ('book', '甲', '次の群の外伝（下）', 'sc2'), ('book', '乙', '別表示の題', 'sc3'),
        ('book', '甲', '再登場も未所属', 'sc4')]),
        ('word_い', [('group', '甲/乙', '同題アンソロジー', None), ('book', '甲/乙', '完全な合作題', '9781234567897')])]
    fragments = []
    for dlid, rows in specs:
        dd = []
        for kind, name, title, ident in rows:
            if kind == 'group': dd.append('<dd><b>'+name+'</b><span><em>＜'+title+'シリーズ＞</em></span></dd>')
            else: dd.append('<dd><a href="/detail.php?book='+ident+'" title="PRIVATE_PROSE" onmouseover="PRIVATE_TOOLTIP"><b>'+name+'</b><span>'+title+'</span></a></dd>')
        fragments.append('<div class="line"><dl id="'+dlid+'">'+''.join(dd)+'</dl></div>')
    html = '<html><head><title>集英社eコバルト文庫 | e!集英社 | 集英社の電子書籍のプロモーションHPです</title></head><body id="kobalt"><div id="wrap"><div id="contentsBlock"><div id="leftBlock"><h2><img alt="'+CAPTION+'" src="PRIVATE_IMAGE"></h2><div class="serchKana">'+''.join(fragments)+'</div></div></div></div><script>PRIVATE_SCRIPT</script></body></html>'
    raw = html.encode(); (tmp_path/'page.html').write_bytes(raw)
    r = e['receipts'][1]; r.update(sha256=sha(raw), byte_length=len(raw))
    for dlid, rows in specs:
        active = None
        for number, (kind, name, title, ident) in enumerate(rows, 1):
            selector = f'dl#{dlid} dd:nth-of-type({number})'
            if kind == 'group':
                loc = selector+' span em'
                active = {'reference_id': 'publisher-series-heading:'+sha((PAGE+'\n'+r['sha256']+'\n'+loc).encode())[:24],
                    'kind': 'publisher_series_group', 'is_story_series': 'unknown_requires_group_review_including_anthologies',
                    'source_url': PAGE, 'publisher_series_id': None, 'official_series_url': None, 'title': title,
                    'publisher_series_heading': '＜'+title+'シリーズ＞', 'title_basis': 'explicit_official_em_series_heading_not_volume_stripping',
                    'publisher': '集英社', 'label_scope': '集英社eコバルト文庫 catalogue (includes Super Fantasy reissues; item imprint unverified)',
                    'index_id': 'shueisha-ebooks-cobalt-author-list-v1', 'author_credit': '著者：'+name,
                    'credits': [{'role': '著者', 'name': name, 'role_basis': 'official 著者50音別書籍リスト author b field'}],
                    'identity_to_catalogue': 'unassessed', 'light_novel_status': 'unknown_per_work_imprint_not_inferred',
                    'index_source_url': PAGE, 'index_source_sha256': r['sha256'], 'index_retrieved_at': r['retrieved_at'],
                    'source_evidence': evidence(r, loc), 'explicit_memberships': [], 'publisher_group_locator': loc,
                    'local_assertion_id_not_official_series_id': True}
                e['handoff'].append(active)
            else:
                belongs = active is not None and active['credits'][0]['name'] == name
                book = {'kind': 'book', 'source_url': 'https://ebooks.shueisha.co.jp/detail.php?book='+ident,
                    'publisher_book_id': ident, 'title': ' '.join(title.split()), 'isbn': None, 'publisher': '集英社',
                    'label_scope': '集英社eコバルト文庫 catalogue', 'credits': [{'role': '著者', 'name': name}],
                    'source_evidence': evidence(r, selector+' a > b / span'),
                    'membership_basis': 'adjacent_after_explicit_series_heading_within_same_dl_and_author' if belongs else None,
                    'membership_status': 'publisher_index_grouping_candidate_no_invented_series_url' if belongs else 'ungrouped'}
                e['books'].append(book)
                if belongs: active['explicit_memberships'].append(copy.deepcopy(book))
                else: active = None; e['ungrouped'].append(copy.deepcopy(book))
    e['frame'] = {'frame_id': 'shueisha-ebooks-cobalt-author-list-v1', 'kind': 'finite_official_author_book_list_with_explicit_series_headings',
        'source_evidence': evidence(r, 'div.list dl dd; dd span em'), 'raw_sha256_verified': True,
        'complete_within_single_page': True, 'publisher_reported_total': None, 'series_heading_count': 3, 'parsed_series_rows': 3,
        'books': 6, 'unique_book_urls': 6, 'book_memberships_under_explicit_heading': 3, 'ungrouped_books': 3,
        'whole_historical_cobalt_complete': False, 'official_series_ids_available': False, 'rights': FRAME_RIGHTS,
        'holds': ['All series references are local snapshot assertions, not invented official series IDs',
            'Publisher index grouping candidates require confirmation before catalogue identity or volume linking',
            'Current e-distribution list includes neighboring historical labels; per-work LN/imprint not inferred',
            'ISBN absent from index: do not infer ISBN from ebook product IDs']}
    e['ledger'] = {'version': 1, 'rights': LEDGER_RIGHTS, 'frames': [{'publisher_series_groups': 3,
        'is_story_series': 'unknown_requires_group_review', 'books': 6, 'grouped_membership_candidates': 3,
        'ungrouped_books': 3, 'finite_edistribution_frame_complete': True, 'official_series_id': None}]}
    e['proof'] = {'schema_version': 'shueisha-ecobalt-publisher-groups-proof-v1', 'usage_scope': USAGE.copy(),
        'selector_correction': CORRECTION.copy(), 'expected_counts': {'groups': 3, 'products': 6, 'members': 3, 'ungrouped': 3}}
    save(e)
    return e


def verify(e):
    return verify_ecobalt_groups(e['root']/'proof.json', e['sha'])


def rebind_raw(e, raw):
    r = e['receipts'][1]
    r.update(sha256=sha(raw), byte_length=len(raw))
    e['frame']['source_evidence']['source_sha256'] = r['sha256']
    for g in e['handoff']:
        g['index_source_sha256'] = r['sha256']
        g['source_evidence']['source_sha256'] = r['sha256']
        g['reference_id'] = 'publisher-series-heading:'+sha((PAGE+'\n'+r['sha256']+'\n'+g['publisher_group_locator']).encode())[:24]
        for book in g['explicit_memberships']:
            book['source_evidence']['source_sha256'] = r['sha256']
    for book in e['books']+e['ungrouped']:
        book['source_evidence']['source_sha256'] = r['sha256']


def test_complete_heading_intervals_partition_roles_and_opaque_products(source):
    a, b, c = verify(source)
    assert a['title'] == c['title'] == '同題アンソロジー' and a['reference_id'] != c['reference_id']
    assert a['group_end'] == {'selector': 'dl#word_あ dd:nth-of-type(4)', 'reason': 'next_explicit_heading'}
    assert b['group_end']['reason'] == 'author_display_changed' and c['group_end']['reason'] == 'dl_end'
    assert a['members'][0]['title'] == '完全題　１【電子限定】'
    assert c['members'][0]['publisher_reference_id'] == 'shueisha-ebook:9781234567897'
    assert c['members'][0]['isbn'] is None and c['members'][0]['credits'] == [{'role': 'unknown', 'name': '甲/乙'}]
    assert c['heading_author_display'] == '甲/乙' and c['authors'] is None
    assert c['members'][0]['credit_literals'][0]['entry_role_literal'] is None
    assert c['members'][0]['credit_literals'][0]['role_context_literal'] == CAPTION
    assert a['frame_partition'] == {'product_references': 6, 'grouped_members': 3, 'ungrouped_products': 3}
    assert sum(len(g['members']) for g in [a,b,c]) == 3
    assert all(g['authors'] is None and g['official_lightnovel_tag'] is None and g['global_membership_complete'] is None
               and g['independent_official_group_id'] is None and not g['story_series_asserted'] for g in [a,b,c])
    output = json.dumps([a,b,c], ensure_ascii=False)
    assert all(x not in output for x in ['PRIVATE_PROSE', 'PRIVATE_TOOLTIP', 'PRIVATE_IMAGE', 'PRIVATE_SCRIPT', '著者：'])


@pytest.mark.parametrize('kind', ['group_id', 'group_title', 'member_title', 'member_url', 'role', 'selector',
                                 'ungrouped_partition', 'transport', 'rights', 'raw_hash', 'path_escape', 'finite_count', 'manifest_hash'])
def test_pinned_raw_transport_metadata_and_partition_tampering_is_rejected(source, kind):
    g = source['handoff'][0]
    if kind == 'group_id': g['reference_id'] = 'publisher-series-heading:'+'0'*24
    elif kind == 'group_title': g['title'] = '偽題'
    elif kind == 'member_title': g['explicit_memberships'][0]['title'] = '巻を削った題'
    elif kind == 'member_url': g['explicit_memberships'][0]['source_url'] += 'x'
    elif kind == 'role': g['explicit_memberships'][0]['credits'][0]['role'] = '原作'
    elif kind == 'selector': g['source_evidence']['selector'] = 'div.list dd'
    elif kind == 'ungrouped_partition': source['ungrouped'].pop()
    elif kind == 'transport': source['receipts'][1]['location'] = PAGE+'other'
    elif kind == 'rights': source['proof']['usage_scope']['commercial_reuse'] = 'allowed'
    elif kind == 'raw_hash': (source['root']/'page.html').write_bytes(b'forged')
    elif kind == 'path_escape': source['receipts'][1]['raw_path'] = '../outside'
    elif kind == 'finite_count': source['frame']['books'] = 5
    else:
        with pytest.raises(ValueError): verify_ecobalt_groups(source['root']/'proof.json', '0'*64)
        return
    save(source)
    with pytest.raises(ValueError): verify(source)


@pytest.mark.parametrize('kind', ['caption', 'outer_scope', 'heading_link', 'duplicate_native_id'])
def test_rehashed_raw_must_still_support_own_dom_and_role_context(source, kind):
    p = source['root']/'page.html'; html = p.read_text(encoding='utf-8')
    if kind == 'caption': html = html.replace(CAPTION, '書籍リスト')
    elif kind == 'outer_scope': html = html.replace('class="serchKana"', 'class="unrelated"')
    elif kind == 'heading_link': html = html.replace('<b>甲</b><span><em>', '<a href="/forged"><b>甲</b></a><span><em>', 1)
    else: html = html.replace('book=sc2', 'book=sc1')
    raw = html.encode(); p.write_bytes(raw)
    rebind_raw(source, raw)
    save(source)
    expected = {'caption': 'global author-index caption', 'outer_scope': 'corrected DOM selector',
                'heading_link': 'heading name display', 'duplicate_native_id': 'absent or duplicate'}
    with pytest.raises(ValueError, match=expected[kind]): verify(source)
