"""Temporary fake CLI only: no model service, network or real database writes."""
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from test_light_novel_admission_v3 import source, claim
from test_light_novel_work_admission_v3 import work_source
from test_light_novel_work_sources_v1 import work_bundle
from recommend_book.light_novel_work_history_v1 import observe,verify,recheck_current
import recommend_book.light_novel_work_classification_v1 as runner


@pytest.fixture
def history(tmp_path):
    world=tmp_path/'world.sqlite';ln=tmp_path/'ln.sqlite';runs=tmp_path/'runs';ledger=tmp_path/'reserved'
    runs.mkdir();ledger.mkdir()
    with sqlite3.connect(world) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT,manifest_path TEXT,manifest_sha256 TEXT)')
    with sqlite3.connect(ln) as db:
        db.execute('CREATE TABLE features(entry_id TEXT,manifest_path TEXT,manifest_sha256 TEXT)')
    def capture():
        audit=observe(['lnw:wd:Q1'],world_database=world,ln_database=ln,run_roots=[runs],reservation_roots=[ledger])
        path=tmp_path/'history.json';path.write_text(json.dumps(audit),encoding='utf8')
        return path,runner.sha(path),audit
    return world,ln,runs,ledger,capture


@pytest.mark.parametrize('kind',['world','ln_work','ln_old_series','failed_run','unstarted_series_reservation'])
def test_all_independent_history_namespaces_hold_same_qid(history,kind):
    world,ln,runs,ledger,capture=history
    if kind=='world':
        with sqlite3.connect(world) as db:db.execute('INSERT INTO native_classifications VALUES(?,?,?)',('Q1','old','a'*64))
    elif kind.startswith('ln_'):
        with sqlite3.connect(ln) as db:db.execute('INSERT INTO features VALUES(?,?,?)',('lns:wd:Q1' if kind=='ln_old_series' else 'lnw:wd:Q1','old','a'*64))
    elif kind=='failed_run':(runs/'manifest.json').write_text(json.dumps({'failures':[{'work_id':'wd:Q1'}]}),encoding='utf8')
    else:(ledger/'old.json').write_text(json.dumps({'series_id':'lns:wd:Q1','automatic_retry_allowed':False}),encoding='utf8')
    path,pin,audit=capture()
    assert audit['blocked_work_ids']==['lnw:wd:Q1']
    with pytest.raises(ValueError,match='never fresh calls'):verify(path,pin,['lnw:wd:Q1'])


def test_new_history_after_checkpoint_is_rechecked_before_calls(history):
    world,ln,runs,ledger,capture=history;path,pin,audit=capture()
    verify(path,pin,['lnw:wd:Q1'])
    with sqlite3.connect(world) as db:db.execute('INSERT INTO native_classifications VALUES(?,?,?)',('Q1','new','b'*64))
    with pytest.raises(ValueError,match='prevents new model calls'):recheck_current(audit)


FAKE_CLI = r'''
import json,sys
if '--version' in sys.argv:
 print('offline-fixture-1.0');raise SystemExit(0)
prompt=sys.stdin.read();data=json.loads(prompt.rsplit('LOCAL DATA\n'.replace('\\n','\n'),1)[1])
if isinstance(data['work'],list):
 value={'reviews':[{'work_id':row['input']['work_id'],'reviewed_label_ids':[label['label_id'] for label in row['native_result']['labels']],'summary':'Offline fixture review','findings':[]} for row in data['work']]}
else:
 work=data['work'];ev=work['evidence'][0]
 value={**data['output_identity'],'labels':[{'label_id':'genre.fantasy','axis':'genre','decision':'present','scope':'work','relevance':.9,'raw_confidence':.8,'calibrated_confidence':None,'calibration_status':'uncalibrated','calibration_version':None,'evidence':[{'evidence_id':ev['evidence_id'],'locator':ev['locator'],'quote':None,'summary':'Offline fixture label','directness':'direct'}],'missing_reason':None,'spoiler':'premise'}],'missing_axes':[{'axis':a,'decision':'unknown','reason':'Insufficient evidence'} for a in ('theme','structure','protagonist','style','atmosphere')],'conflicts':[],'needs_human_review':False}
text=json.dumps(value);open(sys.argv[sys.argv.index('-o')+1],'w',encoding='utf8').write(text)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':text}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
'''


@pytest.fixture
def completed(work_bundle,history,tmp_path,monkeypatch):
    work=work_bundle[3];source_manifest=work_bundle[5];world,ln,runs,ledger,capture=history
    hp,hpin,audit=capture();archive=runner.freeze_runtime(tmp_path/'runtime')
    monkeypatch.setattr(runner,'ROOT',archive)
    executable=tmp_path/'fake-cli.py';executable.write_text(FAKE_CLI,encoding='utf8')
    monkeypatch.setattr(runner,'resolve_executable',lambda value:[sys.executable,str(executable)])
    result=runner.run_window([work],runs/'window',source_manifest=source_manifest,
        source_sha256=runner.sha(source_manifest),reservations=ledger,
        history_manifest=hp,history_sha256=hpin,concurrency=4,timeout=30)
    return result,archive,ledger,runs


def test_full_fake_cli_work_classification_review_and_export_replay(completed):
    result,archive,ledger,runs=completed
    assert result['gate']['passed'] and result['content_features_adopted_works']==1
    verified=runner.verify_feature_export(result['manifest_path'],result['manifest_sha256'])
    row=verified['rows'][0]
    assert row['entry_id']==row['work_id']=='lnw:wd:Q1' and row['scope']=='work'
    assert 'series_id' not in row
    assert len(list(ledger.glob('*.json')))==1
    assert len(list((runs/'window').glob('classification/*/process-start.json')))==1
    assert len(list((runs/'window').glob('review/*/process-start.json')))==1
    assert set(json.loads((archive/'light-novel-work-runtime-snapshot.json').read_bytes())['files'])==set(runner.RUNTIME_FILES)


