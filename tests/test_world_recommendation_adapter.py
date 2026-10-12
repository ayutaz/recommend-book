"""Work recommendation rows from a sealed derived DB; no model invocation."""
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3

import pytest

from recommend_book.classification.validation import input_hash, target_language, validate_result
from recommend_book.database import DatasetDB
from recommend_book.jobs import canonical_json
from recommend_book.models import Evidence, WorkInput
from test_import_classification_database import fixture, module, record, classification


def adapter():
    return importlib.import_module('recommend_book.recommendation.world')


@pytest.fixture
def database(fixture):
    module('import_classification_database').import_classifications(**fixture)
    return fixture['output_dir']/'dataset.sqlite'


def change_result(db, key, mutate):
    value=json.loads(db.execute('SELECT result_json FROM classification_inputs WHERE job_key=?',(key,)).fetchone()[0])
    mutate(value)
    raw=canonical_json(value)
    db.execute('UPDATE classification_inputs SET result_json=?,result_sha256=? WHERE job_key=?',
        (raw,hashlib.sha256(raw.encode()).hexdigest(),key))
    for label in value['labels']:
        db.execute('''UPDATE classification_labels SET axis=?,decision=?,scope=?,expression_id=?,
            relevance=?,raw_confidence=?,label_json=? WHERE job_key=? AND label_id=?''',
            (label['axis'],label['decision'],label['scope'],label['expression_id'],label['relevance'],
             label['raw_confidence'],canonical_json(label),key,label['label_id']))


def test_work_rows_keep_all_selected_work_ids_and_explicit_defaults(database):
    before=hashlib.sha256(database.read_bytes()).hexdigest()
    loaded=adapter().load_world_database(database)
    rows=loaded['rows'];audit=loaded['audit']
    assert len(rows)==2 and len({r['work_id'] for r in rows})==2
    assert audit['selected_inputs']==3 and audit['distinct_works']==2
    assert audit['completed_inputs']==2 and audit['held_inputs']==1
    assert audit['empty_features']==1 and audit['scope_excluded_labels']==1
    featured=next(r for r in rows if r['features'])
    assert featured['features']=={'genre.mystery':1.0}
    for row in rows:
        assert set(row)=={'work_id','title','features','rating','author_ids','series_ids','read_status',
                         'external_ids','feature_provenance','popularity','authors','original_language',
                         'first_publication_year','region','series','source_urls'}
        assert row['rating'] is None and row['popularity'] is None and row['read_status']=='unknown'
        assert row['author_ids']==[] and row['series_ids']==[]
        assert row['authors'] and row['original_language']=='en'
        assert row['first_publication_year'] is None and row['region']==[] and row['series'] is None
        assert row['source_urls'] and all(url.startswith('https://example.org/') for url in row['source_urls'])
        assert all(set(i)=={'source','value'} for i in row['external_ids'])
    provenance=featured['feature_provenance']['genre.mystery'][0]
    assert provenance['relevance'] is None and provenance['feature_strength']==1.0
    assert provenance['raw_confidence']==0.8123456789 and provenance['raw_confidence_used_as_weight'] is False
    assert provenance['input_hash'] and provenance['source_record_ids'] and provenance['evidence'][0]['locator']=='summary'
    assert audit['database_sha256']==before and audit['database_unchanged'] is True
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before
    assert not Path(str(database)+'-wal').exists() and not Path(str(database)+'-shm').exists()


def test_duplicate_expression_inputs_use_max_without_confidence_weight(database):
    with sqlite3.connect(database) as db:
        original,expression=[r[0] for r in db.execute("SELECT job_key FROM classification_inputs WHERE status='completed' ORDER BY expression_id IS NOT NULL")]
        change_result(db,original,lambda r:r['labels'][0].update(relevance=0.4,raw_confidence=0.99))
        change_result(db,expression,lambda r:r['labels'][0].update(scope='work',expression_id=None,relevance=0.8,raw_confidence=0.01))
    loaded=adapter().load_world_database(database)
    row=next(r for r in loaded['rows'] if r['features'])
    assert row['features']=={'genre.mystery':0.8}
    assert len(row['feature_provenance']['genre.mystery'])==2
    assert loaded['audit']['duplicate_feature_observations']==1


