import hashlib
import json

import pytest

from recommend_book.light_novel_orange_groups_v1 import verify_orange_groups


ORIGIN = 'https://orangebunko.shueisha.co.jp'
RECEIPT_RIGHTS = 'reference/existence only; synopsis/images excluded from AI input and redistribution'
FRAME_RIGHTS = 'reference/existence only; synopsis and images not emitted'
ALL_RIGHTS = 'reference/existence only; publisher synopsis, prose and images excluded from AI/public exports'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(env, name, value):
    p = env['root']/name
    p.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return {'path': name, 'sha256': digest(p.read_bytes())}


def receipt(env, name, url, text, kind='text/html'):
    raw = text.encode('utf-8'); (env['root']/name).write_bytes(raw)
    r = {'source_url': url, 'status': 200, 'content_type': kind+'; charset=UTF-8', 'location': None,
         'sha256': digest(raw), 'byte_length': len(raw), 'raw_path': name, 'retrieved_at': '2026-10-24T15:00:00+00:00',
         'rights': RECEIPT_RIGHTS}
    env['receipts'].append(r)
    return r


def evidence(r, selector):
    return {'source_url': r['source_url'], 'source_sha256': r['sha256'], 'raw_path': r['raw_path'],
            'retrieved_at': r['retrieved_at'], 'selector': selector}


def save(env):
    log = env['root']/'receipts.jsonl'
    log.write_text(''.join(json.dumps(r) + '\n' for r in env['receipts']), encoding='utf-8')
    env['proof']['source_receipts'] = {'path': log.name, 'sha256': digest(log.read_bytes())}
    env['proof']['source_manifest'] = pin(env, 'manifest.json', env['frame'])
    env['proof']['source_handoff'] = pin(env, 'handoff.json', env['handoff'])
    env['ledger']['frames'][0].update(manifest='manifest.json', manifest_sha256=env['proof']['source_manifest']['sha256'],
                                     handoff='handoff.json', handoff_sha256=env['proof']['source_handoff']['sha256'])
    env['proof']['rights_ledger'] = pin(env, 'ledger.json', env['ledger'])
    env['sha'] = pin(env, 'proof.json', env['proof'])['sha256']


