"""Actual transport measurements bound groups without dropping reviewed works."""
import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]


def load():
    sys.path.insert(0,str(ROOT/'scripts'))
    spec=importlib.util.spec_from_file_location('sized_content_review',ROOT/'scripts/review_scale_content_v3.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_normal_actual_sizes_keep_two_fifty_work_requests():
    module=load();selected=list(range(100));measured=[]
    def size(ids):measured.append(ids);return len(ids)*10000+100000
    groups=module.bounded_groups(selected,size)
    assert groups==[selected[:50],selected[50:]]
    assert len(measured)==2


def test_actual_measured_oversize_splits_before_send_without_omission_or_duplicate():
    module=load();selected=list(range(100))
    groups=module.bounded_groups(selected,lambda ids:len(ids)*25000+100000)
    assert [len(ids) for ids in groups]==[25,25,25,25]
    assert [work_id for ids in groups for work_id in ids]==selected
    assert all(len(ids)*25000+100000<=module.MAX_PROMPT_CHARS for ids in groups)


def test_single_oversized_actual_work_is_held_without_any_request():
    module=load()
    with pytest.raises(ValueError,match='no request sent'):
        module.bounded_groups(['large'],lambda ids:module.MAX_PROMPT_CHARS+1)


def test_exact_limit_is_allowed_and_native_review_code_remains_unchanged():
    module=load()
    assert module.bounded_groups(['w'],lambda ids:module.MAX_PROMPT_CHARS)==[['w']]
    original=ROOT/'scripts/review_world_pilot_v1.py'
    before=hashlib.sha256(original.read_bytes()).hexdigest()
    bounded=module.load_bounded_review(['w'],100)
    assert bounded.EXPECTED_PROMPT_CHARS==100
    assert bounded.MAX_PROMPT_CHARS==1048576
    assert any(isinstance(value,str) and 'model not started' in value for value in bounded.execute.__code__.co_consts)
    assert hashlib.sha256(original.read_bytes()).hexdigest()==before