@pytest.mark.parametrize('decision',['absent','unknown'])
def test_nonpresent_and_unreported_axes_are_not_negative_features(database,decision):
    with sqlite3.connect(database) as db:
        key=db.execute("SELECT job_key FROM classification_inputs WHERE expression_id IS NULL AND status='completed'").fetchone()[0]
        def mutate(result):
            result['labels'][0].update(decision=decision,relevance=None)
            if decision=='unknown':
                result['labels'][0]['missing_reason']='Unknown from provided evidence'
                result['missing_axes'].append({'axis':'genre','reason':'Unknown','decision':'unknown'})
        change_result(db,key,mutate)
    loaded=adapter().load_world_database(database)
    assert all(row['features']=={} for row in loaded['rows'])
    assert loaded['audit']['empty_features']==2


def test_only_explicit_db_author_identity_and_independent_series_are_used(database):
    with sqlite3.connect(database) as db:
        agent,work,version=db.execute("SELECT agent_id,work_id,source_version_id FROM contributions WHERE role='author' AND work_id IN (SELECT work_id FROM classification_inputs WHERE status='held')").fetchone()
        db.execute('INSERT INTO external_ids VALUES(?,?,?,?,?,?)',('wikidata','Q999','Q999','agent',agent,version))
        db.execute('INSERT INTO series VALUES(?,?,?)',('series-fixture','Series fixture','series fixture'))
        db.execute('INSERT INTO work_relations VALUES(?,?,?,?,?,?)',(work,None,'series-fixture','series_member','1',version))
    row=next(r for r in adapter().load_world_database(database)['rows'] if not r['features'])
    assert row['author_ids']==[agent] and row['series_ids']==['series-fixture']
    assert not any(i['value']=='Q999' for i in row['external_ids'])


@pytest.mark.parametrize('mutation',['input_hash','result_hash','label_value','label_json','evidence_ref'])
def test_changed_input_or_label_contract_is_rejected(database,mutation):
    with sqlite3.connect(database) as db:
        key=db.execute("SELECT job_key FROM classification_inputs WHERE status='completed' AND expression_id IS NULL").fetchone()[0]
        if mutation=='input_hash':db.execute('UPDATE classification_inputs SET input_hash=? WHERE job_key=?',('0'*64,key))
        if mutation=='result_hash':db.execute('UPDATE classification_inputs SET result_sha256=? WHERE job_key=?',('0'*64,key))
        if mutation=='label_value':db.execute('UPDATE classification_labels SET relevance=? WHERE job_key=?',(0.01,key))
        if mutation=='label_json':db.execute('UPDATE classification_labels SET label_json=? WHERE job_key=?',('{"invalid":true}',key))
        if mutation=='evidence_ref':db.execute('UPDATE classification_label_evidence SET locator=? WHERE job_key=?',('invented',key))
    with pytest.raises(ValueError):adapter().load_world_database(database)


def test_invalid_translation_style_is_validated_before_scope_exclusion(database):
    with sqlite3.connect(database) as db:
        key=db.execute("SELECT job_key FROM classification_inputs WHERE expression_id IS NOT NULL").fetchone()[0]
        # Keep the valid translation style evidence; it cannot be a work feature.
        value=json.loads(db.execute('SELECT result_json FROM classification_inputs WHERE job_key=?',(key,)).fetchone()[0])
        label=value['labels'][0]
        label.update(label_id='style.dialogue_driven',axis='style',scope='translation',relevance=0.9)
        value['missing_axes']=[r for r in value['missing_axes'] if r['axis']!='style']+[{'axis':'genre','decision':'unknown','reason':'Missing'}]
        db.execute('DELETE FROM classification_label_evidence WHERE job_key=?',(key,))
        db.execute('DELETE FROM classification_labels WHERE job_key=?',(key,))
        db.execute('INSERT INTO classification_labels VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (key,label['label_id'],label['axis'],label['decision'],label['scope'],label['expression_id'],
             label['relevance'],label['raw_confidence'],None,'uncalibrated',None,0,canonical_json(label)))
        ref=label['evidence'][0]
        db.execute('INSERT INTO classification_label_evidence VALUES(?,?,?,?,?,?,?,?,?)',
            (key,label['label_id'],0,ref['evidence_id'],ref['locator'],ref['quote'],ref['summary'],ref['directness'],canonical_json(ref)))
        raw=canonical_json(value);db.execute('UPDATE classification_inputs SET result_json=?,result_sha256=? WHERE job_key=?',
            (raw,hashlib.sha256(raw.encode()).hexdigest(),key))
    # Adapter must validate before excluding a label, so unqualified expression
    # evidence is rejected rather than using scope exclusion as a validation bypass.
    with pytest.raises(ValueError):adapter().load_world_database(database)