@pytest.fixture
def source(tmp_path):
    env = {'root': tmp_path, 'receipts': [], 'handoff': [], 'proof': {'schema_version': 'shueisha-orange-publisher-groups-proof-v1',
        'expected_groups': 2, 'expected_members': 3, 'usage_scope': {'bibliographic_reference_only': True,
        'commercial_reuse': 'unknown', 'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False}}}
    receipt(env, 'robots.txt', ORIGIN+'/robots.txt', 'User-agent: *\nDisallow: /admin/\n', 'text/plain')
    urls = [ORIGIN+'/book/series/opaque-a', ORIGIN+'/book/series/opaque-b']
    sitemap = receipt(env, 'sitemap.xml', ORIGIN+'/series-sitemap.xml', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+u+'</loc></url>' for u in urls)+'</urlset>', 'text/xml')
    data = [[('完全な題　２【外伝】', '408680400x', [('著者', '架空 著者'), ('装画', '架空 絵師')]),
             ('副題を省略しない作品', 'opaque-other', [('原作', '原作者'), ('脚本', '脚本者')])],
            [('単独の完全題', '9781234567897', [(None, '役割不明の人')])]]
    for i, specs in enumerate(data):
        title = ['親の正式な題', 'もう一つの親'][i]
        html_title = '書籍 シリーズ別 '+title+' - 集英社 オレンジ文庫' if i == 0 else title+' | シリーズ | 集英社オレンジ文庫'
        cards = []
        for t, ident, credits in specs:
            names = ''.join('<p class="book-info-name">'+('<span>'+role+'</span>' if role else '')+name+'</p>' for role, name in credits)
            cards.append('<article class="book-box box"><a class="thumb" href="'+ORIGIN+'/book/'+ident+'"><img src="PRIVATE_IMAGE"></a><h2>'+t+'</h2>'+names+'<p class="description">PRIVATE_PROSE</p></article>')
        raw = '<html><head><title>'+html_title+'</title></head><body><section class="content-wrap book-wrap"><div class="content"><div class="row col4">'+''.join(cards)+'</div></div></section><script>PRIVATE_SCRIPT</script></body></html>'
        r = receipt(env, f'parent{i}.html', urls[i], raw)
        books = []
        for j, (t, ident, credits) in enumerate(specs, 1):
            books.append({'source_url': ORIGIN+'/book/'+ident, 'title': ' '.join(t.split()),
                'credits': [{'role': role, 'name': name} for role, name in credits if role is not None],
                'evidence': evidence(r, f'article.book-box.box:nth-of-type({j}) h2 / p.book-info-name'),
                'membership': 'explicit_on_publisher_series_page', 'isbn': None})
        allcredits = []
        for book in books:
            for c in book['credits']:
                if c not in allcredits and c['role'] in ['著者', '著', '作者', '原作', '原案']: allcredits.append(c)
        env['handoff'].append({'reference_id': 'publisher-series:'+digest(urls[i].encode())[:24], 'kind': 'series',
            'source_url': urls[i], 'title': title, 'title_basis': 'official_series_taxonomy_html_title',
            'publisher': '集英社', 'label_scope': '集英社オレンジ文庫', 'index_id': 'shueisha-orange-series-sitemap-v1',
            'credits': allcredits, 'author_credit': ' '.join(c['role']+'：'+c['name'] for c in allcredits) or None,
            'identity_to_catalogue': 'unassessed', 'light_novel_status': 'unknown_boundary_women_general_literature_not_inferred',
            'explicit_memberships': books, 'source_evidence': evidence(r, 'title; article.book-box.box h2 / p.book-info-name'),
            'pagination_urls': [], 'index_source_url': sitemap['source_url'], 'index_source_sha256': sitemap['sha256'],
            'index_retrieved_at': sitemap['retrieved_at']})
    env['frame'] = {'frame_id': 'shueisha-orange-series-sitemap-v1', 'publisher': '集英社', 'label_scope': '集英社オレンジ文庫',
        'frame_kind': 'official_series_taxonomy_sitemap', 'reported_total': None, 'finite_sitemap_url_count': 2, 'processed': 2,
        'series_rows': 2, 'explicit_memberships': 3, 'failures': [], 'complete': True, 'known_pagination_holds': [],
        'sitemap_evidence': evidence(sitemap, 'urlset/url/loc'), 'rights': FRAME_RIGHTS,
        'unique_explicit_books': 3, 'all_books_in_label_claim': False}
    env['ledger'] = {'version': 1, 'rights': ALL_RIGHTS, 'frames': [{'story_series': 2, 'explicit_memberships': 3,
        'unique_explicit_books': 3, 'finite_series_frame_complete': True}]}
    save(env)
    return env


def verify(env):
    return verify_orange_groups(env['root']/'proof.json', env['sha'])


def test_replays_native_parent_members_full_titles_and_roles_without_ndl_or_identity(source):
    groups = verify(source)
    assert len(groups) == 2 and sum(len(g['members']) for g in groups) == 3
    first, second = groups
    assert first['title'] == '親の正式な題' and first['reference_id_kind'] == 'local_hash_of_official_taxonomy_url'
    assert first['members'][0]['title'] == '完全な題　２【外伝】'
    assert first['members'][1]['credits'] == [{'role': 'original_author', 'name': '原作者'}, {'role': 'screenplay_author', 'name': '脚本者'}]
    assert second['members'][0]['isbn'] is None and second['members'][0]['credits'][0]['role'] == 'unknown'
    assert second['members'][0]['opaque_book_url_key'] == '9781234567897'
    assert all(g['authors'] is None and g['publisher'] is None and g['label'] is None and g['form'] is None
               and g['official_lightnovel_tag'] is None and not g['story_series_asserted']
               and g['global_membership_complete'] is None for g in groups)
    assert all(m['isbn'] is None and m['label'] is None and m['form'] is None for g in groups for m in g['members'])
    assert all('オレンジ文庫' in g['site_context']['brand_title_literal'] for g in groups)
    assert all(s not in json.dumps(groups) for s in ['PRIVATE_PROSE', 'PRIVATE_IMAGE', 'PRIVATE_SCRIPT'])


@pytest.mark.parametrize('kind', ['group_id', 'group_title', 'member_title', 'member_url', 'credit', 'selector',
                                 'rights', 'ledger_scope', 'transport', 'receipt_status', 'raw_hash', 'path_escape', 'finite_total', 'pagination'])
def test_manifest_projection_transport_and_raw_mutations_fail_closed_after_repinning(source, kind):
    g = source['handoff'][0]
    if kind == 'group_id': g['reference_id'] = 'publisher-series:'+'0'*24
    elif kind == 'group_title': g['title'] = '偽の親題'
    elif kind == 'member_title': g['explicit_memberships'][0]['title'] = '不完全な題'
    elif kind == 'member_url': g['explicit_memberships'][0]['source_url'] = ORIGIN+'/book/forged'
    elif kind == 'credit': g['explicit_memberships'][0]['credits'][0]['role'] = '原作'
    elif kind == 'selector': g['explicit_memberships'][0]['evidence']['selector'] = 'article:nth-of-type(99)'
    elif kind == 'rights': source['proof']['usage_scope']['commercial_reuse'] = 'allowed'
    elif kind == 'ledger_scope': source['ledger']['rights'] = 'unrestricted commercial reuse'
    elif kind == 'transport': source['receipts'][2]['location'] = ORIGIN+'/redirect'
    elif kind == 'receipt_status': source['receipts'][2]['status'] = 503
    elif kind == 'raw_hash': (source['root']/'parent0.html').write_bytes(b'<title>forged</title>')
    elif kind == 'path_escape': source['receipts'][2]['raw_path'] = '../outside'
    elif kind == 'finite_total': source['frame']['finite_sitemap_url_count'] = 3
    else: g['pagination_urls'] = [g['source_url']+'/page/2']
    save(source)
    with pytest.raises(ValueError): verify(source)


def test_manifest_hash_is_required(source):
    with pytest.raises(ValueError, match='hash'):
        verify_orange_groups(source['root']/'proof.json', '0'*64)


@pytest.mark.parametrize('kind', ['brand', 'own_parent_container'])
def test_rehashed_raw_cannot_change_the_brand_or_explicit_parent_list_scope(source, kind):
    p = source['root']/'parent0.html'
    text = p.read_text(encoding='utf-8')
    text = text.replace('集英社 オレンジ文庫', '別のブランド') if kind == 'brand' else text.replace('content-wrap book-wrap', 'content-wrap unrelated')
    raw = text.encode();p.write_bytes(raw)
    source['receipts'][2].update(sha256=digest(raw), byte_length=len(raw))
    source['handoff'][0]['source_evidence']['source_sha256'] = digest(raw)
    for member in source['handoff'][0]['explicit_memberships']:
        member['evidence']['source_sha256'] = digest(raw)
    save(source)
    with pytest.raises(ValueError): verify(source)
