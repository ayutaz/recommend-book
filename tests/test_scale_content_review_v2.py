"""Review transport boundaries preserve exact real completion coverage."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]


def load():
    spec=importlib.util.spec_from_file_location('sharded_content_review',ROOT/'scripts/review_scale_content_v2.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('completed_count',[95,100])
def test_disjoint_requests_cover_completed_only_without_fabricated_held_rows(tmp_path,monkeypatch,completed_count):
    sys.path.insert(0,str(ROOT/'scripts'))
    import review_scale_content_v1 as original
    selected=[f'w{i:03d}' for i in range(completed_count)]
    selection=tmp_path/'selected.json';selection.write_text(json.dumps({'selected_work_ids':selected}))
    called=[]

    def bounded(ids):
        def execute(args,**kwargs):
            called.append(list(ids));args.output.mkdir()
            audit=args.output/'source-audit.json'
            audit.write_text(json.dumps({'reviews':[{'work_id':work_id} for work_id in ids]}))
            document={'run_id':'actual-run','ordered_inputs_sha256':'inputs','run_manifest_sha256':'run',
                'results_sha256':'results','execution_context_manifest_sha256':'context','cases':[],
                'source_audits':[{'path':str(audit),'sha256':hashlib.sha256(audit.read_bytes()).hexdigest()}]}
            review=args.output/'quality-adoption-review.json';review.write_text(json.dumps(document))
            return {'review_path':str(review),'observed_usage':{'input_tokens':100,'output_tokens':10}}
        return SimpleNamespace(execute=execute)

    monkeypatch.setattr(original,'load_review',bounded)
    frozen=ModuleType('recommend_book.next_database_v4');frozen._quality_document=lambda path:json.loads(path.read_bytes())
    monkeypatch.setitem(sys.modules,'recommend_book.next_database_v4',frozen)
    result=load().execute(SimpleNamespace(selection_path=selection,output=tmp_path/'review'))
    assert sorted(len(group) for group in called)==[completed_count-50,50]
    flattened=[work_id for group in called for work_id in group]
    assert len(flattened)==len(set(flattened))==completed_count
    assert set(flattened)==set(selected)
    assert result['reviewed_n']==completed_count and result['actual_review_model_request_count']==2
    assert result['observed_usage']=={'input_tokens':200,'output_tokens':20}
    merged=json.loads(Path(result['review_path']).read_bytes())
    assert len(merged['source_audits'])==2


def test_duplicate_review_authorities_rejected_before_any_group(tmp_path):
    selection=tmp_path/'selected.json';selection.write_text(json.dumps({'selected_work_ids':['w','w']}))
    with pytest.raises(ValueError,match='Distinct actual review selection'):
        load().execute(SimpleNamespace(selection_path=selection,output=tmp_path/'review'))
    assert not (tmp_path/'review').exists()


@pytest.mark.parametrize('fault',['missing_group','duplicate_work','different_parent'])
def test_merge_rejects_missing_duplicate_or_different_parent(tmp_path,monkeypatch,fault):
    sys.path.insert(0,str(ROOT/'scripts'))
    import review_scale_content_v1 as original
    selection=tmp_path/'selected.json'
    selection.write_text(json.dumps({'selected_work_ids':[f'w{i:03d}' for i in range(100)]}))
    def fake(ids):
        def execute(args,**kwargs):
            args.output.mkdir(); second=ids[0]=='w050'; covered=list(ids)
            if second and fault=='missing_group':covered=[]
            if second and fault=='duplicate_work':covered[0]='w000'
            audit=args.output/'source-audit.json'
            audit.write_text(json.dumps({'reviews':[{'work_id':x} for x in covered]}))
            document={'run_id':'different' if second and fault=='different_parent' else 'same',
                'ordered_inputs_sha256':'i','run_manifest_sha256':'r','results_sha256':'s',
                'execution_context_manifest_sha256':'c','cases':[],
                'source_audits':[{'path':str(audit),'sha256':hashlib.sha256(audit.read_bytes()).hexdigest()}]}
            review=args.output/'quality-adoption-review.json';review.write_text(json.dumps(document))
            return {'review_path':str(review),'observed_usage':{}}
        return SimpleNamespace(execute=execute)
    monkeypatch.setattr(original,'load_review',fake)
    with pytest.raises(ValueError,match='bindings differ|omit or repeat'):
        load().execute(SimpleNamespace(selection_path=selection,output=tmp_path/'review'))
    assert not (tmp_path/'review/quality-adoption-review.json').exists()


@pytest.mark.parametrize('fault',['nonzero_terminal','unrelated_failure'])
def test_resume_rejects_ineligible_terminal_without_native_execution(tmp_path,monkeypatch,fault):
    script=ROOT/'scripts/resume_scale_content_review_v1.py'
    spec=importlib.util.spec_from_file_location('resume_content',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('Native coordinator must not execute')
    monkeypatch.setattr(module,'ModuleType',forbidden)
    import recommend_book.scale_coordinator_proof_v1 as chain
    monkeypatch.setattr(chain,'coordinator_chain_pins',lambda document:[])
    source=tmp_path/'source';source.mkdir();(source/'manifest.json').write_text(json.dumps({'chunks':[{'run_name':'run'}]}))
    runtime=tmp_path/'runtime';terminal=runtime/'terminal-run';terminal.mkdir(parents=True)
    original=tmp_path/'scripts/run_scale_content_classification_v1.py';original.parent.mkdir()
    original.write_text('raise AssertionError("not reached")')
    nonzero=fault=='nonzero_terminal'
    (terminal/'adoption-arguments.json').write_text(json.dumps({'exit_code':1 if nonzero else 0,'run_dir':str(tmp_path/'run')}))
    (terminal/'terminal-proof.json').write_text(json.dumps({'exit_code':1 if nonzero else 0,
        'process_exit_observed':True,'manifest_sha256':'m'}))
    (terminal/'request.json').write_text(json.dumps({'coordinator_sha256':hashlib.sha256(original.read_bytes()).hexdigest()}))
    failed=tmp_path/'data/autonomous-next-v3/scale-30000-v1/separate-review/run';failed.mkdir(parents=True)
    (failed/'process-exit.json').write_text(json.dumps({'actual_exit_code':1}))
    (failed/'stderr.txt').write_text('unrelated failure')
    with pytest.raises(ValueError,match='terminal-completed|only handles observed'):
        module.execute(source,runtime)
