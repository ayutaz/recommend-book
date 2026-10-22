"""Measured single-work budget; no changes to old gates or stopped outputs."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_native_actual_config_and_preflight_share_ten_million_budget():
    preparer = module('prepare_scale100k_content_chunk_v2')
    builder = preparer.load_bounded_preparer()
    constants = builder.prepare_chunk.__code__.co_consts
    assert 10000000 in constants and 2000000 not in constants
    source = preparer.adapted_source((ROOT / 'scripts/prepare_scale100k_content_chunk_v1.py').read_bytes())
    assert "load_taxonomy(), 1, execution_spec='provenance-v4')" in source
    assert "controls = {'batch_size': 1" in source
    assert 'from prepare_scale100k_content_chunk_v2 import frozen_prepare' in source


def test_review_combined_gate_and_native_contract_share_same_budget():
    coordinator = module('run_scale100k_content_classification_v2')
    source = coordinator.adapted_source((ROOT / 'scripts/run_scale100k_content_classification_v1.py').read_bytes())
    assert source.count('10000000') == 3 and '2000000' not in source
    assert 'full=True' in source and 'remaining_seconds = 3600' in source
    assert "prior_control['window_index'] != route_index - 1" in source
    assert 'prepare_scale100k_content_chunk_v2 import trial_control' in source
    assert 'scripts/run_scale100k_native_v2.py' in source
    assert coordinator.check_gate({'selected':100,'failed':0,'pending':24,'stopped':True,
                                   'stop_reasons':['token_budget']},
                                  {'actual_feature_adopted_works':76})['passed'] is False


@pytest.mark.parametrize('failed,adopted,passed', [(6,90,False),(0,69,False),(5,70,True)])
def test_actual_quality_thresholds_unchanged(failed, adopted, passed):
    coordinator = module('run_scale100k_content_classification_v2')
    assert coordinator.check_gate({'selected':100,'failed':failed,'pending':0},
                                  {'actual_feature_adopted_works':adopted})['passed'] is passed


@pytest.mark.parametrize('name', ['prepare_scale100k_content_chunk_v2',
                                'run_scale100k_content_classification_v2',
                                'run_scale100k_native_v2'])
def test_all_v2_adapters_reject_changed_v1_bytes(name):
    adapter = module(name)
    with pytest.raises(ValueError, match='changed'):
        adapter.adapted_source(b'changed')


def test_future_driver_uses_only_new_handoff_and_same_reserved_freshness_boundary():
    driver = module('run_scale100k_native_v2')
    raw = (ROOT / 'scripts/run_scale100k_native_v1.py').read_bytes()
    source = driver.adapted_source(raw)
    assert 'window-source-overrides-v2.json' in source
    assert "BASE / 'classification-sources-v2'" in source
    assert 'scripts/prepare_scale100k_content_chunk_v2.py' in source
    assert 'scripts/run_scale100k_content_classification_v2.py' in source
    assert 'state.reserve_window(source, global_index, index_path, expected_index_sha)' in source
    assert 'previous_control, previous_sha = state.PRIOR_CONTROL, state.PRIOR_CONTROL_SHA' in source
    assert 'concurrency = 8 if cohort and clean' in source
    assert 'verify_control_receipt(previous_control, previous_sha)' in source


def test_old_frozen_versions_still_exact():
    expected = {
        'src/recommend_book/scale100k_native_v1.py':'bb421f84e288261d34a1a6642dc35aa014eb88e32e85ba3f76040561628896d0',
        'scripts/prepare_scale100k_content_chunk_v1.py':'d68ec805c9f138fb2861831d7b9e56be5c0de0581c4714deeb3e6ab6299066db',
        'scripts/run_scale100k_content_classification_v1.py':'e9ed0dc490303a7d005a959bdc06fcdccc68a38d8a46ff1d03c2187f335ffd70',
        'scripts/run_scale100k_native_v1.py':'ca2c823fa1d822c2d69fda9d3ced266ae9246411783ec951373ade1a9d63c809',
    }
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in expected.items())
