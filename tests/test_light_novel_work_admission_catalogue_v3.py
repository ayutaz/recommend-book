"""Work-v3 reaches the real catalogue projector without acquiring series identity."""
import hashlib
import json
from pathlib import Path

import pytest

from test_light_novel_admission_v3 import source, claim
from test_light_novel_work_admission_v3 import work_source
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.light_novel_classification_v1 import _entity


def encoded(record):
    text = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return text, hashlib.sha256(text.encode()).hexdigest()


def test_work_v3_import_projects_work_and_idempotent_replay_preserves_read_status(work_source, tmp_path):
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    record = work_source()
    assert cat.ingest([record])['inserted'] == 1
    row = cat.work_detail('lnw:wd:Q1')
    assert row['entry_kind'] == 'work' and row['entry_id'] == 'lnw:wd:Q1'
    assert row['title'] == record['title'] and row['authors'] == [{'qid': 'Q2', 'name': '著者甲'}]
    assert row['original_language'] == 'ja' and row['family'] == 'light_novel'
    assert any(genre['qid'] == 'Q747381' for genre in row['genres'])
    assert not row.get('series_entry_id') and not row.get('series_membership_evidence')
    cat.set_read_status('lnw:wd:Q1', 'reading')
    with cat._connect() as db:
        before = tuple(db.execute('SELECT * FROM entries').fetchone())
        statuses = [tuple(r) for r in db.execute('SELECT * FROM read_status')]
    assert cat.ingest([record])['unchanged'] == 1
    with cat._connect() as db:
        assert tuple(db.execute('SELECT * FROM entries').fetchone()) == before
        assert [tuple(r) for r in db.execute('SELECT * FROM read_status')] == statuses
        assert db.execute("SELECT COUNT(*) FROM entries WHERE entry_id='lns:wd:Q1'").fetchone()[0] == 0
    # Series-only source preparation must not silently relabel this work.
    with pytest.raises(ValueError, match='Only admitted Japanese novel series'):
        _entity(record)


@pytest.mark.parametrize('existing_kind', ['work', 'series', 'titleless_ndl', 'same_qid_series'])
def test_existing_work_or_series_alias_conflict_holds_and_preserves_old_rows(work_source, tmp_path, existing_kind):
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    record = work_source()
    identity, kind = ('lnw:wd:Q9', 'work') if existing_kind == 'work' else ('lns:wd:Q9', 'series')
    old = {'title': 'Distinct old primary title', 'aliases': ['別 題・甲']}
    if existing_kind == 'titleless_ndl':
        identity = 'lns:ndl:R100000002-I9'
        old = {'schema_version': 'light-novel-ndl-series-v1', 'anchor_edition_record': {
            'source_record': {'titles': [{'value': '作品・甲 : 保存済み副題'}], 'extra': {}}}}
    elif existing_kind == 'same_qid_series':
        identity = 'lns:wd:Q1'
        old = {'title': 'Different title even after normalization', 'aliases': []}
    text, pin = encoded(old)
    with cat._connect() as db:
        db.execute('INSERT INTO entries VALUES(?,?,?,?)', (identity, kind, text, pin))
        db.execute('INSERT INTO read_status VALUES(?,?,?)', (identity, 'read', 'saved-before-v3'))
    with cat._connect() as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM entries')]
        statuses = [tuple(r) for r in db.execute('SELECT * FROM read_status')]
    result = cat.ingest([record])
    assert result['failed'] == 1 and result['inserted'] == result['updated'] == 0
    assert 'crosswalk hold' in result['failures'][0]['reason']
    with cat._connect() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM entries')] == before
        assert [tuple(r) for r in db.execute('SELECT * FROM read_status')] == statuses
        assert db.execute('SELECT COUNT(*) FROM edition_series_memberships').fetchone()[0] == 0


@pytest.mark.parametrize('tamper', ['series_cotype', 'anime_cotype', 'manga_cotype', 'P629',
    'novel_authority_hash', 'linked_author_hash', 'conditions_hash', 'kind_series'])
def test_real_projector_rejects_conflicts_or_bad_pins_before_entry_insertion(work_source, tmp_path, tamper):
    record = work_source()
    if tamper in ('series_cotype', 'anime_cotype', 'manga_cotype', 'P629'):
        provenance = record['provenance'];path = Path(provenance['raw_path'])
        raw = json.loads(path.read_bytes());entity = raw['entities']['Q1']
        if tamper == 'P629':entity['claims']['P629'] = [claim('Q9')]
        else:
            qid = {'series_cotype': 'Q1667921', 'anime_cotype': 'Q21198342', 'manga_cotype': 'Q8274'}[tamper]
            entity['claims']['P31'].append(claim(qid));record['types'] = sorted([*record['types'], qid])
        path.write_text(json.dumps(raw), encoding='utf8')
        provenance['raw_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif tamper == 'novel_authority_hash':record['work_admission_proof']['type_authorities']['Q8261']['raw_sha256'] = '0' * 64
    elif tamper == 'linked_author_hash':record['provenance']['linked_provenance']['Q2']['raw_sha256'] = '0' * 64
    elif tamper == 'conditions_hash':record['provenance']['conditions_sha256'] = '0' * 64
    else:record['kind'] = 'series'
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    with pytest.raises(ValueError):cat._project(record, {})
    result = cat.ingest([record])
    assert result['failed'] == 1 and result['inserted'] == result['updated'] == 0
    with cat._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM features').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM edition_series_memberships').fetchone()[0] == 0


def test_cycle_membership_claim_remains_work_metadata_without_auto_membership(work_source, tmp_path):
    record = work_source(lambda e: e['claims'].update(P179=[claim('Q99')], P155=[claim('Q8')]))
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    assert cat.ingest([record])['inserted'] == 1
    row = cat.work_detail('lnw:wd:Q1')
    assert row['entry_kind'] == 'work'
    assert row['relations']['P179'] == ['Q99'] and row['relations']['P155'] == ['Q8']
    assert not row.get('series_entry_id')
    with cat._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM edition_series_memberships').fetchone()[0] == 0
