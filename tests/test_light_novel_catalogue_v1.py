import hashlib
import json

import pytest

from test_light_novel_futaba_workgroups_v1 import source as futaba_source_raw
from test_light_novel_orange_groups_v1 import source as _orange_raw_fixture
from test_light_novel_ecobalt_groups_v1 import source as _ecobalt_raw_fixture

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue, normalize_search


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def claim(qid):
    return {'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id': qid}}}}


@pytest.fixture
def futaba_source_group_bundle(futaba_source_raw):
    from recommend_book.light_novel_futaba_workgroups_v1 import verify_futaba_mnovels_workgroups
    env = futaba_source_raw
    path = env['root'] / 'proof.json'
    groups = verify_futaba_mnovels_workgroups(path, env['manifest_sha'])
    return LightNovelCatalogue(env['root'] / 'source-groups.sqlite'), env, path, env['manifest_sha'], groups


@pytest.fixture
def orange_source_group_bundle(tmp_path):
    from recommend_book.light_novel_orange_groups_v1 import verify_orange_groups
    root = tmp_path / 'orange-raw'
    root.mkdir()
    env = _orange_raw_fixture.__wrapped__(root)
    path = root / 'proof.json'
    groups = verify_orange_groups(path, env['sha'])
    return LightNovelCatalogue(tmp_path / 'orange.sqlite'), env, path, env['sha'], groups


@pytest.fixture
def ecobalt_source_group_bundle(tmp_path):
    from recommend_book.light_novel_ecobalt_groups_v1 import verify_ecobalt_groups
    root = tmp_path / 'ecobalt-raw'
    root.mkdir()
    env = _ecobalt_raw_fixture.__wrapped__(root)
    path = root / 'proof.json'
    groups = verify_ecobalt_groups(path, env['sha'])
    return LightNovelCatalogue(tmp_path / 'ecobalt.sqlite'), env, path, env['sha'], groups


def _ecobalt_next_snapshot(env, replace_child=False):
    from copy import deepcopy
    from test_light_novel_ecobalt_groups_v1 import save, rebind_raw
    following = deepcopy(env)
    following['root'] = env['root'] / 'next'
    following['root'].mkdir()
    for item in env['root'].iterdir():
        if item.is_file():
            (following['root'] / item.name).write_bytes(item.read_bytes())
    following['proof']['capture_revision'] = 2
    if replace_child:
        page = following['root'] / 'page.html'
        raw = page.read_bytes().replace(b'book=sc1', b'book=replaced1')
        page.write_bytes(raw)
        for book in following['books'] + following['handoff'][0]['explicit_memberships']:
            if book['publisher_book_id'] == 'sc1':
                book['publisher_book_id'] = 'replaced1'
                book['source_url'] = book['source_url'].replace('book=sc1', 'book=replaced1')
        rebind_raw(following, raw)
    save(following)
    return following['root'] / 'proof.json', following['sha']


def test_ecobalt_source_group_preserves_listing_display_roles_and_complete_titles(ecobalt_source_group_bundle):
    cat, _, path, sha, groups = ecobalt_source_group_bundle
    result = cat.import_ecobalt_source_groups(path, sha)
    assert result['inserted'] == 3 and result['held'] == 0
    assert len(set(result['reference_entries'].values())) == 3
    identity = result['reference_entries'][groups[0]['reference_id']]
    row = cat.work_detail(identity)
    assert identity.startswith('lng:pub:ecobalt:')
    assert row['publisher_work_group_kind'] == 'ecobalt_author_index_source_group'
    assert row['entry_kind'] == 'publisher_group' and row['heading_author_display'] == '甲'
    assert row['role_context'] == {'literal': '著者50音別書籍リスト', 'scope': 'author_index_display_only'}
    assert row['authors'] == row['aliases'] == row['members'] == row['genres'] == row['native_features'] == []
    assert row['qid'] is row['publication_label'] is None and row['isbn'] == []
    assert not row['light_novel_confirmed'] and not row['story_series_identity_asserted']
    child = row['publisher_product_references'][0]
    assert child['title'] == '完全題　１【電子限定】' and child['author_display'] == '甲'
    assert child['credits'] == [{'role': 'unknown', 'name': '甲'}]
    assert child['credit_literals'] == [{'name': '甲', 'entry_role_literal': None,
        'role_context_literal': '著者50音別書籍リスト', 'role_scope': 'author_index_display_only'}]
    assert child['label'] is child['form'] is None and child['media_references'] == []
    assert cat.search_catalogue('完全題 1【電子限定】')['items'][0]['entry_id'] == identity
    assert cat.search_catalogue('甲/乙')['total'] == 1
    assert cat.search_catalogue('未所属の完全題')['total'] == 0
    assert cat.search_catalogue('同題アンソロジー')['total'] == 2
    assert cat.search_catalogue(entry_kind='series')['total'] == 3
    assert cat.catalogue_stats()['publisher_work_groups'] == 3 and cat.catalogue_stats()['series'] == 0
    assert not any(marker in json.dumps(row, ensure_ascii=False) for marker in
        ('PRIVATE_', 'raw_path', 'source_selector', '著者：', str(path.parent)))


@pytest.mark.parametrize('replace_child', [False, True])
def test_ecobalt_source_group_snapshot_requires_shared_child_for_uuid_continuity(ecobalt_source_group_bundle, replace_child):
    cat, env, path, sha, groups = ecobalt_source_group_bundle
    first = cat.import_ecobalt_source_groups(path, sha)
    identity = first['reference_entries'][groups[0]['reference_id']]
    cat.set_read_status(identity, 'reading')
    assert cat.import_ecobalt_source_groups(path, sha)['unchanged'] == 3
    following, digest = _ecobalt_next_snapshot(env, replace_child)
    result = cat.import_ecobalt_source_groups(following, digest)
    assert result['inserted'] == 0 and result['held'] == int(replace_child)
    assert result['updated'] == 3 - int(replace_child)
    assert cat.work_detail(identity)['read_status'] == 'reading'
    with cat._connect() as db:
        assert {r[0] for r in db.execute('SELECT entry_id FROM entries')} == set(first['reference_entries'].values())
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
    assert len(record['source_group_assertions']) == (1 if replace_child else 2)
    assert record['source_group_assertions'][0]['manifest_sha256'] == sha


