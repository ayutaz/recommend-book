"""Safe projection admission never rewrites historical source or input identity."""
import importlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from recommend_book.classification.validation import input_hash
from recommend_book.jobs import canonical_json
from recommend_book.models import SourceRecord, Title
from recommend_book.database import DatasetDB
from recommend_book import next_database as next_db
from test_next_database import source_database
from test_import_classification_database import sha, save


def api():
    return importlib.import_module('recommend_book.sealed_projection')


@pytest.fixture
def pack(source_database, tmp_path):
    with sqlite3.connect(source_database) as db:
        record = SourceRecord.model_validate_json(db.execute('SELECT record_json FROM source_records LIMIT 1').fetchone()[0])
    db.close()
    record.titles.append(Title(value='日本語別名', language='ja', kind='alternative'))
    with DatasetDB(source_database) as db:
        db.ingest([record])
    base = tmp_path / 'baseline'
    next_db.build_next_database(source_database=source_database, output_dir=base)
    proposals = next_db.get_next_inputs(base / 'dataset.sqlite')
    directory = tmp_path / 'sealed-source'
    directory.mkdir()
    lineages = []
    paths = {}
    for kind, key in [('bibliographic','bibliographic'), ('classification','classification'), ('alias','aliases')]:
        works = proposals[key]
        if kind == 'classification':
            # A metadata-only classification is a new input, not an old result.
            projected = [w.model_copy(update={'evidence': []}, deep=True) for w in works]
        else:
            projected = works
        path = directory / (key + '-inputs.jsonl')
        path.write_text(''.join(canonical_json(w.model_dump(mode='json'))+'\n' for w in projected), encoding='utf-8')
        paths[kind] = path
        for position, (old, work) in enumerate(zip(works, projected), 1):
            lineages.append({'kind':kind, 'input_hash':input_hash(work), 'base_input_hash':input_hash(old),
                'source_evidence_keys':{}, 'excluded_refs':[], 'excluded_evidence':[
                    {'evidence_id':ev.evidence_id,'model_sha256':next_db._value_sha(ev.model_dump(mode='json')),'reasons':['fixture unverified material']}
                    for ev in old.evidence], 'purposes':{purpose:{'allowed':True,'reasons':[]} for purpose in ['display','external_llm']},
                'line_number':position})
    lineage = directory / 'lineage.jsonl'
    lineage.write_text(''.join(canonical_json(row)+'\n' for row in lineages), encoding='utf-8')
    ledger = directory / 'usage-ledger.json'
    save(ledger, {'fixture_only':True})
    manifest = directory / 'manifest.json'
    from recommend_book.usage_guard import GUARD_VERSION
    save(manifest, {'base_database_sha256':sha(base/'dataset.sqlite'), 'usage_ledger_sha256':sha(ledger),
        'guard_version':GUARD_VERSION, 'inputs':{kind:{'path':str(path),'sha256':sha(path)} for kind,path in paths.items()},
        'lineage_path':'lineage.jsonl','lineage_sha256':sha(lineage), 'record_scope':{'inventory_canonical_n':2}})
    class Guard:
        ledger_path = ledger
        def check_input(self, work, usage):
            return SimpleNamespace(allowed=True, reasons=[])
    return {'base_database':base/'dataset.sqlite', 'bibliography_inputs':paths['bibliographic'],
        'classification_inputs':paths['classification'], 'alias_inputs':paths['alias'],
        'projection_manifest':manifest,'usage_ledger':ledger,'output_dir':tmp_path/'projected', 'guard':Guard()}


