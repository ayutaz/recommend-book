"""Synthetic fixtures only; private personal records are never test fixtures."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from recommend_book.recommendation.narou import load_narou_reference


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hash(rows):
    return hashlib.sha256((json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode()).hexdigest()


def fixture(tmp_path):
    db = tmp_path/'fixture.sqlite'
    with sqlite3.connect(db) as c:
        c.executescript('''PRAGMA user_version=1;
        CREATE TABLE imports(snapshot_id TEXT PRIMARY KEY, observed_at TEXT, imported_at TEXT);
        CREATE TABLE rating_observations(snapshot_id TEXT, ncode TEXT, title TEXT, my_rating INTEGER, first_rated_at TEXT, last_rated_at TEXT, PRIMARY KEY(snapshot_id,ncode));
        CREATE TABLE reading_status(ncode TEXT PRIMARY KEY, read_status TEXT, completed_reading INTEGER, dropped_at_episode INTEGER);
        CREATE TABLE novel_metadata(ncode TEXT PRIMARY KEY,title TEXT,writer TEXT,genre INTEGER,keyword TEXT,story TEXT,global_point INTEGER,raw_json TEXT,fetch_status TEXT);
        INSERT INTO imports VALUES ('old','2026-01-01','2026-01-02'),('new','2026-02-01','2026-02-02');
        INSERT INTO rating_observations VALUES ('old','N0001AA','Old title',2,'2026-01-01',NULL),('new','N0001AA','Fixture title',5,'2026-01-01',NULL),('new','N0002BB','Missing metadata',3,NULL,NULL);
        INSERT INTO reading_status VALUES ('N0001AA','unknown',NULL,NULL),('N0002BB','unread',0,NULL);
        ''')
        c.execute('INSERT INTO novel_metadata VALUES (?,?,?,?,?,?,?,?,?)', ('N0001AA','Official title',' Same Writer ',201,'keyword','A character grows and seeks revenge.',123,json.dumps({'ncode':'N0001AA','userid':42,'writer':' Same Writer '}),'success'))
        c.execute('INSERT INTO novel_metadata VALUES (?,?,?,?,?,?,?,?,?)', ('N0002BB',None,None,None,None,None,None,None,'not_found'))
    sources = [{'ncode':'N0001AA','title':'Fixture title','keyword':'keyword','story':'A character grows and seeks revenge.'}]
    annotation = {'prompt_version':'content-v1','rating_blinded':True,'source_sha256':source_hash(sources),'batches':[], 'review_version':'content-review-v1', 'novels':[{'ncode':'N0001AA','summary':'Fixture','premise':'Fixture','goal':'Fixture','gender':'unknown','tone':'unknown','themes':[{'id':'growth','prominence':'secondary','source':'story','evidence':'grows'},{'id':'revenge','prominence':'central','source':'story','evidence':'revenge'},{'id':'power','prominence':'central','source':'title','evidence':'Fixture'}]}]}
    annotations = tmp_path/'annotations.json'
    annotations.write_text(json.dumps(annotation), 'utf-8')
    return db, annotations


def change_annotation(path, change):
    value = json.loads(path.read_bytes()); change(value)
    path.write_text(json.dumps(value), 'utf-8')


def test_reference_api_bridge_identity_reading_and_byte_preservation(tmp_path):
    db, annotations = fixture(tmp_path)
    before = digest(db), digest(annotations)
    result = load_narou_reference(db, annotations)
    assert (digest(db), digest(annotations)) == before
    assert [r['work_id'] for r in result['rows']] == ['narou:n0001aa','narou:n0002bb']
    first, missing = result['rows']
    assert first['rating'] == 5 and first['title'] == 'Fixture title'
    assert first['features'] == {'genre.fantasy':1.0,'theme.growth':.3,'theme.revenge':1.0}
    assert first['author_ids'] == ['narou_user:42']
    assert first['author_group_keys'] == ['narou_writer_name:same writer']
    assert first['series_ids'] == [] and first['read_status'] == 'unknown'
    assert first['native_features']['narou.theme.power'] == 1.0
    assert first['external_ids'] == [{'source':'narou','value':'n0001aa'}]
    assert first['popularity'] == 123
    assert missing['features'] == {} and missing['rating'] == 3 and missing['read_status'] == 'unread'
    assert all(r['provenance']['human_taxonomy_truth'] is False for r in result['rows'])
    assert result['audit']['observations_n'] == 3
    assert result['audit']['latest_rows_n'] == 2
    assert result['audit']['rated_history_exclusion_n'] == 2
    assert result['audit']['missing_annotation_n'] == 1


@pytest.mark.parametrize('change',[
    lambda a:a.update(source_sha256='0'*64),
    lambda a:a['novels'].append(dict(a['novels'][0])),
    lambda a:a['novels'][0].update(ncode='N9999ZZ'),
    lambda a:a['novels'][0]['themes'][0].update(evidence='not in source'),
    lambda a:a['novels'][0]['themes'][0].update(id='invented'),
    lambda a:a['novels'][0]['themes'][0].update(prominence='invented'),
    lambda a:a.update(rating_blinded=False),
])
def test_stale_or_invalid_annotations_rejected_without_personal_identifiers(tmp_path, change):
    db, annotations = fixture(tmp_path)
    change_annotation(annotations,change)
    with pytest.raises(ValueError) as exc: load_narou_reference(db,annotations)
    assert 'N0001AA' not in str(exc.value) and 'Fixture title' not in str(exc.value)


def test_names_blocking_only_and_never_canonical_author_ids(tmp_path):
    db,annotations=fixture(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute('UPDATE novel_metadata SET raw_json=? WHERE ncode=?',(json.dumps({'ncode':'N0001AA','writer':'Same Writer'}),'N0001AA'))
    row=load_narou_reference(db,annotations)['rows'][0]
    assert row['author_ids']==[]
    assert row['author_group_keys']==['narou_writer_name:same writer']


def test_unmapped_secondary_revenge_gender_tone_not_inferred(tmp_path):
    db,annotations=fixture(tmp_path)
    def change(a):
        a['novels'][0].update(gender='female',tone='dark')
        a['novels'][0]['themes'][1]['prominence']='secondary'
        a['novels'][0]['themes'].append({'id':'cozy','prominence':'central','source':'title','evidence':'Fixture'})
    change_annotation(annotations,change)
    row=load_narou_reference(db,annotations)['rows'][0]
    assert row['features']=={'genre.fantasy':1.0,'theme.growth':.3}
    assert set(row['unmapped_native_theme_ids'])=={'power','revenge','cozy'}
    assert 'atmosphere.tranquil' not in row['features']


@pytest.mark.parametrize('raw',['{"x":1,"x":2}','{"x":NaN}','{"x":1e999}'])
def test_strict_json_refuses_ambiguous_annotations(tmp_path,raw):
    db,annotations=fixture(tmp_path);annotations.write_text(raw,'utf-8')
    with pytest.raises(ValueError):load_narou_reference(db,annotations)


def test_live_wal_refused(tmp_path):
    db,annotations=fixture(tmp_path)
    Path(str(db)+'-wal').write_bytes(b'not empty')
    with pytest.raises(ValueError,match='WAL'):load_narou_reference(db,annotations)


def test_snapshot_ties_resolve_by_imported_then_snapshot_id(tmp_path):
    db,annotations=fixture(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO imports VALUES ('z','2026-02-01','2026-02-02')")
        c.execute("INSERT INTO rating_observations VALUES ('z','N0001AA','Fixture title',4,'2026-01-01',NULL)")
    row=load_narou_reference(db,annotations)['rows'][0]
    assert row['rating']==4 and row['provenance']['rating_observation']['snapshot_id']=='z'


def test_metadata_other_work_identity_is_not_merged(tmp_path):
    db,annotations=fixture(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute('UPDATE novel_metadata SET raw_json=? WHERE ncode=?',(json.dumps({'ncode':'N9999ZZ','userid':42}),'N0001AA'))
    with pytest.raises(ValueError,match='Metadata identity'):load_narou_reference(db,annotations)


def test_orphan_import_and_unsupported_schema_rejected(tmp_path):
    db,annotations=fixture(tmp_path)
    with sqlite3.connect(db) as c:c.execute("DELETE FROM imports WHERE snapshot_id='old'")
    with pytest.raises(ValueError,match='Orphaned'):load_narou_reference(db,annotations)
    with sqlite3.connect(db) as c:c.execute('PRAGMA user_version=2')
    with pytest.raises(ValueError,match='schema'):load_narou_reference(db,annotations)


def test_conservative_mapping_cannot_silently_expand(tmp_path):
    db,annotations=fixture(tmp_path)
    root=Path(__file__).parents[1]
    bridge=json.loads((root/'config/recommendation-feature-bridge.json').read_bytes())
    bridge['theme_rules'].append({'source_id':'power','prominence':['central'],'target_id':'theme.power'})
    path=tmp_path/'bridge.json';path.write_text(json.dumps(bridge),'utf-8')
    with pytest.raises(ValueError,match='Unapproved'):load_narou_reference(db,annotations,bridge=path)


@pytest.mark.parametrize('userid',[True,0,-1,'42',42.0,None])
def test_only_explicit_integer_api_userid_becomes_author_identity(tmp_path,userid):
    db,annotations=fixture(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute('UPDATE novel_metadata SET raw_json=? WHERE ncode=?',(json.dumps({'ncode':'N0001AA','userid':userid}),'N0001AA'))
    assert load_narou_reference(db,annotations)['rows'][0]['author_ids']==[]


def test_popularity_is_observed_only_not_rating_prediction(tmp_path):
    db,annotations=fixture(tmp_path)
    with sqlite3.connect(db) as c:c.execute('UPDATE novel_metadata SET global_point=NULL')
    rows=load_narou_reference(db,annotations)['rows']
    assert all(r['popularity'] is None for r in rows)
    assert rows[0]['rating']==5


def test_source_changes_during_read_refused(tmp_path,monkeypatch):
    from recommend_book.recommendation import narou
    db,annotations=fixture(tmp_path)
    original=narou.validate_annotations
    def validating(*args):
        result=original(*args)
        annotations.write_bytes(annotations.read_bytes()+b'\n')
        return result
    monkeypatch.setattr(narou,'validate_annotations',validating)
    with pytest.raises(ValueError,match='changed during'):load_narou_reference(db,annotations)


def test_feature_provenance_keeps_observed_weight_separate_from_confidence(tmp_path):
    db,annotations=fixture(tmp_path)
    row=load_narou_reference(db,annotations)['rows'][0]
    for label,proofs in row['feature_provenance'].items():
        for proof in proofs:
            assert proof['raw_confidence'] is None and proof['calibrated_confidence'] is None
            assert proof['human_taxonomy_truth'] is False and proof['feature_strength']==row['features'][label]
            assert proof['database_sha256']==digest(db) and proof['annotations_sha256']==digest(annotations)
    assert row['provenance']['has_read_whole_work'] is None


@pytest.mark.parametrize('original,common',[('unknown','unknown'),('unread','unread'),('reading','read'),('completed','read'),('dropped','read')])
def test_native_reading_states_map_to_common_encountered_state_and_engine(tmp_path,original,common):
    from recommend_book.recommendation.engine import rank_candidates
    db,annotations=fixture(tmp_path)
    with sqlite3.connect(db) as c:c.execute('UPDATE reading_status SET read_status=? WHERE ncode=?',(original,'N0001AA'))
    loaded=load_narou_reference(db,annotations)
    row=loaded['rows'][0]
    assert row['read_status']==common
    assert row['source_read_status']==original
    assert row['provenance']['original_read_status']==original
    assert row['provenance']['has_read_whole_work'] is None
    assert rank_candidates(loaded['rows'],loaded['rows'])['audit']['eligible_work_ids']==[]
