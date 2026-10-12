import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from recommend_book.database import DatasetDB
from recommend_book.models import Contributor,Identifier,Title
from test_integrate_prose_poc import script as integration_script,snapshot
from test_resolution import record


def script():
    spec=importlib.util.spec_from_file_location('combine_poc_snapshots',Path(__file__).parents[1]/'scripts/combine_poc_snapshots.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def fixture(tmp_path,*,overlap=False):
    prose,resolution=snapshot(tmp_path)
    integrated=tmp_path/'integrated'
    integration_script().integrate(prose,resolution,integrated,old_results=None)
    frozen=tmp_path/'frozen';frozen.mkdir()
    ref=Identifier(source='wikidata',value='Q200' if overlap else 'Q999',scope='work')
    original=record('main',source='wikidata',titles=[Title(value='Unrelated main book')],contributors=[Contributor(name='Other author')],identifiers=[ref],original_language='en')
    edition=record('ndl-edition',source='ndl',record_kind='edition',work_refs=[ref],titles=[Title(value='Unrelated translated edition')],contributors=[Contributor(name='Other author'),Contributor(name='Translator',role='translator')],language='ja',original_language=None,identifiers=[Identifier(source='ndl',value='NDL-1',scope='edition')])
    with DatasetDB(frozen/'dataset.sqlite') as db:
        db.ingest([original,edition]);inputs=[w for w in db.work_inputs() if w.expression_id is None]
    path=frozen/'work-inputs.jsonl';path.write_text(''.join(w.model_dump_json()+'\n' for w in inputs),encoding='utf8')
    (frozen/'manifest.json').write_text(json.dumps({'work_count':1,'work_inputs_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}),encoding='utf8')
    return frozen,prose,integrated


def test_combination_preserves_main_inputs_ndl_and_immutable_prose_versions(tmp_path):
    frozen,prose,integrated=fixture(tmp_path);output=tmp_path/'combined'
    protected={p:hashlib.sha256(p.read_bytes()).hexdigest() for root in (frozen,prose,integrated) for p in root.rglob('*') if p.is_file()}
    report=script().combine(frozen,prose,integrated,output,expected_main=1,expected_prose=2,old_results=None,extra_protected=[])
    assert report['canonical_works']==3 and report['expected_canonical_works']==3
    assert report['main_inputs_unchanged']==1 and report['main_source_versions_unchanged']
    assert report['ndl_editions_preserved']==1
    assert report['prose_source_versions_preserved']==4
    assert report['classification_rebound'] is False
    assert not any(name.endswith(('-shm','-wal')) for name in report['output_sha256'])
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in protected.items())
    audit=json.loads((output/'audit.json').read_text())
    assert audit['integrity_check']=='ok' and audit['foreign_key_violations']==0
    assert audit['prose_evidence_rights_fulfilled']==4 and audit['edition_work_links_verified']==1
    comparisons=json.loads((output/'main-input-comparison.json').read_text())
    assert comparisons[0]['input_hash_unchanged'] and comparisons[0]['source_versions_unchanged']
    with sqlite3.connect(output/'dataset.sqlite') as db:
        assert db.execute("SELECT count(*) FROM editions e JOIN source_records r ON r.version_id=e.source_version_id WHERE r.source='ndl'").fetchone()[0]==1
        versions=db.execute("SELECT version_id,parent_version_id FROM source_records WHERE source='wikisource' AND source_id='3'").fetchall()
        assert len(versions)==2 and sum(parent is not None for _,parent in versions)==1
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('DELETE FROM source_records WHERE version_id=?',(versions[0][0],))
    crosswalk=json.loads((output/'prose-id-crosswalk.json').read_text())
    assert len(crosswalk)==2 and all(not c['old_classification_valid_for_new_input'] for c in crosswalk)
    assert {c['combined_canonical_work_id'] for c in crosswalk}.isdisjoint({c['work_id'] for c in comparisons})
    assert all(c['original_source_version_preserved'] for c in crosswalk)


def test_existing_work_id_overlap_is_rejected_without_count_adjustment(tmp_path):
    frozen,prose,integrated=fixture(tmp_path,overlap=True)
    with pytest.raises(ValueError,match='already present'):
        script().combine(frozen,prose,integrated,tmp_path/'out',expected_main=1,expected_prose=2,old_results=None,extra_protected=[])


def test_mismatched_frozen_input_is_rejected_before_ingestion(tmp_path):
    frozen,prose,integrated=fixture(tmp_path)
    path=frozen/'work-inputs.jsonl';input_row=json.loads(path.read_text());input_row['title']='Tampered frozen title';path.write_text(json.dumps(input_row)+'\n')
    manifest=json.loads((frozen/'manifest.json').read_text());manifest['work_inputs_sha256']=hashlib.sha256(path.read_bytes()).hexdigest();(frozen/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='frozen input differs'):
        script().combine(frozen,prose,integrated,tmp_path/'out',expected_main=1,expected_prose=2,old_results=None,extra_protected=[])


def test_import_manifest_hash_mismatch_is_rejected(tmp_path):
    frozen,prose,integrated=fixture(tmp_path)
    with (integrated/'enriched-prose-source-records.jsonl').open('a') as f:f.write('\n')
    with pytest.raises(ValueError,match='import output SHA'):
        script().combine(frozen,prose,integrated,tmp_path/'out',expected_main=1,expected_prose=2,old_results=None,extra_protected=[])


def test_import_provenance_must_match_immutable_standalone_versions(tmp_path):
    frozen,prose,integrated=fixture(tmp_path)
    path=integrated/'enriched-prose-source-records.jsonl';payload=json.loads(path.read_text(encoding='utf8'))
    payload['extra']['injected_provenance']='not present in verified standalone DB'
    path.write_text(json.dumps(payload)+'\n',encoding='utf8')
    manifest_path=integrated/'manifest.json';manifest=json.loads(manifest_path.read_text())
    manifest['output_sha256'][path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='immutable prose import source-version provenance changed'):
        script().combine(frozen,prose,integrated,tmp_path/'out',expected_main=1,expected_prose=2,old_results=None,extra_protected=[])


def test_combination_never_overwrites_existing_snapshot(tmp_path):
    frozen,prose,integrated=fixture(tmp_path);output=tmp_path/'out';output.mkdir();(output/'preserve').write_text('original')
    with pytest.raises(ValueError,match='new'):
        script().combine(frozen,prose,integrated,output,expected_main=1,expected_prose=2,old_results=None,extra_protected=[])
    assert (output/'preserve').read_text()=='original'