def rewrite(pack, kind, mutate):
    manifest = json.loads(pack['projection_manifest'].read_bytes())
    path = Path(manifest['inputs'][kind]['path'])
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    mutate(rows[0])
    path.write_text(''.join(canonical_json(row)+'\n' for row in rows), encoding='utf-8')
    manifest['inputs'][kind]['sha256'] = sha(path)
    # Attacker updates the public input hash too; source/semantic checks remain necessary.
    lineage_path = pack['projection_manifest'].parent / 'lineage.jsonl'
    lines = [json.loads(line) for line in lineage_path.read_bytes().splitlines()]
    from recommend_book.models import WorkInput
    next(row for row in lines if row['kind']==kind)['input_hash'] = input_hash(WorkInput.model_validate(rows[0]))
    lineage_path.write_text(''.join(canonical_json(row)+'\n' for row in lines), encoding='utf-8')
    manifest['lineage_sha256'] = sha(lineage_path)
    save(pack['projection_manifest'], manifest)


def test_seal_archives_old_projections_and_preserves_history_with_new_hashes(pack):
    helper = next_db._importer()
    source = next_db._readonly(pack['base_database'])
    signatures = helper.table_signatures(source)
    original_rows = [tuple(row) for row in source.execute('SELECT * FROM next_projection_inputs')]
    source.close()
    before = sha(pack['base_database'])
    report = api().seal_next_projections(**pack)
    assert report['state'] == 'purpose_certified_projections_derivation_seal_pending'
    assert report['bibliographic_work_count'] == 2
    assert report['classification_candidate_count'] == 2
    assert report['classification_adopted_count'] == 0
    assert report['alias_input_count'] == 1
    assert sha(pack['base_database']) == before
    with sqlite3.connect(pack['output_dir']/'dataset.sqlite') as db:
        after = helper.table_signatures(db)
        assert all(after[name]==value for name,value in signatures.items() if name not in ('next_projection_inputs','next_source_refs'))
        assert sorted(db.execute('SELECT * FROM next_projection_inputs_archive').fetchall()) == sorted(original_rows)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    inputs = next_db.get_next_inputs(pack['output_dir']/'dataset.sqlite')
    assert all(not work.evidence for work in inputs['classification'])
    assert all(packet['originating_input_hashes']==[packet['input_hash']] for packet in
               [json.loads(line) for line in (pack['output_dir']/'derivations.jsonl').read_bytes().splitlines()])


@pytest.mark.parametrize('failure',['metadata','expression','source','evidence','alias','duplicate','denied_claim','source_sha'])
def test_seal_rejects_semantic_or_provenance_substitution(pack, failure):
    if failure == 'metadata': rewrite(pack,'classification',lambda row:row.update(authors=['Invented author']))
    elif failure == 'expression': rewrite(pack,'classification',lambda row:row.update(expression_id='translation-new'))
    elif failure == 'source': rewrite(pack,'classification',lambda row:row.update(source_record_ids=['unknown-version']))
    elif failure == 'evidence':
        base = next_db.get_next_inputs(pack['base_database'])['classification'][0]
        ev = base.evidence[0].model_dump(mode='json')
        ev['text'] = 'A fabricated replacement for real evidence.'
        rewrite(pack,'classification',lambda row:row.update(evidence=[ev]))
    elif failure == 'alias': rewrite(pack,'alias',lambda row:row.update(title='Invented translation title'))
    elif failure == 'duplicate':
        path=pack['bibliography_inputs']
        path.write_bytes(path.read_bytes()+path.read_bytes().splitlines()[0]+b'\n')
        manifest=json.loads(pack['projection_manifest'].read_bytes())
        manifest['inputs']['bibliographic']['sha256']=sha(path)
        save(pack['projection_manifest'],manifest)
    elif failure == 'denied_claim':
        pack['guard'].check_input=lambda work,usage:SimpleNamespace(allowed=False,reasons=['held material'])
    else:
        manifest=json.loads(pack['projection_manifest'].read_bytes())
        manifest['base_database_sha256']='0'*64
        save(pack['projection_manifest'],manifest)
    with pytest.raises(ValueError): api().seal_next_projections(**pack)
    assert not pack['output_dir'].exists()


