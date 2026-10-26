"""Real source parsers and temporary SQLite only; no network or model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from test_light_novel_catalogue_v1 import setup_catalogue, edition_bundle, publisher_edition_v2
from recommend_book.light_novel_catalogue_v1 import normalize_search
from recommend_book.light_novel_classification_v1 import series_source_policy, TERMS_URL, WIKIDATA_TERMS, LICENSE_URL
from recommend_book.light_novel_hj_wikipedia_links_v1 import (
    VERSION, RULE, KIND, _novel_link, verify_links, resolve_memberships, membership_candidates, _selected,
)

URL = 'https://firecross.jp/hjbunko/series/71'


def pin(path):
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return pin(path)


@pytest.fixture
def bundle(setup_catalogue, publisher_edition_v2, tmp_path):
    cat, records = setup_catalogue
    parent = records[0]; title = parent['title']
    raw = Path(parent['provenance']['raw_path']); payload = json.loads(raw.read_bytes())
    payload['entities']['Q1']['sitelinks'] = {'jawiki': {'site': 'jawiki', 'title': title, 'badges': []}}
    payload['entities']['Q1']['claims']['P856'] = [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': 'http://hobbyjapan.co.jp/old/'}}}]
    parent['sitelinks'] = payload['entities']['Q1']['sitelinks']
    parent['provenance']['raw_sha256'] = write(raw, payload)['sha256']
    cat, edition, _ = publisher_edition_v2(title=title, volume='2', imprint='HJ文庫', publisher='ホビージャパン')
    parent['provenance']['conditions_sha256'] = edition['provenance']['conditions_sha256']
    assert cat.ingest([parent, edition])['failed'] == 0
    meta = tmp_path/'metadata.json'; write(meta, parent)
    text = ('== 既刊一覧 ==\n=== 小説 ===\n'
        '{{Cite web|url='+URL+'|title='+title+'|website=HJ文庫公式Webサイト|publisher=ホビージャパン}}\n'
        '=== 漫画 ===\n[https://firecross.jp/comic/series/70 漫画公式]\n')
    page = {'pageid': 42, 'ns': 0, 'title': title, 'pageprops': {'wikibase_item': 'Q1'},
        'revisions': [{'revid': 91, 'slots': {'main': {'contentmodel': 'wikitext', 'contentformat': 'text/x-wiki', 'content': text}}}]}
    wp = tmp_path/'wikipedia.json'; write(wp, {'query': {'pages': [page]}})
    from urllib.parse import urlencode
    receipt = {'path': str(wp.resolve()), 'sha256': pin(wp)['sha256'], 'size': len(wp.read_bytes()),
        'source_url': 'https://ja.wikipedia.org/w/api.php?'+urlencode({'titles': title}), 'metadata': {'status_code': 200}}
    ledger = tmp_path/'ledger.jsonl'; ledger.write_text(json.dumps(receipt), encoding='utf-8')
    book = {'@type': 'Book', 'url': 'https://firecross.jp/hjbunko/product/216', 'name': title+' 2', 'isbn': '9784798613161'}
    series = {'@type': 'BookSeries', 'url': URL, 'name': title,
        'author': [{'@type': 'Person', 'name': '著者（著者）'}, {'@type': 'Person', 'name': '画家（イラスト）'}],
        'publisher': {'@type': 'Organization', 'name': 'ホビージャパン（HJ文庫）'}, 'hasPart': [book]}
    pub = tmp_path/'publisher.html'; pub.write_text('<script type="application/ld+json">'+json.dumps(series, ensure_ascii=False)+'</script>', encoding='utf-8')
    pr = tmp_path/'receipt.json'; write(pr, {'source_url': URL, 'final_url': URL, 'http_status': 200,
        'rights': 'Reference-only publisher page; no synopsis, image or AI-input reuse inferred',
        'content_type': 'text/html; charset=UTF-8', 'sha256': pin(pub)['sha256'],
        'raw_path': str(pub.resolve()), 'byte_length': len(pub.read_bytes())})
    policy = tmp_path/'policy.json'; write(policy, series_source_policy())
    terms = {}
    for i, u in enumerate([TERMS_URL, WIKIDATA_TERMS, LICENSE_URL]):
        p = tmp_path/f'terms-{i}.html'; p.write_text('pinned official terms fixture', encoding='utf-8'); terms[u] = pin(p)
    manifest = {'version': VERSION, 'source_policy': pin(policy), 'conditions': terms, 'series': [{
        'series_entry_id': 'lns:wd:Q1', 'metadata': pin(meta),
        'wikidata': {'path': parent['provenance']['raw_path'], 'sha256': parent['provenance']['raw_sha256']},
        'official_series_url': URL, 'wikipedia': {'language': 'ja', 'raw': pin(wp), 'receipt_ledger': pin(ledger),
            'receipt_line': 1, 'page_pointer': '/query/pages/0', 'page_id': 42, 'revision_id': 91,
            'novel_link_locator': _novel_link(text, URL, title, normalize_search)},
        'publisher': {'receipt': pin(pr), 'raw': pin(pub)}}]}
    path = tmp_path/'manifest.json'; write(path, manifest)
    return {'cat': cat, 'parent': parent, 'edition': edition, 'manifest': manifest, 'path': path,
            'page': page, 'wp': wp, 'ledger': ledger, 'series': series, 'pub': pub, 'pr': pr, 'meta': meta}


@pytest.mark.parametrize('missing', [False, True])
def test_publisher_receipt_must_bind_the_exact_selected_artifact(bundle, missing):
    e = bundle
    receipt = json.loads(e['pr'].read_bytes())
    if missing:
        del receipt['raw_path']
    else:
        other = e['pub'].with_name('other-publisher.html')
        other.write_bytes(e['pub'].read_bytes())
        receipt['raw_path'] = str(other.resolve())
    write(e['pr'], receipt)
    sha = repin(e)
    with pytest.raises(ValueError, match='publisher receipt'):
        verify_links(e['path'], sha, e['cat']._artifact, {})


def test_original_coverage_relative_receipt_binds_same_named_snapshot(bundle):
    e = bundle
    store = e['pr'].parent / 'source-snapshots'; store.mkdir()
    old_receipt = json.loads(e['pr'].read_bytes())
    e['pub'] = store / 'original.html'
    e['pr'] = store / 'original.json'
    old_receipt['raw_path'] = str(Path('source-snapshots') / 'original.html')
    write(e['pr'], old_receipt)
    sha = repin(e)
    assert verify_links(e['path'], sha, e['cat']._artifact, {})['lns:wd:Q1']['source_url'] == URL


def repin(env, *, locator=True):
    w = env['manifest']['series'][0]
    write(env['wp'], {'query': {'pages': [env['page']]}}); w['wikipedia']['raw'] = pin(env['wp'])
    receipt = json.loads(env['ledger'].read_bytes()); receipt.update(path=str(env['wp'].resolve()), sha256=pin(env['wp'])['sha256'], size=len(env['wp'].read_bytes()))
    env['ledger'].write_text(json.dumps(receipt), encoding='utf-8'); w['wikipedia']['receipt_ledger'] = pin(env['ledger'])
    if locator:
        w['wikipedia']['novel_link_locator'] = _novel_link(env['page']['revisions'][0]['slots']['main']['content'], URL, env['parent']['title'], normalize_search)
    env['pub'].write_text('<script type="application/ld+json">'+json.dumps(env['series'], ensure_ascii=False)+'</script>', encoding='utf-8')
    r = json.loads(env['pr'].read_bytes()); r.update(sha256=pin(env['pub'])['sha256'], byte_length=len(env['pub'].read_bytes()))
    write(env['pr'], r); w['publisher'] = {'receipt': pin(env['pr']), 'raw': pin(env['pub'])}
    return write(env['path'], env['manifest'])['sha256']


def test_new_explicit_route_resolves_idempotently_preserves_old_metadata_and_no_p856_repair(bundle):
    e = bundle; cat = e['cat']; sha = repin(e); identity = 'lne:ndl:R100000002-I1'
    with cat._connect() as db: before = [tuple(r) for r in db.execute('SELECT * FROM entries ORDER BY entry_id')]
    assert resolve_memberships(cat, e['path'], sha, edition_ids=[identity]) == {
        'requested_editions': 1, 'linked': 1, 'unchanged': 0, 'protected_existing': 0, 'unresolved_editions': 0}
    assert resolve_memberships(cat, e['path'], sha, edition_ids=[identity])['unchanged'] == 1
    with cat._connect() as db:
        assert before == [tuple(r) for r in db.execute('SELECT * FROM entries ORDER BY entry_id')]
        proof = json.loads(db.execute('SELECT proof_json FROM edition_series_memberships').fetchone()[0])
    assert proof['kind'] == KIND and proof['membership']['volume_number'] == 2
    assert proof['membership']['P50_backfilled'] is False
    assert proof['membership']['wikipedia_attribution']['quote_allowed'] is False
    # The writer is also responsible for the public catalogue graph: detail,
    # search, and recommendation must all see the same authenticated relation.
    series = cat.work_detail('lns:wd:Q1')
    edition = cat.work_detail(identity)
    member = next(item for item in series['members'] if item['entry_id'] == identity)
    assert member['series_membership_evidence']['rule_version'] == RULE
    assert member['series_membership_evidence']['series_identity_basis'] == 'exact_wikidata_sitelink_same_pageprops_and_explicit_novel_official_link'
    assert member['series_membership_evidence']['official_series_url'] == URL
    assert edition['series_entry_id'] == 'lns:wd:Q1'
    assert edition['series_membership_evidence']['official_book']['isbn'] == '9784798613161'
    assert cat.search_catalogue(e['parent']['title'], entry_kind='edition')['items'][0]['entry_id'] == identity
    recommendation = cat.recommend_preferences(liked_entry_ids=[identity], limit=10)
    assert recommendation['liked_entry_ids'] == [identity]
    assert all(item['entry_id'] != 'lns:wd:Q1' for item in recommendation['items'])
    assert e['parent']['authors'] == []
    assert URL not in json.dumps(json.loads(Path(e['parent']['provenance']['raw_path']).read_bytes())['entities']['Q1']['claims']['P856'])


@pytest.mark.parametrize('tamper', ['pageprops', 'title', 'revision', 'pointer', 'locator', 'receipt_status',
    'novel_to_manga', 'comment', 'duplicate_citation', 'cite_title', 'cite_provider', 'publisher_url',
    'publisher_name', 'publisher_role', 'publisher_manga', 'duplicate_isbn', 'terms_missing', 'terms_bytes', 'policy'])
def test_forged_or_cross_media_source_rejected_before_any_membership(bundle, tamper):
    e = bundle; w = e['manifest']['series'][0]; text = e['page']['revisions'][0]['slots']['main']
    if tamper == 'pageprops': e['page']['pageprops']['wikibase_item'] = 'Q2'
    elif tamper == 'title': e['page']['title'] = '別作品'
    elif tamper == 'revision': w['wikipedia']['revision_id'] = 92
    elif tamper == 'pointer': w['wikipedia']['page_pointer'] = '/query/pages/1'
    elif tamper == 'locator': w['wikipedia']['novel_link_locator']['line'] += 1
    elif tamper == 'receipt_status':
        r = json.loads(e['ledger'].read_bytes()); r['metadata']['status_code'] = 404; e['ledger'].write_text(json.dumps(r), encoding='utf-8')
    elif tamper == 'novel_to_manga': text['content'] = text['content'].replace('=== 小説 ===', '=== 漫画 ===')
    elif tamper == 'comment': text['content'] = text['content'].replace('{{Cite web', '<!--{{Cite web').replace('}}\n', '}}-->\n')
    elif tamper == 'duplicate_citation': text['content'] = text['content'].replace('=== 漫画 ===', text['content'].splitlines()[2]+'\n=== 漫画 ===')
    elif tamper == 'cite_title': text['content'] = text['content'].replace('|title='+e['parent']['title'], '|title=別作品')
    elif tamper == 'cite_provider': text['content'] = text['content'].replace('HJ文庫公式Webサイト', '漫画公式Webサイト')
    elif tamper == 'publisher_url': e['series']['url'] = URL+'0'
    elif tamper == 'publisher_name': e['series']['name'] = '別作品'
    elif tamper == 'publisher_role': e['series']['author'][0]['name'] = '著者'
    elif tamper == 'publisher_manga': e['series']['hasPart'][0]['url'] = 'https://firecross.jp/comic/product/216'
    elif tamper == 'duplicate_isbn': e['series']['hasPart'].append(deepcopy(e['series']['hasPart'][0]))
    elif tamper == 'terms_missing': e['manifest']['conditions'].pop(LICENSE_URL)
    elif tamper == 'terms_bytes': Path(e['manifest']['conditions'][LICENSE_URL]['path']).write_text('changed')
    else:
        p = Path(e['manifest']['source_policy']['path']); write(p, {'unknown': 'policy'}); e['manifest']['source_policy'] = pin(p)
    sha = repin(e, locator=False)
    with pytest.raises(ValueError): resolve_memberships(e['cat'], e['path'], sha, edition_ids=['lne:ndl:R100000002-I1'])
    with e['cat']._connect() as db: assert db.execute('SELECT count(*) FROM edition_series_memberships').fetchone()[0] == 0


@pytest.mark.parametrize('tamper', ['swapped_number', 'extra_sku', 'different_title', 'different_isbn'])
def test_official_book_isbn_does_not_replace_full_own_title_volume_match(bundle, tamper):
    e = bundle; book = e['series']['hasPart'][0]
    if tamper == 'swapped_number': book['name'] = e['parent']['title']+' 3'
    elif tamper == 'extra_sku': book['name'] += ' 特装版'
    elif tamper == 'different_title': book['name'] = '別作品 2'
    else: book['isbn'] = '9784894259003'
    result = resolve_memberships(e['cat'], e['path'], repin(e), edition_ids=['lne:ndl:R100000002-I1'])
    assert result['linked'] == 0 and result['unresolved_editions'] == 1


def test_old_p856_matcher_not_changed_and_source_mutation_inside_transaction_rolls_back(bundle, monkeypatch):
    e = bundle; cat = e['cat']; sha = repin(e)
    original = cat._membership_base_rows; calls = []
    def changing(stored, cache):
        rows = original(stored, cache); calls.append(True)
        if len(calls) == 1: e['wp'].write_text('{}', encoding='utf-8')
        return rows
    monkeypatch.setattr(cat, '_membership_base_rows', changing)
    with pytest.raises(ValueError, match='SHA'): resolve_memberships(cat, e['path'], sha, edition_ids=['lne:ndl:R100000002-I1'])
    with cat._connect() as db: assert db.execute('SELECT count(*) FROM edition_series_memberships').fetchone()[0] == 0


def test_previous_relation_is_never_replaced(bundle):
    e = bundle; cat = e['cat']
    with cat._connect() as db:
        db.execute('INSERT INTO edition_series_memberships VALUES(?,?,?,?)',
            ('lne:ndl:R100000002-I1', 'lns:wd:Q1', '{"old":"proof"}', 'old-sha'))
    assert resolve_memberships(cat, e['path'], repin(e), edition_ids=['lne:ndl:R100000002-I1'])['protected_existing'] == 1
    with cat._connect() as db: assert db.execute('SELECT proof_json FROM edition_series_memberships').fetchone()[0] == '{"old":"proof"}'


@pytest.mark.parametrize('config', [
    {'volume': '7.5'}, {'volume': '8.5'}, {'volume': ''}, {'volume': '上'},
    {'authority': False}, {'literal': ''}, {'literal': '著者 イラスト'},
    {'ndc': '726.1'}, {'publisher': '別出版社'}, {'imprint': '別文庫'},
    {'extra': '<d:language>eng</d:language>'},
])
def test_all_independent_own_bibliographic_and_authority_guards_remain_required(bundle, publisher_edition_v2, config):
    e = bundle
    args = {'title': e['parent']['title'], 'volume': '2', 'imprint': 'HJ文庫', 'publisher': 'ホビージャパン', **config}
    _, edition, _ = publisher_edition_v2(**args)
    # The duplicate language fixture is intentionally an unknown-language raw
    # record; keep its serialized summary aligned so ingest reaches the HJ
    # membership gate instead of failing the generic raw replay first.
    if config.get('extra'):
        edition['languages'] = []
    assert e['cat'].ingest([edition])['failed'] == 0
    result = resolve_memberships(e['cat'], e['path'], repin(e), edition_ids=['lne:ndl:R100000002-I1'])
    assert result['linked'] == 0 and result['unresolved_editions'] == 1


def test_duplicate_title_parent_is_a_hold_even_when_only_one_has_new_link_proof(bundle):
    e = bundle; parent = deepcopy(e['parent']); entity_path = Path(parent['provenance']['raw_path'])
    entity = json.loads(entity_path.read_bytes())['entities']['Q1']; entity['id'] = 'Q9'
    newraw = entity_path.parent/'duplicate-entity.json'; write(newraw, {'entities': {'Q9': entity,
        'Q100': {'id': 'Q100', 'labels': {'ja': {'value': 'ファンタジー'}}}}})
    parent['id'] = 'Q9'; parent['provenance'].update(raw_path=str(newraw), raw_sha256=pin(newraw)['sha256'], url='https://www.wikidata.org/wiki/Q9')
    assert e['cat'].ingest([parent])['inserted'] == 1
    result = resolve_memberships(e['cat'], e['path'], repin(e), edition_ids=['lne:ndl:R100000002-I1'])
    assert result['linked'] == 0 and result['unresolved_editions'] == 1


def test_duplicate_native_page_ownership_and_raw_hash_forgery_are_rejected(bundle):
    e = bundle; sha = repin(e); w = e['manifest']['series'][0]
    write(e['wp'], {'query': {'pages': [e['page'], deepcopy(e['page'])]}})
    with pytest.raises(ValueError, match='SHA'):
        verify_links(e['path'], sha, e['cat']._artifact, {})
    w['wikipedia']['raw'] = pin(e['wp']); receipt = json.loads(e['ledger'].read_bytes())
    receipt.update(sha256=pin(e['wp'])['sha256'], size=len(e['wp'].read_bytes()))
    e['ledger'].write_text(json.dumps(receipt), encoding='utf-8'); w['wikipedia']['receipt_ledger'] = pin(e['ledger'])
    sha = write(e['path'], e['manifest'])['sha256']
    with pytest.raises(ValueError, match='ownership'):
        verify_links(e['path'], sha, e['cat']._artifact, {})