def test_ecobalt_source_group_scope_preserves_both_previous_providers(
        ecobalt_source_group_bundle, orange_source_group_bundle, futaba_source_group_bundle, monkeypatch):
    _, _, path, sha, _ = ecobalt_source_group_bundle
    _, _, opath, osha, _ = orange_source_group_bundle
    cat, _, fpath, fsha, _ = futaba_source_group_bundle
    fids = cat.import_futaba_source_groups(fpath, fsha)['reference_entries']
    cat.import_orange_source_groups(opath, osha)
    cat.set_read_status(next(iter(fids.values())), 'read')
    tables = ['entries', 'read_status', 'edition_series_memberships', 'features']
    with cat._connect() as db:
        before = {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY 1')] for t in tables}
    def forbidden(*args, **kwargs):
        raise AssertionError('ecobalt import must not replay other providers')
    monkeypatch.setattr(cat, '_futaba_workgroups', forbidden)
    monkeypatch.setattr(cat, '_orange_workgroups', forbidden)
    assert cat.import_ecobalt_source_groups(path, sha)['inserted'] == 3
    assert cat.import_ecobalt_source_groups(path, sha)['unchanged'] == 3
    with cat._connect() as db:
        for table in tables:
            rows = [tuple(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
            assert [r for r in rows if table != 'entries' or not r[0].startswith('lng:pub:ecobalt:')] == before[table]


@pytest.mark.parametrize('change', ['raw', 'rehash', 'typed'])
def test_ecobalt_source_group_authenticates_raw_and_display_metadata(ecobalt_source_group_bundle, change):
    cat, env, path, sha, _ = ecobalt_source_group_bundle
    if change == 'raw':
        (env['root'] / 'page.html').write_bytes(b'changed source')
        with pytest.raises(ValueError):
            cat.import_ecobalt_source_groups(path, sha)
        with cat._connect() as db:
            assert db.execute('SELECT count(*) FROM entries').fetchone()[0] == 0
        return
    identity = next(iter(cat.import_ecobalt_source_groups(path, sha)['reference_entries'].values()))
    with cat._connect() as db:
        if change == 'typed':
            db.execute('UPDATE entries SET kind=? WHERE entry_id=?', ('series', identity))
        else:
            record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
            record['heading_author_display'] = '偽の表示'
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
                (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError):
        cat.work_detail(identity)


def test_ecobalt_source_listing_does_not_exclude_or_rewrite_independent_cobalt_story(
        ecobalt_source_group_bundle, cobalt_member, monkeypatch):
    _, _, path, sha, _ = ecobalt_source_group_bundle
    cat, edition, frames, fsha, extra, esha, reference, _ = cobalt_member
    assert cat.import_ndl_series_from_publishers(frames, fsha, source_root=frames.parent,
        cobalt_series_path=extra, cobalt_series_sha256=esha, reference_ids={reference})['inserted'] == 1
    story = 'lns:ndl:' + edition['id']
    cat.set_read_status(story, 'reading')
    with cat._connect() as db:
        before = {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
            for table in ['entries', 'features', 'read_status', 'edition_series_memberships']}
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError('new eCobalt source scope cannot re-audit old story/NDL proof')
        patch.setattr(cat, '_project_ndl_series', forbidden)
        assert cat.import_ecobalt_source_groups(path, sha)['inserted'] == 3
    with cat._connect() as db:
        for table, old in before.items():
            rows = [tuple(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
            assert [r for r in rows if table != 'entries' or not r[0].startswith('lng:pub:ecobalt:')] == old
    assert cat.work_detail(story)['read_status'] == 'reading'
    assert cat.search_catalogue('完全題 1【電子限定】')['total'] == 1
    assert cat.catalogue_stats()['series'] == 1 and cat.catalogue_stats()['publisher_work_groups'] == 3


def _orange_next_snapshot(env, change=None):
    from copy import deepcopy
    from test_light_novel_orange_groups_v1 import save
    following = deepcopy(env)
    following['root'] = env['root'] / 'next'
    following['root'].mkdir()
    for item in env['root'].iterdir():
        if item.is_file():
            (following['root'] / item.name).write_bytes(item.read_bytes())
    following['proof']['capture_revision'] = 2
    if change:
        path = following['root'] / 'parent0.html'
        text = path.read_text(encoding='utf-8')
        if change in {'title', 'title_and_children'}:
            text = text.replace('親の正式な題', '変更された親の題')
            following['handoff'][0]['title'] = '変更された親の題'
        if change in {'children', 'title_and_children'}:
            for index, member in enumerate(following['handoff'][0]['explicit_memberships']):
                old = member['source_url']
                member['source_url'] = old + '-new'
                text = text.replace(old, member['source_url'])
        path.write_text(text, encoding='utf-8')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        following['receipts'][2].update(sha256=digest, byte_length=path.stat().st_size)
        following['handoff'][0]['source_evidence']['source_sha256'] = digest
        for member in following['handoff'][0]['explicit_memberships']:
            member['evidence']['source_sha256'] = digest
    save(following)
    return following['root'] / 'proof.json', following['sha']


def test_orange_source_group_formal_raw_preserves_complete_child_roles_and_unknown_identity(orange_source_group_bundle):
    cat, _, path, sha, groups = orange_source_group_bundle
    result = cat.import_orange_source_groups(path, sha)
    assert result['inserted'] == 2 and result['held'] == 0
    identity = result['reference_entries'][groups[0]['reference_id']]
    assert identity.startswith('lng:pub:orange:')
    row = cat.work_detail(identity)
    assert row['entry_kind'] == 'publisher_group' and row['publisher_work_group_kind'] == 'orange_native_taxonomy_source_group'
    assert row['aliases'] == row['authors'] == row['members'] == []
    assert row['qid'] is row['language'] is row['original_language'] is None
    assert row['isbn'] == [] and not row['light_novel_confirmed'] and not row['story_series_identity_asserted']
    first, second = row['publisher_product_references']
    assert first['title'] == '完全な題　２【外伝】'
    assert first['credits'] == [{'role': 'author', 'name': '架空 著者'}, {'role': 'cover_illustrator', 'name': '架空 絵師'}]
    assert first['credit_literals'] == [{'role_literal': '著者', 'name': '架空 著者'}, {'role_literal': '装画', 'name': '架空 絵師'}]
    assert second['credits'][1]['role'] == 'screenplay_author'
    assert all(child['label'] is child['form'] is None and child['media_references'] == []
               for child in row['publisher_product_references'])
    assert cat.search_catalogue('完全な題 2【外伝】')['items'][0]['entry_id'] == identity
    assert cat.search_catalogue('架空 著者')['total'] == 1
    assert cat.search_catalogue('架空 絵師')['total'] == cat.search_catalogue('脚本者')['total'] == 0
    assert cat.search_catalogue(entry_kind='series')['total'] == 2
    assert cat.catalogue_stats()['publisher_work_groups'] == 2 and cat.catalogue_stats()['series'] == 0
    assert not any(marker in json.dumps(row) for marker in ('PRIVATE_', 'raw_path', 'source_selector', str(path.parent)))


def test_orange_source_group_scoped_import_never_reaudits_or_rewrites_futaba(
        orange_source_group_bundle, futaba_source_group_bundle, monkeypatch):
    _, _, path, sha, _ = orange_source_group_bundle
    cat, _, fpath, fsha, _ = futaba_source_group_bundle
    first = cat.import_futaba_source_groups(fpath, fsha)
    cat.set_read_status(next(iter(first['reference_entries'].values())), 'reading')
    with cat._connect() as db:
        before = {r['entry_id']: tuple(r) for r in db.execute('SELECT * FROM entries')}
        statuses = [tuple(r) for r in db.execute('SELECT * FROM read_status')]
    def forbidden(*args, **kwargs):
        raise AssertionError('provider-scoped import cannot replay the other provider')
    with monkeypatch.context() as patch:
        patch.setattr(cat, '_futaba_workgroups', forbidden)
        assert cat.import_orange_source_groups(path, sha)['inserted'] == 2
        assert cat.import_orange_source_groups(path, sha)['unchanged'] == 2
    with monkeypatch.context() as patch:
        patch.setattr(cat, '_orange_workgroups', forbidden)
        assert cat.import_futaba_source_groups(fpath, fsha)['unchanged'] == 4
    with cat._connect() as db:
        after = {r['entry_id']: tuple(r) for r in db.execute('SELECT * FROM entries')}
        assert [tuple(r) for r in db.execute('SELECT * FROM read_status')] == statuses
    assert {identity: after[identity] for identity in before} == before


def test_orange_source_group_snapshot_continuation_keeps_uuid_and_versioned_pin(orange_source_group_bundle):
    cat, env, path, sha, _ = orange_source_group_bundle
    first = cat.import_orange_source_groups(path, sha)
    identity = next(iter(first['reference_entries'].values()))
    cat.set_read_status(identity, 'read')
    assert cat.import_orange_source_groups(path, sha)['unchanged'] == 2
    following, digest = _orange_next_snapshot(env)
    result = cat.import_orange_source_groups(following, digest)
    assert result['updated'] == 2 and result['inserted'] == 0
    assert result['reference_entries'] == first['reference_entries']
    assert cat.work_detail(identity)['read_status'] == 'read'
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
    assert record['source_group_assertions'][0]['manifest_sha256'] == sha
    assert record['source_group_assertions'][1]['manifest_sha256'] == digest
    assert record['source_group_assertions'][0]['local_reference_key']['literal'] == '親の正式な題'


@pytest.mark.parametrize('change', ['title', 'children'])
def test_orange_source_group_does_not_inherit_a_renamed_or_totally_replaced_group(orange_source_group_bundle, change):
    cat, env, path, sha, _ = orange_source_group_bundle
    first = cat.import_orange_source_groups(path, sha)
    following, digest = _orange_next_snapshot(env, change)
    result = cat.import_orange_source_groups(following, digest)
    assert result['held'] == 1 and result['inserted'] == 0 and result['updated'] == 1
    with cat._connect() as db:
        assert {r[0] for r in db.execute('SELECT entry_id FROM entries')} == set(first['reference_entries'].values())


def test_orange_source_group_stable_url_cannot_mint_new_uuid_after_rename_and_all_child_replacement(orange_source_group_bundle):
    cat, env, path, sha, groups = orange_source_group_bundle
    first = cat.import_orange_source_groups(path, sha)
    identity = first['reference_entries'][groups[0]['reference_id']]
    cat.set_read_status(identity, 'reading')
    with cat._connect() as db:
        previous = tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone())
        status = tuple(db.execute('SELECT * FROM read_status WHERE entry_id=?', (identity,)).fetchone())
    following, digest = _orange_next_snapshot(env, 'title_and_children')
    result = cat.import_orange_source_groups(following, digest)
    assert result['held'] == 1 and result['inserted'] == 0
    assert result['held_reasons'] == {'source_group_scope_or_title_changed': 1}
    with cat._connect() as db:
        assert {r[0] for r in db.execute('SELECT entry_id FROM entries')} == set(first['reference_entries'].values())
        assert tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone()) == previous
        assert tuple(db.execute('SELECT * FROM read_status WHERE entry_id=?', (identity,)).fetchone()) == status


@pytest.mark.parametrize('change', ['raw', 'rehash', 'typed'])
def test_orange_source_group_authenticates_source_and_selected_metadata(orange_source_group_bundle, change):
    cat, env, path, sha, _ = orange_source_group_bundle
    if change == 'raw':
        (env['root'] / 'parent1.html').write_bytes(b'changed late source')
        with pytest.raises(ValueError):
            cat.import_orange_source_groups(path, sha)
        with cat._connect() as db:
            assert db.execute('SELECT count(*) FROM entries').fetchone()[0] == 0
        return
    identity = next(iter(cat.import_orange_source_groups(path, sha)['reference_entries'].values()))
    with cat._connect() as db:
        if change == 'typed':
            db.execute('UPDATE entries SET kind=? WHERE entry_id=?', ('series', identity))
        else:
            record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
            record['publisher_product_references'][0]['credit_literals'][0]['role_literal'] = '原作'
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
                (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError):
        cat.work_detail(identity)


def _futaba_next_source_snapshot(env):
    target = env['root'] / 'next-source'
    target.mkdir()
    for item in env['root'].iterdir():
        if item.is_file() and item.suffix not in {'.sqlite', '.sqlite-journal'}:
            (target / item.name).write_bytes(item.read_bytes())
    manifest = json.loads((target / 'm-proof.json').read_bytes())
    manifest['capture_revision'] = 2
    digest = write(target / 'm-proof.json', manifest)
    proof = json.loads((target / 'proof.json').read_bytes())
    proof['mnovels_references']['sha256'] = digest
    return target / 'proof.json', write(target / 'proof.json', proof)


def test_futaba_source_group_formal_raw_without_ndl_supports_title_child_author_and_safe_detail(futaba_source_group_bundle):
    cat, _, path, sha, groups = futaba_source_group_bundle
    result = cat.import_futaba_source_groups(path, sha)
    assert result['inserted'] == 4 and result['held'] == 0
    identity = result['reference_entries'][groups[0]['reference_id']]
    import re
    assert re.fullmatch(r'lng:pub:futaba:[0-9a-f]{32}', identity)
    row = cat.work_detail(identity)
    assert row['entry_kind'] == 'publisher_group' and row['qid'] is None
    assert row['series_identity_basis'] == 'publisher_source_group'
    assert row['series_title_source'] == 'publisher_explicit_work_group'
    assert row['admission_status'] == row['relation_resolution'] == 'publisher_source_group_verified'
    assert row['aliases'] == row['authors'] == row['members'] == []
    assert not row['light_novel_confirmed'] and not row['story_series_identity_asserted']
    assert cat.search_catalogue('完全題 0 巻３【特装版】')['items'][0]['entry_id'] == identity
    assert cat.search_catalogue('著者0')['items'][0]['entry_id'] == identity
    assert cat.search_catalogue('絵師')['total'] == cat.search_catalogue('著者3')['total'] == 0
    assert cat.search_catalogue(entry_kind='series')['total'] == 4
    assert cat.search_catalogue(entry_kind='publisher_group')['total'] == 4
    child = row['publisher_product_references'][0]
    assert set(child) == {'publisher_reference_id', 'title', 'source_url', 'label', 'form', 'credits', 'media_references'}
    assert len(child['media_references']) == 2
    assert 'PRIVATE_PROSE' not in json.dumps(row) and 'raw_path' not in json.dumps(row)
    assert str(path.parent) not in json.dumps(row)
    stats = cat.catalogue_stats()
    assert stats['publisher_work_groups'] == 4 and stats['series'] == stats['editions'] == 0


def test_futaba_source_group_same_snapshot_and_continuing_snapshot_keep_local_identity(futaba_source_group_bundle):
    cat, env, path, sha, groups = futaba_source_group_bundle
    first = cat.import_futaba_source_groups(path, sha)
    identity = first['reference_entries'][groups[0]['reference_id']]
    cat.set_read_status(identity, 'read')
    assert cat.import_futaba_source_groups(path, sha)['unchanged'] == 4
    next_path, next_sha = _futaba_next_source_snapshot(env)
    following = cat.import_futaba_source_groups(next_path, next_sha)
    assert following['updated'] == 4 and following['inserted'] == 0
    assert set(following['reference_entries'].values()) == set(first['reference_entries'].values())
    assert cat.work_detail(identity)['read_status'] == 'read'
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
    assert len(record['source_group_assertions']) == 2
    assert record['source_group_assertions'][0]['manifest_sha256'] == sha
    assert record['source_group_assertions'][1]['manifest_sha256'] == next_sha


@pytest.mark.parametrize('change', ['scope', 'literal', 'members', 'ambiguous'])
def test_futaba_source_group_continuity_does_not_claim_renames_splits_or_new_members(futaba_source_group_bundle, change):
    from copy import deepcopy
    cat, _, path, sha, groups = futaba_source_group_bundle
    old = cat._futaba_source_assertion(str(path.resolve()), sha, groups[0])
    new = deepcopy(old)
    new['manifest_sha256'] = 'f' * 64
    if change == 'scope':
        new['local_reference_key']['label_id_scope'] = 21
    elif change == 'literal':
        new['local_reference_key']['literal'] += ' 外伝'
    elif change == 'members':
        new['member_reference_ids'] = ['futaba-product:new-unrelated']
    else:
        new['local_reference_key']['source_frame_id'] = 'mnovels-f'
    assert not cat._futaba_source_continuity(old, new)


def test_futaba_source_group_raw_and_stored_typed_projection_fail_before_any_save(futaba_source_group_bundle):
    cat, env, path, sha, _ = futaba_source_group_bundle
    raw = env['root'] / 'm-page1.json'
    original = raw.read_bytes()
    raw.write_bytes(b'{}')
    with pytest.raises(ValueError):
        cat.import_futaba_source_groups(path, sha)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM entries').fetchone()[0] == 0
    raw.write_bytes(original)
    identity = next(iter(cat.import_futaba_source_groups(path, sha)['reference_entries'].values()))
    with cat._connect() as db:
        db.execute('UPDATE entries SET kind=? WHERE entry_id=?', ('series', identity))
    with pytest.raises(ValueError, match='typed'):
        cat.work_detail(identity)


def test_futaba_source_group_metadata_rehash_cannot_forge_child_or_parent_title(futaba_source_group_bundle):
    cat, _, path, sha, _ = futaba_source_group_bundle
    identity = next(iter(cat.import_futaba_source_groups(path, sha)['reference_entries'].values()))
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        record['publisher_product_references'][0]['title'] = '偽造児童題'
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
                   (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError):
        cat.search_catalogue('偽造児童題')


def test_futaba_source_group_unknowns_remain_unknown_and_typed_ids_are_strict(futaba_source_group_bundle):
    cat, _, path, sha, groups = futaba_source_group_bundle
    result = cat.import_futaba_source_groups(path, sha)
    singleton = next(g for g in groups if any(m['publisher_reference_id'] == 'futaba-product:opaque-3' for m in g['members']))
    row = cat.work_detail(result['reference_entries'][singleton['reference_id']])
    assert row['publisher_product_references'][0]['credits'][0]['role'] is None
    assert row['original_language'] is row['language'] is row['publication_label'] is None
    for identity in ['lng:pub:other:' + 'a' * 32, 'lng:pub:futaba:' + 'A' * 32, 'lng:pub:futaba:Q1']:
        with pytest.raises(ValueError):
            cat.work_detail(identity)
    with pytest.raises(ValueError):
        cat.import_futaba_source_groups(path, sha, reference_ids=['missing'])


def test_futaba_source_group_never_recommends_inherits_features_or_replaces_anchored_group(futaba_source_group_bundle):
    cat, _, path, sha, _ = futaba_source_group_bundle
    first = cat.import_futaba_source_groups(path, sha)
    identity = next(iter(first['reference_entries'].values()))
    assert cat.recommend_preferences(liked_entry_ids=[identity])['mode'] == 'insufficient_evidence'
    assert cat.similar_works(identity)['items'] == []
    assert cat.work_detail(identity)['classification_scope'] == 'publisher_group'
    assert cat.work_detail(identity)['llm_features'] == []
    assert cat.import_futaba_source_groups(path, sha)['inserted'] == 0
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM edition_series_memberships').fetchone()[0] == 0


def test_futaba_source_group_ambiguous_existing_local_keys_are_held_without_partial_updates(futaba_source_group_bundle):
    from copy import deepcopy
    import uuid
    cat, _, path, sha, _ = futaba_source_group_bundle
    first = cat.import_futaba_source_groups(path, sha)
    identity = next(iter(first['reference_entries'].values()))
    with cat._connect() as db:
        record = deepcopy(json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0]))
        record['id'] = uuid.uuid4().hex
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('INSERT INTO entries VALUES(?,?,?,?)', ('lng:pub:futaba:' + record['id'], 'publisher_group',
            encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    result = cat.import_futaba_source_groups(path, sha)
    assert result['held'] == 1 and result['inserted'] == result['updated'] == 0
    assert result['held_reasons'] == {'ambiguous_existing_source_group': 1}


def test_futaba_source_group_preserves_actual_anchor_bindings_and_reading_states(futaba_workgroup_integration):
    cat, first, refs, m_path, m_sha, _, _, path, sha, _, _ = futaba_workgroup_integration
    cat.correlate_publisher_bibliographies('futaba-mnovels', m_path, m_sha)
    assert cat.import_futaba_mnovels_workgroups(path, sha)['inserted'] == 1
    anchor = 'lns:ndl:' + first['id']
    cat.set_read_status(anchor, 'reading')
    cat.set_read_status('lne:ndl:' + first['id'], 'read')
    tables = ('entries', 'read_status', 'edition_series_memberships', 'publisher_bibliographic_correspondences')
    with cat._connect() as db:
        before = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                  for table in tables}
    result = cat.import_futaba_source_groups(path, sha)
    assert result['protected_existing'] == 1 and result['held'] == result['inserted'] == result['updated'] == 0
    assert list(result['reference_entries'].values()) == [anchor]
    shown = cat.work_detail(anchor)['publisher_product_references']
    assert len(shown) == 1 and shown[0]['title'] == refs[0]['title']
    assert shown[0]['publisher_reference_id'].startswith('futaba-product:')
    assert 'raw_path' not in json.dumps(shown)
    with cat._connect() as db:
        assert {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                for table in tables} == before


@pytest.mark.parametrize('race_kind', ['source', 'anchor', 'orange', 'ecobalt'])
def test_futaba_source_group_new_target_race_is_detected_before_first_write(request, monkeypatch, race_kind):
    from contextlib import contextmanager
    import sqlite3
    if race_kind in {'orange', 'ecobalt'}:
        cat, _, path, sha, _ = request.getfixturevalue(race_kind + '_source_group_bundle')
        saved = getattr(cat, 'import_' + race_kind + '_source_groups')(path, sha)
        identity = next(iter(saved['reference_entries'].values()))
    elif race_kind == 'source':
        cat, _, path, sha, _ = request.getfixturevalue('futaba_source_group_bundle')
        saved = cat.import_futaba_source_groups(path, sha)
        identity = next(iter(saved['reference_entries'].values()))
    else:
        cat, first, _, m_path, m_sha, _, _, path, sha, _, _ = request.getfixturevalue('futaba_workgroup_integration')
        cat.correlate_publisher_bibliographies('futaba-mnovels', m_path, m_sha)
        cat.import_futaba_mnovels_workgroups(path, sha)
        identity = 'lns:ndl:' + first['id']
    importer = getattr(cat, 'import_' + race_kind + '_source_groups') if race_kind in {'orange', 'ecobalt'} else cat.import_futaba_source_groups
    with cat._connect() as db:
        racing = tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone())
        db.execute('DELETE FROM edition_series_memberships WHERE series_id=?', (identity,))
        db.execute('DELETE FROM entries WHERE entry_id=?', (identity,))
        initial = {r['entry_id']: tuple(r) for r in db.execute('SELECT * FROM entries')}
    original = cat._connect
    calls, writes = [], []
    @contextmanager
    def interleaved():
        with original() as db:
            calls.append(True)
            if len(calls) == 2:
                # A genuine independent connection commits after the initial
                # SELECT and before the importer's BEGIN IMMEDIATE.
                with sqlite3.connect(cat.db_path) as other:
                    other.execute('INSERT INTO entries VALUES(?,?,?,?)', racing)
                db.set_trace_callback(lambda sql: writes.append(sql) if sql.startswith(('INSERT', 'UPDATE', 'DELETE')) else None)
            yield db
    with monkeypatch.context() as patch:
        patch.setattr(cat, '_connect', interleaved)
        with pytest.raises(ValueError, match='target set changed'):
            importer(path, sha)
    assert writes == []
    with cat._connect() as db:
        after = {r['entry_id']: tuple(r) for r in db.execute('SELECT * FROM entries')}
    assert after == {**initial, identity: racing}


@pytest.fixture
def setup_catalogue(tmp_path):
    snapshot = tmp_path / 'license.html'
    snapshot.write_text('Wikidata structured data CC0', encoding='utf-8')
    conditions = tmp_path / 'conditions.json'
    chash = write(conditions, {'version': 'light-novel-source-conditions-v1',
        'snapshots': [{'url': 'https://www.wikidata.org/wiki/Wikidata:Licensing',
            'raw_path': str(snapshot), 'raw_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest()}],
        'wikidata': {'scope': 'structured metadata only', 'license': 'CC0', 'prose_excluded': True,
            'usages': {'local_storage': True, 'metadata_display': True, 'metadata_recommendation': True, 'llm_input': False}}})
    records = []
    for n, title, kind, parent in [(1, '魔法科高校の劣等生', 'series', None),
                                  (2, 'ソードアート・オンライン', 'series', None),
                                  (3, '魔法科高校の劣等生 第一巻', 'work', 'Q1')]:
        entity = {'id': f'Q{n}', 'lastrevid': n, 'labels': {'ja': {'value': title}},
                  'aliases': {'ja': [{'value': 'まほうか' if n == 1 else title}]},
                  'claims': {'P31': [claim('Q104213567' if kind == 'series' else 'Q747381')],
                             'P407': [claim('Q5287')], 'P136': [claim('Q100')]}, 'sitelinks': {}}
        if parent:
            entity['claims']['P179'] = [claim(parent)]
        raw = tmp_path / f'{n}.json'
        rhash = write(raw, {'entities': {f'Q{n}': entity, 'Q100': {'id': 'Q100', 'labels': {'ja': {'value': 'ファンタジー'}}}}})
        records.append({'schema_version': 'light-novel-metadata-v1', 'id': f'Q{n}', 'source': 'wikidata',
            'kind': kind, 'title': title, 'aliases': list(dict.fromkeys([title, 'まほうか' if n == 1 else title])),
            'authors': [], 'original_languages': ['Q5287'], 'genres': [{'id': 'Q100', 'label': 'ファンタジー'}],
            'subjects': [], 'publishers': [], 'publication_dates': [], 'sitelinks': {},
            'relations': {p: ([parent] if p == 'P179' and parent else []) for p in ('P179', 'P527', 'P629', 'P155', 'P156', 'P144')},
            'provenance': {'url': f'https://www.wikidata.org/wiki/Q{n}', 'entity_revision': n,
                'raw_path': str(raw), 'raw_sha256': rhash, 'conditions_path': str(conditions), 'conditions_sha256': chash},
            'admission': {'accepted': True, 'reasons': [], 'evidence': {}}})
    catalogue = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    return catalogue, records


def test_import_search_detail_and_no_private_paths(setup_catalogue):
    cat, records = setup_catalogue
    assert cat.ingest(records)['inserted'] == 3
    assert cat.search_catalogue('マホウカ')['items'][0]['entry_id'] == 'lns:wd:Q1'
    assert cat.search_catalogue('  魔法科　高校 ')['total'] == 2
    row = cat.work_detail('lns:wd:Q1')
    assert row['entry_kind'] == 'series' and row['read_status'] == 'unknown'
    assert row['members'][0]['entry_id'] == 'lnw:wd:Q3'
    assert 'raw_path' not in json.dumps(row)
    assert row['llm_features'] == []


def test_metadata_and_raw_forgery_rejected(setup_catalogue):
    cat, records = setup_catalogue
    records[0]['title'] = 'forged'
    assert cat.ingest(records)['failed'] == 1
    records[0]['title'] = '魔法科高校の劣等生'
    cat.ingest(records)
    raw = records[0]['provenance']['raw_path']
    from pathlib import Path
    Path(raw).write_text('{}')
    with pytest.raises(ValueError, match='SHA'):
        cat.work_detail('lns:wd:Q1')


def test_adaptation_and_unknown_language_stay_held(setup_catalogue):
    cat, records = setup_catalogue
    from pathlib import Path
    record = records[0]
    raw = Path(record['provenance']['raw_path'])
    payload = json.loads(raw.read_text(encoding='utf-8'))
    payload['entities']['Q1']['claims']['P31'].append(claim('Q5398426'))
    record['provenance']['raw_sha256'] = write(raw, payload)
    assert cat.ingest([record])['failed'] == 1
    payload['entities']['Q1']['claims']['P31'].pop()
    payload['entities']['Q1']['claims']['P407'] = []
    record['provenance']['raw_sha256'] = write(raw, payload)
    assert cat.ingest([record])['failed'] == 1


def test_preference_recommendation_excludes_family_and_read_series(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    result = cat.recommend_preferences(liked_entry_ids=['lns:wd:Q1'])
    assert [x['entry_id'] for x in result['items']] == ['lns:wd:Q2']
    assert result['items'][0]['reasons'][0]['kind'] == 'source_genre'
    cat.set_read_status('lnw:wd:Q3', 'read')
    assert cat.work_detail('lns:wd:Q1')['read_status'] == 'unknown'
    assert cat.similar_works('lns:wd:Q2')['items'] == []
    cat.set_read_status('lnw:wd:Q3', 'unknown')
    assert cat.search_catalogue(read_filter='unread')['total'] == 0
    assert cat.search_catalogue(read_filter='unknown')['total'] == 3


def test_qid_is_not_typed_entry_and_stats_separate_kinds(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    with pytest.raises(ValueError):
        cat.work_detail('Q1')
    stats = cat.catalogue_stats()
    assert stats['series'] == 2 and stats['individual_works'] == 1 and stats['editions'] == 0
    assert stats['content_feature_series'] == 0 and stats['source_genre_series'] == 2


def test_search_normalization():
    assert normalize_search(' ＳＡＯ　まほうか ') == normalize_search('saoマホウカ')


def test_changed_conditions_snapshot_fails_closed(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    from pathlib import Path
    conditions = json.loads(Path(records[0]['provenance']['conditions_path']).read_text())
    Path(conditions['snapshots'][0]['raw_path']).write_text('changed')
    with pytest.raises(ValueError, match='SHA'):
        cat.search_catalogue('魔法科')


def test_updated_metadata_preserves_independent_read_state(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    cat.set_read_status('lns:wd:Q1', 'reading')
    records[0]['admission']['evidence'] = {'review_note': 'new evidence without changed metadata'}
    assert cat.ingest(records)['updated'] == 1
    assert cat.work_detail('lns:wd:Q1')['read_status'] == 'reading'


def test_only_work_qids_are_removed_for_world_overlap(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    result = cat.search_catalogue(exclude_work_qids={'Q1', 'Q3'})
    assert {r['entry_id'] for r in result['items']} == {'lns:wd:Q1', 'lns:wd:Q2'}


def test_authenticated_series_features_never_become_volume_features(setup_catalogue, monkeypatch, tmp_path):
    cat, records = setup_catalogue
    cat.ingest(records)
    feature = {'entry_id': 'lns:wd:Q1', 'scope': 'series', 'content_features_adopted': True,
               'labels': [{'label_id': 'genre.fantasy', 'label': 'ファンタジー', 'axis': 'genre',
                           'decision': 'present', 'scope': 'series', 'relevance': .8,
                           'raw_path': '/private/source', 'evidence': [{'evidence_id': 'e1', 'locator': 'paragraph:1', 'raw_path': '/private/source'}]}],
               'source_attributions': [{'evidence_id': 'e1', 'kind': 'synopsis',
                   'source_url': 'https://ja.wikipedia.org/w/index.php?oldid=1', 'license': 'CC-BY-SA-4.0',
                   'raw_path': '/private/source', 'attribution': {'revision_id': 1, 'raw_path': '/private/source'}}]}
    import recommend_book.light_novel_classification_v1 as verifier
    calls = []
    def verify(path, sha):
        calls.append((path, sha))
        return {'rows': [feature]}
    monkeypatch.setattr(verifier, 'verify_feature_export', verify)
    assert cat.import_features(tmp_path / 'manifest.json', 'a' * 64)['imported'] == 1
    assert cat.work_detail('lns:wd:Q1')['llm_features'][0]['evidence_basis'] == 'content'
    assert cat.work_detail('lnw:wd:Q3')['llm_features'] == []
    assert 'raw_path' not in json.dumps(cat.work_detail('lns:wd:Q1'))
    assert cat.catalogue_stats()['content_feature_series'] == 1
    assert len(calls) >= 4  # Relevant evidence is checked anew for separate operations.
    def invalid(path, sha):
        raise ValueError('changed pinned artifact')
    monkeypatch.setattr(verifier, 'verify_feature_export', invalid)
    assert cat.work_detail('lns:wd:Q1')['llm_features'] == []
    assert cat.catalogue_stats()['content_feature_series'] == 0
    assert cat.search_catalogue('魔法科')['total'] == 2


def test_world_genres_are_only_ranking_references(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    result = cat.recommend_preferences(reference_genres=[{'qid': 'Q100', 'label': 'ファンタジー',
        'source_url': 'https://www.wikidata.org/wiki/Q999', 'scope': 'work'}])
    assert len(result['items']) == 2
    assert result['items'][0]['llm_features'] == []
    assert result['items'][0]['reasons'][0]['reference_sources'][0]['scope'] == 'work'


@pytest.fixture
def edition_bundle(setup_catalogue, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    cat, records = setup_catalogue
    conditions = Path(records[0]['provenance']['conditions_path'])
    document = json.loads(conditions.read_text())
    for name, url in [('guide', 'https://www.ndl.go.jp/data/data_service/quickguide'),
                      ('api', 'https://ndlsearch.ndl.go.jp/help/api')]:
        page = tmp_path / name
        page.write_text(name)
        document['snapshots'].append({'url': url, 'raw_path': str(page),
            'raw_sha256': hashlib.sha256(page.read_bytes()).hexdigest()})
    document['ndl'] = {'provider': 'iss-ndl-opac', 'usages': {
        'local_storage': True, 'metadata_display': True, 'metadata_recommendation': True}}
    chash = write(conditions, document)
    xml = '''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:d="http://purl.org/dc/terms/" xmlns:n="http://ndl.go.jp/dcndl/terms/">
        <n:BibResource rdf:about="https://ndlsearch.ndl.go.jp/books/R100000002-I1">
        <d:title>書誌候補 第一巻</d:title><d:creator>著者 著</d:creator>
        <d:publisher>出版社</d:publisher><d:issued>2020</d:issued>
        <d:language>jpn</d:language><n:seriesTitle>ラノベ文庫 ; 1</n:seriesTitle>
        <n:volume>1</n:volume></n:BibResource></rdf:RDF>'''
    raw = tmp_path / 'edition.xml'
    raw.write_text(xml, encoding='utf-8')
    rhash = hashlib.sha256(raw.read_bytes()).hexdigest()
    artifact = RawArtifact(rhash, str(raw), len(raw.read_bytes()), '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru')
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), artifact, 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    record = {'schema_version': 'light-novel-edition-v1', 'source': 'ndl', 'provider': 'iss-ndl-opac',
        'id': source['source_id'], 'title': source['titles'][0]['value'], 'volume': source['volume'],
        'authors': [c['name'] for c in source['contributors'] if c['role'] == 'author'],
        'publisher': source['publisher'], 'date': source['publication_year'], 'isbn': [],
        'languages': ['jpn'], 'label': source['series'], 'ndc': [], 'source_url': source['source_url'],
        'source_record': source, 'label_evidence': [{'query_term': 'ラノベ文庫'}], 'label_candidate': True,
        'admission': {'light_novel_confirmed': False}, 'provenance': {'url': source['source_url'],
            'raw_path': str(raw), 'raw_sha256': rhash, 'conditions_path': str(conditions), 'conditions_sha256': chash}}
    return cat, records, record


def test_ndl_edition_is_not_promoted_to_story_series_or_original_language(edition_bundle):
    cat, _, record = edition_bundle
    assert cat.import_editions([record])['inserted'] == 1
    row = cat.work_detail('lne:ndl:R100000002-I1')
    assert row['title'] == '書誌候補 第一巻' and row['entry_kind'] == 'edition'
    assert row['qid'] is None and row['original_language'] is None and row['language'] == 'ja'
    assert row['genres'] == [] and row['members'] == [] and not row['light_novel_confirmed']
    assert cat.catalogue_stats()['series'] == 0
    assert cat.search_catalogue(language='ja')['total'] == 1
    assert cat.recommend_preferences(preferred_features=['genre.fantasy'])['items'] == []
    record['title'] = '捏造'
    assert cat.import_editions([record])['failed'] == 1
    record['title'] = '書誌候補 第一巻'
    record['series_entry_id'] = 'lns:wd:Q1'
    assert cat.import_editions([record])['failed'] == 1


def test_real_feature_verifier_binding_and_native_mutation(setup_catalogue, tmp_path, monkeypatch):
    from pathlib import Path
    from test_light_novel_classification_v1 import build_verified_export_fixture
    cat, records = setup_catalogue
    cat.ingest(records)
    # The fixture executes deterministic child processes, with no model/network.
    result = build_verified_export_fixture(tmp_path, monkeypatch)
    assert cat.import_features(result['manifest_path'], result['manifest_sha256'])['imported'] == 1
    assert cat.catalogue_stats()['content_feature_series'] == 1
    row = cat.work_detail('lns:wd:Q1')
    assert row['llm_features'][0]['label'] and row['llm_features'][0]['scope'] == 'series'
    assert cat.work_detail('lnw:wd:Q3')['llm_features'] == []
    final = Path(result['manifest_path']).parent / 'classification/000/final.json'
    final.write_text('{}', encoding='utf-8')
    assert cat.work_detail('lns:wd:Q1')['llm_features'] == []
    assert cat.catalogue_stats()['content_feature_series'] == 0


def test_v2_explicit_japanese_literary_description_preserves_unknown_original_language(setup_catalogue):
    from pathlib import Path
    cat, records = setup_catalogue
    record = records[0]
    raw = Path(record['provenance']['raw_path'])
    payload = json.loads(raw.read_text(encoding='utf-8'))
    payload['entities']['Q1']['claims']['P407'] = []
    payload['entities']['Q1']['descriptions'] = {'en': {'value': 'Japanese light novel series'}}
    record['provenance']['raw_sha256'] = write(raw, payload)
    record['schema_version'] = 'light-novel-metadata-v2'
    record['original_languages'] = []
    record['original_language_resolution'] = {'method': 'wikidata_description_explicit_japanese_literary_original_v1',
        'language': 'Q5287', 'source_url': record['provenance']['url'], 'raw_path': str(raw),
        'raw_sha256': record['provenance']['raw_sha256'], 'entity_revision': 1,
        'locator': 'descriptions.en.value', 'text': 'Japanese light novel series'}
    assert cat.ingest([record])['inserted'] == 1
    row = cat.work_detail('lns:wd:Q1')
    assert row['original_language'] is None
    assert row['japanese_identity_basis'] == 'explicit_wikidata_description'
    assert cat.search_catalogue(language='ja')['total'] == 0
    record['original_language_resolution']['text'] = 'Japanese manga series'
    assert cat.ingest([record])['failed'] == 1
    record['original_language_resolution']['text'] = 'Japanese light novel series'
    record['schema_version'] = 'light-novel-metadata-v1'
    assert cat.ingest([record])['failed'] == 1


@pytest.fixture
def membership_bundle(edition_bundle, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, records, edition = edition_bundle
    series = records[0]
    series['provenance']['conditions_sha256'] = edition['provenance']['conditions_sha256']
    raw_series = Path(series['provenance']['raw_path'])
    payload = json.loads(raw_series.read_text(encoding='utf-8'))
    payload['entities']['Q1']['claims'].update(P50=[claim('Q500')], P123=[claim('Q600')])
    payload['entities']['Q500'] = {'id': 'Q500', 'labels': {'ja': {'value': '著者'}},
        'claims': {'P349': [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': '01234567'}}}]}}
    payload['entities']['Q600'] = {'id': 'Q600', 'labels': {'ja': {'value': 'ラノベ文庫'}}}
    series['provenance']['raw_sha256'] = write(raw_series, payload)
    series['authors'] = [{'id': 'Q500', 'label': '著者'}]
    series['publishers'] = [{'id': 'Q600', 'label': 'ラノベ文庫'}]
    raw_edition = Path(edition['provenance']['raw_path'])
    xml = raw_edition.read_text(encoding='utf-8').replace('書誌候補 第一巻', series['title'])
    xml = xml.replace('<d:creator>著者 著</d:creator>', '''<d:creator><rdf:Description rdf:about="http://id.ndl.go.jp/auth/entity/01234567">
        <foaf:name xmlns:foaf="http://xmlns.com/foaf/0.1/">著者</foaf:name></rdf:Description></d:creator>''')
    xml = xml.replace('</n:BibResource>', '<d:subject rdf:resource="http://id.ndl.go.jp/class/ndc9/913.6"/></n:BibResource>')
    raw_edition.write_text(xml, encoding='utf-8')
    rhash = hashlib.sha256(raw_edition.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(rhash, str(raw_edition), len(raw_edition.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(title=source['titles'][0]['value'], source_record=source, authors=['著者'], ndc=['913.6'])
    edition['provenance']['raw_sha256'] = rhash
    assert cat.ingest([series])['inserted'] == 1
    assert cat.import_editions([edition])['inserted'] == 1
    manifest = tmp_path / 'author-authorities.json'
    msha = write(manifest, {'version': 'light-novel-author-authorities-v1', 'authors': [{
        'author_qid': 'Q500', 'raw_path': str(raw_series), 'raw_sha256': series['provenance']['raw_sha256']}]})
    return cat, series, edition, manifest, msha


def test_edition_series_membership_requires_all_fields_and_never_creates_original_work(membership_bundle):
    cat, _, _, manifest, msha = membership_bundle
    result = cat.resolve_edition_series_memberships(manifest, msha)
    assert result['linked'] == 1
    row = cat.work_detail('lns:wd:Q1')
    assert row['members'][0]['entry_id'] == 'lne:ndl:R100000002-I1'
    assert row['members'][0]['entry_kind'] == 'edition'
    edition = cat.work_detail('lne:ndl:R100000002-I1')
    assert edition['series_entry_id'] == 'lns:wd:Q1'
    assert edition['relation_resolution'] == 'edition_series_membership_verified'
    assert edition['original_language'] is None
    assert cat.catalogue_stats()['individual_works'] == 0
    assert 'raw_path' not in json.dumps(row)
    assert cat.resolve_edition_series_memberships(manifest, msha)['unchanged'] == 1
    cat.set_read_status('lne:ndl:R100000002-I1', 'read')
    assert cat.work_detail('lns:wd:Q1')['read_status'] == 'unknown'


def _scoped_membership_duplicate_series(cat, series, tmp_path):
    from copy import deepcopy
    from pathlib import Path
    duplicate = deepcopy(series)
    duplicate['id'] = 'Q4'
    payload = json.loads(Path(series['provenance']['raw_path']).read_text(encoding='utf-8'))
    payload['entities']['Q4'] = deepcopy(payload['entities']['Q1'])
    payload['entities']['Q4']['id'] = 'Q4'
    raw = tmp_path / 'scoped-duplicate-series.json'
    duplicate['provenance'].update(raw_path=str(raw), raw_sha256=write(raw, payload),
                                   url='https://www.wikidata.org/wiki/Q4')
    assert cat.ingest([duplicate])['inserted'] == 1
    return duplicate


def _scoped_membership_duplicate_edition(cat, edition, tmp_path, *, title_suffix=''):
    from copy import deepcopy
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    duplicate = deepcopy(edition)
    raw = tmp_path / 'scoped-unrelated-edition.xml'
    raw.write_text(Path(edition['provenance']['raw_path']).read_text(encoding='utf-8')
                   .replace('R100000002-I1', 'R100000002-I2')
                   .replace(edition['title'], edition['title'] + title_suffix), encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(raw.read_text(encoding='utf-8')),
        RawArtifact(digest, str(raw), raw.stat().st_size, '2026-10-24T00:00:00Z',
                    'https://ndlsearch.ndl.go.jp/api/sru'),
        'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    duplicate.update(id=source['source_id'], source_record=source, source_url=source['source_url'],
                     title=source['titles'][0]['value'])
    duplicate['provenance'].update(raw_path=str(raw), raw_sha256=digest, url=source['source_url'])
    assert cat.import_editions([duplicate])['inserted'] == 1
    return duplicate


def test_scoped_wikidata_membership_links_and_repeats_without_replacing(membership_bundle, monkeypatch):
    cat, _, _, manifest, msha = membership_bundle
    def forbidden_full_rows(*args, **kwargs):
        raise AssertionError('scoped membership must not call the full catalogue reader')
    monkeypatch.setattr(cat, '_rows', forbidden_full_rows)
    result = cat.resolve_wikidata_edition_memberships(manifest, msha,
        edition_ids=['lne:ndl:R100000002-I1'])
    assert result == {'scope': 'wikidata_series_only', 'requested_editions': 1,
        'candidate_series': 1, 'linked': 1, 'unchanged': 0, 'protected_existing': 0,
        'unresolved_editions': 0}
    with cat._connect() as db:
        before = tuple(db.execute('SELECT * FROM edition_series_memberships').fetchone())
    assert cat.resolve_wikidata_edition_memberships(manifest, msha,
        edition_ids=['lne:ndl:R100000002-I1'])['unchanged'] == 1
    with cat._connect() as db:
        assert tuple(db.execute('SELECT * FROM edition_series_memberships').fetchone()) == before


@pytest.mark.parametrize('scope', [[], ['lne:ndl:missing'], ['lns:wd:Q1'],
                                 ['Q1'], ['lne:ndl:R100000002-I1'] * 2])
def test_scoped_wikidata_membership_empty_or_invalid_scope_is_write_free(membership_bundle, scope):
    cat, _, _, manifest, msha = membership_bundle
    if not scope:
        assert cat.resolve_wikidata_edition_memberships('missing-manifest', 'invalid',
            edition_ids=scope)['requested_editions'] == 0
    else:
        with pytest.raises(ValueError):
            cat.resolve_wikidata_edition_memberships(manifest, msha, edition_ids=scope)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM edition_series_memberships').fetchone()[0] == 0


def test_scoped_wikidata_membership_reads_only_requested_and_title_candidate_raws(
        membership_bundle, setup_catalogue, tmp_path):
    from pathlib import Path
    cat, _, edition, manifest, msha = membership_bundle
    _, records = setup_catalogue
    # Its conditions pin predates the edition fixture; preserve the current one.
    records[1]['provenance']['conditions_sha256'] = edition['provenance']['conditions_sha256']
    assert cat.ingest([records[1]])['inserted'] == 1
    unrelated = _scoped_membership_duplicate_edition(cat, edition, tmp_path)
    Path(records[1]['provenance']['raw_path']).write_text('changed', encoding='utf-8')
    Path(unrelated['provenance']['raw_path']).write_text('changed', encoding='utf-8')
    result = cat.resolve_wikidata_edition_memberships(manifest, msha,
        edition_ids=['lne:ndl:R100000002-I1'])
    assert result['linked'] == 1 and result['candidate_series'] == 1
    with cat._connect() as db:
        assert [r['edition_id'] for r in db.execute('SELECT * FROM edition_series_memberships')] == ['lne:ndl:R100000002-I1']


@pytest.mark.parametrize('change', ['valid_ambiguous', 'raw', 'sql_kind', 'record_sha'])
def test_scoped_wikidata_membership_cannot_hide_second_candidate_or_write_partial(
        membership_bundle, tmp_path, change):
    from pathlib import Path
    cat, series, _, manifest, msha = membership_bundle
    duplicate = _scoped_membership_duplicate_series(cat, series, tmp_path)
    if change == 'raw':
        Path(duplicate['provenance']['raw_path']).write_text('{}', encoding='utf-8')
    elif change == 'sql_kind':
        with cat._connect() as db:
            db.execute("UPDATE entries SET kind='work' WHERE entry_id='lns:wd:Q4'")
    elif change == 'record_sha':
        with cat._connect() as db:
            db.execute("UPDATE entries SET record_sha256=? WHERE entry_id='lns:wd:Q4'", ('0' * 64,))
    if change == 'valid_ambiguous':
        result = cat.resolve_wikidata_edition_memberships(manifest, msha,
            edition_ids=['lne:ndl:R100000002-I1'])
        assert result['candidate_series'] == 2 and result['unresolved_editions'] == 1
    else:
        with pytest.raises(ValueError):
            cat.resolve_wikidata_edition_memberships(manifest, msha,
                edition_ids=['lne:ndl:R100000002-I1'])
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM edition_series_memberships').fetchone()[0] == 0


def test_scoped_wikidata_membership_preserves_different_existing_proof(membership_bundle, tmp_path):
    cat, _, _, manifest, msha = membership_bundle
    assert cat.resolve_edition_series_memberships(manifest, msha)['linked'] == 1
    with cat._connect() as db:
        before = tuple(db.execute('SELECT * FROM edition_series_memberships').fetchone())
    new_manifest = tmp_path / 'independent-author-authorities.json'
    new_manifest.write_bytes(manifest.read_bytes())
    result = cat.resolve_wikidata_edition_memberships(new_manifest, msha,
        edition_ids=['lne:ndl:R100000002-I1'])
    assert result['protected_existing'] == 1 and result['linked'] == result['unchanged'] == 0
    with cat._connect() as db:
        assert tuple(db.execute('SELECT * FROM edition_series_memberships').fetchone()) == before


def test_scoped_wikidata_membership_keeps_complete_title_and_authority_gates(membership_bundle, tmp_path):
    cat, _, edition, manifest, msha = membership_bundle
    empty = tmp_path / 'unproven-authorities.json'
    digest = write(empty, {'version': 'light-novel-author-authorities-v1', 'authors': []})
    result = cat.resolve_wikidata_edition_memberships(empty, digest,
        edition_ids=['lne:ndl:R100000002-I1'])
    assert result['linked'] == 0 and result['unresolved_editions'] == 1
    # The exact title preselection uses the same complete normalization as the
    # six-gate matcher; a suffixed title cannot become its parent story title.
    _scoped_membership_duplicate_edition(cat, edition, tmp_path, title_suffix=' 外伝')
    result = cat.resolve_wikidata_edition_memberships(manifest, msha,
        edition_ids=['lne:ndl:R100000002-I2'])
    assert result['candidate_series'] == 0 and result['unresolved_editions'] == 1
    assert result['linked'] == 0


def test_scoped_wikidata_membership_authenticates_all_requested_before_any_insert(membership_bundle, tmp_path):
    from pathlib import Path
    cat, _, edition, manifest, msha = membership_bundle
    duplicate = _scoped_membership_duplicate_edition(cat, edition, tmp_path)
    Path(duplicate['provenance']['raw_path']).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError):
        cat.resolve_wikidata_edition_memberships(manifest, msha,
            edition_ids=['lne:ndl:R100000002-I1', 'lne:ndl:R100000002-I2'])
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM edition_series_memberships').fetchone()[0] == 0


def test_membership_missing_authority_does_not_link_and_pin_mutation_expires(membership_bundle):
    from pathlib import Path
    cat, _, _, manifest, msha = membership_bundle
    cat.resolve_edition_series_memberships(manifest, msha)
    manifest.write_text('{}', encoding='utf-8')
    assert cat.work_detail('lns:wd:Q1')['members'] == []
    assert cat.work_detail('lne:ndl:R100000002-I1')['relation_resolution'] == 'original_work_and_story_series_unresolved'
    document = {'version': 'light-novel-author-authorities-v1', 'authors': []}
    msha = write(manifest, document)
    result = cat.resolve_edition_series_memberships(manifest, msha)
    assert result['linked'] == 0


def test_membership_is_not_title_author_or_isbn_only(membership_bundle):
    from copy import deepcopy
    cat, _, edition, _, _ = membership_bundle
    series = cat.work_detail('lns:wd:Q1')
    source = edition['source_record']
    authorities = {'Q500': {'01234567'}}
    assert cat._membership_match(series, source, authorities)
    for field, replacement in [('volume', None), ('series', '別ラノベ文庫 ; 1'),
                               ('language', 'eng'), ('titles', [{'value': '魔法科高校の劣等生 コミック'}]),
                               ('contributors', [{'role': 'author', 'name': '著者', 'identifiers': []}]),
                               ('extra', {'ndc_codes': ['726.1']})]:
        wrong = deepcopy(source)
        wrong[field] = replacement
        assert cat._membership_match(series, wrong, authorities) is None
    parallel = deepcopy(source)
    parallel['series'] = 'ラノベ文庫 = LIGHT NOVEL LIBRARY ; 1'
    assert cat._membership_match(series, parallel, authorities)


def test_new_ambiguous_series_invalidates_previously_unique_edition_link(membership_bundle, tmp_path):
    from copy import deepcopy
    from pathlib import Path
    cat, series, _, manifest, msha = membership_bundle
    assert cat.resolve_edition_series_memberships(manifest, msha)['linked'] == 1
    duplicate = deepcopy(series)
    duplicate['id'] = 'Q4'
    raw = Path(series['provenance']['raw_path'])
    payload = json.loads(raw.read_text(encoding='utf-8'))
    payload['entities']['Q4'] = deepcopy(payload['entities']['Q1'])
    payload['entities']['Q4']['id'] = 'Q4'
    newraw = tmp_path / 'duplicate-series.json'
    duplicate['provenance'].update(raw_path=str(newraw), raw_sha256=write(newraw, payload), url='https://www.wikidata.org/wiki/Q4')
    assert cat.ingest([duplicate])['inserted'] == 1
    assert cat.work_detail('lns:wd:Q1')['members'] == []
    assert cat.resolve_edition_series_memberships(manifest, msha)['linked'] == 0


def test_series_title_search_keeps_series_on_first_page_with_32_editions(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    series = cat.work_detail('lns:wd:Q1')
    editions = [{**series, 'entry_id': f'lne:ndl:R100000002-I{n}', 'entry_kind': 'edition',
                 'title': series['title']} for n in range(1, 33)]
    monkeypatch.setattr(cat, '_rows', lambda **kwargs: [*editions, series])
    result = cat.search_catalogue(series['title'], page_size=30)
    assert result['total'] == 33 and result['items'][0]['entry_id'] == 'lns:wd:Q1'
    assert cat.search_catalogue('魔法科')['items'][0]['entry_kind'] == 'series'
    assert cat.search_catalogue('マホウカ')['items'][0]['entry_kind'] == 'series'


@pytest.fixture
def publisher_ndl_bundle(membership_bundle, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    _, _, edition, _, _ = membership_bundle
    raw = Path(edition['provenance']['raw_path'])
    xml = raw.read_text(encoding='utf-8').replace('出版社', 'ホビージャパン').replace('ラノベ文庫', 'HJ文庫')
    raw.write_text(xml, encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source, publisher=source['publisher'], label=source['series'], label_evidence=[{'query_term': 'HJ文庫'}])
    edition['provenance']['raw_sha256'] = digest
    cat = LightNovelCatalogue(tmp_path / 'ndl-only.sqlite')
    assert cat.import_editions([edition])['inserted'] == 1
    page = tmp_path / 'publisher.html'
    page.write_text('''<li class="seriesList_item"><a class="seriesList_itemTitle" href="https://firecross.jp/hjbunko/series/1">魔法科高校の劣等生</a>
        <ul class="book-author-list"><li class="book-author-item"><span class="book-author-type">著者</span>
        <a class="book-author-name">著者</a></li></ul></li>''', encoding='utf-8')
    frames = tmp_path / 'publisher-frames.json'
    fsha = write(frames, [{'id': 'hj-bunko-series', 'publisher': 'ホビージャパン', 'label': 'HJ文庫',
        'pages': [{'source_url': 'https://firecross.jp/hjbunko/series', 'http_status': 200,
            'raw_path': page.name, 'sha256': hashlib.sha256(page.read_bytes()).hexdigest(), 'content_type': 'text/html;charset=utf-8'}]}])
    return cat, edition, frames, fsha, tmp_path, page


def test_publisher_confirmed_ndl_series_is_typed_without_fabricated_work(publisher_ndl_bundle):
    cat, edition, frames, fsha, root, _ = publisher_ndl_bundle
    result = cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)
    assert result['inserted'] == 1
    row = cat.work_detail('lns:ndl:R100000002-I1')
    assert row['qid'] is None and row['original_language'] is None and row['entry_kind'] == 'series'
    assert row['title'] == edition['source_record']['titles'][0]['value']
    assert row['source_url'] == edition['source_url']
    assert row['series_assertion_source_url'] == 'https://firecross.jp/hjbunko/series/1'
    assert row['members'][0]['entry_id'] == 'lne:ndl:R100000002-I1'
    assert cat.catalogue_stats()['individual_works'] == 0
    assert cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)['unchanged'] == 1
    assert 'raw_path' not in json.dumps(row)


@pytest.fixture
def dated_legacy_series(publisher_ndl_bundle):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    cat, original, frames, fsha, root, page = publisher_ndl_bundle
    original_xml = Path(original['provenance']['raw_path']).read_text(encoding='utf-8')
    def make(change=None):
        record = json.loads(json.dumps(original))
        xml = original_xml.replace('>著者</foaf:name>', '>著, 者, 1965-</foaf:name>')
        xml = xml.replace('</n:BibResource>', '<d:creator>著者 著</d:creator></n:BibResource>')
        if change == 'multiple_publishers':
            xml = xml.replace('</n:BibResource>', '<d:publisher>別出版社</d:publisher></n:BibResource>')
        elif change == 'other_role': xml = xml.replace('著者 著</d:creator>', '著者 イラスト</d:creator>')
        elif change == 'unknown_uri': xml = xml.replace('http://id.ndl.go.jp/auth/entity/01234567', 'https://example.org/unknown')
        elif change == 'wrong_title': xml = xml.replace('<d:title>魔法科高校の劣等生</d:title>', '<d:title>別の作品</d:title>')
        elif change == 'related_title':
            xml = xml.replace('<d:title>魔法科高校の劣等生</d:title>', '<d:title>別の作品</d:title><d:relation><n:BibResource><d:title>魔法科高校の劣等生</d:title></n:BibResource></d:relation>')
        elif change == 'wrong_volume': xml = xml.replace('<n:volume>1</n:volume>', '<n:volume>上巻不明</n:volume>')
        elif change == 'related_volume':
            xml = xml.replace('<n:volume>1</n:volume>', '<d:relation><n:BibResource><n:volume>1</n:volume></n:BibResource></d:relation>')
        elif change == 'wrong_imprint': xml = xml.replace('HJ文庫', '別文庫')
        elif change == 'wrong_language': xml = xml.replace('>jpn<', '>eng<')
        elif change == 'wrong_ndc': xml = xml.replace('/913.6', '/726.1')
        elif change == 'distribution':
            xml = xml.replace('</n:BibResource>', '<d:publisher><foaf:Agent xmlns:foaf="http://xmlns.com/foaf/0.1/"><foaf:name>頒布会社</foaf:name><d:description>頒布</d:description></foaf:Agent></d:publisher></n:BibResource>')
            xml = xml.replace('<d:publisher>ホビージャパン</d:publisher>', '<d:publisher><foaf:Agent xmlns:foaf="http://xmlns.com/foaf/0.1/"><foaf:name>ホビージャパン</foaf:name></foaf:Agent></d:publisher>')
        raw = Path(record['provenance']['raw_path'])
        raw.write_text(xml, encoding='utf-8')
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()
        source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), raw.stat().st_size,
            record['source_record']['retrieved_at'], 'https://ndlsearch.ndl.go.jp/api/sru'),
            'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
        record.update(source_record=source, title=source['titles'][0]['value'], volume=source['volume'],
            authors=[c['name'] for c in source['contributors'] if c['role'] == 'author'],
            publisher=source['publisher'], label=source['series'], languages=[source['language']] if source['language'] else [],
            ndc=source['extra']['ndc_codes'], label_candidate=bool(source['series'] and 'HJ文庫' in source['series']))
        record['provenance']['raw_sha256'] = digest
        cache = {}
        cat._project_edition(record, cache)
        from recommend_book.light_novel_ndl_series_v1 import publisher_assertions
        proof = {'frames_path': str(frames), 'frames_sha256': fsha, 'source_root': str(root)}
        assertion = next(iter(publisher_assertions(proof, cat._artifact, cache).values()))
        return record, assertion, cache, proof
    return cat, make


def test_legacy_dated_author_binding_is_edition_bound_and_membership_v2(dated_legacy_series):
    cat, make = dated_legacy_series
    record, assertion, cache, proof = make()
    before = json.loads(json.dumps(record))
    match = cat._publisher_series_match(assertion, record, cache, allow_unnumbered=True)
    assert match is not None
    assert match['dated_own_author_bindings'][0]['source_raw_sha256'] == record['provenance']['raw_sha256']
    assert match['dated_own_author_bindings'][0]['edition_bound_corroboration']['literal_credit']['literal'] == '著者 著'
    assert record == before
    series_record = {'anchor_edition_record': record, 'publisher_series_proof': dict(proof, reference_id=assertion['reference_id'])}
    membership = cat._ndl_membership_match({'entry_id': 'lns:ndl:' + record['id']}, series_record, record, cache)
    assert membership['rule_version'] == 'publisher-ndl-series-membership-v2'
    assert membership['edition_match']['dated_own_author_bindings'] == match['dated_own_author_bindings']


@pytest.mark.parametrize('change', ['multiple_publishers', 'other_role', 'unknown_uri', 'wrong_title',
    'related_title', 'wrong_volume', 'related_volume', 'wrong_imprint', 'wrong_language', 'wrong_ndc'])
def test_legacy_dated_author_fallback_keeps_all_own_bibliographic_gates(dated_legacy_series, change):
    cat, make = dated_legacy_series
    record, assertion, cache, _ = make(change)
    assert cat._publisher_series_match(assertion, record, cache, allow_unnumbered=False) is None


def test_legacy_dated_author_accepts_only_explicit_distribution_and_own_sb_writer_intersection(dated_legacy_series):
    cat, make = dated_legacy_series
    record, assertion, cache, _ = make('distribution')
    match = cat._publisher_series_match(assertion, record, cache, allow_unnumbered=False)
    assert match is not None and len(match['own_bibliographic_locators']['distribution']) == 1
    mixed = {**assertion, 'authors': [], 'creator_display_names': ['著者', '絵担当']}
    match = cat._publisher_series_match(mixed, record, cache, allow_unnumbered=False)
    assert match is not None and [author['name'] for author in match['authors']] == ['著者']
    only_illustrator = {**mixed, 'creator_display_names': ['絵担当']}
    assert cat._publisher_series_match(only_illustrator, record, cache, allow_unnumbered=False) is None


def test_legacy_strict_series_match_and_membership_proof_stay_unchanged(publisher_ndl_bundle, monkeypatch):
    from recommend_book.light_novel_ndl_series_v1 import publisher_assertions, match_bibliography
    from recommend_book.light_novel_catalogue_v1 import normalize_search
    cat, record, frames, sha, root, _ = publisher_ndl_bundle
    cache = {}
    cat._project_edition(record, cache)
    proof = {'frames_path': str(frames), 'frames_sha256': sha, 'source_root': str(root)}
    assertion = next(iter(publisher_assertions(proof, cat._artifact, cache).values()))
    expected = match_bibliography(assertion, cat._source_with_title_parts(record, cache), normalize_search, allow_unnumbered=True)
    def forbidden(*args, **kwargs):
        raise AssertionError('strict positives must not enter fallback')
    monkeypatch.setattr(cat, '_edition_primary_bibliographic_parts', forbidden)
    assert cat._publisher_series_match(assertion, record, cache, allow_unnumbered=True) == expected
    series_record = {'anchor_edition_record': record, 'publisher_series_proof': dict(proof, reference_id=assertion['reference_id'])}
    membership = cat._ndl_membership_match({'entry_id': 'lns:ndl:' + record['id']}, series_record, record, cache)
    assert membership['rule_version'] == 'publisher-ndl-series-membership-v1'
    assert 'edition_match' not in membership and 'anchor_match' not in membership


def test_publisher_ndl_series_holds_missing_author_and_changed_raw(publisher_ndl_bundle):
    cat, _, frames, fsha, root, page = publisher_ndl_bundle
    text = page.read_text(encoding='utf-8')
    page.write_text(text.replace('book-author-name">著者', 'book-author-name">別人'), encoding='utf-8')
    document = json.loads(frames.read_text(encoding='utf-8'))
    document[0]['pages'][0]['sha256'] = hashlib.sha256(page.read_bytes()).hexdigest()
    fsha = write(frames, document)
    assert cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)['inserted'] == 0
    assert cat.catalogue_stats()['series'] == 0
    page.write_text(text, encoding='utf-8')
    document[0]['pages'][0]['sha256'] = hashlib.sha256(page.read_bytes()).hexdigest()
    fsha = write(frames, document)
    assert cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)['inserted'] == 1
    page.write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA'):
        cat.work_detail('lns:ndl:R100000002-I1')


def test_ndl_series_does_not_duplicate_uncertain_wikidata_title(publisher_ndl_bundle, membership_bundle):
    cat, _, frames, fsha, root, _ = publisher_ndl_bundle
    _, series, _, _, _ = membership_bundle
    assert cat.ingest([series])['inserted'] == 1
    result = cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)
    assert result['inserted'] == 0
    assert result['held_reasons']['potential_wikidata_series_overlap_requires_authority_review'] == 1
    assert cat.catalogue_stats()['series'] == 1
    assert cat.work_detail('lns:ndl:R100000002-I1') is None


def test_official_series_can_have_one_unnumbered_ndl_book(publisher_ndl_bundle):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    cat, edition, frames, fsha, root, _ = publisher_ndl_bundle
    raw = Path(edition['provenance']['raw_path'])
    raw.write_text(raw.read_text(encoding='utf-8').replace('<n:volume>1</n:volume>', ''), encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(raw.read_bytes()), RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source, volume=None)
    edition['provenance']['raw_sha256'] = digest
    assert cat.ingest([edition])['updated'] == 1
    assert cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)['inserted'] == 1
    row = cat.work_detail('lns:ndl:R100000002-I1')
    assert len(row['members']) == 1 and row['members'][0]['volume'] is None
    assert row['original_language'] is None and row['year'] is None


def test_detail_and_search_do_not_verify_unrelated_native_manifests(setup_catalogue, tmp_path, monkeypatch):
    import recommend_book.light_novel_classification_v1 as native
    cat, records = setup_catalogue
    cat.ingest(records)
    calls = []
    def verify(path, sha):
        calls.append((str(path), sha))
        return {'rows': [{'entry_id': 'lns:wd:Q1', 'scope': 'series', 'labels': [], 'admitted_labels': []}]}
    monkeypatch.setattr(native, 'verify_feature_export', verify)
    cat.import_features(tmp_path / 'manifest.json', 'a' * 64)
    calls.clear()
    assert cat.work_detail('lns:wd:Q2')['entry_id'] == 'lns:wd:Q2'
    assert cat.search_catalogue('ソードアート')['total'] == 1
    assert calls == []
    cat.work_detail('lns:wd:Q1')
    assert len(calls) == 1
    cat.search_catalogue('魔法科')
    assert len(calls) == 2


def test_ndl_batch_normalizes_only_records_used_in_this_request(edition_bundle, monkeypatch):
    from pathlib import Path
    from recommend_book.ingestion.ndl import NDLAdapter
    cat, _, record = edition_bundle
    raw = Path(record['provenance']['raw_path'])
    first = raw.read_text(encoding='utf-8')
    other = first.replace('R100000002-I1', 'R100000002-I2').replace('書誌候補 第一巻', '無関係な別の書誌')
    raw.write_text('<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">'
        '<srw:records><srw:record><srw:recordData>' + first + '</srw:recordData></srw:record>'
        '<srw:record><srw:recordData>' + other + '</srw:recordData></srw:record></srw:records></srw:searchRetrieveResponse>', encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    record['provenance']['raw_sha256'] = record['source_record']['raw_sha256'] = digest
    calls = []
    original = NDLAdapter._normalize
    def normalize(self, *args, **kwargs):
        calls.append(kwargs.get('locator'))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(NDLAdapter, '_normalize', normalize)
    assert cat.ingest([record])['inserted'] == 1
    assert calls == ['records[1]/recordData']


def test_sbcr_series_membership_uses_bookloop_and_ndl_author_role(publisher_ndl_bundle, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, edition, frames, _, root, page = publisher_ndl_bundle
    raw = Path(edition['provenance']['raw_path'])
    xml = raw.read_text(encoding='utf-8').replace('ホビージャパン', 'SBクリエイティブ').replace('HJ文庫', 'GA文庫')
    raw.write_text(xml, encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source, publisher=source['publisher'], label=source['series'], label_evidence=[{'query_term': 'GA文庫'}])
    edition['provenance']['raw_sha256'] = digest
    assert cat.ingest([edition])['updated'] == 1
    series_url = 'https://ebook.sbcr.jp/lightnovel/series/id/1'
    page.write_text('<a href="/lightnovel/series/id/1">「魔法科高校の劣等生」シリーズ (1)</a>', encoding='utf-8')
    fsha = write(frames, [{'id': 'ga-ebook-series', 'publisher': 'SBクリエイティブ', 'label': 'GA文庫 / GAノベル / ツギクルブックス',
        'pages': [{'source_url': 'https://ebook.sbcr.jp/lightnovel/series', 'http_status': 200,
            'raw_path': page.name, 'sha256': hashlib.sha256(page.read_bytes()).hexdigest(), 'content_type': 'text/html;charset=utf-8'}]}])
    series_page = tmp_path / 'series-page.html'
    series_page.write_text('''<a href="/product/RANKING">無関係なランキング</a>
        <!-- ↓↓↓↓ partial.comic.bookloop ↓↓↓↓ --><a href="/product/BOOK1">魔法科高校の劣等生</a>
        <!-- ↑↑↑↑ partial.comic.bookloop↑↑↑↑ -->''', encoding='utf-8')
    product_page = tmp_path / 'product-page.html'
    product_page.write_text('''<ul class="author clear"><li class="mgnBottom10">著者 / 絵担当</li>
        <li>レーベル：<a>GA文庫</a></li></ul>''', encoding='utf-8')
    def receipt(path, url):
        return {'source_url': url, 'http_status': 200, 'raw_path': path.name,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'content_type': 'text/html;charset=utf-8'}
    book = {'book_url': 'https://ebook.sbcr.jp/product/BOOK1', 'title': '魔法科高校の劣等生'}
    supplemental = tmp_path / 'sbcr-handoff.json'
    handoff = [{'reference_id': 'publisher-series:' + hashlib.sha256(series_url.encode()).hexdigest()[:24],
        'source_url': series_url, 'title': '魔法科高校の劣等生', 'series_source_snapshot': receipt(series_page, series_url),
        'explicit_book_memberships': [book], 'representative_products': [{**book,
            'source_snapshot': receipt(product_page, book['book_url']),
            'fields': {'著者表示': ['著者 / 絵担当'], 'レーベル': 'GA文庫'}}]}]
    ssha = write(supplemental, handoff)
    result = cat.import_ndl_series_from_publishers(frames, fsha, source_root=root,
                                                  sbcr_series_path=supplemental, sbcr_series_sha256=ssha)
    assert result['inserted'] == 1
    row = cat.work_detail('lns:ndl:R100000002-I1')
    assert [a['name'] for a in row['authors']] == ['著者']
    assert row['original_language'] is None and row['isbn'] == []
    handoff[0]['representative_products'][0]['book_url'] = 'https://ebook.sbcr.jp/product/RANKING'
    ssha = write(supplemental, handoff)
    with pytest.raises(ValueError, match='not an explicit member'):
        cat.import_ndl_series_from_publishers(frames, fsha, source_root=root,
                                             sbcr_series_path=supplemental, sbcr_series_sha256=ssha)


def test_gzip_publisher_html_is_decoded_after_raw_hash_binding():
    import gzip
    from recommend_book.light_novel_ndl_series_v1 import _decode
    assert _decode(gzip.compress('<p>公式シリーズ</p>'.encode('utf-8')), 'text/html;charset=utf-8') == '<p>公式シリーズ</p>'


@pytest.mark.parametrize(('raw_title', 'admitted'), [
    ('魔法科高校の劣等生 : 外部書誌の副題', True),
    ('魔法科高校の劣等生 = English Parallel Title : 外部書誌の副題', True),
    ('魔法科高校の劣等生を語る別の小説', False),
    ('魔法科高校の劣等生:独立した題名', False),
])
def test_main_title_is_explicit_rdf_isbd_part_not_fuzzy_prefix(publisher_ndl_bundle, raw_title, admitted):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, edition, frames, fsha, root, _ = publisher_ndl_bundle
    raw = Path(edition['provenance']['raw_path'])
    xml = raw.read_text(encoding='utf-8').replace('魔法科高校の劣等生', raw_title)
    raw.write_text(xml, encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source, title=source['titles'][0]['value'])
    edition['provenance']['raw_sha256'] = digest
    assert cat.ingest([edition])['updated'] == 1
    result = cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)
    assert result['inserted'] == int(admitted)
    if admitted:
        row = cat.work_detail('lns:ndl:R100000002-I1')
        assert row['title'] == '魔法科高校の劣等生'
        assert row['title_origin']['kind'] == 'rdf_title_isbd_main'
        assert row['members'][0]['entry_id'] == 'lne:ndl:R100000002-I1'


def test_typed_read_states_survive_new_python_process(membership_bundle, setup_catalogue):
    import subprocess
    import sys
    cat, series, _, manifest, msha = membership_bundle
    _, records = setup_catalogue
    records[2]['provenance']['conditions_sha256'] = series['provenance']['conditions_sha256']
    assert cat.ingest([records[2]])['inserted'] == 1
    cat.resolve_edition_series_memberships(manifest, msha)
    cat.set_read_status('lns:wd:Q1', 'reading')
    cat.set_read_status('lne:ndl:R100000002-I1', 'read')
    command = [sys.executable, '-c', '''import json, sys
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
c = LightNovelCatalogue(sys.argv[1])
print(json.dumps([c.work_detail('lns:wd:Q1')['read_status'],
                  c.work_detail('lne:ndl:R100000002-I1')['read_status'],
                  c.work_detail('lnw:wd:Q3')['read_status']]))
''', str(cat.db_path)]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    assert json.loads(result.stdout) == ['reading', 'read', 'unknown']


def test_hj_label_alias_requires_two_exact_edition_membership_witnesses(publisher_ndl_bundle, tmp_path):
    from pathlib import Path
    from recommend_book.light_novel_ndl_series_v1 import publisher_assertions
    cat, edition, frames, _, root, _ = publisher_ndl_bundle
    base = Path(edition['provenance']['raw_path']).read_text(encoding='utf-8')
    cards, witnesses = [], []
    def receipt(path, url):
        return {'source_url': url, 'http_status': 200, 'raw_path': path.name,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'content_type': 'text/html;charset=utf-8'}
    for n, title, isbn in [(1, '魔法科高校の劣等生', '9784798613161'), (2, '別の小説シリーズ', '9784798613178')]:
        url = f'https://firecross.jp/hjnovels/series/{n}'
        book_url = f'https://firecross.jp/hjnovels/product/{n}'
        ref = 'publisher-series:' + hashlib.sha256(url.encode()).hexdigest()[:24]
        cards.append(f'''<li class="seriesList_item"><a class="seriesList_itemTitle" href="{url}">{title}</a>
            <ul class="book-author-list"><li class="book-author-item"><span class="book-author-type">著者</span>
            <a class="book-author-name">著者</a></li></ul></li>''')
        ndl = tmp_path / f'alias-ndl-{n}.xml'
        xml = base.replace('魔法科高校の劣等生', title).replace('HJ文庫', 'HJ NOVELS').replace('R100000002-I1', f'R100000002-I{n}')
        xml = xml.replace('</n:BibResource>', f'<d:identifier rdf:datatype="ISBN">{isbn}</d:identifier></n:BibResource>')
        ndl.write_text(xml, encoding='utf-8')
        series = tmp_path / f'alias-series-{n}.html'
        series.write_text('<script type="application/ld+json">' + json.dumps({'@type': 'BookSeries',
            'name': title, 'url': url, 'hasPart': [{'@type': 'Book', 'url': book_url, 'isbn': isbn}]}) + '</script>', encoding='utf-8')
        book = tmp_path / f'alias-book-{n}.html'
        book.write_text('<script type="application/ld+json">' + json.dumps({'@type': 'Book',
            'url': book_url, 'isbn': isbn, 'isPartOf': {'url': url}}) + '</script>', encoding='utf-8')
        witnesses.append({'ndl_record_id': f'R100000002-I{n}', 'ndl_source_url': f'https://ndlsearch.ndl.go.jp/books/R100000002-I{n}',
            'ndl_raw_path': str(ndl), 'ndl_raw_sha256': hashlib.sha256(ndl.read_bytes()).hexdigest(), 'ndl_imprint': 'HJ NOVELS ; 1',
            'publisher_reference': {'reference_id': ref}, 'series_source_snapshot': receipt(series, url),
            'publisher_product_checks': [{'source_snapshot': receipt(book, book_url)}]})
    page = tmp_path / 'alias-index.html'
    page.write_text(''.join(cards), encoding='utf-8')
    fsha = write(frames, [{'id': 'hj-novels-series', 'publisher': 'ホビージャパン', 'label': 'HJノベルス',
                          'pages': [receipt(page, 'https://firecross.jp/hjnovels/series')]}])
    alias = tmp_path / 'alias.json'
    asha = write(alias, witnesses)
    proof = {'frames_path': str(frames), 'frames_sha256': fsha, 'source_root': str(root),
             'label_aliases_path': str(alias), 'label_aliases_sha256': asha}
    rows = publisher_assertions(proof, cat._artifact, {})
    assert len(rows) == 2 and all(row['label_aliases'] == ['HJ NOVELS'] for row in rows.values())
    # Same ISBN without Book.isPartOf proving this series cannot establish the alias.
    book = tmp_path / 'alias-book-2.html'
    book.write_text(book.read_text(encoding='utf-8').replace('/series/2', '/series/999'), encoding='utf-8')
    witnesses[1]['publisher_product_checks'][0]['source_snapshot']['sha256'] = hashlib.sha256(book.read_bytes()).hexdigest()
    proof['label_aliases_sha256'] = write(alias, witnesses)
    with pytest.raises(ValueError, match='exact edition ISBN'):
        publisher_assertions(proof, cat._artifact, {})


def test_official_book_confirms_only_ndl_edition_and_expires_on_raw_change(publisher_ndl_bundle, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, edition, _, _, _, _ = publisher_ndl_bundle
    raw = Path(edition['provenance']['raw_path'])
    xml = raw.read_text(encoding='utf-8').replace('HJ文庫', '電撃文庫').replace('ホビージャパン', '角川書店')
    xml = xml.replace('</n:BibResource>', '<d:identifier rdf:datatype="ISBN">9784798613161</d:identifier></n:BibResource>')
    raw.write_text(xml, encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source, publisher=source['publisher'], label=source['series'], label_evidence=[{'query_term': '電撃文庫'}], isbn=['9784798613161'])
    edition['provenance']['raw_sha256'] = digest
    assert cat.ingest([edition])['updated'] == 1
    publisher_raw = tmp_path / 'official-books.json'
    item = {'itemCode': '123456789012', 'title': '魔法科高校の劣等生', 'isbn': '9784798613161',
            'label_id': 330, 'disp_author_name': '<li>著者 著者</li><li>イラスト 絵担当</li>', 'catchphrase': 'not public'}
    rawsha = write(publisher_raw, {'count_raw': {'book': 1}, 'result': {'book': [item]}})
    manifest = tmp_path / 'official-books-manifest.json'
    body = {'schema_version': 'kadokawa-publisher-books-v1', 'rights_scope': 'reference_only_bibliography',
        'pages': [{'source_url': 'https://www.kadokawa.co.jp/product/search/', 'final_url': 'https://www.kadokawa.co.jp/product/search/',
            'http_status': 200, 'http_method': 'POST', 'request_data': {'label': '330', 'releaseDate': '1', 'size': '20',
                'pageno': '1', 'pageno_book': '1', 'pageno_media': '1'}, 'raw_path': publisher_raw.name, 'sha256': rawsha}]}
    sha = write(manifest, body)
    result = cat.confirm_publisher_editions('kadokawa', manifest, sha)
    assert result['confirmed'] == 1
    row = cat.work_detail('lne:ndl:R100000002-I1')
    assert row['edition_bibliography_confirmed'] and row['light_novel_confirmed']
    assert row['entry_kind'] == 'edition' and row['original_language'] is None
    assert row['publishers'][0]['label'] == '角川書店'
    assert row['source_url'] == source['source_url']
    assert row['confirmation_source_url'] == 'https://www.kadokawa.co.jp/product/123456789012/'
    assert row['members'] == [] and row['llm_features'] == []
    assert cat.catalogue_stats()['series'] == 0 and cat.catalogue_stats()['individual_works'] == 0
    assert cat.confirm_publisher_editions('kadokawa', manifest, sha)['unchanged'] == 1
    assert 'raw_path' not in json.dumps(row) and 'not public' not in json.dumps(row)
    publisher_raw.write_text('{}', encoding='utf-8')
    row = cat.work_detail('lne:ndl:R100000002-I1')
    assert not row['light_novel_confirmed'] and 'edition_bibliography_confirmed' not in row


@pytest.mark.parametrize('label', ['ヤングジャンプコミックス', 'ジャンプコミックスDIGITAL',
    'ヤングジャンプコミックスDIGITAL', 'ダッシュエックス文庫コミックス'])
def test_dx_comics_are_denied_even_when_other_edition_evidence_matches(setup_catalogue, monkeypatch, label):
    cat, _ = setup_catalogue
    source = {'language': 'jpn', 'publisher': '集英社', 'series': label,
        'extra': {'ndc_codes': ['913.6']}, 'identifiers': [{'source': 'isbn', 'value': '9784798613161'}],
        'titles': [{'value': '小説と同名の漫画'}], 'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'contributors': [{'name': '著者', 'role': 'author', 'identifiers': [
            {'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/01234567'}]}]}
    monkeypatch.setattr(cat, '_source_with_title_parts', lambda record, cache: source)
    book = {'publisher_book_id': '123', 'source_url': 'https://dash.shueisha.co.jp/bookDetail/index/9784798613161',
        'isbn': '9784798613161', 'title': '小説と同名の漫画', 'credits': [{'role': '著者', 'name': '著者'}],
        'publisher': '集英社', 'label': label}
    assert cat._publisher_edition_match('shueisha-dx', book, {}, {}) is None


@pytest.fixture
def publisher_edition_v2(edition_bundle):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from xml.sax.saxutils import escape
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, _, template = edition_bundle

    def build(*, title='主書名 : 副書名', volume='3', official_title='主書名３ 副書名',
              imprint='角川スニーカー文庫', label='角川スニーカー文庫', label_id=339,
              publisher='KADOKAWA', authority=True, literal='著者 著', extra='', ndc='913.6', publisher_agent=False,
              dct_title=None):
        record = json.loads(json.dumps(template))
        raw = Path(record['provenance']['raw_path'])
        url = record['source_url']
        creator = ('<d:creator><rdf:Description rdf:about="http://id.ndl.go.jp/auth/entity/01234567">'
                   '<f:name>著者</f:name></rdf:Description></d:creator>') if authority else ''
        publisher_xml = ('<d:publisher><f:Agent><f:name>' + escape(publisher) + '</f:name></f:Agent></d:publisher>') if publisher_agent else '<d:publisher>' + escape(publisher) + '</d:publisher>'
        xml = f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
          xmlns:d="http://purl.org/dc/terms/" xmlns:c="http://purl.org/dc/elements/1.1/"
          xmlns:n="http://ndl.go.jp/dcndl/terms/" xmlns:f="http://xmlns.com/foaf/0.1/">
          <n:BibResource rdf:about="{url}#material"><d:title>{escape(dct_title if dct_title is not None else title + ('. ' + volume if volume else ''))}</d:title>
          <c:title><rdf:Description><rdf:value>{escape(title)}</rdf:value></rdf:Description></c:title>
          {('<n:volume>' + escape(volume) + '</n:volume>') if volume else ''}{creator}
          {('<c:creator>' + escape(literal) + '</c:creator>') if literal else ''}
          {publisher_xml}<d:language>jpn</d:language>
          <n:seriesTitle>{escape(imprint)}</n:seriesTitle><d:identifier rdf:datatype="ISBN">9784798613161</d:identifier>
          <d:subject rdf:resource="http://id.ndl.go.jp/class/ndc10/{ndc}"/>{extra}</n:BibResource></rdf:RDF>'''
        raw.write_text(xml, encoding='utf-8')
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()
        source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
            '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
        record.update(source_record=source, title=source['titles'][0]['value'], volume=source['volume'],
            authors=[a['name'] for a in source['contributors'] if a['role'] == 'author'],
            publisher=source['publisher'], date=source['publication_year'],
            isbn=[i['value'] for i in source['identifiers'] if i['source'] == 'isbn'],
            label=source['series'], ndc=[ndc], label_evidence=[{'query_term': imprint}])
        record['provenance']['raw_sha256'] = digest
        book = {'publisher_book_id': 'kadokawa:123456789012', 'source_url': 'https://www.kadokawa.co.jp/product/123456789012/',
            'isbn': '9784798613161', 'title': official_title, 'credits': [{'role': '著者', 'name': '著者'}],
            'publisher': 'KADOKAWA', 'label': label, 'official_label_id': label_id, 'product_form_id': 3}
        return cat, record, book
    return build


def test_publisher_edition_v1_proof_is_unchanged(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='主書名', volume=None, official_title='主書名')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) == {
        'rule_version': 'publisher-ndl-edition-confirmation-v1', 'scope': 'edition_only',
        'isbn13': '9784798613161', 'matched_ndl_title': '主書名',
        'authors': [{'official_credit': '著者', 'ndl_authorities': ['http://id.ndl.go.jp/auth/entity/01234567']}],
        'ndl_imprint': '角川スニーカー文庫', 'official_label': '角川スニーカー文庫',
        'ndl_publisher': 'KADOKAWA', 'original_work_identity_asserted': False,
        'story_series_identity_asserted': False, 'sources': [record['source_url'], book['source_url']]}


@pytest.mark.parametrize('official_title,version', [('主書名３ 副書名', 'v2'), ('主書名 副書名３', 'v1')])
def test_publisher_edition_v2_composes_all_title_parts_with_exact_volume(publisher_edition_v2, official_title, version):
    cat, record, book = publisher_edition_v2(official_title=official_title)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-' + version
    if version == 'v2':
        assert proof['title_match']['volume'] == '3' and proof['title_match']['subtitle'] == '副書名'
        assert proof['title_match']['locators'] and not proof['story_series_identity_asserted']
    book['title'] = official_title.replace('３', '２')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_publisher_edition_v2_wave_decoration_keeps_complete_subtitle(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='17歳と、18以上 : 後輩ギャル', volume=None,
        official_title='17歳と、18以上 ～後輩ギャル～')
    assert cat._publisher_edition_match('kadokawa', book, record, {})['title_match']['subtitle'] == '後輩ギャル'
    book['title'] = '17歳と、18以上 ～別の後輩ギャル～'
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_publisher_edition_v2_never_uses_contents_title(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='本の正式題', volume=None, official_title='別作品 ～副題～',
        extra='<d:tableOfContents><rdf:Description><d:title>別作品 : 副題</d:title></rdf:Description></d:tableOfContents>')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_publisher_edition_v2_never_uses_contents_isbn_or_electronic_format(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(extra='<d:tableOfContents><rdf:Description>'
        '<d:identifier rdf:datatype="ISBN">9784041173701</d:identifier></rdf:Description></d:tableOfContents>')
    book['isbn'] = '9784041173701'
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None
    book['isbn'] = '9784798613161'
    book['product_form_id'] = 13
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


@pytest.mark.parametrize('change,accepted', [({}, True), ({'label_id': 330}, False),
    ({'publisher': '別出版社'}, False), ({'imprint': '富士見ファンタジア文庫コミックス'}, False)])
def test_publisher_edition_v2_fantasia_pair_is_edition_scoped(publisher_edition_v2, change, accepted):
    options = dict(title='主書名', volume=None, official_title='主書名', imprint='富士見ファンタジア文庫',
        label='ファンタジア文庫', label_id=344)
    options.update(change)
    cat, record, book = publisher_edition_v2(**options)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert bool(proof) is accepted
    if proof:
        assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-v2'
        assert proof['imprint_match']['official_label_id'] == 344
        assert proof['scope'] == 'edition_only'


@pytest.mark.parametrize('literal,accepted', [('著者 著', True), ('著者', False), ('著者 イラスト', False)])
def test_publisher_edition_v2_missing_authority_requires_explicit_writer_literal(publisher_edition_v2, literal, accepted):
    cat, record, book = publisher_edition_v2(title='主書名', volume=None, official_title='主書名', authority=False, literal=literal)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert bool(proof) is accepted
    if proof:
        assert proof['authors'][0]['authority_status'] == 'unknown'
        assert proof['authors'][0]['ndl_authorities'] == []
        assert proof['authors'][0]['ndl_literal_credit'] == '著者 著'


def test_publisher_edition_v2_unknown_authority_cannot_replace_publisher_identity(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='主書名', volume=None, official_title='主書名',
        authority=False, publisher='別出版社')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_existing_v2_proof_shape_values_and_selection_remain_stable(publisher_edition_v2):
    cat, record, book = publisher_edition_v2()
    base = 'records[1]/recordData/dcndl:BibResource[@rdf:about=' + record['source_url'] + '#material]'
    title_locator = base + '/{http://purl.org/dc/terms/}title[1]'
    volume_locator = base + '/{http://ndl.go.jp/dcndl/terms/}volume[3]'
    assert cat._publisher_edition_match('kadokawa', book, record, {}) == {
        'rule_version': 'publisher-ndl-edition-confirmation-v2', 'scope': 'edition_only',
        'isbn13': '9784798613161', 'matched_ndl_title': '主書名 : 副書名. 3',
        'title_match': {'rule': 'primary_isbd_title_and_explicit_volume', 'source_value': '主書名 : 副書名. 3',
            'main': '主書名', 'subtitle': '副書名', 'volume': '3', 'locators': [title_locator, volume_locator]},
        'authors': [{'official_credit': '著者', 'ndl_authorities': ['http://id.ndl.go.jp/auth/entity/01234567'],
            'authority_status': 'known', 'ndl_literal_credit': '著者 著',
            'locator': base + '/{http://purl.org/dc/elements/1.1/}creator[5]'}],
        'ndl_imprint': '角川スニーカー文庫', 'official_label': '角川スニーカー文庫',
        'imprint_match': {'rule': 'exact_label', 'ndl_imprint': '角川スニーカー文庫', 'official_label': '角川スニーカー文庫'},
        'ndl_publisher': 'KADOKAWA', 'official_product_form_id': 3,
        'source_raw_sha256': record['provenance']['raw_sha256'],
        'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
        'sources': [record['source_url'], book['source_url']]}


@pytest.mark.parametrize('title', ['副書名 主書名３', '副書名３ 主書名'])
def test_publisher_edition_v3_reverse_preserves_every_title_part_and_volume(publisher_edition_v2, title):
    cat, record, book = publisher_edition_v2(official_title=title)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-v3'
    assert proof['title_match']['rule'] == 'primary_isbd_reverse_title_and_explicit_volume_v1'
    assert proof['title_match']['main'] == '主書名' and proof['title_match']['subtitle'] == '副書名'
    assert proof['title_match']['volume'] == '3' and len(proof['title_match']['locators']) == 2
    for false_title in (title.replace('３', '２'), title.replace('副書名', ''), title.replace('主書名', '別書名')):
        book['title'] = false_title
        assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_publisher_edition_v3_contents_and_unapproved_writer_roles_remain_held(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='正式主書名 : 正式副書名', official_title='副書名 主書名３',
        extra='<d:tableOfContents><rdf:Description><d:title>主書名 : 副書名. 3</d:title></rdf:Description></d:tableOfContents>')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None
    cat, record, book = publisher_edition_v2(official_title='副書名 主書名３')
    for role in ('Author', '編者', 'イラスト'):
        book['credits'][0]['role'] = role
        assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def own_part_title(value='完全巻題', *, description_extra='', attributes=''):
    return ('<n:partInformation' + attributes + '><rdf:Description>'
        '<d:title>' + value + '</d:title>' + description_extra + '</rdf:Description></n:partInformation>')


@pytest.mark.parametrize('volume', ['3', 'ep.5', '不可解02', 'Logic.5'])
def test_kadokawa_v4_composes_only_the_unique_own_part_information_title(publisher_edition_v2, volume):
    cat, record, book = publisher_edition_v2(title='正式主題', volume=volume,
        official_title='正式主題' + volume + ' 完全巻題', extra=own_part_title())
    cache = {}
    parts = cat._edition_primary_bibliographic_parts(record, cache)
    assert [r['value'] for r in parts['titles']] == ['正式主題. ' + volume, '正式主題']
    assert parts['own_part_information_count'] == 1
    assert parts['own_part_information_structure_valid'] is True
    part = parts['own_part_information_titles'][0]
    assert part['value'] == '完全巻題'
    assert '/{http://ndl.go.jp/dcndl/terms/}partInformation[' in part['part_information_locator']
    assert part['description_locator'] == part['part_information_locator'] + '/{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description[1]'
    assert part['locator'] == part['description_locator'] + '/{http://purl.org/dc/terms/}title[1]'
    proof = cat._publisher_edition_match('kadokawa', book, record, cache)
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-v4'
    assert proof['scope'] == 'edition_only'
    title = proof['title_match']
    assert title['main'] == '正式主題' and title['volume'] == volume
    assert title['part_information_title'] == part
    assert title['composed_title'] == '正式主題' + volume + '完全巻題'
    assert title['scope'] == 'own_primary_and_unique_contained_part_title_to_official_edition_title'
    assert part['locator'] in title['locators']
    assert parts['titles'][0]['locator'] in title['locators']
    assert parts['titles'][1]['locator'] in title['locators']
    assert parts['volume']['locator'] in title['locators']
    assert not proof['part_title_is_volume_title_asserted']
    assert not proof['product_sku_identity_asserted']
    assert not proof['original_work_identity_asserted']
    assert not proof['story_series_identity_asserted']


@pytest.mark.parametrize('extra', [
    own_part_title() + own_part_title('別の短編'),
    own_part_title() * 7,
    '<d:tableOfContents>' + own_part_title() + '</d:tableOfContents>',
    '<d:hasPart><n:BibResource rdf:about="https://example.com/other">' + own_part_title() + '</n:BibResource></d:hasPart>',
    own_part_title(attributes=' rdf:resource="https://example.com/other"'),
    '<n:partInformation><rdf:Description rdf:about="https://example.com/other"><d:title>完全巻題</d:title></rdf:Description></n:partInformation>',
    '<n:partInformation><rdf:Description><d:title>完全巻題</d:title></rdf:Description><rdf:Description><d:title>別題</d:title></rdf:Description></n:partInformation>',
    own_part_title(description_extra='<d:title>別題</d:title>'),
    '<n:partInformation><rdf:Description><d:tableOfContents><d:title>完全巻題</d:title></d:tableOfContents></rdf:Description></n:partInformation>',
    '<n:partInformation><rdf:Description><c:title>完全巻題</c:title></rdf:Description></n:partInformation>',
    '<d:partInformation><rdf:Description><d:title>完全巻題</d:title></rdf:Description></d:partInformation>',
    '<n:partInformation><rdf:Description><d:title rdf:resource="https://example.com/other">完全巻題</d:title></rdf:Description></n:partInformation>',
    '<n:partInformation><rdf:Description><d:title><n:BibResource><d:title>完全巻題</d:title></n:BibResource></d:title></rdf:Description></n:partInformation>',
    '<n:partInformation>不明な追加語<rdf:Description><d:title>完全巻題</d:title></rdf:Description></n:partInformation>',
    '<n:partInformation><rdf:Description>不明な追加語<d:title>完全巻題</d:title></rdf:Description></n:partInformation>',
    '<n:partInformation><rdf:Description><d:title>完全巻題</d:title>不明な追加語</rdf:Description></n:partInformation>',
])
def test_kadokawa_v4_holds_multiple_nested_external_and_untyped_part_titles(publisher_edition_v2, extra):
    cat, record, book = publisher_edition_v2(title='正式主題', official_title='正式主題３ 完全巻題', extra=extra)
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


@pytest.mark.parametrize('official', ['正式主題２ 完全巻題', '正式主題３', '正式主題 完全巻題',
    '正式主題３ 別の巻題', '正式主題３ 完全巻題 特装版', '別主題３ 完全巻題'])
def test_kadokawa_v4_never_drops_title_words_volume_or_edition_markers(publisher_edition_v2, official):
    # An exact old primary title remains an old success, so use a non-primary
    # bare-volume variant for this fallback-specific missing-word negative.
    cat, record, book = publisher_edition_v2(title='正式主題', official_title=official, extra=own_part_title())
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    if official == '正式主題３':
        assert proof['rule_version'] != 'publisher-ndl-edition-confirmation-v4'
    else:
        assert proof is None


@pytest.mark.parametrize('change', ['missing_volume', 'alternate_dc_title', 'alternate_dct_title',
    'wrong_publisher', 'sales_only_publisher', 'nonfiction', 'foreign_language', 'wrong_isbn',
    'electronic', 'unknown_writer', 'Author', 'other_provider', 'wrong_imprint'])
def test_kadokawa_v4_keeps_all_existing_edition_gates(publisher_edition_v2, change):
    options = dict(title='正式主題', official_title='正式主題３ 完全巻題', extra=own_part_title())
    if change == 'missing_volume': options['volume'] = None
    elif change == 'alternate_dc_title': options['extra'] += '<c:title>別の主題</c:title>'
    elif change == 'alternate_dct_title': options['extra'] += '<d:title>別の主題. 3</d:title>'
    elif change == 'wrong_publisher': options['publisher'] = '富士見書房'
    elif change == 'sales_only_publisher':
        options['extra'] += '<d:publisher><f:Agent><f:name>KADOKAWA</f:name><d:description>発売</d:description></f:Agent></d:publisher>'
    elif change == 'nonfiction': options['ndc'] = '726.1'
    elif change == 'foreign_language': options['extra'] += '<d:language>eng</d:language>'
    elif change == 'unknown_writer': options.update(authority=False, literal='著者 イラスト')
    elif change == 'wrong_imprint': options['imprint'] = '別レーベル'
    cat, record, book = publisher_edition_v2(**options)
    if change == 'foreign_language': record['languages'] = []
    provider = 'kadokawa'
    if change == 'wrong_isbn': book['isbn'] = '9784041173701'
    elif change == 'electronic': book['product_form_id'] = 13
    elif change == 'Author': book['credits'][0]['role'] = 'Author'
    elif change == 'other_provider': provider = 'shueisha-dx'
    assert cat._publisher_edition_match(provider, book, record, {}) is None


def test_kadokawa_v4_unknown_authority_stays_unknown_and_fantasia_pair_stays_edition_scoped(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='正式主題', official_title='正式主題３ 完全巻題',
        authority=False, extra=own_part_title(), imprint='富士見ファンタジア文庫', label='ファンタジア文庫', label_id=344)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert proof['rule_version'].endswith('-v4')
    assert proof['authors'][0]['authority_status'] == 'unknown'
    assert proof['imprint_match']['scope'] == 'this_isbn_edition_only'
    book['official_label_id'] = 330
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_kadokawa_v4_space_volume_suffix_is_bound_by_own_dc_and_literal_volume(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='正式主題!?', dct_title='正式主題!? 3',
        official_title='正式主題!?３ 完全巻題', extra=own_part_title())
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert proof['rule_version'].endswith('-v4')
    assert proof['title_match']['source_value'] == '正式主題!? 3'
    assert proof['title_match']['volume'] == '3'


@pytest.mark.parametrize('complete', ['正式主題. 2', '正式主題 = English title. 3',
    '正式主題 : 別の副題. 3', '別の主題. 3', '正式主題'])
def test_kadokawa_v4_own_dc_main_dct_and_explicit_volume_must_agree(publisher_edition_v2, complete):
    cat, record, book = publisher_edition_v2(title='正式主題', dct_title=complete,
        official_title='正式主題３ 完全巻題', extra=own_part_title())
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_kadokawa_v4_cannot_hide_a_duplicate_own_volume(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='正式主題', official_title='正式主題３ 完全巻題',
        extra=own_part_title() + '<n:volume>3</n:volume>')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_kadokawa_v4_pure_title_helper_does_not_read_files(publisher_edition_v2, monkeypatch):
    from recommend_book.light_novel_kadokawa_parts_match_v1 import match_unique_own_part_information_title
    cat, record, book = publisher_edition_v2(title='正式主題', official_title='正式主題３ 完全巻題', extra=own_part_title())
    parts = cat._edition_primary_bibliographic_parts(record, {})
    monkeypatch.setattr('builtins.open', lambda *args, **kwargs: pytest.fail('unexpected I/O'))
    assert match_unique_own_part_information_title(parts, book['title']) is not None
    parts['own_part_information_titles'][0]['locator'] = parts['titles'][0]['locator']
    assert match_unique_own_part_information_title(parts, book['title']) is None


def test_v4_optional_parts_cannot_replace_old_v1_v2_v3_proofs(publisher_edition_v2, monkeypatch):
    from recommend_book import light_novel_kadokawa_parts_match_v1 as matcher
    monkeypatch.setattr(matcher, 'match_unique_own_part_information_title',
        lambda *args: pytest.fail('new fallback must not replace an old proof'))
    for title, official, version in [('主書名', '主書名', 'v1'),
        ('主書名 : 副書名', '主書名３ 副書名', 'v2'), ('主書名 : 副書名', '副書名 主書名３', 'v3')]:
        cat, record, book = publisher_edition_v2(title=title, volume=None if version=='v1' else '3',
            official_title=official, extra=own_part_title())
        assert cat._publisher_edition_match('kadokawa', book, record, {})['rule_version'].endswith(version)


def test_existing_v3_complete_proof_is_unchanged(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(official_title='副書名 主書名３')
    base = 'records[1]/recordData/dcndl:BibResource[@rdf:about=' + record['source_url'] + '#material]'
    assert cat._publisher_edition_match('kadokawa', book, record, {}) == {
        'rule_version':'publisher-ndl-edition-confirmation-v3','scope':'edition_only','isbn13':'9784798613161',
        'matched_ndl_title':'主書名 : 副書名. 3', 'title_match':{
            'rule':'primary_isbd_reverse_title_and_explicit_volume_v1','source_value':'主書名 : 副書名. 3',
            'main':'主書名','subtitle':'副書名','volume':'3','locators':[
                base+'/{http://purl.org/dc/terms/}title[1]',base+'/{http://ndl.go.jp/dcndl/terms/}volume[3]']},
        'authors':[{'official_credit':'著者','ndl_authorities':['http://id.ndl.go.jp/auth/entity/01234567'],
            'authority_status':'known','ndl_literal_credit':'著者 著','locator':base+'/{http://purl.org/dc/elements/1.1/}creator[5]'}],
        'ndl_imprint':'角川スニーカー文庫','official_label':'角川スニーカー文庫',
        'imprint_match':{'rule':'exact_label','ndl_imprint':'角川スニーカー文庫','official_label':'角川スニーカー文庫'},
        'ndl_publisher':'KADOKAWA','official_product_form_id':3,'source_raw_sha256':record['provenance']['raw_sha256'],
        'original_work_identity_asserted':False,'story_series_identity_asserted':False,
        'sources':[record['source_url'],book['source_url']]}


def kadokawa_owner_context(book):
    book.update(catalogue_owner='KADOKAWA', publisher_field_basis='catalogue_owner_context', individual_publisher=None,
        evidence={'source_url':'https://www.kadokawa.co.jp/product/search/', 'source_sha256':'e'*64,
            'json_pointer':'/result/book/0', 'raw_path':'private-source-must-not-be-in-context-proof'})
    return book


@pytest.mark.parametrize('publisher', ['アスキー・メディアワークス','エンターブレイン','角川学芸出版',
    '角川書店','角川プロダクション','角川マガジンズ','中経出版','富士見書房','メディアファクトリー'])
def test_kadokawa_v5_uses_assessed_owner_context_without_changing_actual_publisher(publisher_edition_v2, publisher):
    cat, record, book = publisher_edition_v2(publisher=publisher)
    kadokawa_owner_context(book)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-v5'
    assert proof['scope'] == 'edition_only' and proof['confirmed_scope'] == 'contained_bibliographic_edition_only'
    assert proof['ndl_publisher'] == publisher
    context = proof['publisher_context_match']
    assert context['catalogue_owner'] == 'KADOKAWA'
    assert context['individual_publisher'] is None
    assert context['ndl_publisher_policy']['publisher_agent']['name'] == publisher
    assert context['history_context']['announcement_date'] == '2013-10-01'
    assert context['history_context']['source_url'] == 'https://group.kadokawa.co.jp/documents/topics/20131001_kado.pdf'
    assert context['history_context']['source_sha256'] == '0582e31c8b292b13042afd9d143c377ef5c528cfd3c409af0be04e8ffc220189'
    assert context['history_context']['source_pages'] == [1]
    assert not proof['publisher_identity_asserted'] and not proof['global_publisher_alias_asserted']
    assert not proof['label_history_map_asserted']
    assert not proof['product_sku_identity_asserted']
    assert not proof['original_work_identity_asserted'] and not proof['story_series_identity_asserted']
    assert 'private-source-must-not-be-in-context-proof' not in json.dumps(context)
    assert 'raw_path' not in json.dumps(context) and 'pdf_path' not in json.dumps(context)


@pytest.mark.parametrize('role', ['発売','頒布'])
@pytest.mark.parametrize('publisher', ['メディアファクトリー','KADOKAWA'])
def test_kadokawa_v5_accepts_only_explicit_sales_distribution_beside_sole_publisher(publisher_edition_v2, role, publisher):
    extra = '<d:publisher><f:Agent><f:name>別販売社</f:name><d:description>' + role + '</d:description></f:Agent></d:publisher>'
    cat, record, book = publisher_edition_v2(publisher=publisher, publisher_agent=True, extra=extra)
    kadokawa_owner_context(book)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert proof['rule_version'].endswith('-v5')
    assert proof['ndl_publisher'] == publisher + '; 別販売社'
    rows = proof['publisher_context_match']['ndl_publisher_policy']['distribution_agents']
    assert rows[0]['name'] == '別販売社' and rows[0]['role'] == role
    assert '/{http://purl.org/dc/terms/}description[' in rows[0]['description_locator']


def test_kadokawa_v5_fantasia_pair_is_only_this_isbn_edition_and_keeps_both_labels(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(publisher='富士見書房', imprint='富士見ファンタジア文庫',
        label='ファンタジア文庫', label_id=344)
    kadokawa_owner_context(book)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert proof['rule_version'].endswith('-v5')
    assert proof['imprint_match']['scope'] == 'this_isbn_edition_only'
    assert proof['imprint_match']['ndl_imprint'] == '富士見ファンタジア文庫'
    assert proof['imprint_match']['official_label'] == 'ファンタジア文庫'
    assert not proof['imprint_match']['global_alias_asserted']
    assert not proof['global_label_alias_asserted']
    for bad_id in (None,330):
        book['official_label_id'] = bad_id
        assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_kadokawa_v5_context_helper_is_pure_and_rejects_unbound_role_locators(publisher_edition_v2, monkeypatch):
    from recommend_book.light_novel_kadokawa_publisher_context_v1 import match_publisher_context
    extra='<d:publisher><f:Agent><f:name>別販売社</f:name><d:description>頒布</d:description></f:Agent></d:publisher>'
    cat, record, book=publisher_edition_v2(publisher='メディアファクトリー',publisher_agent=True,extra=extra)
    kadokawa_owner_context(book)
    parts=cat._edition_primary_bibliographic_parts(record,{})
    monkeypatch.setattr('builtins.open',lambda *args,**kwargs:pytest.fail('unexpected I/O'))
    assert match_publisher_context(book,parts) is not None
    parts['publisher_agents'][1]['description_locators'][0]=parts['publisher_agents'][0]['locator']+'/{http://purl.org/dc/terms/}description[1]'
    assert match_publisher_context(book,parts) is None


@pytest.mark.parametrize('change', ['unrelated_publisher','mediaworks_not_named_in_pdf','two_publishers',
    'unknown_additional_role','sales_only','wrong_isbn','wrong_volume','missing_subtitle','unknown_writer',
    'different_writer','additional_unmatched_writer','wrong_imprint','nonfiction','foreign_language',
    'owner_missing','basis_missing','individual_publisher_claimed','context_source_unbound',
    'electronic','part_information_old_publisher','dc_only_cannot_discard_dct_subtitle'])
def test_kadokawa_v5_holds_incomplete_or_contradictory_edition_context(publisher_edition_v2, change):
    options = {'publisher':'メディアファクトリー'}
    if change == 'unrelated_publisher': options['publisher']='無関係出版社'
    elif change == 'mediaworks_not_named_in_pdf': options['publisher']='メディアワークス'
    elif change == 'two_publishers': options['extra']='<d:publisher>角川書店</d:publisher>'
    elif change == 'unknown_additional_role': options['extra']='<d:publisher><f:Agent><f:name>別社</f:name><d:description>不明</d:description></f:Agent></d:publisher>'
    elif change == 'sales_only':
        options['publisher_agent']=True
        options['publisher']='無関係出版社'
        options['extra']='<d:publisher><f:Agent><f:name>メディアファクトリー</f:name><d:description>発売</d:description></f:Agent></d:publisher>'
    elif change == 'unknown_writer': options.update(authority=False,literal='著者 イラスト')
    elif change == 'wrong_imprint': options['imprint']='別レーベル'
    elif change == 'nonfiction': options['ndc']='726.1'
    elif change == 'foreign_language': options['extra']='<d:language>eng</d:language>'
    elif change == 'part_information_old_publisher':
        options.update(title='正式主題',official_title='正式主題３ 完全巻題',extra=own_part_title())
    elif change == 'dc_only_cannot_discard_dct_subtitle':
        options.update(title='主書名',dct_title='主書名 : 欠かせない副書名. 3',official_title='主書名３')
    cat, record, book = publisher_edition_v2(**options)
    kadokawa_owner_context(book)
    if change == 'wrong_isbn': book['isbn']='9784041173701'
    elif change == 'wrong_volume': book['title']='主書名２ 副書名'
    elif change == 'missing_subtitle': book['title']='主書名３'
    elif change == 'different_writer': book['credits'][0]['name']='別人'
    elif change == 'additional_unmatched_writer': book['credits'].append({'role':'著者','name':'別人'})
    elif change == 'owner_missing': book.pop('catalogue_owner')
    elif change == 'basis_missing': book.pop('publisher_field_basis')
    elif change == 'individual_publisher_claimed': book['individual_publisher']='KADOKAWA'
    elif change == 'context_source_unbound': book['evidence']['source_sha256']=''
    elif change == 'electronic': book['product_form_id']=13
    if change == 'foreign_language': record['languages']=[]
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_kadokawa_v5_does_not_change_old_direct_publisher_success_or_v1_to_v4_proofs(publisher_edition_v2, monkeypatch):
    cat, record, book = publisher_edition_v2(publisher='メディアファクトリー',title='主書名',volume=None,official_title='主書名')
    before=cat._publisher_edition_match('kadokawa',book,record,{})
    assert before['rule_version'].endswith('-v1')
    kadokawa_owner_context(book)
    assert cat._publisher_edition_match('kadokawa',book,record,{}) == before
    from recommend_book import light_novel_kadokawa_publisher_context_v1 as context
    monkeypatch.setattr(context,'match_publisher_context',lambda *args: pytest.fail('old proof must take precedence'))
    for title,official,version,extra in [('主書名','主書名','v1',''),
        ('主書名 : 副書名','主書名３ 副書名','v2',''),('主書名 : 副書名','副書名 主書名３','v3',''),
        ('主書名','主書名３ 完全巻題','v4',own_part_title())]:
        cat,record,book=publisher_edition_v2(title=title,volume=None if version=='v1' else '3',official_title=official,extra=extra)
        before=cat._publisher_edition_match('kadokawa',book,record,{})
        kadokawa_owner_context(book)
        assert cat._publisher_edition_match('kadokawa',book,record,{}) == before
        assert before['rule_version'].endswith('-'+version)


@pytest.fixture
def publisher_confirmation_pair(publisher_edition_v2, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter, SRU
    from recommend_book.ingestion.core import RawArtifact

    def build(*, shared_raw=False, second_isbn='9784041173701'):
        cat, first, book = publisher_edition_v2(title='主書名', volume=None, official_title='主書名')
        first_xml = Path(first['provenance']['raw_path']).read_text(encoding='utf-8')
        second_xml = first_xml.replace('R100000002-I1', 'R100000002-I2').replace('9784798613161', second_isbn)
        if shared_raw:
            combined = ET.Element(f'{{{SRU}}}searchRetrieveResponse')
            for xml in (first_xml, second_xml):
                data = ET.SubElement(combined, f'{{{SRU}}}recordData')
                data.append(ET.fromstring(xml))
            shared_path = tmp_path / 'two-editions.xml'
            shared_path.write_bytes(ET.tostring(combined, encoding='utf-8'))
        records, paths = [], []
        for index, xml in enumerate((first_xml, second_xml), 1):
            record = json.loads(json.dumps(first))
            raw = shared_path if shared_raw else tmp_path / f'one-edition-{index}.xml'
            if not shared_raw:
                raw.write_text(xml, encoding='utf-8')
            digest = hashlib.sha256(raw.read_bytes()).hexdigest()
            source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
                '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac',
                locator=f'records[{index if shared_raw else 1}]/recordData').model_dump(mode='json')
            record.update(id=source['source_id'], source_url=source['source_url'], source_record=source,
                isbn=[i['value'] for i in source['identifiers'] if i['source'] == 'isbn'])
            record['provenance'].update(url=source['source_url'], raw_path=str(raw), raw_sha256=digest)
            records.append(record)
            paths.append(raw)
        assert cat.import_editions(records)['inserted'] == 2
        return cat, records, book, paths
    return build


def test_book_confirmation_reference_scope_excludes_unselected_raw(publisher_confirmation_pair, monkeypatch, tmp_path):
    cat, _, book, paths = publisher_confirmation_pair()
    other = {**book, 'publisher_book_id': 'kadokawa:unselected', 'isbn': '9784041173701'}
    paths[1].write_bytes(b'unselected raw must not be opened')
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: [book, other])
    result = cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64,
        reference_ids={book['publisher_book_id']})
    assert result['confirmed'] == result['ndl_candidate_editions_verified'] == 1
    assert result['available_publisher_books'] == 2
    assert result['publisher_books'] == result['requested_publisher_books'] == 1
    assert result['unmatched_publisher_books'] == 0
    with cat._connect() as db:
        assert db.execute('SELECT edition_id FROM edition_book_confirmations').fetchone()[0] == 'lne:ndl:R100000002-I1'


@pytest.mark.parametrize('scope', [set(), {'missing'}])
def test_book_confirmation_empty_or_unknown_scope_never_opens_raw(publisher_confirmation_pair, monkeypatch, tmp_path, scope):
    cat, _, book, paths = publisher_confirmation_pair()
    for path in paths:
        path.write_bytes(b'raw must not be opened')
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: [book])
    if scope:
        with pytest.raises(ValueError, match='unknown publisher book reference'):
            cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64, reference_ids=scope)
    else:
        result = cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64, reference_ids=scope)
        assert result['confirmed'] == result['ndl_candidate_editions_verified'] == result['publisher_books'] == 0
    with cat._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM edition_book_confirmations').fetchone()[0] == 0


def test_book_confirmation_duplicate_source_ids_are_rejected_before_scope(publisher_confirmation_pair, monkeypatch, tmp_path):
    cat, _, book, paths = publisher_confirmation_pair()
    for path in paths:
        path.write_bytes(b'raw must not be opened')
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: [book, {**book, 'isbn': '9784041173701'}])
    with pytest.raises(ValueError, match='duplicate publisher book reference'):
        cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64,
            reference_ids={book['publisher_book_id']})


def test_book_confirmation_scoped_and_default_proofs_are_identical(publisher_confirmation_pair, monkeypatch, tmp_path):
    cat, _, book, _ = publisher_confirmation_pair()
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: [book])
    cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64,
        reference_ids={book['publisher_book_id']})
    with cat._connect() as db:
        before = tuple(db.execute('SELECT * FROM edition_book_confirmations').fetchone())
    result = cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64)
    assert result['unchanged'] == 1 and 'requested_publisher_books' not in result
    with cat._connect() as db:
        assert tuple(db.execute('SELECT * FROM edition_book_confirmations').fetchone()) == before


def test_book_confirmation_opens_only_requested_isbn_candidates(publisher_confirmation_pair, monkeypatch, tmp_path):
    cat, _, book, paths = publisher_confirmation_pair()
    paths[1].write_bytes(b'unrelated raw is unavailable')
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: [book])
    result = cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64)
    assert result['confirmed'] == 1
    with cat._connect() as db:
        assert db.execute('SELECT edition_id FROM edition_book_confirmations').fetchone()[0] == 'lne:ndl:R100000002-I1'


@pytest.mark.parametrize('tamper', ['sha', 'typed_id', 'rehashed_projection'])
def test_book_confirmation_rejects_target_projection_tampering(publisher_confirmation_pair, monkeypatch, tmp_path, tamper):
    cat, records, book, _ = publisher_confirmation_pair()
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: [book])
    with cat._connect() as db:
        if tamper == 'sha':
            db.execute('UPDATE entries SET record_sha256=? WHERE entry_id=?', ('f' * 64, 'lne:ndl:R100000002-I1'))
        elif tamper == 'typed_id':
            db.execute('UPDATE entries SET entry_id=? WHERE entry_id=?', ('lne:ndl:R100000002-Ifake', 'lne:ndl:R100000002-I1'))
        else:
            records[0]['publisher'] = '偽出版社'
            encoded = json.dumps(records[0], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
                (encoded, hashlib.sha256(encoded.encode()).hexdigest(), 'lne:ndl:R100000002-I1'))
    with pytest.raises(ValueError):
        cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64)


def test_book_confirmation_revalidates_second_record_even_when_raw_is_cached(publisher_confirmation_pair, monkeypatch, tmp_path):
    cat, records, book, _ = publisher_confirmation_pair(shared_raw=True, second_isbn='9784798613161')
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: [book])
    records[1]['publisher'] = '偽出版社'
    encoded = json.dumps(records[1], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with cat._connect() as db:
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), 'lne:ndl:R100000002-I2'))
    with pytest.raises(ValueError, match='fields differ'):
        cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM edition_book_confirmations').fetchone()[0] == 0


@pytest.fixture
def iris_neo_edition(publisher_edition_v2):
    def build(**changes):
        options = dict(title='四竜帝の大陸', volume=None, official_title='四竜帝の大陸',
            imprint='IRIS NEO', publisher='一迅社', label='アイリスNEO')
        options.update(changes)
        cat, record, book = publisher_edition_v2(**options)
        book['publisher'] = '一迅社'
        return cat, record, book
    return build


def test_iris_neo_bridge_is_same_isbn_edition_only_with_known_authority(iris_neo_edition):
    cat, record, book = iris_neo_edition()
    proof = cat._publisher_edition_match('ichijinsha-iris', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v1'
    assert proof['scope'] == 'edition_only' and proof['isbn13'] == '9784798613161'
    assert proof['imprint_match'] == {'rule': 'same_edition_ichijinsha_iris_neo_pair',
        'official_label': 'アイリスNEO', 'ndl_imprint': 'IRIS NEO', 'scope': 'this_isbn_edition_only'}
    assert proof['authors'][0]['ndl_authorities'] == ['http://id.ndl.go.jp/auth/entity/01234567']
    assert proof['primary_title_locator'] and not proof['original_work_identity_asserted']
    assert not proof['story_series_identity_asserted']


@pytest.mark.parametrize('change', [{'publisher': '別出版社'}, {'label': '一迅社文庫アイリス'},
    {'imprint': 'IRIS NEOアンソロジー'}, {'authority': False}, {'ndc': '726.5'},
    {'title': '四竜帝の大陸 : 省略不可の副題'},
    {'title': '別の正式題', 'extra': '<d:tableOfContents><rdf:Description><d:title>四竜帝の大陸</d:title></rdf:Description></d:tableOfContents>'}])
def test_iris_neo_bridge_rejects_different_scope_or_incomplete_evidence(iris_neo_edition, change):
    cat, record, book = iris_neo_edition(**change)
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None


def test_iris_neo_bridge_rejects_wrong_isbn_provider_or_editor(iris_neo_edition):
    cat, record, book = iris_neo_edition()
    book['isbn'] = '9784041173701'
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None
    book['isbn'] = '9784798613161'
    assert cat._publisher_edition_match('shueisha-dx', book, record, {}) is None
    book['credits'][0]['role'] = '編者'
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None


def test_iris_neo_original_bridge_proof_remains_unchanged(iris_neo_edition):
    cat, record, book = iris_neo_edition()
    locator = 'records[1]/recordData/dcndl:BibResource[@rdf:about=' + record['source_url'] + '#material]/{http://purl.org/dc/terms/}title[1]'
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) == {
        'rule_version': 'publisher-ndl-edition-confirmation-iris-neo-v1', 'scope': 'edition_only',
        'isbn13': '9784798613161', 'matched_ndl_title': '四竜帝の大陸', 'primary_title_locator': locator,
        'source_raw_sha256': record['provenance']['raw_sha256'],
        'authors': [{'official_credit': '著者', 'ndl_authorities': ['http://id.ndl.go.jp/auth/entity/01234567']}],
        'ndl_imprint': 'IRIS NEO', 'official_label': 'アイリスNEO', 'ndl_publisher': '一迅社',
        'imprint_match': {'rule': 'same_edition_ichijinsha_iris_neo_pair', 'official_label': 'アイリスNEO',
            'ndl_imprint': 'IRIS NEO', 'scope': 'this_isbn_edition_only'},
        'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
        'sources': [record['source_url'], book['source_url']]}


def test_iris_neo_explicit_distribution_role_preserves_original_publisher_values(iris_neo_edition):
    extra = '<d:publisher><f:Agent><f:name>講談社</f:name><d:description>頒布</d:description></f:Agent></d:publisher>'
    cat, record, book = iris_neo_edition(publisher_agent=True, extra=extra)
    proof = cat._publisher_edition_match('ichijinsha-iris', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v2'
    assert proof['ndl_publisher'] == record['source_record']['publisher'] == '一迅社; 講談社'
    assert proof['publisher_match']['rule'] == 'unique_publisher_and_explicit_distribution_agents'
    assert proof['publisher_match']['distribution_agents'][0]['name'] == '講談社'
    assert proof['publisher_match']['distribution_agents'][0]['description'] == '頒布'
    assert proof['publisher_match']['distribution_agents'][0]['description_locator']


@pytest.mark.parametrize('extra', [
    '<d:publisher><f:Agent><f:name>講談社</f:name></f:Agent></d:publisher>',
    '<d:publisher><f:Agent><f:name>講談社</f:name><d:description>出版</d:description></f:Agent></d:publisher>',
    '<d:publisher>講談社</d:publisher>',
    '<d:publisher><f:Agent><f:name>一迅社</f:name></f:Agent></d:publisher><d:publisher><f:Agent><f:name>講談社</f:name><d:description>頒布</d:description></f:Agent></d:publisher>'])
def test_iris_neo_unknown_or_multiple_actual_publishers_remain_held(iris_neo_edition, extra):
    cat, record, book = iris_neo_edition(publisher_agent=True, extra=extra)
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None


@pytest.mark.parametrize('official_title,volume', [('主書名３ 副書名', '3'), ('主書名 副書名３', '3'),
    ('主書名～副書名～', None)])
def test_iris_neo_complete_title_variants_keep_every_part(iris_neo_edition, official_title, volume):
    cat, record, book = iris_neo_edition(title='主書名 : 副書名', official_title=official_title, volume=volume)
    proof = cat._publisher_edition_match('ichijinsha-iris', book, record, {})
    if volume == '3' and official_title == '主書名 副書名３':
        assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v1'
    else:
        assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v2'
        assert proof['title_match']['main'] == '主書名' and proof['title_match']['subtitle'] == '副書名'
        assert proof['title_match']['volume'] == volume
    for false_title in ('主書名', '副書名', '主書名２ 副書名'):
        book['title'] = false_title
        assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None


def test_iris_neo_explicit_volume_subtitle_and_unknown_publisher_are_not_dropped(iris_neo_edition):
    cat, record, book = iris_neo_edition(title='主書名', volume='[2] (巻副題)', official_title='主書名2')
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None
    book['title'] = '主書名 巻副題'
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None
    cat, record, book = iris_neo_edition(title='主書名 : 副書名', volume='3', official_title='主書名３ 副書名',
        publisher_agent=True, extra='<d:publisher><f:Agent/></d:publisher>')
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None


def test_iris_neo_distribution_and_complete_title_extensions_work_together(iris_neo_edition):
    cat, record, book = iris_neo_edition(title='主書名 : 副書名', volume='3', official_title='主書名３～副書名～',
        publisher_agent=True, extra='<d:publisher><f:Agent><f:name>講談社</f:name><d:description>頒布</d:description></f:Agent></d:publisher>')
    proof = cat._publisher_edition_match('ichijinsha-iris', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v2'
    assert proof['publisher_match']['distribution_agents'] and proof['title_match']['volume'] == '3'


@pytest.fixture
def cobalt_member(publisher_edition_v2, tmp_path, monkeypatch):
    cat, record, _ = publisher_edition_v2(publisher='集英社', imprint='コバルト文庫',
        title='巻独自の正式題', volume=None, official_title='巻独自の正式題')
    series_url = 'https://www.shueisha.co.jp/books/search/search.html?seriesid=52670'
    book_url = 'https://ebooks.shueisha.co.jp/detail.php?book=sc1'
    group = {'kind': 'official_series_reference', 'official_series_id': '52670',
        'official_series_url': series_url, 'series_title': '出版社の正式シリーズ名', 'publisher': None, 'label': None,
        'series_author': {'role': '著者', 'display_text': '著者', 'role_source': 'ebook_author_list',
            'role_label_selector': 'h2 img[alt="著者50音別書籍リスト"]', 'author_field_selector': 'dl#word_1 dd[1]>b'},
        'independent_series_evidence': {'source_key': 'cobalt_anniversary', 'selector': 'ul.anthology-list li[1]',
            'label_text': 'シリーズ本編購入はこちら'},
        'ebook_group_evidence': {'source_key': 'ebook_author_list', 'selector': 'dl#word_1 dd[1] span em'},
        'books': [{'title': '巻独自の正式題', 'source_url': book_url, 'publisher_book_id': 'sc1',
            'author_display_text': '著者', 'isbn': None, 'source_selector': 'dl#word_1 dd[2]'}],
        'source_evidence': [{'source_url': 'https://orangebunko.shueisha.co.jp/feature/cobalt50th_specialbooks',
                'raw_sha256': 'a' * 64, 'locator': 'ul.anthology-list li[1]'},
            {'source_url': 'https://ebooks.shueisha.co.jp/cobalt/', 'raw_sha256': 'b' * 64, 'locator': 'dl#word_1'}]}
    import recommend_book.light_novel_cobalt_series_v1 as source
    monkeypatch.setattr(source, 'verify_cobalt_series', lambda path, sha: [group])
    frames = tmp_path / 'empty-frames.json'; fsha = write(frames, [])
    extra = tmp_path / 'cobalt-manifest.json'; esha = write(extra, {})
    assert cat.import_editions([record])['inserted'] == 1
    reference = 'publisher-series:' + hashlib.sha256(series_url.encode()).hexdigest()[:24]
    return cat, record, frames, fsha, extra, esha, reference, group


def test_earthstar_assertion_adapter_pins_supplement_path_and_sha(tmp_path, monkeypatch):
    from recommend_book.light_novel_ndl_series_v1 import publisher_assertions
    import recommend_book.light_novel_earthstar_series_v1 as earthstar
    frames = tmp_path / 'frames.json'
    fsha = write(frames, [])
    first, second = tmp_path / 'first' / 'proof.json', tmp_path / 'second' / 'proof.json'
    first.parent.mkdir(); second.parent.mkdir()
    sha = write(first, {})
    write(second, {})
    group = {'title': '作品グループ名', 'source_url': 'https://earthstar-novel.com/book/detail/group/',
             'index_author_credits': [{'role': 'author', 'name': '著者'}], 'publisher': None, 'label': None}
    verified = []
    def verify(path, digest):
        verified.append((str(path), digest))
        if str(path) == str(second.resolve()):
            raise ValueError('second manifest relative raw is invalid')
        return [group]
    monkeypatch.setattr(earthstar, 'verify_earthstar_series', verify)
    def artifact(path, expected, cache):
        raw = __import__('pathlib').Path(path).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected
        return raw
    proof = {'frames_path': str(frames), 'frames_sha256': fsha, 'source_root': str(tmp_path),
             'earthstar_series_path': str(first.resolve()), 'earthstar_series_sha256': sha}
    cache = {}
    reference = 'publisher-series:' + hashlib.sha256(group['source_url'].encode()).hexdigest()[:24]
    adapted = publisher_assertions(proof, artifact, cache)[reference]
    assert adapted['index_id'] == 'earthstar-explicit-work-group'
    assert adapted['series_title'] == group['title'] and adapted['authors'] == ['著者']
    assert adapted['publisher'] is None and adapted['label'] is None
    with pytest.raises(ValueError, match='relative raw'):
        publisher_assertions({**proof, 'earthstar_series_path': str(second.resolve())}, artifact, cache)
    assert len(verified) == 2
    for key in ('earthstar_series_path', 'earthstar_series_sha256'):
        incomplete = dict(proof); incomplete.pop(key)
        with pytest.raises(ValueError, match='required together'):
            publisher_assertions(incomplete, artifact, cache)


@pytest.fixture
def earthstar_member(publisher_edition_v2, tmp_path, monkeypatch):
    from test_light_novel_earthstar_match_v1 import assertion
    cat, record, _ = publisher_edition_v2(publisher='アース・スターエンターテイメント',
        imprint='EARTH STAR NOVEL', title='巻独自の正式題', volume=None, official_title='巻独自の正式題')
    group = assertion(title='出版社の正式作品グループ名', member='巻独自の正式題')
    group['index_author_credits'][0]['name'] = '著者'
    for role in group['books'][0]['credits'] + group['books'][0]['explicit_role_credit_literals']:
        if role['role'] == 'author':
            role['name'] = '著者'
    import recommend_book.light_novel_earthstar_series_v1 as source
    monkeypatch.setattr(source, 'verify_earthstar_series', lambda path, sha: [group])
    frames = tmp_path / 'empty-earth-frames.json'; fsha = write(frames, [])
    extra = tmp_path / 'earth-proof.json'; esha = write(extra, {})
    assert cat.import_editions([record])['inserted'] == 1
    reference = 'publisher-series:' + hashlib.sha256(group['source_url'].encode()).hexdigest()[:24]
    return cat, record, frames, fsha, extra, esha, reference, group


def test_earthstar_group_is_searchable_without_story_series_or_anchor_metadata_inference(earthstar_member):
    cat, record, frames, sha, extra, esha, reference, group = earthstar_member
    result = cat.import_ndl_series_from_publishers(frames, sha, source_root=frames.parent,
        earthstar_series_path=extra, earthstar_series_sha256=esha, reference_ids={reference})
    assert result['inserted'] == 1 and result['verified_edition_memberships'] == 1
    row = cat.work_detail('lns:ndl:' + record['id'])
    assert row['title'] == group['title'] and row['aliases'] == []
    assert row['publication_label'] is None and row['publishers'] == []
    assert row['series_title_source'] == 'publisher_explicit_work_group'
    assert row['series_identity_basis'] == 'publisher_work_group_and_ndl_bibliography'
    assert row['admission_status'] == 'publisher_work_group_verified'
    assert not row['story_series_identity_asserted'] and not row['original_work_identity_asserted']
    assert row['members'][0]['entry_id'] == 'lne:ndl:' + record['id']
    assert cat.search_catalogue(group['title'], entry_kind='series')['total'] == 1
    member = cat.work_detail('lne:ndl:' + record['id'])
    assert member['series_membership_evidence']['rule_version'] == 'earthstar-explicit-ndl-workgroup-membership-v1'
    assert member['relation_resolution'] == 'publisher_work_group_membership_verified'
    assert member['admission_status'] == 'publisher_work_group_membership_verified'
    assert not member['story_series_identity_asserted'] and not member['original_work_identity_asserted']
    assert member['members'][0]['series_identity_basis'] == 'publisher_work_group_and_ndl_bibliography'
    stats = cat.catalogue_stats()
    assert stats['series'] == 0 and stats['publisher_work_groups'] == 1
    assert stats['verified_edition_series_relations'] == 0 and stats['verified_edition_work_group_relations'] == 1


def test_earthstar_canonical_title_rehash_cannot_publish_forged_group(earthstar_member):
    cat, record, frames, sha, extra, esha, reference, _ = earthstar_member
    assert cat.import_ndl_series_from_publishers(frames, sha, source_root=frames.parent,
        earthstar_series_path=extra, earthstar_series_sha256=esha, reference_ids={reference})['inserted'] == 1
    with cat._connect() as db:
        old = db.execute('SELECT record_json FROM entries WHERE entry_id=?', ('lns:ndl:' + record['id'],)).fetchone()[0]
        forged = json.loads(old); forged['title'] = '偽造された作品グループ'
        encoded = json.dumps(forged, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), 'lns:ndl:' + record['id']))
    with pytest.raises(ValueError, match='search title differs'):
        cat.search_catalogue('偽造された作品グループ', entry_kind='series')


@pytest.mark.parametrize('change', ['canonical_rehash', 'raw_changed', 'typed_identity'])
def test_scoped_completed_work_group_revalidates_before_reporting_unchanged(earthstar_member, change):
    from pathlib import Path
    cat, record, frames, sha, extra, esha, reference, _ = earthstar_member
    kwargs = dict(source_root=frames.parent, earthstar_series_path=extra,
                  earthstar_series_sha256=esha, reference_ids={reference})
    assert cat.import_ndl_series_from_publishers(frames, sha, **kwargs)['inserted'] == 1
    key = 'lns:ndl:' + record['id']
    if change == 'raw_changed':
        raw = Path(record['provenance']['raw_path'])
        raw.write_bytes(raw.read_bytes() + b' changed')
    else:
        with cat._connect() as db:
            if change == 'typed_identity':
                db.execute('UPDATE entries SET kind=? WHERE entry_id=?', ('work', key))
            else:
                saved = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (key,)).fetchone()[0])
                saved['title'] = '原資料にない作品グループ'
                encoded = json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
                           (encoded, hashlib.sha256(encoded.encode()).hexdigest(), key))
    with pytest.raises(ValueError):
        cat.import_ndl_series_from_publishers(frames, sha, **kwargs)


def test_earthstar_group_holds_wikidata_title_overlap(earthstar_member, setup_catalogue):
    cat, edition, frames, sha, extra, esha, reference, group = earthstar_member
    _, records = setup_catalogue
    records[0]['provenance']['conditions_sha256'] = edition['provenance']['conditions_sha256']
    group['title'] = group['series_title'] = records[0]['title']
    assert cat.ingest([records[0]])['inserted'] == 1
    result = cat.import_ndl_series_from_publishers(frames, sha, source_root=frames.parent,
        earthstar_series_path=extra, earthstar_series_sha256=esha, reference_ids={reference})
    assert result['inserted'] == 0
    assert result['held_reasons'] == {'potential_wikidata_series_overlap_requires_authority_review': 1}
    stats = cat.catalogue_stats()
    assert stats['series'] == 1 and stats['publisher_work_groups'] == 0


def test_earthstar_group_holds_existing_anchor_owned_by_another_group(earthstar_member):
    cat, _, frames, sha, extra, esha, reference, group = earthstar_member
    assert cat.import_ndl_series_from_publishers(frames, sha, source_root=frames.parent,
        earthstar_series_path=extra, earthstar_series_sha256=esha, reference_ids={reference})['inserted'] == 1
    group['source_url'] = 'https://earthstar-novel.com/book/detail/another-group/'
    group['official_work_reference_id'] = 'another-group'
    group['reference_id'] = 'earthstar-work:another-group'
    group['title'] = group['series_title'] = '別の公式作品グループ'
    group['source_evidence'][-1]['source_url'] = group['source_url']
    group['books'][0]['source_evidence']['source_url'] = group['source_url']
    second = 'publisher-series:' + hashlib.sha256(group['source_url'].encode()).hexdigest()[:24]
    result = cat.import_ndl_series_from_publishers(frames, sha, source_root=frames.parent,
        earthstar_series_path=extra, earthstar_series_sha256=esha, reference_ids={second})
    assert result['inserted'] == 0
    assert result['held_reasons'] == {'ambiguous_publisher_series_for_same_ndl_anchor': 1}


def test_earthstar_scoped_group_rejects_candidate_typed_identity_mismatch(earthstar_member):
    cat, record, frames, sha, extra, esha, reference, _ = earthstar_member
    with cat._connect() as db:
        db.execute('UPDATE entries SET kind=? WHERE entry_id=?', ('work', 'lne:ndl:' + record['id']))
    with pytest.raises(ValueError, match='typed identity'):
        cat.import_ndl_series_from_publishers(frames, sha, source_root=frames.parent,
            earthstar_series_path=extra, earthstar_series_sha256=esha, reference_ids={reference})


@pytest.mark.parametrize('kwargs', [{'earthstar_series_path': 'missing'}, {'earthstar_series_sha256': 'a' * 64}])
def test_earthstar_supplement_requires_path_sha_pair(publisher_ndl_bundle, kwargs):
    cat, _, frames, sha, root, _ = publisher_ndl_bundle
    with pytest.raises(ValueError, match='required together'):
        cat.import_ndl_series_from_publishers(frames, sha, source_root=root, **kwargs)


@pytest.fixture
def bibliography_bundle(publisher_edition_v2, tmp_path, monkeypatch):
    from recommend_book import light_novel_kodansha_references_v1 as verifier
    cat, record, _ = publisher_edition_v2(publisher='講談社', imprint='講談社ラノベ文庫')
    assert cat.import_editions([record])['inserted'] == 1
    manifest = tmp_path / 'reference-manifest.json'
    sha = write(manifest, {'fixture': 'formally verified reference rows'})
    references = [{'publisher_reference_id': 'kodansha-product:0000000001',
        'source_unit': 'publisher_product_reference', 'source_url': 'https://www.kodansha.co.jp/comic/products/0000000001',
        'title': '主書名３ 副書名', 'isbn': None, 'publisher': None, 'form': None,
        'label': '講談社ラノベ文庫', 'label_literals': ['講談社ラノベ文庫'],
        'credits': [{'role': 'author', 'name': '著者'}], 'official_lightnovel_tag': True,
        'story_series_asserted': False, 'metadata_conflict': False, 'bibliographic_variants': [],
        'source_evidence': [{'source_url': 'https://www.kodansha.co.jp/comic/labels/lanove',
                            'raw_sha256': 'a' * 64, 'locator': 'card[1]'}]}]
    calls = []
    def verify(path, expected):
        calls.append((path, expected))
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != expected:
            raise ValueError('fixture source manifest changed')
        return references
    monkeypatch.setattr(verifier, 'verify_kodansha_references', verify)
    return cat, record, references, manifest, sha, calls


def test_publisher_bibliographic_correspondence_is_not_same_edition_confirmation(bibliography_bundle):
    cat, record, _, manifest, sha, _ = bibliography_bundle
    before = record.copy()
    result = cat.correlate_publisher_bibliographies('kodansha', manifest, sha)
    assert result['new_correspondences'] == 1 and result['matched_publisher_references'] == 1
    row = cat.work_detail('lne:ndl:' + record['id'])
    assert row['publisher_bibliography_matched'] is True and row['light_novel_confirmed'] is True
    assert row['bibliographic_correspondence_scope'] == 'publisher_reference_to_ndl_bibliographic_record'
    assert row['bibliography_source_url'] == 'https://www.kodansha.co.jp/comic/products/0000000001'
    assert row['admission_status'] == 'publisher_bibliographic_record_verified'
    assert not row.get('edition_bibliography_confirmed') and 'edition_identity_basis' not in row
    assert row['original_language'] is None and row['members'] == []
    assert 'bibliographic_correspondence_evidence' not in row and 'raw_path' not in json.dumps(row)
    stats = cat.catalogue_stats()
    assert stats['publisher_bibliographic_correspondences'] == 1 and stats['publisher_confirmed_editions'] == 0
    assert stats['series'] == stats['individual_works'] == 0
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM edition_book_confirmations').fetchone()[0] == 0
        assert json.loads(db.execute('SELECT record_json FROM entries').fetchone()[0]) == before
    assert cat.correlate_publisher_bibliographies('kodansha', manifest, sha)['unchanged_correspondences'] == 1


def test_multiple_publisher_references_do_not_inflate_correspondence_edition_stats(bibliography_bundle):
    from copy import deepcopy
    cat, _, refs, manifest, sha, _ = bibliography_bundle
    other = deepcopy(refs[0])
    other.update(publisher_reference_id='kodansha-product:0000000002',
                 source_url='https://www.kodansha.co.jp/comic/products/0000000002')
    refs.append(other)
    result = cat.correlate_publisher_bibliographies('kodansha', manifest, sha)
    assert result['matched_publisher_references'] == 2 and result['new_correspondences'] == 1
    assert cat.catalogue_stats()['publisher_bibliographic_correspondences'] == 1


@pytest.mark.parametrize('change', ['record_sha', 'rehashed_projection', 'kind', 'cached_second_record'])
def test_bibliographic_correspondence_authenticates_every_selected_record_and_rolls_back(bibliography_bundle, change):
    cat, record, _, manifest, sha, _ = bibliography_bundle
    key = 'lne:ndl:' + record['id']
    with cat._connect() as db:
        if change == 'record_sha':
            db.execute('UPDATE entries SET record_sha256=?', ('0' * 64,))
        elif change == 'kind':
            db.execute('UPDATE entries SET kind=?', ('work',))
        elif change == 'cached_second_record':
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('INSERT INTO entries VALUES(?,?,?,?)', (key + '-second', 'edition', encoded, hashlib.sha256(encoded.encode()).hexdigest()))
        else:
            record['source_record']['publisher'] = record['publisher'] = '原資料にない別社'
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('UPDATE entries SET record_json=?,record_sha256=?', (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    with pytest.raises(ValueError):
        cat.correlate_publisher_bibliographies('kodansha', manifest, sha)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM publisher_bibliographic_correspondences').fetchone()[0] == 0


def test_bibliographic_correspondence_uses_only_target_raw_and_expires_on_source_change(bibliography_bundle, monkeypatch):
    from copy import deepcopy
    from pathlib import Path
    cat, record, _, manifest, sha, _ = bibliography_bundle
    other = deepcopy(record)
    other['source_record']['titles'] = [{'value': '無関係な書名'}]
    other['provenance']['raw_path'] = str(manifest.parent / 'unrelated-missing-raw')
    encoded = json.dumps(other, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with cat._connect() as db:
        db.execute('INSERT INTO entries VALUES(?,?,?,?)', ('lne:ndl:unrelated', 'edition', encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    original = cat._artifact
    def artifact(path, expected, cache):
        assert Path(path).name != 'unrelated-missing-raw'
        return original(path, expected, cache)
    monkeypatch.setattr(cat, '_artifact', artifact)
    monkeypatch.setattr(cat, '_rows', lambda **kw: pytest.fail('correlation must not project all entries'))
    assert cat.correlate_publisher_bibliographies('kodansha', manifest, sha)['new_correspondences'] == 1
    monkeypatch.undo()
    manifest.write_text('{}', encoding='utf-8')
    row = cat.work_detail('lne:ndl:' + record['id'])
    assert not row.get('publisher_bibliography_matched') and not row['light_novel_confirmed']


def test_bibliographic_match_never_downgrades_existing_edition_confirmation_status(bibliography_bundle, monkeypatch):
    cat, record, _, manifest, sha, _ = bibliography_bundle
    cat.correlate_publisher_bibliographies('kodansha', manifest, sha)
    def stronger(rows, stored, cache):
        for row in rows:
            row.update(edition_bibliography_confirmed=True, light_novel_confirmed=True,
                       admission_status='publisher_ndl_edition_verified', edition_identity_basis='existing_isbn_proof')
    monkeypatch.setattr(cat, '_attach_book_confirmations', stronger)
    row = cat.work_detail('lne:ndl:' + record['id'])
    assert row['admission_status'] == 'publisher_ndl_edition_verified'
    assert row['edition_bibliography_confirmed'] is True and row['edition_identity_basis'] == 'existing_isbn_proof'


def test_bibliographic_reference_scope_validation_and_unknown_writer_hold(bibliography_bundle):
    cat, _, refs, manifest, sha, _ = bibliography_bundle
    with pytest.raises(ValueError, match='unknown.*reference'):
        cat.correlate_publisher_bibliographies('kodansha', manifest, sha, reference_ids={'missing'})
    refs[0]['credits'] = [{'role': 'unknown', 'name': '著者'}]
    result = cat.correlate_publisher_bibliographies('kodansha', manifest, sha)
    assert result['new_correspondences'] == 0 and result['candidate_editions_verified'] == 0
    assert result['held_reference_reasons'] == {'official_writer_role_unknown': 1}


def test_rehashed_bibliographic_proof_cannot_claim_a_different_title(bibliography_bundle):
    cat, record, _, manifest, sha, _ = bibliography_bundle
    cat.correlate_publisher_bibliographies('kodansha', manifest, sha)
    with cat._connect() as db:
        proof = json.loads(db.execute('SELECT proof_json FROM publisher_bibliographic_correspondences').fetchone()[0])
        proof['matches'][0]['official_title'] = '原資料にない題'
        encoded = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE publisher_bibliographic_correspondences SET proof_json=?,proof_sha256=?',
                   (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    row = cat.work_detail('lne:ndl:' + record['id'])
    assert not row.get('publisher_bibliography_matched') and not row['light_novel_confirmed']


def test_failed_bibliographic_source_is_verified_once_per_request_not_once_per_binding(tmp_path, monkeypatch):
    cat = LightNovelCatalogue(tmp_path / 'failure-cache.sqlite')
    encoded_record = '{}'
    record_sha = hashlib.sha256(encoded_record.encode()).hexdigest()
    rows = []
    with cat._connect() as db:
        for index in range(10):
            identity = f'lne:ndl:R100000002-I{index + 1}'
            db.execute('INSERT INTO entries VALUES(?,?,?,?)', (identity, 'edition', encoded_record, record_sha))
            proof = {'provider': 'kodansha', 'scope': 'publisher_reference_to_ndl_bibliographic_record',
                'manifest_path': str(tmp_path / 'failed.json'), 'manifest_sha256': 'a' * 64,
                'edition_record_sha256': record_sha, 'reference_ids': ['kodansha-product:0000000001'], 'matches': []}
            encoded = json.dumps(proof, sort_keys=True, separators=(',', ':'))
            db.execute('INSERT INTO publisher_bibliographic_correspondences VALUES(?,?,?,?)',
                (identity, 'kodansha', encoded, hashlib.sha256(encoded.encode()).hexdigest()))
            rows.append({'entry_id': identity, 'entry_kind': 'edition'})
        stored = db.execute('SELECT * FROM entries').fetchall()
    calls = []
    def unavailable(*args):
        calls.append(args)
        raise ValueError('source proof unavailable')
    monkeypatch.setattr(cat, '_publisher_bibliography_references', unavailable)
    cat._attach_bibliographic_correspondences(rows, stored, {})
    assert len(calls) == 1 and not any(row.get('publisher_bibliography_matched') for row in rows)
    cat._attach_bibliographic_correspondences(rows, stored, {})
    assert len(calls) == 2  # failure is not a persistent or TTL cache


def test_catalogue_overview_reuses_one_feature_including_projection_for_both_results(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    original = cat._rows
    calls = []
    def project(**kwargs):
        calls.append(kwargs)
        rows = original(**kwargs)
        if kwargs.get('include_features', True):
            rows[0]['content_features_adopted'] = True
            rows[0]['llm_features'] = [{'label_id': 'theme.friendship', 'axis': 'theme', 'label': '友情'}]
        return rows
    monkeypatch.setattr(cat, '_rows', project)
    expected = {'stats': cat.catalogue_stats(), 'facets': cat.catalogue_facets()}
    calls.clear()
    assert cat.catalogue_overview() == expected
    assert len(calls) == 1 and calls[0].get('include_features', True) is True
    assert expected['stats']['content_feature_series'] == 1


def test_catalogue_overview_observes_new_read_status_without_a_persistent_cache(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    assert cat.catalogue_overview()['stats']['read_status_counts']['read'] == 0
    cat.set_read_status('lns:wd:Q1', 'read')
    original = cat._rows
    calls = []
    def project(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(cat, '_rows', project)
    result = cat.catalogue_overview()
    assert len(calls) == 1 and result['stats']['read_status_counts']['read'] == 1
    assert result['stats']['read_status_counts']['unknown'] == 2


def test_catalogue_overview_still_reauthenticates_its_source_evidence(setup_catalogue):
    from pathlib import Path
    cat, records = setup_catalogue
    cat.ingest(records)
    assert cat.catalogue_overview()['stats']['works'] == 3
    Path(records[0]['provenance']['raw_path']).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA'):
        cat.catalogue_overview()


def test_preference_facets_reuse_their_already_validated_projection(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    original = cat._rows
    calls = []
    def project(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(cat, '_rows', project)
    result = cat.preference_facets()
    assert len(calls) == 1 and result['features'] == []
    assert result['genres'] == [{'qid': 'Q100', 'label': 'ファンタジー', 'count': 3}]


def test_explicit_cobalt_member_adds_searchable_series_and_unnumbered_membership(cobalt_member):
    cat, record, frames, sha, extra, esha, reference, group = cobalt_member
    result = cat.import_ndl_series_from_publishers(frames, sha, source_root=frames.parent,
        cobalt_series_path=extra, cobalt_series_sha256=esha, reference_ids={reference})
    assert result['inserted'] == 1 and result['verified_edition_memberships'] == 1
    series = cat.work_detail('lns:ndl:' + record['id'])
    assert series['title'] == group['series_title'] and series['title'] != record['title']
    assert series['aliases'] == []  # A member's reading is not a series alias.
    assert series['publication_label'] is None and series['publishers'] == []
    assert series['series_title_source'] == 'publisher_explicit_group'
    assert series['members'][0]['entry_id'] == 'lne:ndl:' + record['id']
    assert series['members'][0]['volume_number'] is None
    assert cat.search_catalogue(query=group['series_title'], entry_kind='series')['total'] == 1
    assert cat.work_detail('lne:ndl:' + record['id'])['series_entry_id'] == series['entry_id']


def test_explicit_cobalt_search_title_is_reproduced_even_after_metadata_rehash(cobalt_member):
    cat, record, frames, sha, extra, esha, reference, _ = cobalt_member
    assert cat.import_ndl_series_from_publishers(frames, sha, source_root=frames.parent,
        cobalt_series_path=extra, cobalt_series_sha256=esha, reference_ids={reference})['inserted'] == 1
    with cat._connect() as db:
        key = 'lns:ndl:' + record['id']
        saved = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (key,)).fetchone()[0])
        saved['title'] = '原資料にない偽シリーズ名'
        encoded = json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
                   (encoded, hashlib.sha256(encoded.encode()).hexdigest(), key))
    with pytest.raises(ValueError, match='search title'):
        cat.search_catalogue(query=saved['title'], entry_kind='series')


@pytest.fixture
def tobooks_edition(publisher_edition_v2, monkeypatch):
    cat, record, _ = publisher_edition_v2(publisher='TOブックス', imprint='TOブックス',
        title='主書名 : 副書名', official_title='主書名３ 副書名')
    reference = {'publisher_reference_id': 'tobooks-product:1', 'source_unit': 'publisher_product_reference',
        'source_url': 'https://www.tobooks.jp/contents/1', 'title': '主書名３ 副書名',
        'isbn': '9784798613161', 'publisher': 'TOブックス', 'label': None, 'label_literals': [], 'form': 'B6判',
        'credits': [{'role': 'unknown', 'name': '人物名'}], 'official_lightnovel_tag': True,
        'story_series_asserted': False, 'metadata_conflict': False, 'bibliographic_variants': [],
        'source_evidence': [{'source_url': 'https://www.tobooks.jp/contents', 'raw_sha256': 'a' * 64,
                             'locator': 'ul#search_ul .search_ul_a[1]'}]}
    import recommend_book.light_novel_publisher_references_v1 as source
    monkeypatch.setattr(source, 'verify_publisher_references', lambda provider, path, sha: [reference])
    assert cat.import_editions([record])['inserted'] == 1
    return cat, record, reference


def test_tobooks_reference_dispatch_confirmation_and_detail_preserve_unknown_role(tobooks_edition):
    cat, record, reference = tobooks_edition
    book = cat._publisher_books('tobooks', 'fixture-manifest', 'a' * 64)[0]
    assert book['publisher_book_id'] == reference['publisher_reference_id']
    result = cat.confirm_publisher_editions('tobooks', 'fixture-manifest', 'a' * 64)
    assert result['confirmed'] == 1 and result['ndl_candidate_editions_verified'] == 1
    detail = cat.work_detail('lne:ndl:' + record['id'])
    assert detail['edition_bibliography_confirmed'] is True
    proof = detail['edition_confirmation_evidence']
    assert proof['official_credits'][0]['role'] == 'unknown'
    assert proof['product_sku_identity_asserted'] is False
    assert proof['author_identity_asserted'] is False and proof['story_series_identity_asserted'] is False


@pytest.mark.parametrize('conflict', ['title', 'edition_statement', 'kind'])
def test_tobooks_same_isbn_conflict_outside_detail_title_scope_invalidates_confirmation(tobooks_edition, tmp_path, conflict):
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, record, _ = tobooks_edition
    assert cat.confirm_publisher_editions('tobooks', 'fixture-manifest', 'a' * 64)['confirmed'] == 1
    other = json.loads(json.dumps(record))
    xml = Path(record['provenance']['raw_path']).read_text(encoding='utf-8')
    xml = xml.replace(record['id'], 'R100000002-I2')
    if conflict in {'title', 'kind'}:
        xml = xml.replace('主書名', '矛盾する別書名')
    else:
        xml = xml.replace('</n:BibResource>', '<n:edition>改訂版</n:edition></n:BibResource>')
    raw = tmp_path / 'conflicting-isbn.xml'; raw.write_text(xml, encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), raw.stat().st_size,
        record['source_record']['retrieved_at'], 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac',
        locator='records[1]/recordData').model_dump(mode='json')
    other.update(id=source['source_id'], source_url=source['source_url'], title=source['titles'][0]['value'], source_record=source)
    other['provenance'].update(url=source['source_url'], raw_path=str(raw), raw_sha256=digest)
    assert cat.import_editions([other])['inserted'] == 1
    if conflict == 'kind':
        with cat._connect() as db:
            db.execute('UPDATE entries SET kind=? WHERE entry_id=?', ('work', 'lne:ndl:' + other['id']))
    # A detail request selects the first title, but must still authenticate the
    # contradictory same-ISBN record that lies outside that search selection.
    detail = cat.work_detail('lne:ndl:' + record['id'])
    assert not detail.get('edition_bibliography_confirmed')
    if conflict == 'kind':
        with pytest.raises(ValueError, match='typed identity'):
            cat.confirm_publisher_editions('tobooks', 'fixture-manifest', 'a' * 64)
    else:
        assert cat.confirm_publisher_editions('tobooks', 'fixture-manifest', 'a' * 64)['confirmed'] == 0


def test_tobooks_reference_context_never_opens_an_unrelated_isbn(tobooks_edition, tmp_path):
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, record, _ = tobooks_edition
    other = json.loads(json.dumps(record))
    xml = Path(record['provenance']['raw_path']).read_text(encoding='utf-8')
    xml = xml.replace(record['id'], 'R100000002-I2').replace('9784798613161', '9784758047678')
    raw = tmp_path / 'unrelated-isbn.xml'; raw.write_text(xml, encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), raw.stat().st_size,
        record['source_record']['retrieved_at'], 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac',
        locator='records[1]/recordData').model_dump(mode='json')
    other.update(id=source['source_id'], source_url=source['source_url'], source_record=source, isbn=['9784758047678'])
    other['provenance'].update(url=source['source_url'], raw_path=str(raw), raw_sha256=digest)
    assert cat.import_editions([other])['inserted'] == 1
    raw.write_bytes(b'unrelated raw is unavailable')
    assert cat.confirm_publisher_editions('tobooks', 'fixture-manifest', 'a' * 64)['confirmed'] == 1
    assert cat.work_detail('lne:ndl:' + record['id'])['edition_bibliography_confirmed'] is True


def test_series_delta_scope_avoids_unrelated_raw_and_reuses_same_cache(publisher_ndl_bundle, tmp_path, monkeypatch):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, edition, frames, sha, root, _ = publisher_ndl_bundle
    other = json.loads(json.dumps(edition))
    raw = tmp_path / 'unrelated-edition.xml'
    raw.write_text(Path(edition['provenance']['raw_path']).read_text(encoding='utf-8')
        .replace('R100000002-I1', 'R100000002-I2').replace(edition['title'], '無関係な別書名'), encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(raw.read_bytes()), RawArtifact(digest, str(raw), raw.stat().st_size,
        edition['source_record']['retrieved_at'], 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    other.update(id=source['source_id'], source_url=source['source_url'], title=source['titles'][0]['value'], source_record=source)
    other['provenance'].update(url=source['source_url'], raw_path=str(raw), raw_sha256=digest)
    assert cat.import_editions([other])['inserted'] == 1
    raw.write_bytes(b'unrelated source is unavailable')
    target_raw = Path(edition['provenance']['raw_path'])
    real_read = Path.read_bytes
    reads = []
    def observed_read(path):
        if path == target_raw:
            reads.append(path)
        return real_read(path)
    monkeypatch.setattr(Path, 'read_bytes', observed_read)
    monkeypatch.setattr(cat, '_rows', lambda **kwargs: pytest.fail('full rows must not run inside series import'))
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: pytest.fail('unneeded book confirmation replay'))
    reference = 'publisher-series:' + hashlib.sha256(b'https://firecross.jp/hjbunko/series/1').hexdigest()[:24]
    result = cat.import_ndl_series_from_publishers(frames, sha, source_root=root, reference_ids={reference})
    assert result['inserted'] == 1 and result['verified_edition_memberships'] == 1
    assert len(reads) == 1


@pytest.mark.parametrize('tamper', ['sha', 'rehashed_projection', 'second_cached_record_identity'])
def test_series_delta_revalidates_each_target_projection(publisher_ndl_bundle, tamper):
    cat, edition, frames, sha, root, _ = publisher_ndl_bundle
    with cat._connect() as db:
        if tamper == 'sha':
            db.execute('UPDATE entries SET record_sha256=?', ('f' * 64,))
        elif tamper == 'rehashed_projection':
            edition['publisher'] = '偽出版社'
            encoded = json.dumps(edition, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('UPDATE entries SET record_json=?,record_sha256=?', (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
        else:
            row = db.execute('SELECT * FROM entries').fetchone()
            db.execute('INSERT INTO entries VALUES(?,?,?,?)',
                ('lne:ndl:R100000002-I2', 'edition', row['record_json'], row['record_sha256']))
    reference = 'publisher-series:' + hashlib.sha256(b'https://firecross.jp/hjbunko/series/1').hexdigest()[:24]
    with pytest.raises(ValueError):
        cat.import_ndl_series_from_publishers(frames, sha, source_root=root, reference_ids={reference})
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM entries WHERE kind=?', ('series',)).fetchone()[0] == 0


def test_series_delta_preserves_completed_records_without_reingesting(publisher_ndl_bundle, monkeypatch):
    cat, _, frames, sha, root, _ = publisher_ndl_bundle
    # The existing unscoped API remains compatible.
    assert cat.import_ndl_series_from_publishers(frames, sha, source_root=root)['inserted'] == 1
    with cat._connect() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM entries WHERE kind=?', ('series',))]
        memberships = [tuple(row) for row in db.execute('SELECT * FROM edition_series_memberships')]
    monkeypatch.setattr(cat, 'ingest', lambda *args, **kwargs: pytest.fail('completed series must not be reingested'))
    reference = 'publisher-series:' + hashlib.sha256(b'https://firecross.jp/hjbunko/series/1').hexdigest()[:24]
    result = cat.import_ndl_series_from_publishers(frames, sha, source_root=root, reference_ids={reference})
    assert result['unchanged'] == 1 and result['inserted'] == 0
    with cat._connect() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM entries WHERE kind=?', ('series',))] == before
        assert [tuple(row) for row in db.execute('SELECT * FROM edition_series_memberships')] == memberships


@pytest.fixture
def bulk_bundle(edition_bundle, tmp_path):
    from pathlib import Path
    cat, _, edition = edition_bundle
    conditions = Path(edition['provenance']['conditions_path'])
    policy = json.loads(conditions.read_text())
    policy['version'] = 'light-novel-source-conditions-bulk-v2'
    policy['ndl'].update(provider='iss-ndl-opac-national', source_provider_evidence_url='https://ndlsearch.ndl.go.jp/bib/help')
    policy['ndl']['usages']['llm_input'] = False
    for i, url in enumerate(('https://ndlsearch.ndl.go.jp/bib/help', 'https://www.ndl.go.jp/data/data_service/jnb')):
        raw = tmp_path / f'national-help-{i}'
        raw.write_text('official conditions')
        policy['snapshots'].append({'url': url, 'raw_path': str(raw), 'raw_sha256': hashlib.sha256(raw.read_bytes()).hexdigest()})
    condition_path = tmp_path / 'bulk-conditions.json'
    condition_sha = write(condition_path, policy)
    from test_light_novel_ndl_bulk_v1 import document
    raw = tmp_path / 'bulk.xml'
    raw.write_text(document(), encoding='utf-8')
    receipt = {'version': 'ndl-official-bulk-ui-probe-v2', 'source_provider': 'iss-ndl-opac-national',
        'source_url': 'https://ndlsearch.ndl.go.jp/bib?cs=marc&keyword=test',
        'official_help': 'https://ndlsearch.ndl.go.jp/bib/help', 'retrieved_at': '2026-10-24T00:00:00Z',
        'ui_total': 1, 'documents': 1, 'admin_records': 1, 'bibliographic_records': 1, 'unique_ids': 1,
        'bytes': raw.stat().st_size, 'raw_path': str(raw), 'raw_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
        'repository_ids': ['R100000002'], 'all_count_match': True, 'maximum_selected': 5000,
        'conditions_path': str(condition_path), 'conditions_sha256': condition_sha}
    path = tmp_path / 'bulk-receipt.json'
    return cat, edition, path, write(path, receipt), raw


def test_bulk_own_bibliography_roundtrip_and_raw_revalidation(bulk_bundle):
    cat, _, path, sha, raw = bulk_bundle
    assert cat.import_ndl_bulk(path, sha)['inserted'] == 1
    row = cat.work_detail('lne:ndl:R100000002-I1')
    assert row['title'] == '日本語小説'
    assert row['publishers'] == [{'qid': None, 'label': '出版社'}]
    assert row['source_attributions'][0]['provider'] == 'iss-ndl-opac-national'
    assert not row['light_novel_confirmed'] and row['original_language'] is None
    assert cat.import_ndl_bulk(path, sha)['existing_identities_preserved'] == 1
    assert '他館' not in json.dumps(row, ensure_ascii=False)
    raw.write_bytes(b'changed')
    with pytest.raises(ValueError):
        cat.work_detail('lne:ndl:R100000002-I1')


def test_bulk_preserves_existing_sru_identity_and_history(bulk_bundle):
    cat, edition, path, sha, _ = bulk_bundle
    cat.ingest([edition])
    cat.set_read_status('lne:ndl:R100000002-I1', 'reading')
    result = cat.import_ndl_bulk(path, sha)
    assert result['inserted'] == 0 and result['existing_identities_preserved'] == 1
    row = cat.work_detail('lne:ndl:R100000002-I1')
    assert row['title'] == edition['title'] and row['read_status'] == 'reading'
    assert row['source_attributions'][0]['provider'] == 'iss-ndl-opac'


def test_narrow_search_and_detail_authenticate_only_candidates_and_relatives(setup_catalogue, monkeypatch):
    from pathlib import Path
    cat, records = setup_catalogue
    cat.ingest(records)
    original = cat._project
    calls = []
    def project(record, cache):
        calls.append(record['id'])
        return original(record, cache)
    monkeypatch.setattr(cat, '_project', project)
    Path(records[1]['provenance']['raw_path']).write_text('{}')
    assert cat.search_catalogue('マホウカ')['total'] == 1
    assert set(calls) == {'Q1', 'Q3'}
    calls.clear()
    assert cat.work_detail('lns:wd:Q1')['members'][0]['entry_id'] == 'lnw:wd:Q3'
    assert set(calls) == {'Q1', 'Q3'}
    with pytest.raises(ValueError, match='SHA'):
        cat.catalogue_stats()


def test_narrow_search_does_not_publish_forged_saved_projection(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    record = records[1]
    record['title'] = '偽装題名'
    encoded = json.dumps(record, ensure_ascii=False)
    with cat._connect() as db:
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), 'lns:wd:Q2'))
    with pytest.raises(ValueError, match='title'):
        cat.search_catalogue('偽装題名')


def test_narrow_search_checks_all_saved_record_digests(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    with cat._connect() as db:
        db.execute("UPDATE entries SET record_json='{}' WHERE entry_id='lns:wd:Q2'")
    with pytest.raises(ValueError, match='SHA'):
        cat.search_catalogue('魔法科')


@pytest.mark.parametrize('provider,expected', [('iss-ndl-opac-national', True), ('aozora', False)])
def test_publisher_series_matching_accepts_authenticated_national_provider_only(publisher_ndl_bundle, provider, expected):
    from recommend_book.light_novel_ndl_series_v1 import publisher_assertions, match_bibliography
    cat, edition, frames, sha, root, _ = publisher_ndl_bundle
    proof = {'frames_path': str(frames), 'frames_sha256': sha, 'source_root': str(root)}
    assertion = next(iter(publisher_assertions(proof, cat._artifact, {}).values()))
    source = {**edition['source_record'], 'provider_id': provider}
    assert bool(match_bibliography(assertion, source, normalize_search)) is expected


def test_source_only_recommendation_verifies_liked_and_selected_features_only(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    calls = []
    def verify(entry_ids=None):
        calls.append(entry_ids)
        assert entry_ids is not None  # An irrelevant native window must not be opened.
        return {}
    monkeypatch.setattr(cat, '_feature_rows', verify)
    result = cat.recommend_preferences(liked_entry_ids=['lns:wd:Q1'])
    assert [r['entry_id'] for r in result['items']] == ['lns:wd:Q2']
    assert result['items'][0]['reasons'][0]['kind'] == 'source_genre'
    assert calls == [{'lns:wd:Q1'}, {'lns:wd:Q2'}]


def test_explicit_subseries_p179_deduplicates_recommendations_without_copying_features(setup_catalogue):
    from pathlib import Path
    cat, records = setup_catalogue
    child = records[2]
    child['kind'] = 'series'
    raw = Path(child['provenance']['raw_path'])
    payload = json.loads(raw.read_text(encoding='utf8'))
    payload['entities']['Q3']['claims']['P31'] = [claim('Q104213567')]
    child['provenance']['raw_sha256'] = write(raw, payload)
    cat.ingest(records)
    result = cat.recommend_preferences(liked_entry_ids=['lns:wd:Q2'])
    assert len(result['items']) == 1
    assert cat.work_detail('lns:wd:Q1')['members'][0]['entry_id'] == 'lns:wd:Q3'
    assert cat.work_detail('lns:wd:Q3')['llm_features'] == []
    cat.set_read_status('lns:wd:Q3', 'read')
    assert cat.work_detail('lns:wd:Q1')['read_status'] == 'unknown'
    assert cat.similar_works('lns:wd:Q2')['items'] == []


def test_failed_feature_manifest_is_verified_once_per_operation(setup_catalogue, monkeypatch, tmp_path):
    from recommend_book import light_novel_classification_v1 as verifier
    cat, records = setup_catalogue
    cat.ingest(records)
    path, sha = str(tmp_path / 'changed-native.json'), 'f' * 64
    with cat._connect() as db:
        db.executemany('INSERT INTO features VALUES(?,?,?)',
            [(identity, path, sha) for identity in ('lns:wd:Q1', 'lns:wd:Q2')])
    calls = []
    def reject(path, sha):
        calls.append((path, sha))
        raise ValueError('changed protected native artifact')
    monkeypatch.setattr(verifier, 'verify_feature_export', reject)
    assert cat._feature_rows() == {}
    assert calls == [(path, sha)]
    assert cat._feature_rows() == {}
    assert calls == [(path, sha), (path, sha)]  # A new operation retries verification.


def test_failed_book_manifest_is_verified_once_per_operation(edition_bundle, monkeypatch, tmp_path):
    from copy import deepcopy
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    cat, _, edition = edition_bundle
    second = deepcopy(edition)
    raw = tmp_path / 'second-edition.xml'
    raw.write_text(Path(edition['provenance']['raw_path']).read_text(encoding='utf8')
        .replace('R100000002-I1', 'R100000002-I2'), encoding='utf8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(raw.read_bytes()), RawArtifact(digest,
        str(raw), raw.stat().st_size, edition['source_record']['retrieved_at'],
        'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    second.update(id=source['source_id'], source_url=source['source_url'], source_record=source)
    second['provenance'].update(url=source['source_url'], raw_path=str(raw), raw_sha256=digest)
    assert cat.ingest([edition, second])['inserted'] == 2
    rows = cat._rows(include_features=False)
    with cat._connect() as db:
        stored = db.execute('SELECT * FROM entries').fetchall()
        for entry in stored:
            proof = {'provider': 'kadokawa', 'manifest_path': str(tmp_path / 'changed-books.json'),
                'manifest_sha256': 'f' * 64, 'publisher_book_id': 'book1',
                'edition_record_sha256': entry['record_sha256'], 'match': {}}
            encoded = json.dumps(proof)
            db.execute('INSERT INTO edition_book_confirmations VALUES(?,?,?,?)',
                (entry['entry_id'], 'kadokawa', encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    calls = []
    def reject(*args):
        calls.append(args)
        raise ValueError('changed official book artifact')
    monkeypatch.setattr(cat, '_publisher_books', reject)
    cat._attach_book_confirmations(rows, stored, {})
    assert len(calls) == 1
    assert all(not row.get('edition_bibliography_confirmed') for row in rows)
    cat._attach_book_confirmations(rows, stored, {})
    assert len(calls) == 2  # No failed-result cache survives the operation.


def _avoidance_feature(label_id, axis):
    return {'entry_id': 'lns:wd:Q2', 'scope': 'series', 'content_features_adopted': True,
        'labels': [{'label_id': label_id, 'axis': axis, 'scope': 'series', 'decision': 'present',
            'relevance': 1, 'evidence': [{'evidence_id': 'prose1', 'summary': '内容の直接根拠'}]}],
        'source_attributions': [{'evidence_id': 'prose1', 'kind': 'note',
            'rights_policy_id': 'light-novel-series-wikipedia-screened-v1', 'license': 'CC-BY-SA-4.0'}]}


@pytest.mark.parametrize('label_id,axis', [(None, None), ('genre.fantasy', 'genre'), ('theme.family', 'theme')])
def test_unknown_avoided_feature_warns_without_claiming_absence(setup_catalogue, monkeypatch, label_id, axis):
    cat, records = setup_catalogue
    cat.ingest(records)
    features = {'lns:wd:Q2': _avoidance_feature(label_id, axis)} if label_id else {}
    monkeypatch.setattr(cat, '_feature_rows', lambda ids=None: {key: row for key, row in features.items() if ids is None or key in ids})
    result = cat.recommend_preferences(liked_entry_ids=['lns:wd:Q1'], avoided_features=['theme.revenge'])
    assert [row['entry_id'] for row in result['items']] == ['lns:wd:Q2']
    row = result['items'][0]
    assert row['avoided_feature_status'] == {'matched': [], 'unknown': ['theme.revenge']}
    assert row['feature_insufficiency'] == [{'axis': 'theme', 'status': 'unknown',
        'reason': 'requested_avoided_feature_absence_unproved'}]
    from recommend_book.recommendation.scale_site_v1 import public_work
    explanation = public_work(row)['feature_insufficiency']
    assert 'テーマ' in explanation and '判断できません' in explanation


def test_adopted_avoided_feature_still_excludes_candidate(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    features = {'lns:wd:Q2': _avoidance_feature('theme.revenge', 'theme')}
    monkeypatch.setattr(cat, '_feature_rows', lambda ids=None: {key: row for key, row in features.items() if ids is None or key in ids})
    assert cat.recommend_preferences(liked_entry_ids=['lns:wd:Q1'], avoided_features=['theme.revenge'])['items'] == []


@pytest.mark.parametrize('relevance,score,unknown', [(None, 0, True), (.4, .186667, False)])
def test_nullable_relevance_preserves_match_without_inventing_score(setup_catalogue, monkeypatch, relevance, score, unknown):
    cat, records = setup_catalogue
    cat.ingest(records)
    feature = _avoidance_feature('theme.family', 'theme')
    feature['labels'][0]['relevance'] = relevance
    monkeypatch.setattr(cat, '_feature_rows', lambda ids=None: {'lns:wd:Q2': feature} if ids is None or 'lns:wd:Q2' in ids else {})
    result = cat.recommend_preferences(preferred_features=['theme.family'], axis_weights={'theme': .7})
    assert [row['entry_id'] for row in result['items']] == ['lns:wd:Q2']
    row = result['items'][0]
    assert row['score'] == score
    assert row['reasons'][0]['kind'] == 'series_content_feature'
    assert row['reasons'][0]['label_id'] == 'theme.family'
    assert row['reasons'][0].get('relevance_unknown', False) is unknown
    assert row['llm_features'][0]['relevance'] == relevance


def test_nullable_relevance_still_excludes_adopted_avoided_label(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    feature = _avoidance_feature('theme.revenge', 'theme')
    feature['labels'][0]['relevance'] = None
    monkeypatch.setattr(cat, '_feature_rows', lambda ids=None: {'lns:wd:Q2': feature} if ids is None or 'lns:wd:Q2' in ids else {})
    assert cat.recommend_preferences(liked_entry_ids=['lns:wd:Q1'], avoided_features=['theme.revenge'])['items'] == []


@pytest.fixture
def hifumi_futaba_confirmation(publisher_edition_v2, monkeypatch, tmp_path):
    """Fake only official source I/O; retain real own raw and provider matchers."""
    from recommend_book.light_novel_futaba_references_v1 import _FIRST, _LABEL, _SEARCH
    import recommend_book.light_novel_hifumi_details_v1 as hifumi_source
    import recommend_book.light_novel_publisher_references_v1 as reference_source

    def build(provider, *, authority=True):
        publisher, imprint = ('一二三書房', 'Saga Forest') if provider == 'hifumi' else ('双葉社', _LABEL)
        cat, record, _ = publisher_edition_v2(publisher=publisher, imprint=imprint, authority=authority)
        common = {'title': '主書名３ 副書名', 'isbn': '9784798613161', 'publisher': None,
            'credits': [{'role': 'author', 'name': '著者'}], 'story_series_asserted': False,
            'metadata_conflict': False, 'bibliographic_variants': []}
        if provider == 'hifumi':
            url = 'https://hifumi.co.jp/lineup/opaque-book/'
            book = {**common, 'publisher_book_id': 'hifumi:opaque-book', 'source_unit': 'publisher_book_detail',
                'source_url': url, 'catalogue_owner': '一二三書房', 'individual_publisher': None,
                'label': 'サーガフォレスト', 'label_literals': ['サーガフォレスト'], 'official_lightnovel_tag': True,
                'source_evidence': [{'source_url': url, 'raw_sha256': 'a' * 64, 'locator': 'h1; explicit ISBN/credits/label'},
                    {'source_url': 'https://hifumi.co.jp/lineup_all/', 'raw_sha256': 'b' * 64,
                     'locator': 'explicit LN nav and own card'}]}
        else:
            pointer = '/books/book_informations/0'
            book = {**common, 'publisher_reference_id': 'futaba-product:opaque-book',
                'source_unit': 'publisher_product_reference',
                'source_url': 'https://www.futabasha.co.jp/book/opaque-book?type=1',
                'isbn_source_literal': common['isbn'], 'form': '文庫',
                'label': _LABEL, 'label_literals': [_LABEL], 'official_lightnovel_tag': None,
                'credit_literals': [{'role': '著', 'name': '著者', 'role_field': 'author_type_name',
                                     'locator': pointer + '/book_authors/0'}],
                'source_scope_membership': {'source_unit': 'publisher_label_filtered_product_listing',
                    'membership_basis': 'returned_by_publisher_explicit_label_filter', 'filter_parameter': 'label_name',
                    'filter_literal': _LABEL, 'rendered_index_url': _SEARCH, 'request_url': _FIRST, 'raw_sha256': 'a' * 64,
                    'source_label_id_literal': 14, 'source_label_id_locator': pointer + '/label_id',
                    'card_label_literal': _LABEL, 'card_label_locator': pointer + '/label_name',
                    'official_ui_route_proof': 'synthetic-official-ui-route-proof.json',
                    'does_not_assert_story_series_or_original_identity': True},
                'source_evidence': [{'source_url': _FIRST, 'raw_sha256': 'a' * 64,
                    'locator': pointer + ': book_name/isbn_code/book_authors/label_name/label_id/publication_form_value'}]}
        manifest = tmp_path / (provider + '-synthetic-books.json')
        calls = []

        def verified(path, sha):
            from pathlib import Path
            assert Path(path).resolve() == manifest.resolve() and sha == 'f' * 64
            calls.append((provider, str(path), sha))
            return [book]

        if provider == 'hifumi':
            monkeypatch.setattr(hifumi_source, 'verify_hifumi_product_details', verified)
        else:
            def verified_reference(actual_provider, path, sha):
                assert actual_provider == 'futaba'
                return verified(path, sha)
            monkeypatch.setattr(reference_source, 'verify_publisher_references', verified_reference)
        assert cat.import_editions([record])['inserted'] == 1
        return cat, record, book, manifest, calls
    return build


@pytest.mark.parametrize('provider', ['hifumi', 'futaba'])
@pytest.mark.parametrize('authority', [True, False])
def test_hifumi_futaba_book_dispatch_persists_and_replays_real_matcher_to_public_detail(
        hifumi_futaba_confirmation, provider, authority):
    from recommend_book.recommendation.scale_site_v1 import public_work
    cat, record, book, manifest, calls = hifumi_futaba_confirmation(provider, authority=authority)
    identity = 'lne:ndl:' + record['id']
    result = cat.confirm_publisher_editions(provider, manifest, 'f' * 64)
    assert result['confirmed'] == result['matched_publisher_books'] == result['ndl_candidate_editions_verified'] == 1
    with cat._connect() as db:
        binding = dict(db.execute('SELECT * FROM edition_book_confirmations WHERE edition_id=?', (identity,)).fetchone())
        stored = dict(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone())
    assert binding['provider'] == provider
    assert hashlib.sha256(binding['proof_json'].encode()).hexdigest() == binding['proof_sha256']
    proof = json.loads(binding['proof_json'])
    assert proof['edition_record_sha256'] == stored['record_sha256']
    assert proof['match']['isbn13'] == '9784798613161'
    assert proof['match']['source_raw_sha256'] == record['provenance']['raw_sha256']
    assert proof['match']['scope'] == 'edition_only'
    assert proof['match']['confirmed_scope'] == 'contained_bibliographic_edition_only'
    assert proof['match']['same_isbn_bibliographic_correspondence_asserted'] is True
    assert proof['match']['authors'][0]['authority_status'] == ('known' if authority else 'unknown')
    assert proof['match']['individual_publisher_agreement'] == 'unknown'
    assert proof['match']['original_work_identity_asserted'] is False
    assert proof['match']['story_series_identity_asserted'] is False
    assert proof['match']['product_sku_identity_asserted'] is False
    assert cat.confirm_publisher_editions(provider, manifest, 'f' * 64)['unchanged'] == 1
    detail = cat.work_detail(identity)
    assert detail['edition_confirmation_evidence'] == proof['match']
    assert detail['edition_bibliography_confirmed'] is True and detail['light_novel_confirmed'] is True
    assert detail['confirmation_source_url'] == book['source_url']
    assert detail['publishers'] == [{'qid': None, 'label': '一二三書房' if provider == 'hifumi' else '双葉社'}]
    assert detail['original_language'] is None and detail['members'] == [] and detail['llm_features'] == []
    public = public_work(detail)
    assert public['entry_id'] == identity and public['edition_bibliography_confirmed'] is True
    assert public['confirmation_source_url'] == book['source_url']
    encoded = json.dumps(public, ensure_ascii=False)
    assert 'edition_confirmation_evidence' not in public and 'raw_path' not in encoded and 'records[1]' not in encoded
    assert len(calls) == 3  # Initial confirmation, idempotency, and independent detail replay.


@pytest.mark.parametrize('provider', ['hifumi', 'futaba'])
@pytest.mark.parametrize('change', ['own_raw', 'isbn', 'full_title', 'label'])
def test_hifumi_futaba_book_confirmation_cannot_survive_broken_source_or_bibliography(
        hifumi_futaba_confirmation, provider, change):
    from pathlib import Path
    cat, record, book, manifest, _ = hifumi_futaba_confirmation(provider, authority=False)
    identity = 'lne:ndl:' + record['id']
    assert cat.confirm_publisher_editions(provider, manifest, 'f' * 64)['confirmed'] == 1
    with cat._connect() as db:
        previous = tuple(db.execute('SELECT * FROM edition_book_confirmations').fetchone())
    if change == 'own_raw':
        Path(record['provenance']['raw_path']).write_text('changed own raw', encoding='utf-8')
        with pytest.raises(ValueError, match='SHA'):
            cat.confirm_publisher_editions(provider, manifest, 'f' * 64)
        with pytest.raises(ValueError, match='SHA'):
            cat.work_detail(identity)
    else:
        if change == 'isbn':
            book['isbn'] = '9784758047678'
            if provider == 'futaba':
                book['isbn_source_literal'] = book['isbn']
        elif change == 'full_title':
            book['title'] = '主書名３ 別の副書名'
        else:
            book['label'] = 'メルティアブックス' if provider == 'hifumi' else 'モンスターコミックス'
            book['label_literals'] = [book['label']]
        assert cat.confirm_publisher_editions(provider, manifest, 'f' * 64)['confirmed'] == 0
        detail = cat.work_detail(identity)
        assert detail['light_novel_confirmed'] is False
        assert not detail.get('edition_bibliography_confirmed') and 'edition_confirmation_evidence' not in detail
    with cat._connect() as db:
        assert tuple(db.execute('SELECT * FROM edition_book_confirmations').fetchone()) == previous


@pytest.fixture
def sparse_catalogue_correspondence(publisher_edition_v2, monkeypatch, tmp_path):
    """Only replace official source verification, retaining each real matcher."""
    from recommend_book import light_novel_gc_references_v1 as gc_source
    from recommend_book import light_novel_alphapolis_references_v1 as alpha_source
    from recommend_book import light_novel_hifumi_references_v1 as hifumi_source
    from recommend_book import light_novel_kodansha_references_v1 as kodansha_source

    def build(provider):
        publisher, imprint = {'gc': ('マイクロマガジン社', 'GC NOVELS'),
            'alphapolis': ('アルファポリス', 'アルファポリス'), 'kodansha': ('講談社', '講談社ラノベ文庫'),
            'hifumi': ('一二三書房', 'サーガフォレスト')}[provider]
        cat, record, _ = publisher_edition_v2(publisher=publisher, imprint=imprint)
        assert cat.import_editions([record])['inserted'] == 1
        common = {'source_unit': 'publisher_product_reference', 'title': '主書名３ 副書名',
            'isbn': None, 'publisher': None, 'form': None, 'volume': None,
            'story_series_asserted': False, 'metadata_conflict': False, 'bibliographic_variants': []}
        if provider == 'gc':
            ref = {**common, 'publisher_reference_id': 'gc-product:1', 'official_product_id': '1',
                'source_url': 'https://gcnovels.jp/book/1', 'label': None, 'label_literals': [],
                'credits': [{'role': 'author', 'role_literal': '小説', 'name': '著者'}],
                'official_lightnovel_tag': None, 'source_frame_scope': dict(gc_source._SCOPE),
                'source_evidence': [{'source_url': gc_source._START, 'raw_sha256': 'a' * 64,
                    'locator': 'ul.book-list > li.book-list-item-root[1] h3>a / ul.author>li'}]}
        elif provider == 'alphapolis':
            url = 'https://www.alphapolis.co.jp/book/alphapolis/tankou/100'
            ref = {**common, 'publisher_reference_id': 'alphapolis-commercial-book:1/2',
                'source_url': 'https://www.alphapolis.co.jp/book/detail/1/2', 'opaque_url_ids': ['1', '2'],
                'title_literal': common['title'], 'subtitle_literal': '', 'complete_display_title': common['title'],
                'label': 'アルファポリス', 'label_literals': ['アルファポリス'],
                'credits': [{'role': 'author', 'role_display': '著', 'name': '著者'}],
                'official_lightnovel_tag': None, 'edition_identity_asserted': False,
                'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
                'source_scope_membership': {'source_unit': 'publisher_commercial_book_index_membership',
                    'brand': 'alphapolis', 'entry_url': url, 'index_scope': 'alphapolis_novel_tankou_category_100',
                    'membership_basis': 'explicit_book_card_and_own_label_tag_in_fixed_index',
                    'label_context_literal': 'アルファポリス', 'individual_lightnovel_classification': 'unknown',
                    'full_historical_catalogue_complete': False}, 'occurrence_count': 1,
                'source_evidence': [{'source_url': url, 'raw_sha256': 'a' * 64,
                    'locator': '.book-blocks .book:nth-of-type(1) h2.title/a; h3.sub-title; .label-tag; .authors .author'}]}
        else:
            label, url, identity, index_url = (
                ('講談社ラノベ文庫', 'https://www.kodansha.co.jp/comic/products/0000000001',
                 'kodansha-product:0000000001', 'https://www.kodansha.co.jp/comic/labels/lanove')
                if provider == 'kodansha' else ('サーガフォレスト', 'https://hifumi.co.jp/lineup/opaque1/',
                 'hifumi-product:opaque1', 'https://hifumi.co.jp/saga_forest/'))
            ref = {**common, 'publisher_reference_id': identity, 'source_url': url,
                'label': label, 'label_literals': [label], 'credits': [{'role': 'author', 'name': '著者'}],
                'official_lightnovel_tag': True,
                'source_evidence': [{'source_url': index_url, 'raw_sha256': 'a' * 64, 'locator': 'card[1]'}]}
        manifest = tmp_path / (provider + '-sparse-catalogue.json')
        sha = write(manifest, {'synthetic_source_scope': provider})
        def verify(path, expected):
            from pathlib import Path
            assert Path(path).resolve() == manifest.resolve() and expected == sha
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != sha:
                raise ValueError('synthetic source manifest changed')
            return [ref]
        module, name = {'gc': (gc_source, 'verify_gc_references'),
            'alphapolis': (alpha_source, 'verify_alphapolis_references'),
            'kodansha': (kodansha_source, 'verify_kodansha_references'),
            'hifumi': (hifumi_source, 'verify_hifumi_references')}[provider]
        monkeypatch.setattr(module, name, verify)
        return cat, record, ref, manifest, sha
    return build


@pytest.mark.parametrize('provider', ['gc', 'alphapolis'])
def test_sparse_catalogue_correspondence_never_confirms_unknown_lightnovel_or_isbn_edition(
        sparse_catalogue_correspondence, provider):
    from recommend_book.recommendation.scale_site_v1 import public_work
    cat, record, ref, manifest, sha = sparse_catalogue_correspondence(provider)
    identity = 'lne:ndl:' + record['id']
    result = cat.correlate_publisher_bibliographies(provider, manifest, sha)
    assert result['new_correspondences'] == result['matched_publisher_references'] == 1
    assert result['candidate_editions_verified'] == 1
    with cat._connect() as db:
        binding = dict(db.execute('SELECT * FROM publisher_bibliographic_correspondences').fetchone())
        assert db.execute('SELECT count(*) FROM edition_book_confirmations').fetchone()[0] == 0
        assert json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0]) == record
    assert binding['provider'] == provider
    assert hashlib.sha256(binding['proof_json'].encode()).hexdigest() == binding['proof_sha256']
    proof = json.loads(binding['proof_json'])
    assert proof['scope'] == 'publisher_reference_to_ndl_bibliographic_record'
    assert proof['matches'][0]['light_novel_status'] == 'unknown'
    assert proof['matches'][0]['official_lightnovel_tag'] is None
    assert proof['matches'][0]['official_publisher_asserted'] is False
    if provider == 'gc':
        assert proof['matches'][0]['official_label'] is None
    assert proof['matches'][0]['original_work_identity_asserted'] is False
    assert proof['matches'][0]['story_series_identity_asserted'] is False
    assert cat.correlate_publisher_bibliographies(provider, manifest, sha)['unchanged_correspondences'] == 1
    row = cat.work_detail(identity)
    assert row['publisher_bibliography_matched'] is True and row['light_novel_confirmed'] is False
    assert row['admission_status'] == 'publisher_bibliographic_record_verified'
    assert row['bibliography_source_url'] == ref['source_url']
    assert row['source_url'] == record['source_url'] and row['original_language'] is None
    assert not row.get('edition_bibliography_confirmed') and 'edition_identity_basis' not in row
    assert row['members'] == [] and row['llm_features'] == []
    public = public_work(row)
    assert public['publisher_bibliography_matched'] is True and public['light_novel_confirmed'] is False
    assert public['bibliography_source_url'] == ref['source_url']
    assert not public.get('edition_bibliography_confirmed')
    encoded = json.dumps(public, ensure_ascii=False)
    assert 'bibliographic_correspondence_evidence' not in public and 'raw_path' not in encoded and 'records[1]' not in encoded


@pytest.mark.parametrize('provider', ['gc', 'alphapolis'])
@pytest.mark.parametrize('change', ['own_raw', 'rehashed_projection'])
def test_sparse_catalogue_correspondence_rejects_raw_or_rehashed_projection_tampering(
        sparse_catalogue_correspondence, provider, change):
    from pathlib import Path
    cat, record, _, manifest, sha = sparse_catalogue_correspondence(provider)
    if change == 'own_raw':
        Path(record['provenance']['raw_path']).write_text('changed own raw', encoding='utf-8')
    else:
        record['source_record']['publisher'] = record['publisher'] = '原資料にない出版社'
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with cat._connect() as db:
            db.execute('UPDATE entries SET record_json=?,record_sha256=?',
                (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    with pytest.raises(ValueError):
        cat.correlate_publisher_bibliographies(provider, manifest, sha)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM publisher_bibliographic_correspondences').fetchone()[0] == 0


@pytest.mark.parametrize('provider', ['gc', 'alphapolis'])
def test_sparse_catalogue_correspondence_cannot_replay_a_new_positive_genre_projection(
        sparse_catalogue_correspondence, provider):
    cat, record, ref, manifest, sha = sparse_catalogue_correspondence(provider)
    assert cat.correlate_publisher_bibliographies(provider, manifest, sha)['new_correspondences'] == 1
    ref['official_lightnovel_tag'] = True
    assert cat.correlate_publisher_bibliographies(provider, manifest, sha)['new_correspondences'] == 0
    row = cat.work_detail('lne:ndl:' + record['id'])
    assert not row.get('publisher_bibliography_matched') and row['light_novel_confirmed'] is False


@pytest.mark.parametrize('provider', ['kodansha', 'hifumi'])
def test_sparse_catalogue_dispatch_preserves_legacy_ln_correspondence_status_and_proof(
        sparse_catalogue_correspondence, provider):
    cat, record, ref, manifest, sha = sparse_catalogue_correspondence(provider)
    assert cat.correlate_publisher_bibliographies(provider, manifest, sha)['new_correspondences'] == 1
    with cat._connect() as db:
        proof = json.loads(db.execute('SELECT proof_json FROM publisher_bibliographic_correspondences').fetchone()[0])
    match, = proof['matches']
    assert match['rule_version'] == 'publisher-ndl-bibliographic-correspondence-v1'
    assert match['official_lightnovel_tag'] is True and 'light_novel_status' not in match
    assert match['official_source_url'] == ref['source_url']
    assert match['source_raw_sha256'] == record['provenance']['raw_sha256']
    assert match['official_publisher_asserted'] is False
    assert match['same_edition_identity_asserted'] is False
    row = cat.work_detail('lne:ndl:' + record['id'])
    assert row['publisher_bibliography_matched'] is True and row['light_novel_confirmed'] is True
    assert row['admission_status'] == 'publisher_bibliographic_record_verified'
    assert not row.get('edition_bibliography_confirmed')


_ADDITIONAL_KAD_MATCH_LABELS = [(53, 'MFブックス'), (104, 'カドカワBOOKS'),
    (197, 'ファミ通文庫'), (114, '角川ビーンズ文庫'), (178, 'ビーズログ文庫')]


@pytest.mark.parametrize('label_id,label', _ADDITIONAL_KAD_MATCH_LABELS)
@pytest.mark.parametrize('mode', ['direct', 'composed'])
def test_kadokawa_additional_label_matching_keeps_own_exact_imprint_and_full_title(
        publisher_edition_v2, label_id, label, mode):
    options = {'title': '主書名', 'volume': None, 'official_title': '主書名'} if mode == 'direct' else {}
    cat, record, book = publisher_edition_v2(imprint=label, label=label, label_id=label_id, **options)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert proof is not None
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-' + ('v1' if mode == 'direct' else 'v2')
    assert proof['official_label'] == label and proof['ndl_imprint'] == label
    assert proof['isbn13'] == '9784798613161' and proof['scope'] == 'edition_only'
    assert proof['ndl_publisher'] == 'KADOKAWA'
    assert proof['original_work_identity_asserted'] is False and proof['story_series_identity_asserted'] is False
    if mode == 'composed':
        assert proof['title_match']['main'] == '主書名'
        assert proof['title_match']['subtitle'] == '副書名' and proof['title_match']['volume'] == '3'


@pytest.mark.parametrize('label_id,label', _ADDITIONAL_KAD_MATCH_LABELS)
def test_kadokawa_additional_label_mismatch_cannot_borrow_another_ndl_imprint(
        publisher_edition_v2, label_id, label):
    cat, record, book = publisher_edition_v2(imprint='電撃文庫', label=label, label_id=label_id)
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_kadokawa_paper_form_one_v6_requires_explicit_tankou_literal_for_all_nine_labels(publisher_edition_v2):
    for label_id, label in [(330, '電撃文庫'), (55, 'MF文庫J'), (344, 'ファンタジア文庫'),
            (154, '角川スニーカー文庫'), *_ADDITIONAL_KAD_MATCH_LABELS]:
        cat, record, book = publisher_edition_v2(imprint=label, label=label, label_id=label_id)
        kadokawa_owner_context(book)
        book.update(product_form_id=1, official_product_form_name='単行本')
        proof = cat._publisher_edition_match('kadokawa', book, record, {})
        assert proof is not None, label
        assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-v6'
        assert proof['official_product_form_id'] == 1 and proof['official_product_form_name'] == '単行本'
        assert proof['product_form_evidence'] == {
            'source_url': 'https://www.kadokawa.co.jp/product/search/',
            'source_sha256': 'e' * 64, 'json_pointer': '/product_form/1/name'}
        assert proof['official_label'] == proof['ndl_imprint'] == label
        assert proof['title_match']['main'] == '主書名'
        assert proof['title_match']['subtitle'] == '副書名' and proof['title_match']['volume'] == '3'
        assert proof['original_work_identity_asserted'] is False and proof['story_series_identity_asserted'] is False
        assert 'raw_path' not in json.dumps(proof['product_form_evidence'])
        assert 'B6' not in json.dumps(proof, ensure_ascii=False)


@pytest.mark.parametrize('change', ['missing_literal', 'different_definition', 'electronic', 'missing_subtitle'])
def test_kadokawa_paper_form_one_does_not_replace_unknown_form_or_missing_title_parts(
        publisher_edition_v2, change):
    cat, record, book = publisher_edition_v2(imprint='MFブックス', label='MFブックス', label_id=53)
    kadokawa_owner_context(book)
    book.update(product_form_id=1, official_product_form_name='単行本')
    if change == 'missing_literal':
        book.pop('official_product_form_name')
    elif change == 'different_definition':
        book['official_product_form_name'] = '単行本（電子）'
    elif change == 'electronic':
        book.update(product_form_id=13, official_product_form_name='電子専売')
    else:
        book['title'] = '主書名３'
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_kadokawa_paper_form_one_addition_leaves_form_three_v2_to_v5_complete_proofs_unchanged(publisher_edition_v2):
    from copy import deepcopy
    for version, options in [('v2', {}), ('v3', {'official_title': '副書名 主書名３'}),
            ('v4', {'title': '主書名', 'official_title': '主書名３ 完全巻題', 'extra': own_part_title()}),
            ('v5', {'publisher': 'メディアファクトリー'})]:
        cat, record, book = publisher_edition_v2(**options)
        kadokawa_owner_context(book)
        before = deepcopy(cat._publisher_edition_match('kadokawa', book, record, {}))
        assert before['rule_version'] == 'publisher-ndl-edition-confirmation-' + version
        book['official_product_form_name'] = '文庫'
        assert cat._publisher_edition_match('kadokawa', book, record, {}) == before


@pytest.fixture
def gagaga_catalogue_integration(publisher_edition_v2, monkeypatch, tmp_path):
    import recommend_book.light_novel_publisher_references_v1 as source

    def build(*, release=False, authority=True):
        cat, record, _ = publisher_edition_v2(publisher='小学館', imprint='ガガガ文庫', authority=authority)
        assert cat.import_editions([record])['inserted'] == 1
        url = 'https://gagagabunko.jp/' + ('release/index.html#01' if release else 'lineup/202609.html#01')
        raw_sha = 'a' * 64
        identity = ('publisher-lineup-snapshot:' + hashlib.sha256((url + '\n' + raw_sha).encode()).hexdigest()[:24]
                    if release else 'publisher-lineup:' + hashlib.sha256(url.encode()).hexdigest()[:24])
        ref = {'publisher_reference_id': identity, 'source_unit': 'publisher_lineup_item_reference',
            'source_url': url, 'title': '主書名３ 副書名', 'isbn': '9784094533040' if release else None,
            'credits': [{'role': '著', 'name': '著者'}, {'role': 'イラスト', 'name': '絵担当'}],
            'publisher': '小学館', 'label': None, 'label_literals': [], 'form': None,
            'official_lightnovel_tag': None, 'story_series_asserted': False,
            'metadata_conflict': False, 'bibliographic_variants': [],
            'source_evidence': [{'source_url': 'https://gagagabunko.jp/' + ('release/index.html' if release else 'lineup/index.html'),
                'raw_sha256': raw_sha, 'locator': 'section#01 div#title h3/span.textsize14/ISBN'
                    if release else 'li[1] a/p and explicit credit roles'}]}
        manifest = tmp_path / 'gagaga-catalogue-synthetic.json'
        sha = write(manifest, {'synthetic_source_scope': 'gagaga'})
        def verify(provider, path, expected):
            from pathlib import Path
            assert provider == 'gagaga' and Path(path).resolve() == manifest.resolve() and expected == sha
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != expected:
                raise ValueError('synthetic Gagaga source manifest changed')
            return [ref]
        monkeypatch.setattr(source, 'verify_publisher_references', verify)
        return cat, record, ref, manifest, sha
    return build


@pytest.mark.parametrize('release,authority', [(False, True), (True, False)])
def test_gagaga_catalogue_integration_saves_only_bibliography_and_keeps_classification_unknown(
        gagaga_catalogue_integration, release, authority):
    from recommend_book.recommendation.scale_site_v1 import public_work
    cat, record, ref, manifest, sha = gagaga_catalogue_integration(release=release, authority=authority)
    identity = 'lne:ndl:' + record['id']
    result = cat.correlate_publisher_bibliographies('gagaga', manifest, sha)
    assert result['new_correspondences'] == result['candidate_editions_verified'] == 1
    assert result['matched_publisher_references'] == 1
    with cat._connect() as db:
        binding = dict(db.execute('SELECT * FROM publisher_bibliographic_correspondences').fetchone())
        assert db.execute('SELECT count(*) FROM edition_book_confirmations').fetchone()[0] == 0
    assert binding['provider'] == 'gagaga'
    assert hashlib.sha256(binding['proof_json'].encode()).hexdigest() == binding['proof_sha256']
    proof = json.loads(binding['proof_json'])
    match, = proof['matches']
    assert match['rule_version'] == 'gagaga-lineup-ndl-bibliographic-correspondence-v1'
    assert match['light_novel_status'] == 'unknown' and match['official_lightnovel_tag'] is None
    assert match['official_publisher'] is None and match['official_publisher_asserted'] is False
    assert match['catalogue_owner_context']['literal'] == '小学館'
    assert match['official_label'] is None and match['official_individual_imprint_asserted'] is False
    assert match['authors'][0]['authority_status'] == ('known' if authority else 'unknown')
    assert match['official_isbn'] == ref['isbn'] and match['isbn_identity_asserted'] is False
    assert match['same_edition_identity_asserted'] is False and match['product_sku_identity_asserted'] is False
    assert match['original_work_identity_asserted'] is False and match['story_series_identity_asserted'] is False
    assert cat.correlate_publisher_bibliographies('gagaga', manifest, sha)['unchanged_correspondences'] == 1
    row = cat.work_detail(identity)
    assert row['publisher_bibliography_matched'] is True and row['light_novel_confirmed'] is False
    assert row['admission_status'] == 'publisher_bibliographic_record_verified'
    assert not row.get('edition_bibliography_confirmed') and 'edition_identity_basis' not in row
    assert row['bibliography_source_url'] == ref['source_url'] and row['source_url'] == record['source_url']
    assert row['original_language'] is None and row['members'] == [] and row['llm_features'] == []
    public = public_work(row)
    assert public['publisher_bibliography_matched'] is True and public['light_novel_confirmed'] is False
    assert public['bibliography_source_url'] == ref['source_url']
    assert not public.get('edition_bibliography_confirmed')
    encoded = json.dumps(public, ensure_ascii=False)
    assert 'raw_path' not in encoded and 'records[1]' not in encoded
    assert 'bibliographic_correspondence_evidence' not in public


@pytest.mark.parametrize('change', ['own_raw', 'rehashed_projection'])
def test_gagaga_catalogue_integration_rejects_changed_own_raw_and_projection(
        gagaga_catalogue_integration, change):
    from pathlib import Path
    cat, record, _, manifest, sha = gagaga_catalogue_integration()
    if change == 'own_raw':
        Path(record['provenance']['raw_path']).write_text('changed original NDL raw', encoding='utf-8')
    else:
        record['source_record']['publisher'] = record['publisher'] = '原資料にない出版社'
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with cat._connect() as db:
            db.execute('UPDATE entries SET record_json=?,record_sha256=?',
                       (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    with pytest.raises(ValueError):
        cat.correlate_publisher_bibliographies('gagaga', manifest, sha)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM publisher_bibliographic_correspondences').fetchone()[0] == 0


@pytest.mark.parametrize('change', ['literal_role', 'source_id', 'source_classification'])
def test_gagaga_catalogue_integration_drops_confirmation_on_changed_source_fields(
        gagaga_catalogue_integration, change):
    cat, record, ref, manifest, sha = gagaga_catalogue_integration()
    assert cat.correlate_publisher_bibliographies('gagaga', manifest, sha)['new_correspondences'] == 1
    if change == 'literal_role':
        ref['credits'][0]['role'] = 'author'
    elif change == 'source_id':
        ref['publisher_reference_id'] += '0'
    else:
        ref['official_lightnovel_tag'] = True
    result = cat.correlate_publisher_bibliographies('gagaga', manifest, sha)
    assert result['new_correspondences'] == result['matched_publisher_references'] == 0
    row = cat.work_detail('lne:ndl:' + record['id'])
    assert not row.get('publisher_bibliography_matched') and row['light_novel_confirmed'] is False
    assert not row.get('edition_bibliography_confirmed')


@pytest.fixture
def overlap_details_integration(publisher_edition_v2, bulk_bundle, monkeypatch, tmp_path):
    from pathlib import Path
    import recommend_book.light_novel_overlap_details_v1 as verifier

    cat, template, _ = publisher_edition_v2(publisher='オーバーラップ', imprint='オーバーラップノベルス')
    bulk_cat, _, receipt_template, _, _ = bulk_bundle
    assert bulk_cat is cat
    receipt_body = json.loads(receipt_template.read_text(encoding='utf-8'))

    def import_national(xml, identity, stem):
        raw = tmp_path / (stem + '.xml')
        raw.write_text(xml, encoding='utf-8')
        receipt = {**receipt_body, 'raw_path': str(raw), 'raw_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
                   'bytes': raw.stat().st_size}
        path = tmp_path / (stem + '-receipt.json')
        assert cat.import_ndl_bulk(path, write(path, receipt))['inserted'] == 1
        with cat._connect() as db:
            record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?',
                                ('lne:ndl:' + identity,)).fetchone()[0])
        return record, raw

    xml = Path(template['provenance']['raw_path']).read_text(encoding='utf-8')
    url = template['source_url']
    admin = ('<n:BibAdminResource rdf:about="' + url + '"><n:bibRecordCategory>R100000002</n:bibRecordCategory>'
             '<n:record rdf:resource="' + url + '#material"/></n:BibAdminResource>')
    xml = xml.replace('<n:BibResource', admin + '<n:BibResource', 1)
    first, _ = import_national(xml, template['id'], 'overlap-first-national')

    def reference(isbn='9784798613161'):
        url = 'https://over-lap.co.jp/complete-title/product/0/' + isbn + '/?cat=NVL&swrd='
        locator = '(.sidebar-buy__spec-row)[1] .sidebar-buy__spec-label / .sidebar-buy__spec-value'
        return {'publisher_product_reference_id': 'overlap-nvl-product:' + isbn,
            'source_unit': 'publisher_product_detail_bibliographic_reference_not_confirmed_edition',
            'source_url': url, 'title': '主書名３ 副書名', 'owncard_title_literal': '主書名３ 副書名',
            'isbn': isbn, 'isbn_source_literal': isbn, 'isbn_source_basis': 'explicit_ISBN_label_value_not_URL',
            'credits': [{'role': 'unknown', 'name': '著者', 'role_literal': None,
                'role_basis': 'unmarked_creator_name_retained_unknown_not_inferred_from_position_or_css'},
                {'role': 'illustrator', 'name': '絵担当', 'role_literal': 'イラスト', 'role_marker_literal': 'イラスト／',
                 'role_basis': 'explicit_role_marker_in_own_creator_field'}],
            'creator_field_literal': '著者\u2003イラスト／絵担当', 'label': 'オーバーラップノベルス',
            'label_source_scope': 'own_card_label_only_detail_own_label_not_observed', 'detail_label': None,
            'publisher': None, 'form': 'B6判', 'bibliographic_field_literals': [
                {'field_key': 'ISBN', 'label_literal': 'ISBN', 'value_literal': isbn, 'locator': locator}],
            'source_evidence': [{'source_url': url, 'raw_sha256': 'a' * 64, 'locator': value} for value in (
                '.goods-explanation__body h1.goods-explanation__title',
                '.goods-explanation__body p.goods-explanation__author', locator)],
            'owncard_source_evidence': {'source_url': 'https://over-lap.co.jp/Form/Product/ProductList.aspx?cat=NVL',
                'raw_sha256': 'c' * 64, 'selector': '.product-cards > .product-card[1] a / .product-card__title / .badge'},
            'edition_identity_asserted': False, 'original_work_identity_asserted': False, 'story_series_identity_asserted': False}

    references = [reference()]
    manifest = tmp_path / 'overlap-details-synthetic.json'
    sha = write(manifest, {'synthetic': 'source verifier output'})
    def verify(path, expected):
        assert Path(path).resolve() == manifest.resolve() and expected == sha
        assert hashlib.sha256(manifest.read_bytes()).hexdigest() == expected
        return references
    monkeypatch.setattr(verifier, 'verify_overlap_product_details', verify)

    def add_peer(*, isbn='9784798613161', conflicting_title=False, include_reference=False):
        xml = Path(first['provenance']['raw_path']).read_text(encoding='utf-8')
        xml = xml.replace(first['id'], 'R100000002-I2').replace('9784798613161', isbn)
        if conflicting_title:
            xml = xml.replace('主書名', '矛盾する別書名')
        other, raw = import_national(xml, 'R100000002-I2', 'overlap-peer-national')
        if include_reference:
            added = reference(isbn)
            if conflicting_title:
                added['title'] = added['owncard_title_literal'] = '矛盾する別書名３ 副書名'
            references.append(added)
        return other, raw
    return cat, first, references, manifest, sha, add_peer


def test_overlap_details_integration_idempotent_correspondence_keeps_source_role_and_ln_unknown(overlap_details_integration):
    from recommend_book.recommendation.scale_site_v1 import public_work
    cat, first, refs, manifest, sha, _ = overlap_details_integration
    result = cat.correlate_publisher_bibliographies('overlap-details', manifest, sha)
    assert result['new_correspondences'] == 1, result
    with cat._connect() as db:
        saved = dict(db.execute('SELECT * FROM publisher_bibliographic_correspondences').fetchone())
        assert db.execute('SELECT count(*) FROM edition_book_confirmations').fetchone()[0] == 0
    assert saved['provider'] == 'overlap-details'
    assert hashlib.sha256(saved['proof_json'].encode()).hexdigest() == saved['proof_sha256']
    match, = json.loads(saved['proof_json'])['matches']
    assert match['publisher_reference_id'] == refs[0]['publisher_product_reference_id']
    assert match['source_author_role_status'] == 'unknown' and match['source_writer_role_asserted'] is False
    assert match['light_novel_status'] == 'unknown' and match['official_lightnovel_tag'] is None
    assert match['author_identity_asserted'] is False and match['same_edition_identity_asserted'] is False
    assert match['candidate_raw_sha256s'] == [first['provenance']['raw_sha256']]
    assert cat.correlate_publisher_bibliographies('overlap-details', manifest, sha)['unchanged_correspondences'] == 1
    row = cat.work_detail('lne:ndl:' + first['id'])
    assert row['publisher_bibliography_matched'] is True and row['light_novel_confirmed'] is False
    assert not row.get('edition_bibliography_confirmed') and row['members'] == []
    public = public_work(row)
    assert public['publisher_bibliography_matched'] is True and public['light_novel_confirmed'] is False
    assert public['bibliography_source_url'] == refs[0]['source_url']
    assert 'raw_path' not in json.dumps(public) and 'same_isbn_candidates' not in json.dumps(public)
    assert refs[0].get('publisher_reference_id') is None  # Loader adaptation must not mutate formal source rows.


@pytest.mark.parametrize('conflict', ['title', 'kind', 'raw'])
def test_overlap_details_integration_narrow_detail_authenticates_same_isbn_outside_title_scope(overlap_details_integration, conflict):
    cat, first, _, manifest, sha, add_peer = overlap_details_integration
    assert cat.correlate_publisher_bibliographies('overlap-details', manifest, sha)['new_correspondences'] == 1
    peer, raw = add_peer(conflicting_title=True)
    if conflict == 'kind':
        with cat._connect() as db:
            db.execute('UPDATE entries SET kind=? WHERE entry_id=?', ('work', 'lne:ndl:' + peer['id']))
    elif conflict == 'raw':
        raw.write_bytes(b'changed same-ISBN original raw')
    row = cat.work_detail('lne:ndl:' + first['id'])
    assert not row.get('publisher_bibliography_matched') and row['light_novel_confirmed'] is False
    if conflict == 'title':
        assert cat.correlate_publisher_bibliographies('overlap-details', manifest, sha)['matched_publisher_references'] == 0
    else:
        with pytest.raises(ValueError, match='typed identity|SHA'):
            cat.correlate_publisher_bibliographies('overlap-details', manifest, sha)


def test_overlap_details_integration_second_raw_failure_rolls_back_an_earlier_candidate_write(overlap_details_integration):
    cat, _, _, manifest, sha, add_peer = overlap_details_integration
    # Separate complete titles ensure candidate one can be saved before the
    # second candidate's raw fails; no shared title triggers its ISBN early.
    _, raw = add_peer(isbn='9784758047678', conflicting_title=True, include_reference=True)
    raw.write_bytes(b'second ISBN original raw changed')
    with pytest.raises(ValueError, match='SHA'):
        cat.correlate_publisher_bibliographies('overlap-details', manifest, sha)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM publisher_bibliographic_correspondences').fetchone()[0] == 0


def test_overlap_details_integration_same_isbn_context_never_opens_an_unrelated_raw(overlap_details_integration):
    cat, first, _, manifest, sha, add_peer = overlap_details_integration
    _, raw = add_peer(isbn='9784758047678', conflicting_title=True)
    raw.write_bytes(b'unrelated ISBN raw is deliberately unavailable')
    assert cat.correlate_publisher_bibliographies('overlap-details', manifest, sha)['new_correspondences'] == 1
    assert cat.work_detail('lne:ndl:' + first['id'])['publisher_bibliography_matched'] is True


def test_overlap_details_integration_leaves_legacy_overlap_source_dispatch_unchanged(overlap_details_integration, monkeypatch):
    from recommend_book import light_novel_overlap_references_v1 as legacy
    cat, _, _, manifest, sha, _ = overlap_details_integration
    original_rows = [{'publisher_reference_id': 'legacy-overlap-only', 'credits': [{'role': 'unknown', 'name': '人物'}]}]
    monkeypatch.setattr(legacy, 'verify_overlap_references', lambda path, expected: original_rows)
    assert cat._publisher_bibliography_references('overlap', manifest, sha) is original_rows


@pytest.fixture
def futaba_mnovels_integration(publisher_edition_v2, monkeypatch, tmp_path):
    from copy import deepcopy
    from pathlib import Path
    from urllib.parse import quote
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book import light_novel_futaba_references_v1 as source_verifier

    label = 'Ｍノベルス'
    cat, first, _ = publisher_edition_v2(publisher='双葉社', imprint=label, authority=False)
    assert cat.import_editions([first])['inserted'] == 1
    api = source_verifier._FIRST.replace(quote('モンスター文庫'), quote(label))
    index = 'https://www.futabasha.co.jp/search?label_name=' + quote(label)

    def reference(number=1, *, title='主書名３ 副書名', isbn='9784798613161'):
        pointer = '/books/book_informations/0'
        return {'publisher_reference_id': 'futaba-product:opaque-' + str(number),
            'source_unit': 'publisher_product_reference', 'source_url': 'https://www.futabasha.co.jp/book/opaque-' + str(number) + '?type=1',
            'title': title, 'isbn': isbn, 'isbn_source_literal': isbn, 'publisher': None, 'form': '単行本',
            'credits': [{'role': 'author', 'name': '著者'}], 'label': label, 'label_literals': [label],
            'official_lightnovel_tag': None, 'story_series_asserted': False,
            'metadata_conflict': False, 'bibliographic_variants': [],
            'credit_literals': [{'role': '著', 'name': '著者', 'role_field': 'author_type_name',
                                 'locator': pointer + '/book_authors/0'}],
            'source_scope_membership': {'source_unit': 'publisher_label_filtered_product_listing',
                'membership_basis': 'returned_by_publisher_explicit_label_filter', 'filter_parameter': 'label_name',
                'filter_literal': label, 'rendered_index_url': index, 'request_url': api, 'raw_sha256': 'a' * 64,
                'source_label_id_literal': 12, 'source_label_id_locator': pointer + '/label_id',
                'card_label_literal': label, 'card_label_locator': pointer + '/label_name',
                'official_ui_route_proof': 'synthetic-mnovels-ui-route-proof.json',
                'does_not_assert_story_series_or_original_identity': True},
            'source_evidence': [{'source_url': api, 'raw_sha256': 'a' * 64,
                'locator': pointer + ': book_name/isbn_code/book_authors/label_name/label_id/publication_form_value'}]}

    references = [reference()]
    manifest = tmp_path / 'futaba-mnovels-synthetic.json'
    sha = write(manifest, {'synthetic': 'M novels verified source projection'})
    def verify(path, expected):
        assert Path(path).resolve() == manifest.resolve() and expected == sha
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != expected:
            raise ValueError('synthetic M novels source manifest changed')
        return references
    monkeypatch.setattr(source_verifier, 'verify_futaba_mnovels_references', verify, raising=False)

    def add_second():
        other = deepcopy(first)
        xml = Path(first['provenance']['raw_path']).read_text(encoding='utf-8')
        xml = xml.replace(first['id'], 'R100000002-I2').replace('9784798613161', '9784758047678').replace('副書名', '別副書名')
        raw = tmp_path / 'mnovels-second.xml'
        raw.write_text(xml, encoding='utf-8')
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()
        normalized = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), raw.stat().st_size,
            first['source_record']['retrieved_at'], 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac',
            locator='records[1]/recordData').model_dump(mode='json')
        other.update(id=normalized['source_id'], source_url=normalized['source_url'], source_record=normalized,
                     title=normalized['titles'][0]['value'], isbn=['9784758047678'])
        other['provenance'].update(url=normalized['source_url'], raw_path=str(raw), raw_sha256=digest)
        assert cat.import_editions([other])['inserted'] == 1
        references.append(reference(2, title='主書名３ 別副書名', isbn='9784758047678'))
        return raw
    return cat, first, references, manifest, sha, add_second


def test_futaba_mnovels_integration_is_idempotent_bibliography_without_ln_or_edition_confirmation(futaba_mnovels_integration):
    from recommend_book.recommendation.scale_site_v1 import public_work
    cat, first, refs, manifest, sha, _ = futaba_mnovels_integration
    result = cat.correlate_publisher_bibliographies('futaba-mnovels', manifest, sha)
    assert result['new_correspondences'] == result['candidate_editions_verified'] == 1, result
    with cat._connect() as db:
        saved = dict(db.execute('SELECT * FROM publisher_bibliographic_correspondences').fetchone())
        assert db.execute('SELECT count(*) FROM edition_book_confirmations').fetchone()[0] == 0
    assert saved['provider'] == 'futaba-mnovels'
    assert hashlib.sha256(saved['proof_json'].encode()).hexdigest() == saved['proof_sha256']
    proof, = json.loads(saved['proof_json'])['matches']
    assert proof['scope'] == 'publisher_reference_to_ndl_bibliographic_record'
    assert proof['provider'] == 'futaba-mnovels'
    assert proof['official_lightnovel_tag'] is None and proof['light_novel_status'] == 'unknown'
    assert proof['official_publisher'] is None and proof['official_publisher_asserted'] is False
    assert proof['authors'][0]['authority_status'] == 'unknown'
    assert proof['same_edition_identity_asserted'] is False and proof['product_sku_identity_asserted'] is False
    assert 'label_classification_context' not in proof
    assert cat.correlate_publisher_bibliographies('futaba-mnovels', manifest, sha)['unchanged_correspondences'] == 1
    row = cat.work_detail('lne:ndl:' + first['id'])
    assert row['publisher_bibliography_matched'] is True and row['light_novel_confirmed'] is False
    assert not row.get('edition_bibliography_confirmed') and row['original_language'] is None
    assert row['bibliography_source_url'] == refs[0]['source_url']
    public = public_work(row)
    assert public['publisher_bibliography_matched'] is True and public['light_novel_confirmed'] is False
    assert not public.get('edition_bibliography_confirmed')
    assert 'raw_path' not in json.dumps(public) and 'primary_locators' not in json.dumps(public)


def test_futaba_mnovels_integration_source_reclassification_cannot_replay_saved_proof(futaba_mnovels_integration):
    cat, first, refs, manifest, sha, _ = futaba_mnovels_integration
    assert cat.correlate_publisher_bibliographies('futaba-mnovels', manifest, sha)['new_correspondences'] == 1
    refs[0]['official_lightnovel_tag'] = True
    assert cat.correlate_publisher_bibliographies('futaba-mnovels', manifest, sha)['matched_publisher_references'] == 0
    row = cat.work_detail('lne:ndl:' + first['id'])
    assert not row.get('publisher_bibliography_matched') and row['light_novel_confirmed'] is False


@pytest.mark.parametrize('change', ['own_raw', 'rehashed_projection', 'typed_identity'])
def test_futaba_mnovels_integration_authenticates_raw_and_typed_projection_before_saving(futaba_mnovels_integration, change):
    from pathlib import Path
    cat, first, _, manifest, sha, _ = futaba_mnovels_integration
    if change == 'own_raw':
        Path(first['provenance']['raw_path']).write_bytes(b'changed own M novels raw')
    elif change == 'typed_identity':
        with cat._connect() as db:
            db.execute('UPDATE entries SET kind=?', ('work',))
    else:
        first['source_record']['publisher'] = first['publisher'] = '原資料にない別出版社'
        encoded = json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with cat._connect() as db:
            db.execute('UPDATE entries SET record_json=?,record_sha256=?',
                       (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    with pytest.raises(ValueError):
        cat.correlate_publisher_bibliographies('futaba-mnovels', manifest, sha)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM publisher_bibliographic_correspondences').fetchone()[0] == 0


def test_futaba_mnovels_integration_second_raw_failure_rolls_back_first_correspondence(futaba_mnovels_integration):
    cat, _, _, manifest, sha, add_second = futaba_mnovels_integration
    add_second().write_bytes(b'second original raw unavailable')
    with pytest.raises(ValueError, match='SHA'):
        cat.correlate_publisher_bibliographies('futaba-mnovels', manifest, sha)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM publisher_bibliographic_correspondences').fetchone()[0] == 0


def test_futaba_mnovels_integration_preserves_monster_book_confirmation(hifumi_futaba_confirmation):
    cat, record, _, manifest, _ = hifumi_futaba_confirmation('futaba', authority=False)
    assert cat.confirm_publisher_editions('futaba', manifest, 'f' * 64)['confirmed'] == 1
    row = cat.work_detail('lne:ndl:' + record['id'])
    assert row['edition_bibliography_confirmed'] is True and row['light_novel_confirmed'] is True
    assert row['edition_confirmation_evidence']['rule_version'] == 'futaba-monster-ndl-contained-isbn-bibliography-v1'
    assert row['edition_confirmation_evidence']['scope'] == 'edition_only'


@pytest.fixture
def shueisha_dx_fallback_integration(publisher_edition_v2, monkeypatch, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book import light_novel_shueisha_books_v1 as verifier

    def build(*, legacy=False):
        options = {'title': '主書名', 'volume': None, 'official_title': '主書名'} if legacy else {}
        cat, record, _ = publisher_edition_v2(publisher='集英社',
            imprint='スーパーダッシュ文庫' if legacy else '集英社スーパーダッシュ文庫', **options)
        if not legacy:
            raw = Path(record['provenance']['raw_path'])
            xml = raw.read_text(encoding='utf-8').replace('<f:name>著者</f:name>', '<f:name>著, 者, 1990-</f:name>')
            raw.write_text(xml, encoding='utf-8')
            digest = hashlib.sha256(raw.read_bytes()).hexdigest()
            source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), raw.stat().st_size,
                record['source_record']['retrieved_at'], 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac',
                locator='records[1]/recordData').model_dump(mode='json')
            record.update(source_record=source, authors=[c['name'] for c in source['contributors'] if c['role'] == 'author'])
            record['provenance']['raw_sha256'] = digest
        assert cat.import_editions([record])['inserted'] == 1
        identifier = '978-4-7986-1316-1'
        book = {'kind': 'book', 'publisher_book_id': identifier, 'isbn_as_publisher_url': identifier,
            'isbn': '9784798613161', 'source_url': 'https://dash.shueisha.co.jp/bookDetail/index/' + identifier,
            'title': '主書名' if legacy else '主書名３ 副書名', 'publisher': '集英社', 'label': 'スーパーダッシュ文庫',
            'credits': [{'role': '著者', 'name': '著者'}], 'published_on': None,
            'story_series_identity': 'unassessed_no_title_stripping', 'source_evidence': {
                'source_url': 'https://dash.shueisha.co.jp/library/index/', 'source_sha256': 'a' * 64,
                'selector': 'section.result_box:nth-of-type(1) h4 / span.boldText',
                'raw_path': 'private-source-path-must-not-be-public', 'retrieved_at': '2026-10-25T00:00:00Z'}}
        manifest = tmp_path / 'dx-formal-books-synthetic.json'
        sha = write(manifest, {'synthetic': 'strict formal DX book projection'})
        def verify(path, expected):
            assert Path(path).resolve() == manifest.resolve() and expected == sha
            return [book]
        monkeypatch.setattr(verifier, 'verify_shueisha_books', verify)
        return cat, record, book, manifest, sha
    return build


def test_shueisha_dx_fallback_integration_dated_own_writer_and_prefixed_imprint_are_contained_only(shueisha_dx_fallback_integration):
    from recommend_book.recommendation.scale_site_v1 import public_work
    cat, record, book, manifest, sha = shueisha_dx_fallback_integration()
    result = cat.confirm_publisher_editions('shueisha-dx', manifest, sha)
    assert result['confirmed'] == result['ndl_candidate_editions_verified'] == 1
    with cat._connect() as db:
        saved = dict(db.execute('SELECT * FROM edition_book_confirmations').fetchone())
    assert hashlib.sha256(saved['proof_json'].encode()).hexdigest() == saved['proof_sha256']
    proof = json.loads(saved['proof_json'])['match']
    assert proof['scope'] == 'edition_only' and proof['confirmed_scope'] == 'contained_bibliographic_edition_only'
    assert proof['official_printed_ISBN_asserted'] is False
    assert proof['isbn_lookup_basis'] == 'publisher_url_identifier_to_own_primary_isbn'
    assert proof['publisher_book_id'] == book['publisher_book_id']
    assert proof['original_work_identity_asserted'] is False and proof['story_series_identity_asserted'] is False
    assert proof['title_match']['volume'] == '3' and proof['title_match']['subtitle'] == '副書名'
    assert 'private-source-path' not in json.dumps(proof)
    assert cat.confirm_publisher_editions('shueisha-dx', manifest, sha)['unchanged'] == 1
    row = cat.work_detail('lne:ndl:' + record['id'])
    assert row['edition_bibliography_confirmed'] is True and row['light_novel_confirmed'] is True
    assert row['edition_confirmation_evidence'] == proof
    public = public_work(row)
    assert public['edition_bibliography_confirmed'] is True and public['confirmation_source_url'] == book['source_url']
    assert 'edition_confirmation_evidence' not in public and 'raw_path' not in json.dumps(public)


def test_shueisha_dx_fallback_integration_legacy_v1_proof_has_complete_precedence(shueisha_dx_fallback_integration, monkeypatch):
    from recommend_book import light_novel_shueisha_match_v1 as matcher
    cat, record, book, manifest, sha = shueisha_dx_fallback_integration(legacy=True)
    baseline = cat._publisher_edition_match_v1('shueisha-dx', book, record, {})
    assert baseline['rule_version'] == 'publisher-ndl-edition-confirmation-v1'
    monkeypatch.setattr(matcher, 'match_shueisha_bibliographic_edition',
                        lambda *args: pytest.fail('new fallback must never replace an existing v1 proof'), raising=False)
    assert cat._publisher_edition_match('shueisha-dx', book, record, {}) == baseline
    assert cat.confirm_publisher_editions('shueisha-dx', manifest, sha)['confirmed'] == 1
    assert cat.work_detail('lne:ndl:' + record['id'])['edition_confirmation_evidence'] == baseline


def test_shueisha_dx_fallback_integration_never_saves_missing_subtitle_or_changed_raw(shueisha_dx_fallback_integration):
    from pathlib import Path
    cat, record, book, manifest, sha = shueisha_dx_fallback_integration()
    book['title'] = '主書名３'
    assert cat.confirm_publisher_editions('shueisha-dx', manifest, sha)['confirmed'] == 0
    book['title'] = '主書名３ 副書名'
    Path(record['provenance']['raw_path']).write_bytes(b'original NDL raw changed')
    with pytest.raises(ValueError, match='SHA'):
        cat.confirm_publisher_editions('shueisha-dx', manifest, sha)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM edition_book_confirmations').fetchone()[0] == 0


@pytest.fixture
def futaba_workgroup_integration(futaba_mnovels_integration, monkeypatch, tmp_path):
    from copy import deepcopy
    from pathlib import Path
    from recommend_book import light_novel_futaba_workgroups_v1 as verifier
    cat, first, refs, m_path, m_sha, add_second = futaba_mnovels_integration

    def group(title, members):
        natural = {'snapshot': m_sha, 'frame': 'mnovels', 'label': 12, 'literal': title}
        copied = []
        for row in members:
            copied.append({**deepcopy(row), 'grouping_source_literal': title, 'grouping_no_source_literal': 999,
                'media_references': [],
                'grouping_no_semantics': 'unknown_not_an_independent_volume_number',
                'group_membership_evidence': {'source_url': row['source_evidence'][0]['source_url'],
                    'raw_sha256': 'a' * 64, 'locator': '/books/book_informations/0/grouping',
                    'grouping_no_locator': '/books/book_informations/0/grouping_no'}})
        return {'kind': 'official_publisher_field_work_group_reference', 'source_unit': 'publisher_work_group',
            'reference_id': 'futaba-grouping-local:' + hashlib.sha256(json.dumps(natural, sort_keys=True).encode()).hexdigest(),
            'local_reference_key': {'provider': 'futaba', 'source_scope': 'fixed_mnovels_and_mnovels_f_label_frames',
                'source_field': 'grouping', 'source_snapshot_sha256': m_sha,
                'source_frame_id': 'mnovels', 'label_id_scope': 12, 'literal': title},
            'reference_id_kind': 'local_hash_of_publisher_source_field_key', 'reference_id_is_official': False,
            'independent_official_group_id': None, 'title': title, 'grouping_title': title,
            'source_url': members[0]['source_scope_membership']['rendered_index_url'],
            'source_scope': {'kind': 'fixed_mnovels_and_mnovels_f_label_frames', 'label_ids': [12],
                             'source_frame_id': 'mnovels', 'manifest_sha256': m_sha},
            'authors': None, 'publisher': None, 'label': None, 'form': None, 'isbn': None,
            'official_lightnovel_tag': None, 'story_series_asserted': False, 'story_series_identity_asserted': False,
            'original_work_identity_asserted': False, 'independent_volume_numbers_inferred': False,
            'paper_electronic_same_edition_asserted': False, 'members': copied,
            'frame_member_count': len(copied), 'frame_membership_complete': True,
            'exact_grouping_filter_complete': None, 'global_membership_complete': None,
            'grouping_search_url': None, 'source_evidence': [r['group_membership_evidence'] for r in copied]}

    groups = [group('架空Ａ作品群', [refs[0]])]
    path = tmp_path / 'm-workgroups.json'
    sha = write(path, {'synthetic_source_marker': 'workgroups.marker'})
    (tmp_path / 'workgroups.marker').write_text('valid synthetic group source', encoding='utf-8')
    calls = []
    def verify(manifest, expected):
        manifest = Path(manifest).resolve()
        calls.append((str(manifest), expected))
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != expected:
            raise ValueError('synthetic workgroup manifest SHA differs')
        marker = json.loads(manifest.read_text(encoding='utf-8'))['synthetic_source_marker']
        if (manifest.parent / marker).read_text(encoding='utf-8') != 'valid synthetic group source':
            raise ValueError('synthetic relative workgroup raw changed')
        return deepcopy(groups)
    monkeypatch.setattr(verifier, 'verify_futaba_mnovels_workgroups', verify)
    return cat, first, refs, m_path, m_sha, add_second, groups, path, sha, calls, group


def test_futaba_workgroup_admits_only_ndl_correspondences_and_replays_unknown_parent_fields(futaba_workgroup_integration):
    from copy import deepcopy
    from recommend_book.recommendation.scale_site_v1 import public_work
    cat, first, refs, m_path, m_sha, add_second, groups, path, sha, calls, group = futaba_workgroup_integration
    add_second()
    unbound = deepcopy(refs[0])
    unbound.update(publisher_reference_id='futaba-product:unbound', source_url='https://www.futabasha.co.jp/book/unbound?type=1')
    groups[:] = [group('架空Ａ作品群', [*refs, unbound])]
    assert cat.correlate_publisher_bibliographies('futaba-mnovels', m_path, m_sha)['new_correspondences'] == 2
    result = cat.import_futaba_mnovels_workgroups(path, sha)
    assert result['inserted'] == 1 and result['linked'] == 2
    assert len(calls) == 1
    repeated = cat.import_futaba_mnovels_workgroups(path, sha)
    assert repeated['unchanged'] == 1 and repeated['unchanged_memberships'] == 2
    search = cat.search_catalogue('架空Ａ作品群')
    assert search['total'] == 1
    row = cat.work_detail('lns:ndl:' + first['id'])
    assert row['title'] == groups[0]['title'] and row['authors'] == row['publishers'] == row['aliases'] == []
    assert row['year'] is row['original_language'] is row['language'] is row['volume'] is None
    assert row['isbn'] == row['genres'] == [] and row['light_novel_confirmed'] is False
    assert row['publisher_work_group_kind'] == 'futaba_mnovels_grouping'
    assert row['source_frame_member_count'] == 3 and row['verified_bibliographic_member_count'] == 2
    assert len(row['publisher_product_references']) == 3
    assert row['frame_membership_complete'] is True and row['global_membership_complete'] is None
    assert len(row['members']) == 2 and all(r['volume_number'] is None for r in row['members'])
    assert all(r['light_novel_confirmed'] is False for r in row['members'])
    public = public_work(row)
    assert public['publisher_work_group_kind'] == 'futaba_mnovels_grouping'
    assert public['source_frame_member_count'] == 3 and len(public['members']) == 2
    child = public_work(cat.work_detail('lne:ndl:' + first['id']))
    parent = next(member for member in child['members'] if member['entry_kind'] == 'series')
    assert parent['publisher_work_group_kind'] == 'futaba_mnovels_grouping'
    assert parent['source_frame_member_count'] == 3 and parent['verified_bibliographic_member_count'] == 2
    assert 'raw_path' not in json.dumps(public) and 'proof_json' not in json.dumps(public)
    stats = cat.catalogue_stats()
    assert stats['series'] == 0 and stats['publisher_work_groups'] == 1


def test_futaba_workgroup_revalidates_every_match_in_an_existing_correspondence(futaba_workgroup_integration):
    cat, _, refs, m_path, m_sha, add_second, _, path, sha, _, _ = futaba_workgroup_integration
    add_second()
    cat.correlate_publisher_bibliographies('futaba-mnovels', m_path, m_sha)
    with cat._connect() as db:
        binding = db.execute('SELECT * FROM publisher_bibliographic_correspondences ORDER BY edition_id').fetchone()
        proof = json.loads(binding['proof_json'])
        proof['reference_ids'].append(refs[1]['publisher_reference_id'])
        proof['matches'].append(dict(proof['matches'][0], publisher_reference_id=refs[1]['publisher_reference_id']))
        encoded = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE publisher_bibliographic_correspondences SET proof_json=?,proof_sha256=? WHERE edition_id=?',
                   (encoded, hashlib.sha256(encoded.encode()).hexdigest(), binding['edition_id']))
    result = cat.import_futaba_mnovels_workgroups(path, sha)
    assert result['inserted'] == 0 and result['held'] == 1
    with cat._connect() as db:
        assert db.execute("SELECT count(*) FROM entries WHERE kind='series'").fetchone()[0] == 0


def test_futaba_workgroup_exact_title_cannot_become_stored_authority_after_rehash(futaba_workgroup_integration):
    cat, first, _, m_path, m_sha, _, _, path, sha, _, _ = futaba_workgroup_integration
    cat.correlate_publisher_bibliographies('futaba-mnovels', m_path, m_sha)
    cat.import_futaba_mnovels_workgroups(path, sha)
    identity = 'lns:ndl:' + first['id']
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        record['title'] = '原資料にない作品群'
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
                   (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError, match='title|group'):
        cat.work_detail(identity)


def test_futaba_workgroup_never_overwrites_an_existing_parent_or_anchor(futaba_workgroup_integration):
    cat, first, _, m_path, m_sha, _, _, path, sha, _, _ = futaba_workgroup_integration
    cat.correlate_publisher_bibliographies('futaba-mnovels', m_path, m_sha)
    parent_id = 'lns:ndl:' + first['id']
    protected = json.dumps({'protected_old_parent': True})
    with cat._connect() as db:
        db.execute('INSERT INTO entries VALUES(?,?,?,?)',
                   (parent_id, 'series', protected, hashlib.sha256(protected.encode()).hexdigest()))
        db.execute('INSERT INTO edition_series_memberships VALUES(?,?,?,?)',
                   ('lne:ndl:' + first['id'], parent_id, '{}', hashlib.sha256(b'{}').hexdigest()))
        before = [tuple(r) for r in db.execute('SELECT * FROM entries')]
        old_links = [tuple(r) for r in db.execute('SELECT * FROM edition_series_memberships')]
    result = cat.import_futaba_mnovels_workgroups(path, sha)
    assert result['inserted'] == result['linked'] == 0 and result['held'] == 1
    with cat._connect() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM entries')] == before
        assert [tuple(r) for r in db.execute('SELECT * FROM edition_series_memberships')] == old_links


def test_futaba_workgroup_holds_ambiguous_cross_group_correspondence_without_partial_write(futaba_workgroup_integration):
    cat, _, refs, m_path, m_sha, _, groups, path, sha, _, group = futaba_workgroup_integration
    cat.correlate_publisher_bibliographies('futaba-mnovels', m_path, m_sha)
    groups.append(group('別の明示作品群', [refs[0]]))
    with cat._connect() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM entries')]
    result = cat.import_futaba_mnovels_workgroups(path, sha)
    assert result['inserted'] == result['linked'] == 0 and result['held'] == 2
    with cat._connect() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM entries')] == before
        assert db.execute('SELECT count(*) FROM edition_series_memberships').fetchone()[0] == 0


def test_futaba_workgroup_authenticates_later_candidate_raw_before_any_group_write(futaba_workgroup_integration):
    cat, _, refs, m_path, m_sha, add_second, groups, path, sha, _, group = futaba_workgroup_integration
    second_raw = add_second()
    cat.correlate_publisher_bibliographies('futaba-mnovels', m_path, m_sha)
    groups.append(group('別の明示作品群', [refs[1]]))
    second_raw.write_bytes(b'changed corresponding NDL raw')
    with pytest.raises(ValueError, match='SHA|hash'):
        cat.import_futaba_mnovels_workgroups(path, sha)
    with cat._connect() as db:
        assert db.execute("SELECT count(*) FROM entries WHERE kind='series'").fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM edition_series_memberships').fetchone()[0] == 0


def test_futaba_workgroup_membership_does_not_propagate_classification_history_or_features(futaba_workgroup_integration, monkeypatch):
    cat, first, _, m_path, m_sha, _, _, path, sha, _, _ = futaba_workgroup_integration
    identity = 'lne:ndl:' + first['id']
    cat.correlate_publisher_bibliographies('futaba-mnovels', m_path, m_sha)
    with cat._connect() as db:
        db.execute('INSERT INTO read_status VALUES(?,?,?)', (identity, 'read', 'synthetic-time'))
        db.execute('INSERT INTO features VALUES(?,?,?)', (identity, 'unavailable-feature-source', 'f' * 64))
        history = [tuple(r) for r in db.execute('SELECT * FROM read_status')]
        features = [tuple(r) for r in db.execute('SELECT * FROM features')]
    cat.import_futaba_mnovels_workgroups(path, sha)
    original = cat._attach_book_confirmations
    def independently_confirmed(rows, stored, cache):
        original(rows, stored, cache)
        for row in rows:
            if row['entry_id'] == identity:
                row['light_novel_confirmed'] = True
    monkeypatch.setattr(cat, '_attach_book_confirmations', independently_confirmed)
    row = cat.work_detail('lns:ndl:' + first['id'])
    assert row['light_novel_confirmed'] is False and row['read_status'] == 'unknown'
    assert row['members'][0]['light_novel_confirmed'] is True and row['members'][0]['read_status'] == 'read'
    assert row['llm_features'] == []
    with cat._connect() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM read_status')] == history
        assert [tuple(r) for r in db.execute('SELECT * FROM features')] == features


def test_futaba_workgroup_operation_cache_is_scoped_by_manifest_path_and_reads_only_candidates(futaba_workgroup_integration, tmp_path):
    from copy import deepcopy
    from pathlib import Path
    cat, first, _, m_path, m_sha, add_second, _, path, sha, calls, _ = futaba_workgroup_integration
    cat.correlate_publisher_bibliographies('futaba-mnovels', m_path, m_sha)
    add_second().write_bytes(b'unrelated edition raw deliberately unavailable')
    assert cat.import_futaba_mnovels_workgroups(path, sha)['inserted'] == 1
    assert len(calls) == 1
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?',
                                      ('lns:ndl:' + first['id'],)).fetchone()[0])
    cache = {}
    cat._project(record, cache)
    cat._project(record, cache)
    assert len(calls) == 2  # One source verification in each operation, not per group/member.
    second_root = tmp_path / 'same-manifest-another-root'
    second_root.mkdir()
    second = second_root / path.name
    second.write_bytes(path.read_bytes())
    (second_root / 'workgroups.marker').write_text('different relative source', encoding='utf-8')
    changed = deepcopy(record)
    changed['publisher_series_proof']['manifest_path'] = str(second)
    with pytest.raises(ValueError, match='relative.*raw'):
        cat._project(changed, cache)