def test_seal_rejects_mutation_before_publication_and_existing_or_input_output(pack, monkeypatch):
    module=api()
    monkeypatch.setattr(module,'_before_publish',lambda:pack['bibliography_inputs'].write_bytes(b'{}\n'))
    with pytest.raises(ValueError,match='changed'): module.seal_next_projections(**pack)
    assert not pack['output_dir'].exists()
    assert not list(pack['output_dir'].parent.glob('.projected.projection-*'))
    pack['output_dir']=pack['projection_manifest'].parent/'bad-child'
    with pytest.raises(ValueError,match='outside'): module.seal_next_projections(**pack)


def test_denied_candidates_remain_in_inventory_but_are_not_admitted(pack):
    path=pack['projection_manifest'].parent/'lineage.jsonl'
    rows=[json.loads(line) for line in path.read_bytes().splitlines()]
    for row in rows:
        row['purposes']={usage:{'allowed':False,'reasons':['fixture hold']} for usage in ['display','external_llm']}
    path.write_text(''.join(canonical_json(row)+'\n' for row in rows),encoding='utf-8')
    manifest=json.loads(pack['projection_manifest'].read_bytes())
    manifest['lineage_sha256']=sha(path)
    save(pack['projection_manifest'],manifest)
    pack['guard'].check_input=lambda work,usage:SimpleNamespace(allowed=False,reasons=['fixture hold'])
    report=api().seal_next_projections(**pack)
    assert report['bibliographic_work_count']==2
    assert report['bibliographic_display_allowed_count']==0
    assert report['classification_candidate_count']==0
    assert report['alias_input_count']==0
    with sqlite3.connect(pack['output_dir']/'dataset.sqlite') as db:
        assert db.execute('SELECT count(*) FROM next_sealed_projection_inventory').fetchone()[0]==5


def test_exact_evidence_source_key_subset_can_be_admitted(pack):
    old = next_db.get_next_inputs(pack['base_database'])['classification'][0]
    rewrite(pack,'classification',lambda row:row.update(evidence=[old.evidence[0].model_dump(mode='json')]))
    path = pack['projection_manifest'].parent/'lineage.jsonl'
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    row = next(item for item in rows if item['kind']=='classification')
    row['source_evidence_keys'] = {old.evidence[0].evidence_id:sorted(old.source_record_ids)}
    row['excluded_evidence'] = []
    path.write_text(''.join(canonical_json(item)+'\n' for item in rows),encoding='utf-8')
    manifest = json.loads(pack['projection_manifest'].read_bytes())
    manifest['lineage_sha256'] = sha(path)
    save(pack['projection_manifest'],manifest)
    assert api().seal_next_projections(**pack)['classification_candidate_count']==2


@pytest.mark.parametrize('invalid',['duplicate_json','overflow','bool_count','existing'])
def test_strict_json_counts_and_output_namespace(pack, invalid):
    if invalid=='existing':
        pack['output_dir'].mkdir()
    elif invalid=='bool_count':
        manifest=json.loads(pack['projection_manifest'].read_bytes())
        manifest['record_scope']['inventory_canonical_n']=True
        save(pack['projection_manifest'],manifest)
    else:
        pack['bibliography_inputs'].write_bytes(b'{"work_id":"x","work_id":"y"}\n' if invalid=='duplicate_json' else b'{"x":1e999}\n')
        manifest=json.loads(pack['projection_manifest'].read_bytes())
        manifest['inputs']['bibliographic']['sha256']=sha(pack['bibliography_inputs'])
        save(pack['projection_manifest'],manifest)
    with pytest.raises(ValueError): api().seal_next_projections(**pack)


def test_code_change_before_publication_refuses_sealed_database(pack, monkeypatch):
    module=api()
    original=module._sha
    changed=[False]
    monkeypatch.setattr(module,'_before_publish',lambda:changed.__setitem__(0,True))
    monkeypatch.setattr(module,'_sha',lambda path:'0'*64 if changed[0] and Path(path).resolve()==Path(module.__file__).resolve() else original(path))
    with pytest.raises(ValueError,match='changed'): module.seal_next_projections(**pack)
    assert not pack['output_dir'].exists()
