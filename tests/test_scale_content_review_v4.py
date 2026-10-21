import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).parents[1]


def reviewer():
    spec = importlib.util.spec_from_file_location(
        'review_scale_content_v4', ROOT / 'scripts/review_scale_content_v4.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_review_request_has_at_most_five_distinct_works():
    review = reviewer()
    selected = [f'work-{i}' for i in range(23)]
    groups = review.bounded_groups(selected, lambda ids: len(ids) * 100)
    assert [work for group in groups for work in group] == selected
    assert [len(group) for group in groups] == [5, 5, 5, 5, 3]


def test_transport_bound_still_splits_groups_and_rejects_oversized_single_work():
    review = reviewer()
    selected = list(range(9))
    groups = review.bounded_groups(selected, lambda ids: len(ids) * 300_000)
    assert [work for group in groups for work in group] == selected
    assert all(len(group) <= 5 and len(group) * 300_000 <= review.MAX_PROMPT_CHARS for group in groups)
    with pytest.raises(ValueError, match='no request sent'):
        review.bounded_groups([1], lambda ids: review.MAX_PROMPT_CHARS + 1)


@pytest.mark.parametrize('slots', [4, 8])
def test_review_concurrency_uses_the_explicit_native_slot_count(slots):
    assert reviewer().review_concurrency(SimpleNamespace(max_review_concurrency=slots)) == slots


@pytest.mark.parametrize('slots', [None, 0, True, 5, 9])
def test_invalid_concurrency_is_rejected_before_any_review_work(slots):
    with pytest.raises(ValueError, match='four or eight'):
        reviewer().execute(SimpleNamespace(max_review_concurrency=slots))


def test_review_adapter_rejects_modified_original_code():
    with pytest.raises(ValueError, match='Frozen review-v3 changed'):
        reviewer().adapted_source(b'changed')
