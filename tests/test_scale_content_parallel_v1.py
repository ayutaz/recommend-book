"""Measured expansion cannot bypass genuine previous terminal gate evidence."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def load():
    spec=importlib.util.spec_from_file_location('content_parallel_prepare',ROOT/'scripts/prepare_scale_content_chunk_v2.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('initial,final,failed,pending,stopped',[
    (2,2,0,0,False),(4,2,0,0,False),(8,4,0,0,False),
    (4,4,1,0,False),(4,4,0,1,False),(4,4,0,0,True),
])
def test_expansion_rejects_unmeasured_failed_reduced_or_pending_previous_route(tmp_path,monkeypatch,initial,final,failed,pending,stopped):
    from recommend_book import scale_content_control_v2 as control
    run=tmp_path/'run.json';run.write_text(json.dumps({'initial_concurrency':initial,'final_concurrency':final,
        'failed':failed,'pending':pending,'stopped':stopped,'stop_reasons':[]}))
    receipt={'control_artifacts':{'run':{'path':str(run)}}}
    monkeypatch.setattr(control,'verify_control_receipt',lambda *args:receipt)
    with pytest.raises(ValueError,match='actual clean completed'):
        load().trial_control(tmp_path/'control.json','sha')


@pytest.mark.parametrize('parallel',[4,8])
def test_clean_actual_four_or_eight_slot_gate_can_authorize_eight(tmp_path,monkeypatch,parallel):
    from recommend_book import scale_content_control_v2 as control
    run=tmp_path/'run.json';run.write_text(json.dumps({'initial_concurrency':parallel,'final_concurrency':parallel,
        'failed':0,'pending':0,'stopped':False,'stop_reasons':[]}))
    receipt={'control_artifacts':{'run':{'path':str(run)}}}
    monkeypatch.setattr(control,'verify_control_receipt',lambda *args:receipt)
    assert load().trial_control(tmp_path/'control.json','sha')==receipt


def test_eight_slot_preparation_requires_gate_before_creating_any_namespace(tmp_path):
    with pytest.raises(ValueError,match='authentic control receipt required'):
        load().prepare(tmp_path/'absent-runtime',tmp_path/'inputs',tmp_path/'ledger','new',8)
    assert list(tmp_path.iterdir())==[]


def test_versioned_preparer_keeps_historical_file_unchanged():
    import hashlib
    path=ROOT/'scripts/prepare_scale_classification_v1.py'
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    module=load().load_bounded_preparer()
    assert (2,4,8) in module.prepare_chunk.__code__.co_consts
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
