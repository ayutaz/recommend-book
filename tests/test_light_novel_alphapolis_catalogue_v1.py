"""Formal Alpha/Regina complete titles and literal roles stay sparse listings."""
import hashlib
import json

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_alphapolis_references_v1 import fixture, change, DUP


IDS = ['alphapolis-commercial-book:1/1', 'alphapolis-commercial-book:2/1', DUP]


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    import recommend_book.light_novel_alphapolis_references_v1 as source
    env = fixture(tmp_path)
    # Replace one of the three already-unknown creator roles: no author is invented.
    change(env, '<span>作</span>', '<span>編・訳</span>')
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    verify, raw = source.verify_alphapolis_references, source._source
    calls, pages = [], []
    def checked(path, sha): calls.append(str(path)); return verify(path, sha)
    def read(root, receipt, url): pages.append(url); return raw(root, receipt, url)
    monkeypatch.setattr(source, 'verify_alphapolis_references', checked)
    monkeypatch.setattr(source, '_source', read)
    return cat, env, calls, pages


def adopt(e, ids=IDS):
    cat, env, *_ = e
    return cat.import_alphapolis_publisher_references(env['path'], env['sha'], reference_ids=ids)


def snapshot(cat):
    with cat._connect() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY 1')] for t in tables}


def test_complete_formal_title_core_public_duplicate_and_sparse_roles_are_preserved(bundle):
    cat, _, calls, pages = bundle
    result = adopt(bundle)
    assert result['source_references'] == 2638 and result['requested_references'] == result['inserted'] == 3
    assert len(calls) == 1 and len(pages) == len(set(pages)) == 67
    identity = result['reference_entries'][IDS[0]]
    assert identity.startswith('lnr:pub:alphapolis:') and len(identity.rsplit(':', 1)[1]) == 32
    calls.clear(); pages.clear()
    row = cat.work_detail(identity)
    assert len(calls) == 1 and len(set(pages)) == 67
    assert row['title'] == '完全題alphapolis1 巻３【特装版】\n独立の副題を保持'
    assert row['title_literal'] == '完全題alphapolis1 巻３【特装版】'
    assert row['subtitle_literal'] == '独立の副題を保持' and row['complete_display_title'] == row['title']
    assert row['isbn'] == row['authors'] == row['publishers'] == row['members'] == []
    assert row['publisher_product_reference']['credits'] == [{'role': 'unknown', 'name': '架空名'}]
    assert row['publisher_product_reference']['credit_literals'] == [{'role_literal': '編・訳', 'name': '架空名'}]
    assert row['publisher_product_reference']['form'] is None and row['publication_label'] == 'アルファポリス'
    assert row['source_scope_membership']['brand'] == 'alphapolis'
    assert row['source_scope_membership']['individual_lightnovel_classification'] == 'unknown'
    assert row['source_scope_membership']['full_historical_catalogue_complete'] is False
    assert all(row[k] is False for k in ('light_novel_confirmed', 'light_novel_identity_asserted',
        'edition_identity_asserted', 'story_series_identity_asserted', 'original_work_identity_asserted', 'recommendable'))
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == 'alphapolis_commercial_book_reference'
    assert shown['title'] == row['title'] and shown['isbn'] == []
    assert shown['publisher_product_reference']['title_literal'] == row['title_literal']
    assert shown['publisher_product_reference']['subtitle_literal'] == row['subtitle_literal']
    assert 'source_scope_membership' not in shown and 'PRIVATE_' not in json.dumps(shown)
    assert cat.search_catalogue('独立の副題を保持', entry_kind='publisher_reference')['total'] == 3
    assert cat.search_catalogue('架空名', entry_kind='publisher_reference')['total'] == 3
    regina = cat.work_detail(result['reference_entries'][IDS[1]])
    assert regina['publication_label'] == 'レジーナブックス'
    assert regina['publisher_product_reference']['credits'] == [{'role': 'author', 'name': '架空名'}]
    duplicate = cat.work_detail(result['reference_entries'][DUP])
    assert duplicate['occurrence_count'] == 2
    assert cat.set_read_status(identity, 'reading')['read_status'] == 'reading'
    assert cat.similar_works(identity)['mode'] == 'insufficient_evidence'