def test_archived_work_verifier_replays_in_its_own_snapshot(completed,monkeypatch):
    result,archive,_,_=completed
    live=Path(__file__).resolve().parents[1];monkeypatch.setattr(runner,'ROOT',live)
    verified=runner.verify_feature_export(result['manifest_path'],result['manifest_sha256'])
    assert verified['rows'][0]['scope']=='work'


@pytest.mark.parametrize('tamper',['projection','no_review','history_omitted','reservation_changed','runtime_changed'])
def test_forged_export_or_mutated_original_proofs_never_adopt(completed,tamper):
    result,archive,ledger,_=completed;path=Path(result['manifest_path']);document=json.loads(path.read_bytes())
    if tamper=='projection':document['rows'][0]['labels'][0]['label']='invented'
    elif tamper=='no_review':document['review_attempts']=[]
    elif tamper=='history_omitted':document['artifact_sha256'].pop(document['history_manifest'])
    elif tamper=='reservation_changed':next(ledger.glob('*.json')).write_text('{}',encoding='utf8')
    else:(archive/'src/recommend_book/light_novel_work_admission_v3.py').write_text('changed',encoding='utf8')
    path.write_text(json.dumps(document),encoding='utf8')
    with pytest.raises(ValueError):runner.verify_feature_export(path,runner.sha(path))


def test_live_unfrozen_or_history_collision_never_calls_native(work_bundle,history,tmp_path,monkeypatch):
    work=work_bundle[3];source=work_bundle[5];world,ln,runs,ledger,capture=history;hp,hpin,_=capture()
    monkeypatch.setattr(runner,'_attempt',lambda *a,**k:pytest.fail('Native route must not start'))
    with pytest.raises(ValueError,match='immutable WORK runtime'):
        runner.run_window([work],runs/'unfrozen',source_manifest=source,source_sha256=runner.sha(source),reservations=ledger,history_manifest=hp,history_sha256=hpin)
    archive=runner.freeze_runtime(tmp_path/'archive');monkeypatch.setattr(runner,'ROOT',archive)
    with sqlite3.connect(world) as db:db.execute('INSERT INTO native_classifications VALUES(?,?,?)',('Q1','old','a'*64))
    with pytest.raises(ValueError,match='prevents new model calls'):
        runner.run_window([work],runs/'blocked',source_manifest=source,source_sha256=runner.sha(source),reservations=ledger,history_manifest=hp,history_sha256=hpin)
    assert not list(ledger.glob('*.json'))


def test_failed_native_keeps_original_ledger_and_cannot_restart(work_bundle,history,tmp_path,monkeypatch):
    work=work_bundle[3];source=work_bundle[5];world,ln,runs,ledger,capture=history;hp,hpin,_=capture()
    archive=runner.freeze_runtime(tmp_path/'archive');monkeypatch.setattr(runner,'ROOT',archive)
    executable=tmp_path/'failing-cli.py';executable.write_text(
        "import sys\nif '--version' in sys.argv: print('fixture-1');raise SystemExit(0)\nraise SystemExit(9)\n",encoding='utf8')
    monkeypatch.setattr(runner,'resolve_executable',lambda value:[sys.executable,str(executable)])
    kwargs={'source_manifest':source,'source_sha256':runner.sha(source),'reservations':ledger,
        'history_manifest':hp,'history_sha256':hpin,'timeout':30}
    with pytest.raises(ValueError):runner.run_window([work],runs/'failed',**kwargs)
    saved={p.name:p.read_bytes() for p in ledger.glob('*.json')}
    assert len(saved)==1
    assert len(list((runs/'failed').glob('classification/*/process-start.json')))==1
    with pytest.raises(ValueError,match='prevents new model calls'):
        runner.run_window([work],runs/'restart',**kwargs)
    assert {p.name:p.read_bytes() for p in ledger.glob('*.json')}==saved
    assert not (runs/'restart').exists()


def test_work_export_dispatch_and_target_unit_guard(completed):
    from recommend_book.light_novel_feature_exports_v1 import verify_feature_export,target_allowed
    result,_,_,_=completed
    document=verify_feature_export(result['manifest_path'],result['manifest_sha256']);row=document['rows'][0]
    assert target_allowed(document,row,{'entry_id':'lnw:wd:Q1','entry_kind':'work'})
    assert not target_allowed(document,row,{'entry_id':'lnw:wd:Q1','entry_kind':'series'})
    assert not target_allowed(document,row,{'entry_id':'lns:wd:Q1','entry_kind':'series'})
    assert not target_allowed({'version':'unknown'},row,{'entry_id':'lnw:wd:Q1','entry_kind':'work'})


@pytest.mark.parametrize('version',['light-novel-series-feature-export-v1','light-novel-series-feature-export-v2'])
@pytest.mark.parametrize('shape',['entry_id','series_id_only'])
def test_archived_series_target_gate_retains_original_verified_row_compatibility(version,shape):
    from recommend_book.light_novel_feature_exports_v1 import target_allowed
    row={'series_id':'lns:wd:Q1','scope':'series'}
    if shape=='entry_id':row['entry_id']='lns:wd:Q1'
    assert target_allowed({'version':version},row,{'entry_id':'lns:wd:Q1','entry_kind':'series'})
    assert not target_allowed({'version':version},row,{'entry_id':'lns:wd:Q1','entry_kind':'work'})
    assert not target_allowed({'version':version},row,{'entry_id':'lns:wd:Q9','entry_kind':'series'})
