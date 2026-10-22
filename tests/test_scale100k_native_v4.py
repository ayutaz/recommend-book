"""The fresh v7 entry preserves an actual failed v6 review without replay."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location('native_v4', ROOT / 'scripts/run_scale100k_native_v4.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_recipe_continues_actual_index_and_forces_only_first_window_to_four():
    m = module()
    source = m.adapted_source((ROOT / 'scripts/run_scale100k_native_v3.py').read_bytes())
    assert "cohort = prior['window_index'] - 41" in source
    assert 'starting_cohort = cohort' in source
    assert '8 if cohort > starting_cohort and clean' in source
    assert 'window-source-overrides-v3.json' in source
    assert 'classification-sources-v3' in source
    assert 'scripts/run_scale100k_content_classification_v4.py' in source
    assert 'scripts/run_scale100k_content_classification_v3.py' not in source
    assert 'scripts/prepare_scale100k_content_chunk_v2.py' in source
    assert 'generation_entry_receipt_sha256' in source
    assert 'verify_control_receipt(previous_control, previous_sha)' in source
    assert source.index('        verify_coordinator()') < source.index('        reservation = state.reserve_window')


def test_recipe_refuses_changed_frozen_driver():
    with pytest.raises(ValueError, match='Frozen'):
        module().adapted_source(b'changed')


def test_changed_new_coordinator_is_rejected_before_model(tmp_path,monkeypatch):
    m=module();monkeypatch.setattr(m,'ROOT',tmp_path)
    path=tmp_path/'scripts/run_scale100k_content_classification_v4.py'
    path.parent.mkdir();path.write_text('changed')
    with pytest.raises(ValueError,match='pin'):m.verify_coordinator()


def facts():
    return {
        'native': {'selected':100,'completed':100,'failed':0,'pending':0,'stopped':False,'stop_reasons':[]},
        'native_exit': {'actual_exit_code':0,'process_exit_observed':True},
        'coordinator_exit': {'actual_exit_code':1,'process_exit_observed':True},
        'stop': {'session_id':19712,'actual_exit_code':1,'process_exit_observed':True,
                 'tool_result':{'exit_code':1}},
        'groups': [dict(group=i,actual_exit_code=0,quality_adoption_review_saved=i!=14,
                        source_audit_saved=i!=14,expected_work_ids=[f'w{i}-{k}' for k in range(5)],
                        returned_work_ids=[f'w{i}-{k}' if i!=14 or k!=1 else 'foreign' for k in range(5)])
                   for i in range(1,21)],
        'whole': [{'group':8,'qid':'Q5466562','work_id':'held-work',
                   'status':'held','reason_codes':['unresolved_version_scope_conflict']}],
    }


def test_exact_six_percent_failure_is_quarantined_not_relabelled_pass():
    m = module()
    result = m.validate_failure_facts(**facts())
    assert result == {'unadopted_native_works':100,'mechanically_unreviewed_works':5,
                      'separate_review_whole_holds':1,'conservative_failure_rate':0.06,
                      'failed_window_accepted':False}


@pytest.mark.parametrize('problem', ['native_partial','native_stopped','native_bool_exit',
                                   'coordinator_bool_exit','coordinator_success','stop_bool_exit',
                                   'stop_bool_session','missing_group','wrong_foreign_group',
                                   'missing_audit','no_whole_hold','different_whole_reason'])
def test_other_failures_or_boolean_exit_are_rejected(problem):
    data = facts()
    if problem=='native_partial': data['native']['completed']=99
    elif problem=='native_stopped': data['native']['stopped']=True
    elif problem=='native_bool_exit': data['native_exit']['actual_exit_code']=False
    elif problem=='coordinator_bool_exit': data['coordinator_exit']['actual_exit_code']=True
    elif problem=='coordinator_success': data['coordinator_exit']['actual_exit_code']=0
    elif problem=='stop_bool_exit': data['stop']['tool_result']['exit_code']=True
    elif problem=='stop_bool_session': data['stop']['session_id']=True
    elif problem=='missing_group': data['groups'].pop()
    elif problem=='wrong_foreign_group': data['groups'][13]['group']=13
    elif problem=='missing_audit': data['groups'][0]['source_audit_saved']=False
    elif problem=='no_whole_hold': data['whole']=[]
    elif problem=='different_whole_reason': data['whole'][0]['reason_codes']=['semantic_entailment_uncertain']
    with pytest.raises(ValueError): module().validate_failure_facts(**data)


def test_seal_pin_rejects_changed_file(tmp_path,monkeypatch):
    m=module();monkeypatch.setattr(m,'ROOT',tmp_path)
    p=tmp_path/'proof.json';p.write_text('{}')
    expected=m.state.sha(p);pins={}
    assert m.pinned('proof.json',expected,pins)==p
    p.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='pin'): m.pinned('proof.json',expected,pins)


def test_first_source_cannot_be_replaced_after_entry(tmp_path,monkeypatch):
    import json
    m=module();monkeypatch.setattr(m,'BASE',tmp_path)
    source=tmp_path/'fresh';source.mkdir()
    manifest=source/'manifest.json';manifest.write_text('{"fixed":true}')
    entry={'fresh_source':{'source':str(source),'sha256':m.state.sha(manifest)}}
    (tmp_path/'generation-v7-entry.json').write_text(json.dumps(entry))
    m.assert_first_source(source)
    manifest.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='changed'):m.assert_first_source(source)
    with pytest.raises(ValueError,match='changed'):m.assert_first_source(tmp_path)


def test_exclusive_entry_is_saved_before_operations_and_never_restarted(tmp_path,monkeypatch):
    m=module();monkeypatch.setattr(m,'BASE',tmp_path)
    events=[]
    monkeypatch.setattr(m,'verify_failed_window',lambda:{'artifacts_sha256':{'failed':'fixed'}})
    monkeypatch.setattr(m,'verify_entry_source',lambda:{'source':'fresh','sha256':'fixed'})
    monkeypatch.setattr(m.state,'verify_requirements',lambda:None)
    monkeypatch.setattr(m.state,'load_exclusion_index',lambda *a:None)
    monkeypatch.setattr(m,'verify_coordinator',lambda:None)
    class Op:
        def run(self,*args):
            assert (tmp_path/'generation-v7-entry.json').exists()
            events.append(args)
    monkeypatch.setattr(m,'load_operations',lambda:Op())
    m.start('index','fixed')
    assert len(events)==1
    with pytest.raises(FileExistsError):m.start('index','fixed')
    assert len(events)==1
