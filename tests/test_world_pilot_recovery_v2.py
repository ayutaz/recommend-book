"""Fixture control-flow checks; actual source/model results are never fabricated."""
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('actual_recovery_v2',SCRIPTS/'verify_world_pilot_recovery_v2.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def fixture_plan(tmp_path):
    fixed=tmp_path/'source.txt';fixed.write_text('fixed fixture input')
    run=tmp_path/'original-run';run.mkdir()
    database=tmp_path/'adopted.sqlite';database.write_bytes(b'fixture only; no actual model result')
    output=tmp_path/'published'
    builder=tmp_path/'builder.py'
    builder.write_text("import json,sys\nfrom pathlib import Path\np=Path(sys.argv[1]);p.mkdir()\n(p/'report.json').write_text(json.dumps({'new_display_n':10,'new_feature_bearing_n':4,'merged_display_n':1999,'private_profile_connection_n':2}))\n",encoding='utf-8')
    plan={'fixed_sha256':{str(fixed):module.base.sha(fixed)},'first10_run':str(run),'first10_run_inventory':{},
        'pending_rest26_run':str(tmp_path/'must-not-start'),'preparation_root':str(tmp_path/'preparation'),
        'first10_delivery_output':str(output),'delivery_command':[sys.executable,str(builder),str(output)],
        'adopted_first10_database':str(database),'first10_job_keys':['fixture-job']}
    path=tmp_path/'plan.json';module.base.save(path,plan)
    return path,plan,fixed


def test_normal_boundary_release_reaches_shared_real_subprocess_builder(tmp_path,monkeypatch):
    path,plan,_=fixture_plan(tmp_path)
    monkeypatch.setattr(module.base,'validate_first10',lambda plan:([],{'native_results_verified':10,'fixture_only':True}))
    monkeypatch.setattr(module.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(b'P')))
    result=module.boundary_worker(path)
    assert result['actual_exit_code']==0 and result['actual_delivery_builder_pid']>0
    assert result['coordinator_path']=='released-boundary'
    assert (Path(plan['first10_delivery_output'])/'report.json').exists()
    assert module.base.read(tmp_path/'publication-process-exit.json')['process_exit_observed'] is True


def test_source_drift_refuses_publication_before_subprocess(tmp_path):
    path,plan,fixed=fixture_plan(tmp_path);fixed.write_text('changed')
    with pytest.raises(ValueError,match='changed'):
        module.publish_delivery(path,{})
    assert not Path(plan['first10_delivery_output']).exists()
    assert not (tmp_path/'publication-process-start.json').exists()


def test_existing_output_refuses_duplicate_publication(tmp_path):
    path,plan,_=fixture_plan(tmp_path);Path(plan['first10_delivery_output']).mkdir()
    with pytest.raises(ValueError,match='duplicate publication'):
        module.publish_delivery(path,{})
    assert not (tmp_path/'publication-process-start.json').exists()


def test_changed_durable_checkpoint_refuses_resume_before_validation(tmp_path,monkeypatch):
    path,_,_=fixture_plan(tmp_path)
    module.base.save(tmp_path/'checkpoint.json',{'plan_sha256':module.base.sha(path),'script_sha256':module.base.sha(module.__file__)})
    module.base.save(tmp_path/'interruption.json',{'checkpoint_sha256':'foreign','actual_exit_code':1,'process_exit_observed':True})
    monkeypatch.setattr(module.base,'validate_first10',lambda plan:pytest.fail('Changed checkpoint reached actual validation'))
    with pytest.raises(ValueError,match='checkpoint changed'):
        module.resume(path,True)
