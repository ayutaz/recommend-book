"""DX standalone references keep URL identifiers opaque and credits literal."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work


IDS = ['978-4-08-631001-7', 'XXX-X-XX-XXXXXX-1']
COMIC = '978-4-08-890001-7'
LABELS = ['スーパーダッシュ文庫', 'ダッシュエックス文庫', 'Dノベルf／ダッシュエックスノベルエフ',
          'Dノベル／ダッシュエックスノベル', 'ダッシュエックス文庫DIGITAL']


def frame(root, *, title='試験小説２【合本】', label=LABELS[1]):
    root.mkdir()
    start = 'https://dash.shueisha.co.jp/library/index/'
    def card(identity, title, label):
        return (f'<section class="result_box"><h4><a href="https://dash.shueisha.co.jp/bookDetail/index/{identity}">{title}</a></h4>'
            '<span class="boldText">著者：架空 花子</span><span class="boldText">原作・イラスト：架空 太郎</span>'
            f'<span class="boldText">レーベル</span>：{label}\n<p>PRIVATE_PROSE</p><img src="PRIVATE_IMAGE"></section>')
    first = '<h3>検索結果：3件</h3>' + card(IDS[0], title, label) + card(COMIC, '漫画版小説と同じ題', 'ヤングジャンプコミックス')
    first += '<a href="/library/index/2/">次の20件</a>'
    second = '<h3>検索結果：3件</h3>' + card(IDS[1], '電子小説完全題１', LABELS[4])
    sources = []
    for n, document in enumerate([first, second], 1):
        raw = document.encode('utf-8')
        p = root / f'page{n}.bin'; p.write_bytes(raw)
        sources.append({'source_url': start if n == 1 else start + '2/', 'status': 200,
            'sha256': hashlib.sha256(raw).hexdigest(), 'raw_path': p.name, 'retrieved_at': '2026-10-25T00:00:00Z'})
    manifest = {'frame_id': 'shueisha-dash-library-all-v1', 'complete': True,
        'publisher_reported_books': 3, 'visited_pages': [s['source_url'] for s in sources], 'sources': sources}
    path = root / 'manifest.json'; path.write_text(json.dumps(manifest), encoding='utf-8')
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    import recommend_book.light_novel_shueisha_books_v1 as source
    path, sha = frame(tmp_path / 'source')
    verify, local = source.verify_shueisha_books, source._local_file
    calls, pages = [], []
    def checked(path, sha):
        calls.append((str(path), sha))
        return verify(path, sha)
    def selected(root, relative):
        pages.append(relative)
        return local(root, relative)
    monkeypatch.setattr(source, 'verify_shueisha_books', checked)
    monkeypatch.setattr(source, '_local_file', selected)
    return SimpleNamespace(cat=LightNovelCatalogue(tmp_path / 'catalogue.sqlite'), source=source,
        path=path, sha=sha, calls=calls, pages=pages, verify=checked, root=tmp_path)


def adopt(e, ids=IDS):
    return e.cat.import_shueisha_dx_publisher_references(e.path, e.sha, reference_ids=ids)


def snapshot(cat):
    with cat._connect() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY 1')] for t in tables}


def test_complete_source_core_public_unified_preserves_url_id_and_compound_role_but_no_isbn(bundle):
    e = bundle
    result = adopt(e)
    assert result['source_references'] == 3 and result['requested_references'] == result['inserted'] == 2
    assert result['held'] == 0 and len(e.calls) == 1 and e.pages == ['page1.bin', 'page2.bin']
    identity = result['reference_entries'][IDS[0]]
    assert identity.startswith('lnr:pub:shueishadx:') and len(identity.rsplit(':', 1)[1]) == 32
    def forbidden(*args, **kwargs): pytest.fail('DX standalone reference must not consult world catalogue')
    unified = UnifiedCatalogue(SimpleNamespace(db_path=e.root / 'world.sqlite', work_detail=forbidden,
        set_read_status=forbidden, similar_works=forbidden), e.cat)
    e.calls.clear(); e.pages.clear()
    row = unified.work_detail(identity)
    assert len(e.calls) == 1 and e.pages == ['page1.bin', 'page2.bin']
    assert row['isbn'] == row['authors'] == row['publishers'] == row['members'] == []
    assert row['isbn_basis'] == 'unknown_no_independent_ISBN_field_source_URL_identifier_only'
    product = row['publisher_product_reference']
    assert product['publisher_reference_id'] == IDS[0]
    assert product['source_url'] == 'https://dash.shueisha.co.jp/bookDetail/index/' + IDS[0]
    assert product['form'] is None and product['label'] == LABELS[1]
    assert product['credits'] == [{'role': 'unknown', 'name': '架空 花子'}, {'role': 'unknown', 'name': '架空 太郎'}]
    assert product['credit_literals'] == [{'role_literal': '著者', 'name': '架空 花子'},
        {'role_literal': '原作・イラスト', 'name': '架空 太郎'}]
    assert all(row[k] is False for k in ('light_novel_confirmed', 'light_novel_identity_asserted',
        'actual_work_identity_asserted', 'edition_identity_asserted', 'original_work_identity_asserted',
        'story_series_identity_asserted', 'recommendable'))
    assert row['reference_scope'] == 'private_bibliographic_discovery'
    assert row['usage_permissions'] == {'purpose': 'private_bibliographic_discovery',
        'public_display': 'unknown', 'commercial_reuse': 'unknown', 'prose_ai_permission': False,
        'images_permission': False, 'raw_redistribution_permission': False}
    assert e.cat.search_catalogue('架空 太郎')['total'] == 2
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == 'shueisha_dx_product_reference' and shown['isbn'] == []
    assert shown['publisher_product_reference']['credit_literals'] == product['credit_literals']
    assert 'PRIVATE_' not in json.dumps(shown) and str(e.root) not in json.dumps(shown)
    assert unified.set_read_status(identity, 'reading')['read_status'] == 'reading'
    assert unified.similar_works(identity)['mode'] == 'insufficient_evidence'
    opaque = e.cat.work_detail(result['reference_entries'][IDS[1]])
    assert opaque['publisher_product_reference']['publisher_reference_id'] == IDS[1] and opaque['isbn'] == []


def test_selection_is_explicit_and_does_not_authenticate_invalid_id_collections(bundle):
    e = bundle
    with pytest.raises(TypeError): e.cat.import_shueisha_dx_publisher_references(e.path, e.sha)
    for ids in (None, [], IDS[0], IDS[:1] * 2, [None], ['shueisha:123'], ['ABC']):
        with pytest.raises(ValueError): adopt(e, ids)
    assert e.calls == []
    with pytest.raises(ValueError, match='unknown or duplicate'): adopt(e, ['1-2-3-X'])
    assert snapshot(e.cat)['entries'] == []


def test_unselected_comics_are_verified_but_never_admitted_as_novel_context(bundle):
    e = bundle
    assert adopt(e)['inserted'] == 2
    before = snapshot(e.cat)
    with pytest.raises(ValueError, match='novel imprint'): adopt(e, [COMIC])
    assert snapshot(e.cat) == before


@pytest.mark.parametrize('label', LABELS)
def test_each_exact_novel_imprint_is_context_only(bundle, label):
    e = bundle
    path, sha = frame(e.root / 'label', label=label)
    result = e.cat.import_shueisha_dx_publisher_references(path, sha, reference_ids=IDS[:1])
    row = e.cat.work_detail(result['reference_entries'][IDS[0]])
    assert row['publication_label'] == label and not row['light_novel_confirmed'] and row['authors'] == []


def test_reimport_and_new_snapshot_keep_uuid_and_read_state_but_retitles_hold(bundle):
    e = bundle
    first = adopt(e)
    identity = first['reference_entries'][IDS[0]]
    e.cat.set_read_status(identity, 'reading')
    before = snapshot(e.cat)
    e.calls.clear(); e.pages.clear()
    again = adopt(e)
    assert again['unchanged'] == 2 and again['inserted'] == again['updated'] == 0
    assert again['reference_entries'] == first['reference_entries'] and snapshot(e.cat) == before
    assert len(e.calls) == 1 and e.pages == ['page1.bin', 'page2.bin']
    path, sha = frame(e.root / 'next')
    result = e.cat.import_shueisha_dx_publisher_references(path, sha, reference_ids=IDS[:1])
    assert result['updated'] == 1 and result['reference_entries'][IDS[0]] == identity
    before = snapshot(e.cat)
    path, sha = frame(e.root / 'retitled', title='改題した完全題')
    result = e.cat.import_shueisha_dx_publisher_references(path, sha, reference_ids=IDS[:1])
    assert result['held_reasons'] == {'source_reference_scope_or_title_changed': 1}
    assert result['inserted'] == result['updated'] == 0 and snapshot(e.cat) == before
    assert e.cat.work_detail(identity)['read_status'] == 'reading'


@pytest.mark.parametrize('field', ['id', 'url', 'unit', 'role', 'title', 'story'])
def test_formal_source_output_mismatch_is_atomic(bundle, monkeypatch, field):
    e = bundle
    def invalid(*args):
        rows = e.verify(*args)
        row = rows[-1]
        if field == 'id': row['publisher_book_id'] = IDS[0]
        elif field == 'url': row['source_url'] += '?changed'
        elif field == 'unit': row['kind'] = 'story'
        elif field == 'role': row['credits'][0]['role'] = ''
        elif field == 'title': row['title'] = ''
        else: row['story_series_identity'] = 'confirmed'
        return rows
    monkeypatch.setattr(e.source, 'verify_shueisha_books', invalid)
    before = snapshot(e.cat)
    with pytest.raises(ValueError): adopt(e)
    assert snapshot(e.cat) == before


@pytest.mark.parametrize('field', ['isbn', 'credit', 'permission', 'provider', 'assertion'])
def test_rehashed_stored_fields_cannot_forge_isbn_roles_or_source(bundle, field):
    e = bundle
    identity = adopt(e, IDS[:1])['reference_entries'][IDS[0]]
    with e.cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        if field == 'isbn': record['isbn'] = ['9784086310017']
        elif field == 'credit': record['publisher_product_reference']['credits'][0]['role'] = 'author'
        elif field == 'permission': record['usage_permissions']['public_display'] = True
        elif field == 'provider': record['provider'] = 'gagaga'
        else: record['source_reference_assertions'][0]['reference_id'] = IDS[1]
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError): e.cat.work_detail(identity)


def test_complete_frame_later_raw_failure_prevents_partial_import(bundle):
    e = bundle
    before = snapshot(e.cat)
    (e.path.parent / 'page2.bin').write_bytes(b'changed final source page')
    with pytest.raises(ValueError): adopt(e)
    assert len(e.calls) == 1 and e.pages == ['page1.bin', 'page2.bin'] and snapshot(e.cat) == before


@pytest.mark.parametrize('formal_first', [False, True])
def test_standalone_and_old_book_confirmation_share_native_frame_without_changing_old_facts(bundle, monkeypatch, formal_first):
    e = bundle
    identity, digest, match = 'lne:ndl:synthetic', 'a' * 64, {'scope': 'edition_only', 'old_proof': 'unchanged'}
    proof = {'provider': 'shueisha-dx', 'edition_record_sha256': digest, 'manifest_path': str(e.path),
        'manifest_sha256': e.sha, 'publisher_book_id': IDS[0], 'match': match}
    encoded = json.dumps(proof)
    binding = {'provider': 'shueisha-dx', 'edition_id': identity, 'proof_json': encoded,
        'proof_sha256': hashlib.sha256(encoded.encode()).hexdigest()}
    monkeypatch.setattr(e.cat, '_book_confirmation_bindings', lambda cache: [binding])
    def old_match(provider, book, record, cache):
        assert provider == 'shueisha-dx' and book['isbn'] == '9784086310017'
        assert book['credits'][0] == {'role': '著者', 'name': '架空 花子'}
        assert book['kind'] == 'book' and book['publisher'] == '集英社'
        return match
    monkeypatch.setattr(e.cat, '_publisher_edition_match', old_match)
    cache = {}
    row = {'entry_id': identity, 'entry_kind': 'edition', 'admission_status': 'candidate'}
    stored = [{'entry_id': identity, 'record_sha256': digest, 'record_json': '{}'}]
    def formal(): e.cat._attach_book_confirmations([row], stored, cache)
    if formal_first: formal()
    safe = e.cat._shueisha_dx_publisher_references(e.path, e.sha, cache)
    if not formal_first: formal()
    assert safe[IDS[0]]['isbn'] is None and safe[IDS[0]]['credits'][0]['role'] == 'unknown'
    assert row['edition_confirmation_evidence'] == match
    assert len(e.calls) == 1 and e.pages == ['page1.bin', 'page2.bin']


def test_unrelated_rows_and_independent_namespace_race_are_preserved(bundle, monkeypatch):
    e = bundle
    encoded = '{"absent_unrelated_raw":true}'
    old = ('lnr:pub:overlapdetails:' + 'a' * 32, 'publisher_reference', encoded, hashlib.sha256(encoded.encode()).hexdigest())
    with e.cat._connect() as db: db.execute('INSERT INTO entries VALUES(?,?,?,?)', old)
    identity = adopt(e, IDS[:1])['reference_entries'][IDS[0]]
    with e.cat._connect() as db:
        racing = tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone())
        db.execute('DELETE FROM entries WHERE entry_id=?', (identity,))
    connect, calls, writes = e.cat._connect, [], []
    @contextmanager
    def interleaved():
        with connect() as db:
            calls.append(True)
            if len(calls) == 2:
                with sqlite3.connect(e.cat.db_path) as other: other.execute('INSERT INTO entries VALUES(?,?,?,?)', racing)
                db.set_trace_callback(lambda sql: writes.append(sql) if sql.startswith(('INSERT', 'UPDATE', 'DELETE')) else None)
            yield db
    monkeypatch.setattr(e.cat, '_connect', interleaved)
    with pytest.raises(ValueError, match='target set changed'): adopt(e)
    assert writes == []
    with connect() as db: assert sorted(tuple(r) for r in db.execute('SELECT * FROM entries')) == sorted([old, racing])