def test_explicit_selection_reimport_and_native_correspondence_cache_keep_facts_and_uuid(bundle):
    cat, env, calls, pages = bundle
    with pytest.raises(TypeError): cat.import_alphapolis_publisher_references(env['path'], env['sha'])
    for ids in (None, [], IDS[0], IDS[:1] * 2, [None], ['alphapolis-commercial-book:1']):
        with pytest.raises(ValueError): adopt(bundle, ids)
    assert calls == []
    first = adopt(bundle); before = snapshot(cat); calls.clear(); pages.clear()
    again = adopt(bundle)
    assert again['unchanged'] == 3 and again['inserted'] == again['updated'] == 0
    assert again['reference_entries'] == first['reference_entries'] and snapshot(cat) == before
    assert len(calls) == 1 and len(pages) == 67
    cache = {}; calls.clear(); pages.clear()
    native = cat._alphapolis_native_references(env['path'], env['sha'], cache)
    references = cat._alphapolis_publisher_references(env['path'], env['sha'], cache)
    assert native[0]['title'] == references[IDS[0]]['title']
    assert native[0]['credits'] == references[IDS[0]]['credits']
    assert 'credit_literals' not in native[0] and 'reference_scope' not in native[0]
    assert references['alphapolis-commercial-book:1/2']['credits'][0]['role'] == 'unknown'
    assert references['alphapolis-commercial-book:1/2']['credit_literals'][0]['role_literal'] == '作'
    assert len(calls) == 1 and len(pages) == 67


@pytest.mark.parametrize('field', ['title', 'subtitle', 'role', 'isbn', 'membership', 'count'])
def test_formal_native_shape_not_checkpoint_shape_and_unknown_fields_are_required(bundle, monkeypatch, field):
    import recommend_book.light_novel_publisher_references_v1 as source
    cat, *_ = bundle
    verify = source.verify_publisher_references
    def invalid(*args):
        rows = verify(*args); row = rows[0]
        if field == 'title': row['title'] = row['title_literal']  # Checkpoint main is not the formal complete title.
        elif field == 'subtitle': row['subtitle_literal'] = 'omitted from complete display'
        elif field == 'role': row['credits'][0]['role'] = 'author'
        elif field == 'isbn': row['isbn'] = '9784086310017'
        elif field == 'membership': row['source_scope_membership']['brand'] = 'regina'
        else: row['occurrence_count'] = 2
        return rows
    monkeypatch.setattr(source, 'verify_publisher_references', invalid)
    before = snapshot(cat)
    with pytest.raises(ValueError): adopt(bundle)
    assert snapshot(cat) == before


def test_rehashed_title_component_or_membership_cannot_be_projected(bundle):
    cat, *_ = bundle
    identity = adopt(bundle, IDS[:1])['reference_entries'][IDS[0]]
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        record['subtitle_literal'] = ''
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError): cat.work_detail(identity)


def test_retitled_snapshot_hold_and_conflicting_duplicate_stop_without_partial_write(bundle):
    cat, env, *_ = bundle
    adopt(bundle, IDS[:1]); before = snapshot(cat)
    root = env['root'] / 'next'; root.mkdir()
    revised = fixture(root)
    change(revised, '完全題alphapolis1 巻３【特装版】', '改題alphapolis1 巻３【特装版】')
    result = cat.import_alphapolis_publisher_references(revised['path'], revised['sha'], reference_ids=IDS[:1])
    assert result['held_reasons'] == {'source_reference_scope_or_title_changed': 1} and snapshot(cat) == before
    change(revised, '同じ完全題　巻３【特装版】', '矛盾する重複題', page=2)
    with pytest.raises(ValueError, match='conflicting metadata'):
        cat.import_alphapolis_publisher_references(revised['path'], revised['sha'], reference_ids=[DUP])
    assert snapshot(cat) == before
