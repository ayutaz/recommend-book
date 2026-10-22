"""Model-free entry boundary tests for the review-v6 helper fix."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def module():
    spec = importlib.util.spec_from_file_location('review_scale_content_v6_fixture',
                                                   ROOT / 'scripts/review_scale_content_v6.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize('slots', [4, 8])
def test_execute_reaches_group_plan_without_missing_concurrency_helper(tmp_path, monkeypatch, slots):
    review = module()
    selection = tmp_path / 'selection.json'
    selection.write_text('{"selected_work_ids":["selected"]}', encoding='utf-8')
    monkeypatch.setattr(review, 'preview_prompt_chars', lambda args, ids: 10)

    def stop_before_model(*args, **kwargs):
        raise RuntimeError('fixture stopped before model')

    monkeypatch.setattr(review, 'load_bounded_review', stop_before_model)
    args = SimpleNamespace(selection_path=selection, output=tmp_path / 'out',
                           max_review_concurrency=slots)
    with pytest.raises(RuntimeError, match='before model'):
        review.execute(args)
    assert (args.output / 'review-plan.json').exists()
    assert (args.output / 'group-01.selection.json').exists()


def test_review6_preserves_selected_enum_and_execute_interface():
    review = module()
    schema = review.review_schema(['selected'])
    assert schema['properties']['reviews']['items']['properties']['work_id']['enum'] == ['selected']
    bounded = review.load_bounded_review(['selected'], 10)
    assert bounded.review_schema()['properties']['reviews']['items']['properties']['work_id']['enum'] == ['selected']
    assert 'max_review_concurrency' not in review.execute.__code__.co_varnames
    source = review.adapted_source((ROOT / 'scripts/review_scale_content_v4.py').read_bytes())
    assert 'review_scale_content_v5.py' in source
    assert 'review_scale_content_v4.py' in source
    assert 'review_scale_content_v3.py' in source


def test_review6_rejects_review5_code_drift(monkeypatch):
    review = module()
    original = review._review5
    monkeypatch.setattr(review, '_review5', lambda: original())
    with pytest.raises(ValueError, match='Frozen review-v5'):
        monkeypatch.setattr(review, 'REVIEW_V5_SHA', '0' * 64)
        review.adapted_source((ROOT / 'scripts/review_scale_content_v4.py').read_bytes())