def test_nonempty_wal_rejected_without_writes(database):
    Path(str(database)+'-wal').write_bytes(b'not checkpointed')
    with pytest.raises(ValueError,match='WAL'):adapter().load_world_database(database)


def test_valid_original_style_is_excluded_in_default_work_mode(tmp_path):
    source=tmp_path/'style-source.sqlite';derived=tmp_path/'style-derived.sqlite'
    with DatasetDB(source) as db:
        db.ingest([record('original',evidence=[Evidence(evidence_id='en-excerpt',kind='excerpt',
            text='A detective solves a murder.',source_url='https://example.org/original',
            rights_policy_id='fixture',llm_input_allowed=True,locator='page1',language='en')])])
        work=db.work_inputs()[0]
    value=classification(work)
    value['labels'][0].update(label_id='style.dialogue_driven',axis='style',scope='original',relevance=0.9)
    value['missing_axes']=[r for r in value['missing_axes'] if r['axis']!='style']+[{'axis':'genre','decision':'unknown','reason':'Not established'}]
    assert validate_result(value,work)==value
    importer=module('import_classification_database')
    original=sqlite3.connect(source);original.row_factory=sqlite3.Row
    db=sqlite3.connect(derived)
    try:
        original.backup(db)
        for ddl in importer.DDL:db.execute(ddl)
        state={'status':'completed','key':'fixture-key'}
        manifest={'run_id':'style-fixture','config':{},'jobs':{'fixture-key':state}}
        importer.insert_rows(db,original,manifest,{},{},{},{},{'fixture-key':work},{'fixture-key':value})
        db.commit()
    finally:db.close();original.close()
    loaded=adapter().load_world_database(derived)
    assert loaded['rows'][0]['features']=={} and loaded['rows'][0]['feature_provenance']=={}
    assert loaded['audit']['scope_excluded_by_scope']=={'original':1}
    assert loaded['audit']['empty_features']==1


@pytest.mark.parametrize('strength',[0.0,0.25,1.0])
def test_numeric_relevance_is_preserved_as_strength(database,strength):
    with sqlite3.connect(database) as db:
        key=db.execute("SELECT job_key FROM classification_inputs WHERE expression_id IS NULL AND status='completed'").fetchone()[0]
        change_result(db,key,lambda value:value['labels'][0].update(relevance=strength))
    row=next(r for r in adapter().load_world_database(database)['rows'] if r['features'])
    assert row['features']['genre.mystery']==strength


def test_change_during_read_is_rejected(database,monkeypatch):
    world=adapter();original=world._read
    def changing(db):
        result=original(db)
        with database.open('ab') as stream:stream.write(b'fixture change')
        return result
    monkeypatch.setattr(world,'_read',changing)
    with pytest.raises(ValueError,match='changed'):world.load_world_database(database)


@pytest.mark.parametrize('raw',['{"a":1,"a":2}','{"a":NaN}','{"a":1e999}'])
def test_strict_saved_json_rejects_ambiguous_or_nonfinite_values(raw):
    with pytest.raises(ValueError):adapter()._json(raw)


def test_display_conflicts_keep_original_primary_and_do_not_infer_author_identity(database):
    with sqlite3.connect(database) as db:
        key,raw=db.execute('SELECT job_key,input_json FROM classification_inputs WHERE expression_id IS NOT NULL').fetchone()
        work=WorkInput.model_validate_json(raw)
        work.title='Expression display title';work.authors=['Different displayed name']
        work.original_language='ja';work.first_publication_year=2001;work.region=['JP'];work.series='Display-only series'
        digest=input_hash(work)
        db.execute('UPDATE classification_inputs SET input_json=?,input_hash=? WHERE job_key=?',
            (canonical_json(work.model_dump(mode='json')),digest,key))
        change_result(db,key,lambda value:value.update(input_hash=digest,target_language=target_language(work)))
    loaded=adapter().load_world_database(database)
    row=next(r for r in loaded['rows'] if r['features'])
    assert row['title']=='Book one' and row['authors']==['Author one']
    assert row['original_language']=='en' and row['first_publication_year'] is None
    assert row['region']==[] and row['series'] is None
    assert row['author_ids']==[] and row['series_ids']==[]
    assert {'https://example.org/one','https://example.org/edition'}<=set(row['source_urls'])
    assert loaded['audit']['metadata_conflict_count']==6
    assert {c['field'] for c in loaded['audit']['metadata_conflicts']}=={
        'title','authors','original_language','first_publication_year','region','series'}
